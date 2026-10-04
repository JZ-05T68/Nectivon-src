"""Optional AI capability boundary for Nectivon.

This package holds the vendor-neutral provider contracts
(``src.ai.provider``) and isolated vendor adapters under ``src.ai``. It
performs no I/O at import time and is safe to import in any mode,
including the default manual AI mode with no API key configured.
"""

from src.ai.provider import (
    AIBudgetExceededError,
    AiBudgetGuard,
    AiCallLedger,
    AiCallRecord,
    AIError,
    AIExecutionError,
    AiOutputRecord,
    AIProductionCompositionError,
    AIProvider,
    AIUnavailableError,
    AuditedAIProvider,
    CompletionProvider,
    CompletionResult,
    CompletionUsage,
    EmbeddingProvider,
    EmbeddingResult,
    ProviderCallError,
    ProviderErrorCode,
    RerankHit,
    RerankProvider,
    RerankResult,
    SafeProviderError,
    build_production_audited_provider,
    require_ai_provider,
    require_production_audited_provider,
)

__all__ = [
    "AIError",
    "AIExecutionError",
    "AIProvider",
    "AIBudgetExceededError",
    "AIProductionCompositionError",
    "AIUnavailableError",
    "AiBudgetGuard",
    "AiCallLedger",
    "AiCallRecord",
    "AiOutputRecord",
    "AuditedAIProvider",
    "build_production_audited_provider",
    "CompletionProvider",
    "CompletionResult",
    "CompletionUsage",
    "EmbeddingProvider",
    "EmbeddingResult",
    "ProviderCallError",
    "ProviderErrorCode",
    "RerankHit",
    "RerankProvider",
    "RerankResult",
    "SafeProviderError",
    "require_ai_provider",
    "require_production_audited_provider",
]
