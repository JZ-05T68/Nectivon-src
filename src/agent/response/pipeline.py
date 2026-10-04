"""Single-step Agent orchestration pipeline (v0.6.0 Phase 2C).

``SingleStepAgentService`` is a thin composition of the frozen Phase 2A
executor and the Phase 2C Final Answer Stage. It does not add retry, loops,
planning, or multi-step behavior: the pipeline is exactly

    Decision → 0/1 Tool → Final Answer → STOP.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace

from src.agent.execution.contracts import AgentRequest, AgentRuntimeTrace, DecisionProvider
from src.agent.execution.executor import SingleStepAgentExecutor
from src.agent.response.contracts import AgentResponse
from src.agent.response.final_answer import FinalAnswerStage

__all__ = ["SingleStepAgentService"]

LOGGER = logging.getLogger(__name__)


class SingleStepAgentService:
    """Run one single-step Agent request and produce a structured response."""

    def __init__(
        self,
        executor: SingleStepAgentExecutor,
        final_answer: FinalAnswerStage,
        *,
        audit_sink: Callable[[AgentRuntimeTrace], None] | None = None,
    ) -> None:
        self._executor = executor
        self._final_answer = final_answer
        self._audit_sink = audit_sink

    def run(
        self, request: AgentRequest, decision_provider: DecisionProvider
    ) -> AgentResponse:
        """Execute one decision, at most one Tool, then the Final Answer Stage."""
        execution = self._executor.execute(request, decision_provider)
        response = self._final_answer.answer(request, execution)
        trace = response.trace
        if trace is not None:
            response = replace(
                response,
                failure_reason_code=trace.ui_failure_reason_code,
            )
            LOGGER.info(
                "Agent request audit: %s",
                json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True),
            )
            if self._audit_sink is not None:
                try:
                    self._audit_sink(trace)
                except Exception:
                    LOGGER.exception(
                        "Agent request audit ledger 写入失败：run_id=%s",
                        trace.run_id,
                    )
        return response
