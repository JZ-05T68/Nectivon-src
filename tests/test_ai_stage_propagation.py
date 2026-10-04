"""Full-chain stage-propagation regression (fake transport only).

Added 2026-09-21 after the DeepSeek "384 fake / 128 real" divergence
(docs/REAL_PAYLOAD_POLICY_AUDIT_2026-09-21.md). The fake-tested payload
policy and the real runtime share one code path, so these tests drive the
*real* product composition roots — ``ModelDecisionProvider`` and
``RagAnswerService`` — through the audited wrapper and the provider factory
down to the HTTP payload boundary, and pin the ledger attribution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.agent.decision.provider import ModelDecisionProvider
from src.agent.execution.contracts import AgentRequest
from src.agent.tools.bootstrap import build_phase1_registry
from src.ai.completion_stage import (
    CompletionStage,
    current_completion_stage,
)
from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.deepseek_client import DEFAULT_BASE_URL
from src.ai.model_registry import ProviderId
from src.ai.provider import AiCallRecord
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_factory import (
    build_audited_deepseek_provider,
    resolve_deepseek_runtime,
)
from src.ai.rag_answer_service import RagAnswerService
from src.config import Settings
from src.knowledge_context_packager import KnowledgeContextPackager
from src.models import (
    ContextAnchorType,
    ContextFingerprintState,
    ContextItem,
    ContextItemType,
    ContextSourceAnchor,
)

_SYNTHETIC_KEY = "synthetic-stage-propagation-93f1"
_CUSTOM_MODEL = "deepseek-user-model"
KB_UUID = "12345678-1234-1234-1234-123456789abc"

_DECISION_TEXT = (
    '{"kind": "call_tool", "tool_name": "knowledge_search", '
    '"arguments": {"query": "光速"}}'
)


class _StageCaptureTransport:
    """Fake transport recording the stage visible at the payload boundary."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[Mapping[str, str], Mapping[str, Any]]] = []
        self.stages: list[CompletionStage | None] = []

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ):
        self.stages.append(current_completion_stage())
        self.calls.append((headers, payload))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Ledger:
    def __init__(self) -> None:
        self.records: list[AiCallRecord] = []

    def record(self, call: AiCallRecord) -> None:
        self.records.append(call)


class _AllowBudget:
    def ensure_allowed(self, capability: str) -> None:
        assert capability == "completion"


def _completion_body(text: str) -> dict[str, Any]:
    return {
        "model": _CUSTOM_MODEL,
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
    }


def _sourced_item() -> ContextItem:
    return ContextItem(
        type=ContextItemType.KNOWLEDGE_OBJECT,
        local_id=1,
        stable_id=f"{KB_UUID}:knowledge_object:1",
        title="编码器接线经验",
        content="A/B 相接反会导致 PID 震荡。",
        kind="experience",
        kind_label="经验",
        status="active",
        status_label="现行",
        importance="primary",
        updated_at=None,
        revision_ref="第 1 版",
        source_anchors=(
            ContextSourceAnchor(
                anchor_type=ContextAnchorType.PAGE.value,
                anchor_id=7,
                anchor_label="页面 7",
                fingerprint_state=ContextFingerprintState.VALID.value,
            ),
        ),
        relation_refs=(),
    )


def _audited_deepseek(
    tmp_path: object, transport: _StageCaptureTransport, ledger: _Ledger
):
    config_store = ProviderConfigStore(tmp_path / "ai-providers-v1.json")  # type: ignore[arg-type]
    config_store.save(
        AIProviderConfig().with_provider(
            ProviderSettings(
                provider_id=ProviderId.DEEPSEEK,
                base_url=DEFAULT_BASE_URL,
                model_id=_CUSTOM_MODEL,
                model_selection=ModelSelectionKind.CUSTOM,
            ),
            make_active=True,
        )
    )
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.DEEPSEEK, SecretCredential(_SYNTHETIC_KEY))
    resolved = resolve_deepseek_runtime(
        Settings(_env_file=None),
        config_store=config_store,
        credential_store=credentials,
    )
    assert resolved is not None
    return build_audited_deepseek_provider(
        resolved,
        transport=transport,
        source_feature="stage_propagation_test",
        ledger=ledger,
        budget_guard=_AllowBudget(),
    )


def test_agent_decision_chain_applies_stage_policy_at_the_payload(
    tmp_path,
) -> None:
    """The real Decision call site drives a non-thinking 256-floor payload."""

    transport = _StageCaptureTransport([_completion_body(_DECISION_TEXT)])
    ledger = _Ledger()
    audited = _audited_deepseek(tmp_path, transport, ledger)
    provider = ModelDecisionProvider(
        audited,
        build_phase1_registry(),
        model=_CUSTOM_MODEL,
        source_feature="agent_decision",
    )

    decision = provider.decide(AgentRequest(request_id="t1", text="光速是多少"))

    assert decision.tool_name == "knowledge_search"
    # The stage set by the real call site is visible at the HTTP boundary…
    assert transport.stages[0] is CompletionStage.AGENT_DECISION
    # …and the payload policy used exactly that stage.
    payload = transport.calls[0][1]
    assert payload["max_tokens"] == 256
    assert payload["thinking"] == {"type": "disabled"}
    assert "max_completion_tokens" not in payload


def test_final_answer_chain_applies_stage_policy_at_the_payload(tmp_path) -> None:
    """The RAG Final Answer path drives a non-thinking 512-capped payload."""

    transport = _StageCaptureTransport(
        [_completion_body("依据【来源 #1】：A/B 相接反会导致 PID 震荡。")]
    )
    ledger = _Ledger()
    audited = _audited_deepseek(tmp_path, transport, ledger)
    service = RagAnswerService(audited)
    package = KnowledgeContextPackager(kb_uuid=KB_UUID).build([_sourced_item()])

    output = service.answer(
        "编码器 A/B 相接反会怎样？",
        package,
        model=_CUSTOM_MODEL,
        source_feature="agent_final_answer",
        max_completion_tokens=512,
    )

    assert "A/B 相" in output.answer
    assert transport.stages[0] is CompletionStage.FINAL_ANSWER
    payload = transport.calls[0][1]
    assert payload["max_tokens"] == 512
    assert payload["thinking"] == {"type": "disabled"}


def test_ledger_attribution_is_unchanged_by_the_payload_policy(tmp_path) -> None:
    transport = _StageCaptureTransport([_completion_body(_DECISION_TEXT)])
    ledger = _Ledger()
    audited = _audited_deepseek(tmp_path, transport, ledger)
    provider = ModelDecisionProvider(
        audited,
        build_phase1_registry(),
        model=_CUSTOM_MODEL,
        source_feature="agent_decision",
    )

    provider.decide(AgentRequest(request_id="t2", text="光速是多少"))

    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.provider == "deepseek"
    assert record.model == _CUSTOM_MODEL
    assert record.requested_model == _CUSTOM_MODEL
    assert record.resolved_model == _CUSTOM_MODEL
    assert record.status == "success"
    assert record.finish_reason == "stop"
    assert record.completion_tokens == 6
    assert record.total_tokens == 10
    assert record.source_feature == "agent_decision"
    assert record.capability == "completion"
