"""Local Agent composition over user-imported, explicitly read documents."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from src.agent.decision.provider import ModelDecisionProvider
from src.agent.execution.contracts import AgentRequest
from src.agent.execution.executor import SingleStepAgentExecutor
from src.agent.execution.guard import answer_directly_corpus_guard
from src.agent.response.contracts import AgentResponse
from src.agent.response.final_answer import FinalAnswerStage
from src.agent.response.pipeline import SingleStepAgentService
from src.agent.tools.bootstrap import build_phase1_handlers, build_phase1_registry
from src.agent_document_reader import (
    AgentDocumentReader,
    AgentReadingStore,
    DocumentReadingReport,
)
from src.ai.provider import CompletionProvider
from src.ai.rag_answer_service import RagAnswerService
from src.database import Database
from src.search_service import SearchService


class LocalDocumentAgent:
    """One local composition for explicit reading and grounded questions.

    Both page understanding and question answering use the configured hard
    model (Qwen 3.8 by default). Page retrieval is restricted to fresh page
    readings, and the Final Answer stage receives complete original page text.
    """

    def __init__(
        self,
        *,
        database: Database,
        provider: CompletionProvider | None,
        readings: AgentReadingStore,
        model: str,
        vision_provider: object | None = None,
        vision_model: str | None = None,
        pages_dir: Path | None = None,
        page_image_reader: object | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("Agent 模型不能为空")
        self._model = model.strip()
        self._reader = page_image_reader or AgentDocumentReader(
            database=database,
            provider=provider,
            store=readings,
            model=self._model,
        )
        has_vision = (
            vision_provider is not None
            and hasattr(vision_provider, "complete_vision")
            and pages_dir is not None
        )
        registry = build_phase1_registry(include_visual=has_vision)
        executor = SingleStepAgentExecutor(
            registry,
            handlers=build_phase1_handlers(
                database,
                page_readings=readings,
                require_agent_read=True,
                vision_provider=vision_provider,
                vision_model=vision_model,
                pages_dir=pages_dir,
                agent_readings_store=readings,
            ),
        )
        self._model_decision = ModelDecisionProvider(
            provider,
            registry,
            model=self._model,
            source_feature="agent_decision",
        )
        # FAIL-020 short-query face: bare concept questions whose topics the
        # model knows internally are routed ANSWER_DIRECTLY even when the
        # user's corpus covers them; the guard reconsiders only those
        # decisions against one local lexical search (honest boundary kept
        # when the corpus has no match).
        self._guard_service = SearchService(database)
        self._decision = _GuardedDecisionProvider(self)

        self._service = SingleStepAgentService(
            executor,
            FinalAnswerStage(
                RagAnswerService(provider),
                model=self._model,
                source_feature="agent_final_answer",
            ),
            audit_sink=database.insert_agent_run_audit,
        )

    @property
    def model(self) -> str:
        return self._model

    def read_document(
        self,
        document_id: int,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> DocumentReadingReport:
        """Run the explicit user-triggered, page-by-page reading action."""

        return self._reader.read_document(
            document_id,
            progress_callback=progress_callback,
        )

    def read_page(self, page_id: int):
        """Re-read exactly one page (V086-207: honest page-level reread)."""

        return self._reader.read_page(page_id)

    def ask(self, question: str, *, request_id: str | None = None) -> AgentResponse:
        """Answer one question using only fresh pages the Agent has read."""

        request = AgentRequest(request_id=request_id, text=question)
        return self._service.run(request, self._decision)


class _GuardedDecisionProvider:
    """DecisionProvider facade applying the concept-query corpus guard once.

    The wrapped model decision provider is still called at most once per
    request; the guard only adds a local lexical search for ANSWER_DIRECTLY
    concept questions (see :mod:`src.agent.execution.guard`).
    """

    def __init__(self, agent: LocalDocumentAgent) -> None:
        self._agent = agent

    def decide(self, request: AgentRequest):
        return answer_directly_corpus_guard(
            request.text,
            lambda: self._agent._model_decision.decide(request),
            self._agent._guard_service,
        )


__all__ = ["LocalDocumentAgent"]
