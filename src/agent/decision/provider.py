"""Model-backed DecisionProvider adapter (v0.6.0 Phase 2B).

This is the only production component that turns the Phase 2A ``AgentRequest``
into an ``AgentDecision`` through the existing vendor-neutral AI Provider:

- the Tool catalog comes from the frozen Phase 1 Tool Registry;
- the provider is called exactly once, through the existing audited/budgeted
  runtime when an ``AuditedAIProvider`` is supplied;
- the model output is parsed by the strict parser and no repair/retry exists;
- this adapter never executes Tools and never starts the Final Answer Stage.

It intentionally does not create a second AI Provider abstraction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.agent.decision.parser import DecisionParseError, parse_decision
from src.agent.decision.prompt import build_decision_prompt
from src.agent.execution.contracts import AgentDecision, AgentRequest
from src.agent.tools.bootstrap import build_phase1_registry
from src.agent.tools.contracts import ToolDefinition
from src.agent.tools.registry import ToolRegistry
from src.ai.completion_stage import (
    CompletionStage,
    completion_stage_scope,
    is_length_truncated,
)
from src.ai.provider import (
    AIError,
    AIExecutionError,
    AuditedAIProvider,
    CompletionProvider,
    CompletionResult,
    require_ai_provider,
)

DEFAULT_DECISION_MAX_OUTPUT_TOKENS = 256
DEFAULT_DECISION_SOURCE_FEATURE = "agent_decision"

__all__ = [
    "DEFAULT_DECISION_MAX_OUTPUT_TOKENS",
    "DEFAULT_DECISION_SOURCE_FEATURE",
    "ModelDecisionProvider",
]


class ModelDecisionProvider:
    """DecisionProvider implementation backed by one existing AI completion.

    ``provider`` may be ``None`` (manual mode), a plain ``CompletionProvider``,
    or an ``AuditedAIProvider``. When it is an ``AuditedAIProvider`` the call is
    recorded with ``source_feature="agent_decision"`` through the existing
    audit ledger and evaluated by the existing budget guard before any network
    request is sent.
    """

    def __init__(
        self,
        provider: CompletionProvider | None,
        registry: ToolRegistry | None = None,
        *,
        definitions: Sequence[ToolDefinition] | None = None,
        model: str | None = None,
        max_completion_tokens: int = DEFAULT_DECISION_MAX_OUTPUT_TOKENS,
        source_feature: str = DEFAULT_DECISION_SOURCE_FEATURE,
        target_refs: Sequence[str] = (),
    ) -> None:
        if registry is not None and definitions is not None:
            raise ValueError("registry 与 definitions 不能同时提供")
        if registry is not None:
            tool_definitions = registry.list_definitions()
        elif definitions is not None:
            tool_definitions = tuple(definitions)
        else:
            tool_definitions = build_phase1_registry().list_definitions()
        self._provider = provider
        self._definitions = tuple(tool_definitions)
        self._model = model
        self._max_completion_tokens = max_completion_tokens
        self._source_feature = source_feature
        self._target_refs = tuple(target_refs)

    def decide(self, request: AgentRequest) -> AgentDecision:
        """Return one structured AgentDecision for ``request``.

        Raises the existing AI error hierarchy on unavailable / budget /
        transport / malformed-response failures. No retry is ever attempted.
        """
        if not isinstance(request, AgentRequest):
            raise TypeError("decide 只接受 AgentRequest")
        prompt = build_decision_prompt(request.text, self._definitions)
        provider = require_ai_provider(self._provider)
        try:
            result = self._complete(provider, prompt)
        except AIError:
            raise
        except Exception as exc:
            raise AIExecutionError(
                "AI 决策调用失败。",
                error_class="internal",
                retry_count=0,
            ) from exc
        if is_length_truncated(result.finish_reason):
            raise AIExecutionError(
                "AI 决策输出未完整生成，拒绝解析截断的结构化结果。",
                error_class="incomplete",
                retry_count=result.retry_count,
                finish_reason=result.finish_reason,
                output_chars=len(result.text),
                output_tokens=(
                    result.usage.completion_tokens if result.usage is not None else None
                ),
            )
        try:
            return replace(
                parse_decision(result.text),
                finish_reason=result.finish_reason,
                output_chars=len(result.text),
                output_tokens=(
                    result.usage.completion_tokens if result.usage is not None else None
                ),
            )
        except DecisionParseError as exc:
            raise AIExecutionError(
                "AI 决策输出解析失败：模型输出不是严格的结构化决策 JSON。",
                error_class="parse",
                retry_count=0,
                finish_reason=result.finish_reason,
                output_chars=len(result.text),
                output_tokens=(
                    result.usage.completion_tokens if result.usage is not None else None
                ),
            ) from exc

    def _complete(self, provider: CompletionProvider, prompt: str) -> CompletionResult:
        with completion_stage_scope(CompletionStage.AGENT_DECISION):
            if isinstance(provider, AuditedAIProvider):
                return provider.complete(
                    prompt,
                    model=self._model,
                    max_completion_tokens=self._max_completion_tokens,
                    source_feature=self._source_feature,
                    target_refs=self._target_refs,
                )
            return provider.complete(
                prompt,
                model=self._model,
                max_completion_tokens=self._max_completion_tokens,
            )
