"""Single-step Agent execution contracts (v0.6.0 Phase 2A).

This module defines the minimal, vendor-independent boundary between an
(untrusted, future model-produced) structured decision and the frozen Phase 1
read-only Tool Registry.

It deliberately does not define a second AI provider abstraction: the
:class:`DecisionProvider` protocol only says "give the execution kernel one
already-structured :class:`AgentDecision`". Phase 2B will adapt this protocol
to the existing vendor-neutral AI contracts in ``src.ai.provider``.

Safety invariants frozen by ADR-006 and Phase 2A:

- at most one decision call per run;
- at most one Tool call per run;
- zero Agent autonomous retry;
- no loop / no multi-step;
- write tools are unreachable through the Phase 1 READ_ONLY policy.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from src.agent.tools.contracts import ToolResult

MAX_TOOL_NAME_LENGTH = 100
MAX_AGENT_REQUEST_CHARS = 120_000
_SENSITIVE_ARGUMENT_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "password", "secret", "token"}
)


def _redact_audit_value(value: object) -> object:
    """Redact credential-shaped mapping fields before trace/log persistence."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).casefold() in _SENSITIVE_ARGUMENT_KEYS
                else _redact_audit_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_audit_value(item) for item in value]
    return value


class AgentDecisionKind(StrEnum):
    """The only two actions a single-step Agent Decision may express.

    ``ANSWER_DIRECTLY`` is the ADR-006 name for the NO_TOOL branch: this
    execution step performs no Tool call. It does NOT mean the RAG Final Answer
    Stage has been implemented.
    """

    CALL_TOOL = "call_tool"
    ANSWER_DIRECTLY = "answer_directly"


class AgentExecutionStatus(StrEnum):
    """Final status of one single-step Agent execution."""

    COMPLETED = "completed"
    FAILED = "failed"


class AgentExecutionErrorCode(StrEnum):
    """Closed set of execution-kernel failure codes."""

    DECISION_PROVIDER_FAILED = "decision_provider_failed"
    DECISION_LENGTH_TRUNCATED = "decision_length_truncated"
    DECISION_PARSE_FAILED = "decision_parse_failed"
    INVALID_DECISION = "invalid_decision"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """One user request entering the single-step execution kernel.

    The runtime trace intentionally does not copy ``text``; only ``request_id``
    is carried into the trace.
    """

    request_id: str | None = None
    text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("AgentRequest.text 必须是字符串")
        if len(self.text) > MAX_AGENT_REQUEST_CHARS:
            raise ValueError(
                f"AgentRequest.text 超过大小限制（{MAX_AGENT_REQUEST_CHARS} 字符）"
            )


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """One validated, structured decision for a single execution step.

    The dataclass enforces single-tool semantics from the type level: there is
    no ``tool_calls`` list field, and unknown constructor fields are rejected.
    ``ANSWER_DIRECTLY`` must not smuggle a Tool request.
    """

    kind: AgentDecisionKind
    tool_name: str | None = None
    arguments: Mapping[str, object] = field(default_factory=dict)
    finish_reason: str | None = None
    output_chars: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", AgentDecisionKind(self.kind))
        if self.kind is AgentDecisionKind.CALL_TOOL:
            if not isinstance(self.tool_name, str) or not self.tool_name.strip():
                raise ValueError("CALL_TOOL decision 必须提供非空 tool_name")
            tool_name = self.tool_name.strip()
            if len(tool_name) > MAX_TOOL_NAME_LENGTH:
                raise ValueError(
                    f"tool_name 不能超过 {MAX_TOOL_NAME_LENGTH} 字符"
                )
            object.__setattr__(self, "tool_name", tool_name)
            if not isinstance(self.arguments, Mapping):
                raise TypeError("arguments 必须是 mapping")
            object.__setattr__(
                self, "arguments", MappingProxyType(dict(self.arguments))
            )
        else:
            if self.tool_name is not None:
                raise ValueError("ANSWER_DIRECTLY decision 不能携带 tool_name")
            if self.arguments:
                raise ValueError("ANSWER_DIRECTLY decision 不能携带 arguments")

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic plain-structure view for audit/trace."""
        return {
            "kind": self.kind.value,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments) if self.arguments else {},
        }


@runtime_checkable
class DecisionProvider(Protocol):
    """Boundary that supplies one structured AgentDecision per execution.

    This is NOT a second AI provider abstraction. It is the Agent-domain input
    boundary consumed by the execution kernel; Phase 2B will connect it to the
    existing vendor-neutral provider contracts.
    """

    def decide(self, request: AgentRequest) -> AgentDecision:
        """Return exactly one structured decision for ``request``."""
        ...


@dataclass(frozen=True, slots=True)
class AgentExecutionError:
    """Safe, structured execution-kernel failure.

    ``message`` is safe for public/Agent surfaces; ``detail`` is an internal
    diagnostic (typically the exception class name only) and is never exposed
    by default.
    """

    code: AgentExecutionErrorCode
    message: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRuntimeTrace:
    """In-memory runtime trace for one single-step execution.

    The kernel builds this in memory; the orchestration pipeline may append its
    bounded fields to the request audit ledger and debug log. It never stores
    the full user request, API keys, Authorization headers, stack traces, raw
    model output, or full ToolResult payloads.
    """

    run_id: str
    request_id: str | None
    started_at: str
    duration_ms: int | None
    decision_kind: str | None
    selected_tool: str | None
    decision_call_count: int
    tool_call_count: int
    retry_count: int
    tool_status: str | None
    outcome: str
    error_code: str | None = None
    error_message: str | None = None
    decision_status: str | None = None
    decision_finish_reason: str | None = None
    decision_output_chars: int | None = None
    decision_output_tokens: int | None = None
    decision_tool: str | None = None
    decision_arguments: Mapping[str, object] = field(default_factory=dict)
    tool_audit_status: str = "tool_not_called"
    tool_result_count: int = 0
    top_evidence_page_ids: tuple[int, ...] = ()
    final_status: str | None = None
    final_finish_reason: str | None = None
    final_declared_insufficient: bool = False
    evidence_count: int = 0
    evidence_page_ids: tuple[int, ...] = ()
    evidence_excerpt_chars: int = 0
    ui_failure_reason_code: str | None = None

    def __post_init__(self) -> None:
        redacted_arguments = _redact_audit_value(self.decision_arguments)
        if not isinstance(redacted_arguments, Mapping):
            redacted_arguments = {}
        object.__setattr__(
            self,
            "decision_arguments",
            MappingProxyType(dict(redacted_arguments)),
        )
        object.__setattr__(self, "top_evidence_page_ids", tuple(self.top_evidence_page_ids))
        object.__setattr__(self, "evidence_page_ids", tuple(self.evidence_page_ids))

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic plain-structure view for runtime audit."""
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "decision_kind": self.decision_kind,
            "selected_tool": self.selected_tool,
            "decision_call_count": self.decision_call_count,
            "tool_call_count": self.tool_call_count,
            "retry_count": self.retry_count,
            "tool_status": self.tool_status,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "decision_status": self.decision_status,
            "decision_finish_reason": self.decision_finish_reason,
            "decision_output_chars": self.decision_output_chars,
            "decision_output_tokens": self.decision_output_tokens,
            "decision_tool": self.decision_tool,
            "decision_arguments": dict(self.decision_arguments),
            "tool_audit_status": self.tool_audit_status,
            "tool_result_count": self.tool_result_count,
            "top_evidence_page_ids": list(self.top_evidence_page_ids),
            "final_status": self.final_status,
            "final_finish_reason": self.final_finish_reason,
            "final_declared_insufficient": self.final_declared_insufficient,
            "evidence_count": self.evidence_count,
            "evidence_page_ids": list(self.evidence_page_ids),
            "evidence_excerpt_chars": self.evidence_excerpt_chars,
            "ui_failure_reason_code": self.ui_failure_reason_code,
        }


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    """Structured outcome of one single-step execution.

    ``tool_result`` preserves the structured ToolResult when a Tool was called;
    it is never converted to Markdown or treated as a user-facing final answer.
    """

    status: AgentExecutionStatus
    decision: AgentDecision
    tool_called: bool
    selected_tool: str | None
    tool_result: ToolResult | None
    error: AgentExecutionError | None
    trace: AgentRuntimeTrace


def new_run_id() -> str:
    """Return a fresh run identifier for one Agent execution."""
    return str(uuid.uuid4())


def utc_timestamp() -> str:
    """Return a UTC ISO-8601 timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


__all__ = [
    "AgentDecision",
    "AgentDecisionKind",
    "AgentExecutionError",
    "AgentExecutionErrorCode",
    "AgentExecutionResult",
    "AgentExecutionStatus",
    "AgentRequest",
    "AgentRuntimeTrace",
    "DecisionProvider",
    "MAX_AGENT_REQUEST_CHARS",
    "new_run_id",
    "utc_timestamp",
]
