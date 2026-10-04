"""Final Answer Stage (v0.6.0 Phase 2C).

This component is a thin orchestration layer between the Phase 2A execution
result and the existing audited RAG Answer chain:

- SUCCESS / PARTIAL ToolResult → ToolResultContextMapper →
  KnowledgeContextPackage → RagAnswerService (max one completion);
- EMPTY / FAILED ToolResult → deterministic no-evidence / structured failure
  (zero Final Answer model calls);
- ANSWER_DIRECTLY → deterministic no-evidence response (zero model calls)
  because the existing RAG contract requires grounded context.

It never retries, never repairs citations, never selects a second Tool, and
never fabricates references.
"""

from __future__ import annotations

from dataclasses import replace

from src.agent.execution.contracts import (
    AgentDecisionKind,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentRequest,
)
from src.agent.response.contracts import (
    AgentResponse,
    AgentResponseError,
    AgentResponseErrorCode,
    AgentResponseStatus,
)
from src.agent.response.tool_context import ToolResultContextMapper
from src.agent.tools.contracts import ToolResultStatus
from src.ai.provider import (
    AIBudgetExceededError,
    AIExecutionError,
    AIUnavailableError,
)
from src.ai.rag_answer_service import (
    RagAnswerError,
    RagAnswerErrorCode,
    RagAnswerService,
)
from src.knowledge_context_packager import (
    KnowledgeContextError,
    KnowledgeContextPackage,
    KnowledgeContextPackager,
)

# A 512-token cap proved too small for grounded teaching answers that must
# explain a multi-step derivation and keep page citations.  Keep one bounded
# completion (and the existing fail-closed handling for true truncation), but
# leave enough room for a complete answer.
DEFAULT_FINAL_ANSWER_MAX_OUTPUT_TOKENS = 1536
DEFAULT_FINAL_ANSWER_SOURCE_FEATURE = "agent_final_answer"

#: No-evidence text (HBV2-MORNING-20260909 M1 终答层): never asserts a
#: corpus-level negative ("资料里没有/不存在") — the context package is only a
#: bounded retrieval window, so the answer states that this round's retrieval
#: found nothing and suggests how to re-ask.
_NO_EVIDENCE_MESSAGE = (
    "本次检索没有找到可以支撑该问题的资料；这可能是问法或用词差异所致，"
    "并不代表知识库里一定没有相关内容。可以补充资料名称，或换一种说法再试。"
)
#: Fixed fail-closed text used when the generated answer fails citation
#: validation. It is a constant: the rejected model output is never exposed,
#: but the user no longer receives a blank answer (v0.8.1 FAIL-010 fix).
_CITATION_INVALID_ANSWER = (
    "已找到相关资料，但本次生成的答案未能通过引用校验；"
    "为避免误导，这次不给出结论。可以换一种问法后重试。"
)
#: ANSWER_DIRECTLY text (OBS-M1): the round performed no retrieval, so the
#: message must never claim "该请求不需要调用知识库工具" — that was a false
#: statement exactly when the question did need retrieval (trivial-looking
#: computation or anaphora follow-ups). It states what happened (no search
#: this round) and asks for the missing locator information.
_ANSWER_DIRECTLY_MESSAGE = (
    "这一轮没有检索你的知识库，因此没有找到可依据的资料。"
    "我只能基于你这一轮的问题检索：请补充资料名称，或把问题和你所指的内容"
    "说得更完整（例如写明资料名、设备或上一轮提到的对象）。"
)

__all__ = [
    "DEFAULT_FINAL_ANSWER_MAX_OUTPUT_TOKENS",
    "DEFAULT_FINAL_ANSWER_SOURCE_FEATURE",
    "FinalAnswerStage",
]


class FinalAnswerStage:
    """Turn one AgentExecutionResult into a validated AgentResponse."""

    def __init__(
        self,
        rag_answer_service: RagAnswerService | None = None,
        *,
        packager: KnowledgeContextPackager | None = None,
        model: str | None = None,
        max_completion_tokens: int = DEFAULT_FINAL_ANSWER_MAX_OUTPUT_TOKENS,
        source_feature: str = DEFAULT_FINAL_ANSWER_SOURCE_FEATURE,
    ) -> None:
        self._rag = rag_answer_service
        self._packager = packager or KnowledgeContextPackager()
        self._mapper = ToolResultContextMapper()
        self._model = model
        self._max_completion_tokens = max_completion_tokens
        self._source_feature = source_feature

    def answer(
        self, request: AgentRequest, execution: AgentExecutionResult
    ) -> AgentResponse:
        """Produce the structured final response for one execution result."""
        if execution.status is AgentExecutionStatus.FAILED:
            return self._execution_failure(execution)

        if execution.decision.kind is AgentDecisionKind.ANSWER_DIRECTLY:
            return self._no_evidence_response(
                _ANSWER_DIRECTLY_MESSAGE,
                trace=execution.trace,
                reason_code="tool_not_called",
            )

        tool_result = execution.tool_result
        if tool_result is None:
            return self._no_evidence_response(
                _NO_EVIDENCE_MESSAGE,
                trace=execution.trace,
                reason_code="tool_not_called",
            )

        if tool_result.status is ToolResultStatus.EMPTY:
            return self._no_evidence_response(
                _NO_EVIDENCE_MESSAGE,
                warnings=tool_result.warnings,
                trace=execution.trace,
                reason_code="tool_empty",
            )

        if tool_result.status is ToolResultStatus.FAILED:
            message = (
                tool_result.error.message
                if tool_result.error is not None
                else "工具执行失败"
            )
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.TOOL_FAILED,
                    message=message,
                ),
                trace=_with_final_audit(
                    execution.trace,
                    final_status="final_not_called",
                    ui_failure_reason_code="tool_failed",
                ),
            )

        if self._rag is None:
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.PROVIDER_UNAVAILABLE,
                    message="Final Answer 服务未配置。",
                ),
                trace=_with_final_audit(
                    execution.trace,
                    final_status="final_provider_failed",
                    ui_failure_reason_code="provider_unavailable",
                ),
            )

        try:
            package = self._mapper.build(
                tool_result,
                question=request.text,
                packager=self._packager,
            )
        except KnowledgeContextError:
            return self._no_evidence_response(
                _NO_EVIDENCE_MESSAGE,
                warnings=tool_result.warnings,
                trace=execution.trace,
                reason_code="evidence_mapping_empty",
            )

        try:
            output = self._rag.answer(
                request.text,
                package,
                model=self._model,
                source_feature=self._source_feature,
                max_completion_tokens=self._max_completion_tokens,
            )
        except RagAnswerError as exc:
            if exc.code is RagAnswerErrorCode.EMPTY_CONTEXT:
                return self._no_evidence_response(
                    _NO_EVIDENCE_MESSAGE,
                    warnings=tool_result.warnings,
                    trace=execution.trace,
                    reason_code="evidence_context_empty",
                    package=package,
                )
            if exc.code is RagAnswerErrorCode.CORPUS_WIDE_NEGATIVE:
                # M1R-B guard (HBV2 Phase 2 RUN2): a grounded=True round whose
                # bounded context does not support the question must never
                # surface a corpus-wide negative assertion. The model output
                # was rejected by the deterministic guard; respond with the
                # same scoped no-evidence wording as the empty-retrieval path
                # ("本次检索没有找到…并不代表知识库里一定没有相关内容").
                return self._no_evidence_response(
                    _NO_EVIDENCE_MESSAGE,
                    warnings=tool_result.warnings,
                    trace=execution.trace,
                    reason_code="corpus_wide_negative",
                    final_status="final_validation_failed",
                    package=package,
                )
            if exc.code is RagAnswerErrorCode.CITATION_INVALID:
                return AgentResponse(
                    status=AgentResponseStatus.FAILED,
                    answer=_CITATION_INVALID_ANSWER,
                    grounded=False,
                    warnings=tool_result.warnings,
                    error=AgentResponseError(
                        code=AgentResponseErrorCode.CITATION_INVALID,
                        message=str(exc),
                    ),
                    trace=_with_final_audit(
                        execution.trace,
                        package=package,
                        final_status="final_validation_failed",
                        ui_failure_reason_code="citation_invalid",
                    ),
                )
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.INTERNAL_FAILURE,
                    message=str(exc),
                ),
                trace=_with_final_audit(
                    execution.trace,
                    package=package,
                    final_status="final_provider_failed",
                    ui_failure_reason_code="internal_failure",
                ),
            )
        except AIBudgetExceededError:
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.BUDGET_EXCEEDED,
                    message="Final Answer 调用被预算限制拒绝。",
                ),
                trace=_with_final_audit(
                    execution.trace,
                    package=package,
                    final_status="final_provider_failed",
                    ui_failure_reason_code="budget_exceeded",
                ),
            )
        except AIUnavailableError:
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.PROVIDER_UNAVAILABLE,
                    message="Final Answer 服务不可用。",
                ),
                trace=_with_final_audit(
                    execution.trace,
                    package=package,
                    final_status="final_provider_failed",
                    ui_failure_reason_code="provider_unavailable",
                ),
            )
        except AIExecutionError as exc:
            truncated = exc.error_class == "incomplete"
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=(
                        AgentResponseErrorCode.FINAL_LENGTH_TRUNCATED
                        if truncated
                        else AgentResponseErrorCode.FINAL_ANSWER_FAILED
                    ),
                    message="Final Answer 模型调用失败。",
                    detail=exc.error_class,
                ),
                trace=_with_final_audit(
                    execution.trace,
                    package=package,
                    final_status=(
                        "final_length_truncated"
                        if truncated
                        else "final_provider_failed"
                    ),
                    final_finish_reason=exc.finish_reason,
                    ui_failure_reason_code=(
                        "final_length_truncated"
                        if truncated
                        else "final_provider_failed"
                    ),
                ),
            )
        except Exception as exc:
            return AgentResponse(
                status=AgentResponseStatus.FAILED,
                answer="",
                grounded=False,
                warnings=tool_result.warnings,
                error=AgentResponseError(
                    code=AgentResponseErrorCode.INTERNAL_FAILURE,
                    message="Final Answer 执行失败。",
                    detail=type(exc).__name__,
                ),
                trace=_with_final_audit(
                    execution.trace,
                    package=package,
                    final_status="final_provider_failed",
                    ui_failure_reason_code="internal_failure",
                ),
            )

        warnings = tuple(tool_result.warnings) + output.warnings
        final_status = (
            "final_declared_insufficient"
            if output.declared_insufficient
            else "final_answer_success"
        )
        return AgentResponse(
            status=AgentResponseStatus.COMPLETED,
            answer=output.answer,
            grounded=bool(output.answer_citations),
            citations=output.answer_citations,
            context_stable_ids=output.context_stable_ids,
            warnings=warnings,
            trace=_with_final_audit(
                execution.trace,
                package=package,
                final_status=final_status,
                final_finish_reason=output.finish_reason,
                final_declared_insufficient=output.declared_insufficient,
                ui_failure_reason_code=(
                    "final_declared_insufficient"
                    if output.declared_insufficient
                    else None
                ),
            ),
            token_usage=output.token_usage,
            model=output.model,
        )

    def _execution_failure(self, execution: AgentExecutionResult) -> AgentResponse:
        error = execution.error
        message = error.message if error is not None else "Agent 执行失败"
        code = AgentResponseErrorCode.INTERNAL_FAILURE
        if error is not None and error.code.value in {
            "unknown_tool",
            "tool_not_allowed",
            "tool_execution_failed",
        }:
            code = AgentResponseErrorCode.TOOL_FAILED
        elif error is not None:
            code = {
                "decision_length_truncated": (
                    AgentResponseErrorCode.DECISION_LENGTH_TRUNCATED
                ),
                "decision_parse_failed": AgentResponseErrorCode.DECISION_PARSE_FAILED,
                "decision_provider_failed": (
                    AgentResponseErrorCode.DECISION_PROVIDER_FAILED
                ),
            }.get(error.code.value, code)
        return AgentResponse(
            status=AgentResponseStatus.FAILED,
            answer="",
            grounded=False,
            error=AgentResponseError(
                code=code,
                message=message,
                detail=error.code.value if error is not None else None,
            ),
            trace=_with_final_audit(
                execution.trace,
                final_status="final_not_called",
                ui_failure_reason_code=(error.code.value if error is not None else code.value),
            ),
        )

    def _no_evidence_response(
        self,
        message: str,
        *,
        warnings: tuple[str, ...] = (),
        trace=None,
        reason_code: str,
        final_status: str | None = None,
        package: KnowledgeContextPackage | None = None,
    ) -> AgentResponse:
        return AgentResponse(
            status=AgentResponseStatus.COMPLETED,
            answer=message,
            grounded=False,
            warnings=warnings,
            trace=_with_final_audit(
                trace,
                package=package,
                final_status=final_status or (
                    "final_declared_insufficient"
                    if reason_code == "final_declared_insufficient"
                    else "final_not_called"
                ),
                final_declared_insufficient=(
                    reason_code == "final_declared_insufficient"
                ),
                ui_failure_reason_code=reason_code,
            ),
        )


def _with_final_audit(
    trace,
    *,
    package: KnowledgeContextPackage | None = None,
    final_status: str,
    final_finish_reason: str | None = None,
    final_declared_insufficient: bool = False,
    ui_failure_reason_code: str | None = None,
):
    if trace is None:
        return None
    page_ids: list[int] = []
    excerpt_chars = 0
    evidence_count = 0
    if package is not None:
        evidence_count = len(package.items)
        excerpt_chars = sum(len(item.content) for item in package.items)
        for item in package.items:
            if getattr(item.type, "value", item.type) == "page":
                page_ids.append(item.local_id)
            for anchor in item.source_anchors:
                if anchor.anchor_type == "page" and anchor.anchor_id is not None:
                    page_ids.append(anchor.anchor_id)
    return replace(
        trace,
        final_status=final_status,
        final_finish_reason=final_finish_reason,
        final_declared_insufficient=final_declared_insufficient,
        evidence_count=evidence_count,
        evidence_page_ids=tuple(dict.fromkeys(page_ids))[:10],
        evidence_excerpt_chars=excerpt_chars,
        ui_failure_reason_code=ui_failure_reason_code,
    )
