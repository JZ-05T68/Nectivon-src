"""Shared application services for Streamlit pages."""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.page_image_reader import PageImageReader

from src.ai.coverage_service import PageEmbeddingCoverageService
from src.ai.credential_store import (
    CredentialStoreChain,
    build_default_credential_store,
)
from src.ai.experience_model_service import ExperienceModelService
from src.ai.hybrid_search import HybridSearchService
from src.ai.openai_compatible import urllib_transport
from src.ai.page_indexer import EMBEDDING_CONFIG_VERSION, EMBEDDING_DIMENSIONS
from src.ai.provider import (
    AIBudgetExceededError,
    AiCallRecord,
    AuditedAIProvider,
    EmbeddingProvider,
    build_production_audited_provider,
    require_production_audited_provider,
)
from src.ai.provider_config import ProviderConfigStore
from src.ai.provider_factory import (
    build_provider_adapter,
    resolve_active_provider_runtime,
    resolve_image_provider_runtime,
)
from src.ai.provider_settings_service import ProviderSettingsService
from src.ai.vector_recall import (
    PersistentVectorRecallSource,
    SearchableContentFingerprintSource,
)
from src.ai_ledger_service import AILedgerService
from src.backup_service import BackupService
from src.batch_service import PageBatchService
from src.classification_metadata import ClassificationMetadataService
from src.config import Settings, runtime_settings
from src.database import Database
from src.deletion_recovery import reconcile_quarantine
from src.diagnostic_service import DiagnosticService
from src.document_deletion_service import DocumentDeletionService
from src.document_service import DocumentService
from src.error_analysis_service import ErrorAnalysisService
from src.evidence_basket_service import EvidenceBasketService
from src.knowledge_memory_service import KnowledgeMemoryService
from src.knowledge_object_service import KnowledgeObjectService
from src.knowledge_search_service import KnowledgeSearchService
from src.learning_report_service import LearningReportService
from src.models import QuarantineReconciliation
from src.page_visual_service import PageVisualService
from src.pdf_service import PdfService
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.review_queue_service import ReviewQueueService
from src.search_service import SearchService
from src.storage_path_repair import repair_relocated_asset_paths
from src.targeted_training_service import TargetedTrainingService
from src.training_profile_service import TrainingProfileService
from src.training_session_service import TrainingSessionService


def configure_logging(log_path: Path) -> None:
    """Configure a bounded UTF-8 rotating local log file once per process."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    resolved_path = log_path.resolve()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_path:
            return

    file_handler = RotatingFileHandler(
        resolved_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger.addHandler(file_handler)

    def log_uncaught(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: object,
    ) -> None:
        logging.getLogger("uncaught").critical(
            "未捕获异常", exc_info=(exception_type, exception, traceback)
        )

    sys.excepthook = log_uncaught
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda args: log_uncaught(
            args.exc_type, args.exc_value, args.exc_traceback
        )


@lru_cache(maxsize=1)
def application_settings() -> Settings:
    """Return validated settings and create required local directories.

    Resolves through ``runtime_settings``: a process started as the staging
    instance (``EKB_STAGING_INSTANCE=1``) is fully isolated under the
    staging root; any other process uses the formal guarded settings.
    """

    settings = runtime_settings()
    settings.ensure_directories()
    configure_logging(settings.log_path)
    logging.getLogger(__name__).info(
        "Nectivon 启动：version=%s address=%s:%s",
        settings.app_version,
        settings.host,
        settings.port,
    )
    return settings


@lru_cache(maxsize=1)
def application_database() -> Database:
    """Return the process-wide initialized SQLite database."""

    settings = application_settings()
    database = Database(settings.database_path, image_readings_dir=settings.agent_readings_dir)
    repair_relocated_asset_paths(
        database,
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
    )
    return database


@lru_cache(maxsize=1)
def application_ai_provider() -> AuditedAIProvider | None:
    """Return the optional audited AI provider, or ``None`` when AI is disabled.

    AI is an optional capability, never a startup dependency: manual mode,
    a missing API key, or an unknown provider all yield ``None``, and no
    existing service receives or requires this provider. When enabled, the
    vendor adapter is wrapped in :class:`AuditedAIProvider`, so every real
    completion/embedding call is recorded in the local ``ai_calls`` ledger
    and evaluated against the configured token budgets before any network
    request is sent. Retry policy comes from ``Settings.ai_max_extra_attempts``
    (bounded to 0..2) instead of a hard-coded value.

    Construction performs no network I/O. The durable ledger and budget guard
    resolve the local database lazily on first use, so building the provider
    never initializes the database or page-image reader.
    """

    settings = application_settings()
    resolved = resolve_active_provider_runtime(
        settings,
        credential_store=application_credential_store(),
    )
    if resolved is None:
        return None
    adapter = build_provider_adapter(resolved, transport=urllib_transport)
    provider = build_production_audited_provider(
        adapter,
        default_model=resolved.default_model,
        default_embedding_model=resolved.default_embedding_model,
        source_feature="application",
        ledger=_LazyDatabaseAiCallLedger(),
        budget_guard=_LazyTokenBudgetGuard(settings),
    )
    return require_production_audited_provider(provider)


@lru_cache(maxsize=1)
def application_credential_store() -> CredentialStoreChain:
    """Return one process-wide chain so session-memory fallback survives reruns."""

    return build_default_credential_store()


@lru_cache(maxsize=1)
def application_provider_settings_service() -> ProviderSettingsService:
    """Return the local settings service; construction performs no network I/O."""

    return ProviderSettingsService(
        config_store=ProviderConfigStore(),
        credential_store=application_credential_store(),
        application_settings=application_settings(),
        transport=urllib_transport,
        invalidate_runtime=invalidate_ai_runtime_cache,
    )


def application_ai_model() -> str | None:
    """Return the active completion model without exposing provider secrets."""

    provider = application_ai_provider()
    return provider.default_model if provider is not None else None


def application_ai_vision_provider() -> AuditedAIProvider | None:
    """Return the independently selected image provider for every image feature."""

    return application_question_vision_provider()


def application_question_vision_provider() -> AuditedAIProvider | None:
    """Build the selected image adapter without I/O or vendor fallback on error."""

    settings = application_settings()
    resolved = resolve_image_provider_runtime(
        settings, credential_store=application_credential_store(),
    )
    if resolved is None:
        return None
    return require_production_audited_provider(build_production_audited_provider(
        build_provider_adapter(resolved, transport=urllib_transport),
        default_model=resolved.default_model,
        default_embedding_model=resolved.default_embedding_model,
        source_feature="question_vision",
        ledger=_LazyDatabaseAiCallLedger(), budget_guard=_LazyTokenBudgetGuard(settings),
    ))


def invalidate_ai_runtime_cache() -> None:
    """Invalidate only model-dependent runtime objects after a settings change."""

    application_ai_provider.cache_clear()
    application_experience_model_service.cache_clear()
    application_hybrid_search_service.cache_clear()


class _LazyDatabaseAiCallLedger:
    """Ledger sink that opens the local database only when a call is recorded."""

    def record(self, call: AiCallRecord) -> None:
        application_database().insert_ai_call(call)


class _LazyTokenBudgetGuard:
    """Token budget gate evaluated against the local ``ai_calls`` ledger.

    Budgets are expressed in tokens and read from settings; ``0`` means
    unlimited. The database is resolved lazily so constructing the AI
    provider never touches it. An exceeded budget raises
    ``AIBudgetExceededError`` before any network request is sent.
    """

    def __init__(self, settings: Settings) -> None:
        self._daily_budget = settings.ai_daily_token_budget
        self._monthly_budget = settings.ai_monthly_token_budget

    def ensure_allowed(self, capability: str) -> None:
        if self._daily_budget <= 0 and self._monthly_budget <= 0:
            return
        now = datetime.now(UTC)
        if self._daily_budget > 0:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            used = application_database().total_ai_tokens_since(
                day_start.isoformat(timespec="microseconds")
            )
            if used >= self._daily_budget:
                raise AIBudgetExceededError(
                    f"AI 调用被日预算限制拒绝：今日已用 {used} tokens，"
                    f"上限 {self._daily_budget} tokens。"
                )
        if self._monthly_budget > 0:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            used = application_database().total_ai_tokens_since(
                month_start.isoformat(timespec="microseconds")
            )
            if used >= self._monthly_budget:
                raise AIBudgetExceededError(
                    f"AI 调用被月预算限制拒绝：本月已用 {used} tokens，"
                    f"上限 {self._monthly_budget} tokens。"
                )


@lru_cache(maxsize=1)
def application_hybrid_search_service() -> HybridSearchService:
    """Return the process-wide hybrid search service (lexical + optional vector).

    The lexical side is the natural-language-normalizing ``SearchService`` —
    never the raw ``Database`` — so free-form queries such as
    ``定时器预分频器`` become real FTS5 OR terms instead of an empty literal
    gate. The vector side is a ``PersistentVectorRecallSource`` over the same
    database, assembled only when an embedding provider is configured; without
    AI the service degrades to lexical-only (``vector=None``), identical to
    today's offline search. Construction is cheap and performs no network I/O:
    no provider is initialized in manual mode, and an API key is never
    required for the application to start.
    """

    settings = application_settings()
    database = application_database()
    lexical = SearchService(database)
    provider = application_ai_provider()
    if provider is not None:
        provider = require_production_audited_provider(provider)
    vector = None
    if (
        provider is not None
        and provider.supports_embedding
        and isinstance(provider, EmbeddingProvider)
    ):
        vector = PersistentVectorRecallSource(
            query_embedding=provider,
            embeddings=database,
            fingerprints=SearchableContentFingerprintSource(database),
            model=settings.ai_embedding_model,
            dimensions=EMBEDDING_DIMENSIONS,
            config_version=EMBEDDING_CONFIG_VERSION,
        )
    return HybridSearchService(lexical=lexical, hydration=database, vector=vector)


@lru_cache(maxsize=1)
def application_coverage_service() -> PageEmbeddingCoverageService:
    """Return the process-wide read-only embedding coverage service.

    Coverage classification is zero-cost and zero-side-effect: it never
    constructs an embedding provider, never touches the network or an API key,
    and never writes to the database. The model defaults to the single
    ``Settings.ai_embedding_model`` source read inside the coverage service.
    """

    return PageEmbeddingCoverageService(database=application_database())


@lru_cache(maxsize=1)
def application_document_service() -> DocumentService:
    """Return the document import and Markdown editing service."""

    settings = application_settings()
    pdf_service = PdfService(
        minimum_text_length=settings.minimum_text_length,
        dpi=settings.pdf_render_dpi,
    )
    return DocumentService(
        database=application_database(),
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        pdf_service=pdf_service,
    )


def application_page_image_reader() -> PageImageReader:
    """Build the shared all-format, all-page image recognition workflow."""

    from src.agent_document_reader import AgentReadingStore
    from src.page_image_reader import PageImageReader
    from src.question_candidate_service import QuestionCandidateStore

    settings = application_settings()
    return PageImageReader(
        database=application_database(), provider=application_question_vision_provider(),
        readings=AgentReadingStore(settings.agent_readings_dir),
        candidates=QuestionCandidateStore(settings.data_dir / "question-candidates"),
    )


@lru_cache(maxsize=1)
def application_page_visual_service() -> PageVisualService:
    """Return the process-wide dual-stage page visual parsing service."""

    settings = application_settings()
    return PageVisualService(
        application_database(),
        minimum_text_length=settings.minimum_text_length,
    )


def application_document_deletion_service() -> DocumentDeletionService:
    """Return the process-wide staged document deletion service."""

    settings = application_settings()
    return DocumentDeletionService(
        database=application_database(),
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        data_dir=settings.data_dir,
        agent_readings_dir=settings.agent_readings_dir,
        app_version=settings.app_version,
    )


@lru_cache(maxsize=1)
def application_startup_reconciliation() -> QuarantineReconciliation | None:
    """Settle unfinished deletion quarantines once per process, fail closed.

    Interrupted deletions whose files can be provably restored or destroyed
    are settled automatically; anything ambiguous is preserved untouched and
    reported. An unexpected failure of the reconciliation itself is logged
    as critical and never blocks application startup.
    """

    settings = application_settings()
    try:
        report = reconcile_quarantine(
            database=application_database(),
            data_dir=settings.data_dir,
            raw_dir=settings.raw_dir,
            pages_dir=settings.pages_dir,
            markdown_dir=settings.markdown_dir,
            agent_readings_dir=settings.agent_readings_dir,
        )
    except Exception:
        logging.getLogger(__name__).critical(
            "删除隔离区启动对账失败，已跳过（未改动任何数据）", exc_info=True
        )
        return None
    for operation in report.operations:
        log = logging.getLogger(__name__).info
        if operation.status == "attention":
            log = logging.getLogger(__name__).warning
        log(
            "删除隔离区对账：operation=%s status=%s %s",
            operation.operation_id,
            operation.status,
            operation.detail,
        )
    return report


def run_quarantine_reconciliation() -> QuarantineReconciliation:
    """Run a fresh quarantine reconciliation pass (system maintenance page)."""

    settings = application_settings()
    return reconcile_quarantine(
        database=application_database(),
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        agent_readings_dir=settings.agent_readings_dir,
    )


@lru_cache(maxsize=1)
def application_evidence_basket_service() -> EvidenceBasketService:
    """Return the process-wide durable evidence basket service."""

    return EvidenceBasketService(application_database())


@lru_cache(maxsize=1)
def application_knowledge_object_service() -> KnowledgeObjectService:
    """Return the process-wide knowledge-object service (schema v9)."""

    return KnowledgeObjectService(application_database())


@lru_cache(maxsize=1)
def application_knowledge_memory_service() -> KnowledgeMemoryService:
    """Return the process-wide knowledge-memory service (schema v9)."""

    return KnowledgeMemoryService(application_database())


@lru_cache(maxsize=1)
def application_experience_model_service() -> ExperienceModelService:
    """Return the process-wide experience-model service (v0.5.3 Phase 4).

    The service receives the shared AI provider (which may be ``None`` when AI
    is disabled). User-visible callers render the shared configuration prompt;
    they never substitute a mock response. No network request happens here.
    """

    provider = application_ai_provider()
    if provider is not None:
        provider = require_production_audited_provider(provider)
    return ExperienceModelService(provider)


@lru_cache(maxsize=1)
def application_ai_ledger_service() -> AILedgerService:
    """Return the process-wide read-only AI call ledger service (Phase 5).

    The factory performs no network request and no AI call; it only wires the
    local database.
    """

    return AILedgerService(application_database())


@lru_cache(maxsize=1)
def application_knowledge_search_service() -> KnowledgeSearchService:
    """Return the process-wide offline knowledge search service (Phase 3D).

    Knowledge-scope search is strictly offline: no AI provider, no network and
    no embedding path. The service is resolved lazily by the search page so a
    page-scope search never constructs it.
    """

    return KnowledgeSearchService(application_database())


def application_page_batch_service() -> PageBatchService:
    """Build the stateless batch wrapper around the process-wide database."""

    return PageBatchService(application_database())


def application_classification_metadata_service() -> ClassificationMetadataService:
    """Build a fresh classification reader with no cross-rerun cache state."""

    return ClassificationMetadataService(application_database())


@lru_cache(maxsize=1)
def application_backup_service() -> BackupService:
    """Return the verified local backup service for formal application paths."""

    settings = application_settings()
    return BackupService(
        app_version=settings.app_version,
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        database_path=settings.database_path,
        backups_dir=settings.backups_dir,
        host=settings.host,
        port=settings.port,
        minimum_text_length=settings.minimum_text_length,
        pdf_render_dpi=settings.pdf_render_dpi,
    )


def application_diagnostic_service() -> DiagnosticService:
    """Build a fresh read-only diagnostics service for current formal paths."""

    settings = application_settings()
    return DiagnosticService(
        app_version=settings.app_version,
        project_root=Path(__file__).resolve().parents[1],
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        database_path=settings.database_path,
        backups_dir=settings.backups_dir,
        logs_dir=settings.logs_dir,
        log_path=settings.log_path,
        host=settings.host,
        port=settings.port,
    )


@lru_cache(maxsize=1)
def application_training_profile_service() -> TrainingProfileService:
    """Return the process-wide training profile service."""

    settings = application_settings()
    profile_db_path = settings.data_dir / "training_profile.db"
    return TrainingProfileService(profile_db_path)


@lru_cache(maxsize=1)
def application_targeted_training_service() -> TargetedTrainingService:
    """Return the process-wide targeted training service."""

    database = application_database()
    return TargetedTrainingService(database)


@lru_cache(maxsize=1)
def application_question_source_retrieval_service() -> QuestionSourceRetrievalService:
    """Return the process-wide question source retrieval and verification service."""

    database = application_database()
    return QuestionSourceRetrievalService(database)


@lru_cache(maxsize=1)
def application_error_analysis_service() -> ErrorAnalysisService:
    """Return the process-wide error analysis and mastery state service."""

    database = application_database()
    return ErrorAnalysisService(database)


@lru_cache(maxsize=1)
def application_training_session_service() -> TrainingSessionService:
    """Return the process-wide targeted training session execution service."""

    database = application_database()
    error_service = application_error_analysis_service()
    return TrainingSessionService(database, error_analysis_service=error_service)


@lru_cache(maxsize=1)
def application_learning_report_service() -> LearningReportService:
    """Return the process-wide learning report generation and export service."""

    database = application_database()
    session_service = application_training_session_service()
    error_service = application_error_analysis_service()
    profile_service = application_training_profile_service()
    return LearningReportService(
        database,
        session_service=session_service,
        error_service=error_service,
        profile_service=profile_service,
    )


@lru_cache(maxsize=1)
def application_review_queue_service() -> ReviewQueueService:
    """Return the process-wide targeted training review queue service."""

    database = application_database()
    error_service = application_error_analysis_service()
    return ReviewQueueService(database, error_service=error_service)
