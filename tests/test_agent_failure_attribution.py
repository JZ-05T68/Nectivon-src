"""Deterministic Stage-2 regression for Agent failure attribution."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.agent import (
    AgentRequest,
    FinalAnswerStage,
    ModelDecisionProvider,
    SingleStepAgentExecutor,
    SingleStepAgentService,
)
from src.agent.decision.prompt import USER_REQUEST_BEGIN, USER_REQUEST_END
from src.agent.response import AgentResponseErrorCode
from src.agent.tools import (
    ToolReference,
    ToolResult,
    ToolResultStatus,
    build_phase1_registry,
)
from src.ai.provider import CompletionResult, CompletionUsage
from src.ai.rag_answer_service import RagAnswerService
from src.database import Database
from src.knowledge_context_packager import KnowledgeContextPackager

KB_UUID = "12345678-1234-1234-1234-123456789abc"


class _Provider:
    def __init__(self, result: CompletionResult | Exception) -> None:
        self.result = result
        self.calls = 0

    def complete(self, prompt: str, *, model=None, max_completion_tokens=None):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _RoutingProvider:
    """Controlled provider returning a short decision for the exact request."""

    def __init__(self) -> None:
        self.calls = 0
        self.output_tokens: list[int] = []

    def complete(self, prompt: str, *, model=None, max_completion_tokens=None):
        self.calls += 1
        raw = prompt.split(USER_REQUEST_BEGIN, 1)[1].split(USER_REQUEST_END, 1)[0]
        question = json.loads(raw.strip())
        query = re.sub(r"^\s*\d+[.、]\s*", "", question).strip()
        text = json.dumps(
            {
                "kind": "CALL_TOOL",
                "tool_name": "page_search",
                "arguments": {"query": query},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completion_tokens = max(1, len(text.encode("utf-8")) // 4)
        self.output_tokens.append(completion_tokens)
        return CompletionResult(
            text=text,
            model="controlled",
            finish_reason="stop",
            usage=CompletionUsage(1, completion_tokens, 1 + completion_tokens),
        )


class _Handler:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls = []

    def __call__(self, tool_input, context):
        self.calls.append(dict(tool_input.arguments))
        return self.result


def _executor(result: ToolResult):
    registry = build_phase1_registry()
    page = _Handler(result)
    empty = _Handler(ToolResult(status=ToolResultStatus.EMPTY, data={"results": []}))
    handlers = {
        definition.name: page if definition.name == "page_search" else empty
        for definition in registry.list_definitions()
    }
    return registry, page, SingleStepAgentExecutor(registry, handlers=handlers)


def _empty() -> ToolResult:
    return ToolResult(
        status=ToolResultStatus.EMPTY,
        data={"query": "x", "total": 0, "results": []},
    )


def _evidence() -> ToolResult:
    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        data={
            "query": "x",
            "total": 1,
            "results": [
                {
                    "id": 7,
                    "page_id": 7,
                    "document_title": "PLC 试卷",
                    "snippet": "时间继电器分为通电延时和断电延时。",
                }
            ],
        },
        references=(ToolReference(f"{KB_UUID}:page:7", "第 7 页"),),
    )


def _service(executor, final_provider=None, *, audit_sink=None):
    rag = RagAnswerService(final_provider) if final_provider is not None else None
    return SingleStepAgentService(
        executor,
        FinalAnswerStage(
            rag,
            packager=KnowledgeContextPackager(kb_uuid=KB_UUID, app_version="test"),
        ),
        audit_sink=audit_sink,
    )


def test_decision_truncated_has_explicit_reason_code() -> None:
    registry, _, executor = _executor(_empty())
    decision = _Provider(
        CompletionResult(text='{"kind":"CALL_TOOL"', model="fake", finish_reason="length")
    )
    response = _service(executor).run(
        AgentRequest(text="时间继电器有哪些类型？"),
        ModelDecisionProvider(decision, registry),
    )
    assert response.error is not None
    assert response.error.code is AgentResponseErrorCode.DECISION_LENGTH_TRUNCATED
    assert response.failure_reason_code == "decision_length_truncated"
    assert response.trace is not None
    assert response.trace.decision_status == "decision_length_truncated"
    assert response.trace.decision_finish_reason == "length"
    assert response.trace.decision_output_chars == len('{"kind":"CALL_TOOL"')
    assert response.trace.tool_audit_status == "tool_not_called"


def test_decision_parse_failure_has_independent_reason_code() -> None:
    registry, _, executor = _executor(_empty())
    response = _service(executor).run(
        AgentRequest(text="x"),
        ModelDecisionProvider(
            _Provider(CompletionResult(text="not-json", model="fake", finish_reason="stop")),
            registry,
        ),
    )
    assert response.error is not None
    assert response.error.code is AgentResponseErrorCode.DECISION_PARSE_FAILED
    assert response.trace is not None
    assert response.trace.decision_status == "decision_parse_failed"
    assert response.trace.decision_finish_reason == "stop"


def test_decision_stop_executes_tool_and_tool_empty_is_distinct() -> None:
    registry, page, executor = _executor(_empty())
    response = _service(executor).run(
        AgentRequest(text="x"),
        ModelDecisionProvider(
            _Provider(
                CompletionResult(
                    text=(
                        '{"kind":"CALL_TOOL","tool_name":"page_search",'
                        '"arguments":{"query":"x"}}'
                    ),
                    model="fake",
                    finish_reason="stop",
                )
            ),
            registry,
        ),
    )
    assert page.calls == [{"query": "x"}]
    assert response.trace is not None
    assert response.trace.decision_status == "decision_success"
    assert response.trace.decision_finish_reason == "stop"
    assert response.trace.decision_tool == "page_search"
    assert response.trace.decision_arguments == {"query": "x"}
    assert response.trace.tool_audit_status == "tool_empty"
    assert response.trace.ui_failure_reason_code == "tool_empty"
    assert response.failure_reason_code == "tool_empty"


def test_tool_success_final_declared_insufficient_is_audited() -> None:
    registry, _, executor = _executor(_evidence())
    routing = _RoutingProvider()
    final = _Provider(
        CompletionResult(
            text="知识上下文信息不足，无法可靠回答该问题。",
            model="fake-final",
            finish_reason="stop",
        )
    )
    response = _service(executor, final).run(
        AgentRequest(text="时间继电器有哪些类型？"),
        ModelDecisionProvider(routing, registry),
    )
    assert response.trace is not None
    assert response.trace.tool_audit_status == "tool_success"
    assert response.trace.tool_result_count == 1
    assert response.trace.top_evidence_page_ids == (7,)
    assert response.trace.final_status == "final_declared_insufficient"
    assert response.trace.final_declared_insufficient is True
    assert response.trace.final_finish_reason == "stop"
    assert response.trace.evidence_count == 1
    assert response.trace.evidence_page_ids == (7,)
    assert response.trace.evidence_excerpt_chars > 0
    assert response.trace.ui_failure_reason_code == "final_declared_insufficient"


def test_final_length_truncation_is_distinct() -> None:
    registry, _, executor = _executor(_evidence())
    final = _Provider(
        CompletionResult(text="partial", model="fake", finish_reason="length")
    )
    response = _service(executor, final).run(
        AgentRequest(text="x"), ModelDecisionProvider(_RoutingProvider(), registry)
    )
    assert response.error is not None
    assert response.error.code is AgentResponseErrorCode.FINAL_LENGTH_TRUNCATED
    assert response.trace is not None
    assert response.trace.final_status == "final_length_truncated"
    assert response.trace.final_finish_reason == "length"


def test_request_audit_is_persisted_without_model_output(tmp_path: Path) -> None:
    database = Database(tmp_path / "knowledge.db")
    registry, _, executor = _executor(_empty())
    response = _service(executor, audit_sink=database.insert_agent_run_audit).run(
        AgentRequest(request_id="audit-1", text="x"),
        ModelDecisionProvider(_RoutingProvider(), registry),
    )
    rows = database.list_agent_run_audits()
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == response.trace.run_id  # type: ignore[union-attr]
    assert row["decision_status"] == "decision_success"
    assert json.loads(str(row["decision_arguments"])) == {"query": "x"}
    assert row["tool_status"] == "tool_empty"
    assert row["ui_failure_reason_code"] == "tool_empty"
    assert "not-json" not in json.dumps(row, ensure_ascii=False)


@pytest.mark.parametrize(
    "question",
    [
        "时间继电器共有哪两种类型？",
        "低压断路器包含哪些脱扣器？",
        "顺序控制线路中 QA2 吸合以后为什么要让 KF 退出？",
        "速度继电器一般多少转速动作，多少转速以下复位？",
        "24. 时间继电器共有哪两种类型？",
        "25. 低压断路器包含哪些脱扣器？",
        "26. 顺序控制线路中 QA2 吸合以后为什么要让 KF 退出？",
        "27. 速度继电器一般多少转速动作，多少转速以下复位？",
    ],
)
def test_plc_decision_is_stable_twenty_times(question: str) -> None:
    registry, page, executor = _executor(_empty())
    provider = _RoutingProvider()
    service = _service(executor)
    expected_query = re.sub(r"^\s*\d+[.、]\s*", "", question).strip()
    for index in range(20):
        response = service.run(
            AgentRequest(request_id=f"plc-{index}", text=question),
            ModelDecisionProvider(provider, registry),
        )
        assert response.trace is not None
        assert response.trace.decision_status == "decision_success"
        assert response.trace.decision_finish_reason == "stop"
        assert response.trace.decision_output_tokens is not None
        assert response.trace.decision_tool == "page_search"
        assert response.trace.decision_arguments == {"query": expected_query}
    assert provider.calls == 20
    assert len(page.calls) == 20
