"""M1R-B regression — corpus-wide negative assertion guard.

HBV2 Phase 2 RUN2 (2026-09-09, independent WorkBuddy HY4 evidence): with
grounded=True but an irrelevant bounded context (retrieval returned pump and
gearbox pages while the user asked about a car), the model answered
「当前返回的上下文中没有任何资料提到"车"…」 — even though the correct page
exists in the corpus. TOP-K RETRIEVAL FAILURE ≠ CORPUS-WIDE ABSENCE.

The guard is deterministic and fails closed (mirrors _CITATION_INVALID):
any model answer whose text asserts a quantified corpus-wide absence is
rejected with a typed RagAnswerError; the Final Answer stage maps it to the
existing scoped no-evidence wording ("本次检索没有找到…并不代表知识库里一定
没有相关内容"). Scoped insufficiency statements and normal grounded answers
are untouched — no blanket refusal.
"""

from __future__ import annotations

import pytest

from src.agent import (
    AgentDecision,
    AgentDecisionKind,
    AgentRequest,
    FinalAnswerStage,
)
from src.agent.execution.contracts import (
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentRuntimeTrace,
)
from src.agent.response import AgentResponseStatus
from src.agent.tools import (
    ToolResult,
    ToolResultStatus,
)
from src.ai.provider import CompletionResult
from src.ai.rag_answer_service import (
    RagAnswerError,
    RagAnswerErrorCode,
    RagAnswerService,
)
from src.knowledge_context_packager import KnowledgeContextPackager

KB_UUID = "12345678-1234-1234-1234-123456789abc"


# ---------------------------------------------------------------------------
# fakes (mirroring tests/test_agent_final_answer.py)
# ---------------------------------------------------------------------------


class _StubProvider:
    """Returns a fixed completion text, recording the prompt."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.prompts: list[str] = []

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        self.prompts.append(prompt)
        return CompletionResult(text=self._text, model="fake-1")


class _RaisingRagService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def answer(self, *args: object, **kwargs: object) -> object:
        raise self._error


def _package():
    """One sourced page item (irrelevant maintenance content)."""
    from src.models import (
        ContextAnchorType,
        ContextFingerprintState,
        ContextItem,
        ContextItemType,
        ContextSourceAnchor,
    )

    item = ContextItem(
        type=ContextItemType.KNOWLEDGE_OBJECT,
        local_id=1,
        stable_id=f"{KB_UUID}:knowledge_object:1",
        title="泵维护视觉手册",
        content="泵体巡检：检查机械密封有无渗漏，电机温度是否正常。",
        kind="page",
        kind_label="页面",
        status="active",
        status_label="现行",
        importance="primary",
        updated_at=None,
        revision_ref="第 1 版",
        source_anchors=(
            ContextSourceAnchor(
                anchor_type=ContextAnchorType.PAGE.value,
                anchor_id=11,
                anchor_label="泵维护视觉手册 · 第 1 页",
                fingerprint_state=ContextFingerprintState.VALID.value,
            ),
        ),
        relation_refs=(),
    )
    return KnowledgeContextPackager(kb_uuid=KB_UUID, app_version="test").build(
        [item]
    )


def _service_returning(text: str) -> RagAnswerService:
    return RagAnswerService(_StubProvider(text))


def _success_execution() -> AgentExecutionResult:
    from src.agent.tools.contracts import ToolReference

    tool_result = ToolResult(
        status=ToolResultStatus.SUCCESS,
        data={"query": "那个20米每秒的车", "total": 1, "results": []},
        references=(
            ToolReference(
                stable_id=f"{KB_UUID}:page:11",
                anchor_label="泵维护视觉手册 · 第 1 页",
            ),
        ),
    )
    return AgentExecutionResult(
        status=AgentExecutionStatus.COMPLETED,
        decision=AgentDecision(
            kind=AgentDecisionKind.CALL_TOOL,
            tool_name="page_search",
            arguments={"query": "那个20米每秒的车"},
        ),
        tool_called=True,
        selected_tool="page_search",
        tool_result=tool_result,
        error=None,
        trace=AgentRuntimeTrace(
            run_id="m1rb-run",
            request_id=None,
            started_at="2026-09-09T00:00:00+00:00",
            duration_ms=None,
            decision_kind="CALL_TOOL",
            selected_tool="page_search",
            decision_call_count=1,
            tool_call_count=1,
            retry_count=0,
            tool_status="success",
            outcome="completed",
        ),
    )


def _request() -> AgentRequest:
    return AgentRequest(text="那个20米每秒的车", request_id="m1rb-001")


# ---------------------------------------------------------------------------
# RagAnswerService guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        # RUN2 实例 2 exact shape (scoped prefix does not license the claim)
        (
            "无法确定您所指的“那个20米每秒的车”。"
            "当前返回的上下文中没有任何资料提到“车”或“20米每秒”这一速度参数。"
            "依据：【来源 #1】"
        ),
        "知识库里没有与这辆车相关的内容。依据：【来源 #1】",
        "你的资料中不存在关于“20米每秒”的记录。依据：【来源 #1】",
        "所有资料都没有提到汽车行驶的路程问题。依据：【来源 #1】",
        "库里完全没有车辆行驶相关的资料。依据：【来源 #1】",
    ],
    ids=[
        "run2-exact",
        "knowledge-base-absence",
        "your-materials-absence",
        "universal-absence",
        "colloquial-absence",
    ],
)
def test_corpus_wide_negative_assertion_is_rejected(answer: str) -> None:
    with pytest.raises(RagAnswerError) as exc_info:
        _service_returning(answer).answer("那个20米每秒的车", _package())
    assert exc_info.value.code is RagAnswerErrorCode.CORPUS_WIDE_NEGATIVE


@pytest.mark.parametrize(
    "answer",
    [
        # normal grounded answer — must never be degraded
        (
            "这辆汽车 10 秒内通过的路程是 200 米，"
            "由 s = v*t = 20 * 10 计算得出。"
            "（《我的物理错题本》，第 1 页）【来源 #1】"
        ),
        # scoped insufficiency — the prompt-mandated honest form (rule 7/16)
        "根据提供的知识上下文，信息不足：上下文没有提到这辆车的行驶数据。",
        # doc-scoped absence — faithful to one specified source, no corpus claim
        (
            "《泵维护视觉手册》中没有写明车速或路程数据，"
            "无法从这份资料确认。依据：【来源 #1】"
        ),
        # RUN2 实例 1 shape: bounded-context description without a universal
        # quantifier over the whole corpus
        (
            "当前返回的知识上下文包中只包含《泵维护视觉手册》等资料，"
            "缺少关于车辆速度、时间与距离计算的相关信息，"
            "无法据此回答。依据：【来源 #1】"
        ),
    ],
    ids=[
        "grounded-answer",
        "sanctioned-insufficiency",
        "doc-scoped-absence",
        "bounded-context-description",
    ],
)
def test_legitimate_answers_pass_through_unchanged(answer: str) -> None:
    output = _service_returning(answer).answer("那个20米每秒的车", _package())
    assert output.answer == answer
    if "【来源 #1】" in answer:
        assert output.answer_citations == (f"{KB_UUID}:knowledge_object:1",)
    else:
        assert output.answer_citations == ()


# ---------------------------------------------------------------------------
# Final Answer stage mapping
# ---------------------------------------------------------------------------


def test_final_answer_stage_maps_guard_to_scoped_no_evidence() -> None:
    stage = FinalAnswerStage(
        _RaisingRagService(
            RagAnswerError(
                RagAnswerErrorCode.CORPUS_WIDE_NEGATIVE,
                "回答包含超出本次检索返回范围的全称否定断言，拒绝显示。",
            )
        ),  # type: ignore[arg-type]
        packager=KnowledgeContextPackager(kb_uuid=KB_UUID, app_version="test"),
    )

    response = stage.answer(_request(), _success_execution())

    assert response.status is AgentResponseStatus.COMPLETED
    assert response.grounded is False
    assert "本次检索没有找到" in response.answer
    assert "并不代表知识库里一定没有相关内容" in response.answer
    # the rejected model output must never be exposed
    assert "没有任何资料" not in response.answer
    assert response.citations == ()
