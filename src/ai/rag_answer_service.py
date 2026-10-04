"""Audited RAG answer service (v0.5.3 Phase 3).

``RagAnswerService`` turns a ``KnowledgeContextPackage`` plus a user question
into one fully traceable ``AuditedAIOutput`` through a completion provider.

Boundaries (frozen for v0.5.x):

- the provider never accesses the database — it only receives the prompt;
- the provider input is always a ``KnowledgeContextPackage``, never raw
  search-result text;
- every output carries its used context ids, citations, exclusions and
  warnings, so the chain can always answer "what knowledge was this based on";
- provider failures propagate as typed AI errors; the service never swallows
  them into a fake answer;
- no knowledge is written, summarised into memory, or modified.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from src.ai.answer_format import normalize_numbered_list_lines
from src.ai.completion_stage import (
    CompletionStage,
    completion_stage_scope,
    is_length_truncated,
)
from src.ai.provider import (
    AIExecutionError,
    AuditedAIProvider,
    CompletionProvider,
    require_ai_provider,
)
from src.ai.rag_prompt_builder import RagPromptBuilder
from src.knowledge_context_packager import KnowledgeContextPackage
from src.models import AuditedAIOutput

_STABLE_ID_TOKEN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}:[a-z_]+:[0-9]+"
)
_CITATION_NUMBER_TOKEN = re.compile(r"#([0-9]{1,3})")
_FIRST_SENTENCE = re.compile(r"^(.*?[。！？!?]|[^\n]+\n)", re.DOTALL)
_CHOICE_ASSERTION = re.compile(
    r"(?:答案.{0,16}?(?:不是|是|为|选)|(?:不是|是|而是))\s*[A-DＡ-Ｄ]",
    re.IGNORECASE,
)

#: Corpus-wide negative assertion shapes (HBV2 Phase 2 RUN2, 2026-09-09 M1R-B).
#: The context package is a bounded retrieval window — top-k failure is never
#: corpus-wide absence. Generic quantified-absence shapes only (no domain or
#: topic vocabulary); scoped insufficiency wording (上下文不足以确认 / 信息不足)
#: deliberately does not match. Detection is deterministic and fails closed:
#: the model output is discarded, never partially edited.
_CORPUS_WIDE_NEGATIVE_PATTERNS: Final[tuple[str, ...]] = (
    "没有任何资料",
    "没有任何文档",
    "没有任何相关内容",
    "没有任何内容",
    "没有任何记录",
    "所有资料都没有",
    "所有资料都未",
    "所有资料均未",
    "所有文档都没有",
    "所有文档均未",
    "知识库里没有",
    "知识库中没有",
    "知识库中不存在",
    "知识库里不存在",
    "库里完全没有",
    "库中完全没有",
    "整个知识库没有",
    "整个知识库中没",
    "不存在任何资料",
    "不存在任何文档",
    "不存在任何相关",
    "你的资料中不存在",
    "你的资料里没有",
    "从未提到",
    "从未记载",
)

__all__ = [
    "MockCompletionProvider",
    "RagAnswerError",
    "RagAnswerErrorCode",
    "RagAnswerService",
]


class RagAnswerErrorCode(StrEnum):
    """Closed set of machine-readable RAG safety rejection codes.

    Machine consumers must classify on ``RagAnswerError.code`` only; the
    human-readable message never participates in control flow.
    """

    EMPTY_CONTEXT = "empty_context"
    CITATION_INVALID = "citation_invalid"
    CORPUS_WIDE_NEGATIVE = "corpus_wide_negative"


class RagAnswerError(ValueError):
    """Raised when the audited answer chain cannot run safely.

    ``code`` is the closed machine-readable classification and ``message``
    is the safe human-readable text. The two are independent: rewriting the
    message never changes the machine semantics.
    """

    def __init__(self, code: RagAnswerErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class MockCompletionProvider:
    """Deterministic offline provider for the first-stage implementation.

    It performs no network I/O, reads no API key and returns a clearly
    labelled mock answer. It exists so the audited chain can be exercised and
    tested without any credential or paid call.
    """

    is_configured = False

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ):
        from src.ai.provider import CompletionResult

        return CompletionResult(
            text=(
                "（离线演示回答，未调用真实 AI 模型）\n\n"
                "已收到问题与知识上下文。请以上下文包中的【来源 #编号】"
                "逐条核对事实依据；本演示不产生真实推理结论。\n\n"
                "依据：【来源 #1】。"
            ),
            model=model or "mock-1",
            usage=None,
        )


class RagAnswerService:
    """Run the controlled answer chain over one context package."""

    def __init__(
        self,
        provider: CompletionProvider | None,
        *,
        prompt_builder: RagPromptBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder or RagPromptBuilder()

    def answer(
        self,
        query: str,
        package: KnowledgeContextPackage,
        *,
        model: str | None = None,
        source_feature: str = "rag_answer",
        max_completion_tokens: int | None = None,
    ) -> AuditedAIOutput:
        """Answer ``query`` strictly from ``package``, or raise fail-closed.

        ``max_completion_tokens`` is an optional vendor-neutral output cap;
        ``None`` preserves the provider's default (backward compatible).
        """

        if not package.items:
            raise RagAnswerError(
                RagAnswerErrorCode.EMPTY_CONTEXT,
                "空上下文：没有可用知识，拒绝生成 AI 回答。",
            )
        if all(not item.source_anchors for item in package.items):
            raise RagAnswerError(
                RagAnswerErrorCode.EMPTY_CONTEXT,
                "无来源上下文：全部知识项都没有可回源来源，拒绝生成 AI 回答。",
            )
        provider = require_ai_provider(self._provider)
        prompt = self._prompt_builder.build(query, package)
        target_refs = tuple(item.stable_id for item in package.items)

        with completion_stage_scope(CompletionStage.FINAL_ANSWER):
            if isinstance(provider, AuditedAIProvider):
                result = provider.complete(
                    prompt,
                    model=model,
                    max_completion_tokens=max_completion_tokens,
                    source_feature=source_feature,
                    target_refs=target_refs,
                )
            else:
                result = provider.complete(
                    prompt,
                    model=model,
                    max_completion_tokens=max_completion_tokens,
                )

        if is_length_truncated(result.finish_reason):
            raise AIExecutionError(
                "AI 最终回答未完整生成，拒绝校验或展示截断内容。",
                error_class="incomplete",
                retry_count=result.retry_count,
                finish_reason=result.finish_reason,
                output_chars=len(result.text),
            )

        warnings = tuple(warning.message for warning in package.warnings)
        if not warnings and any(not item.source_anchors for item in package.items):
            warnings = ("部分知识项没有可回源来源。",)
        guarded_text = _normalize_choice_conflict_conclusion(result.text)
        if _contains_corpus_wide_negative(guarded_text):
            # M1R-B guard (HBV2 Phase 2 RUN2): grounded=True with an
            # irrelevant bounded context does not license corpus-wide
            # absence claims. Fail closed to the deterministic no-evidence
            # path; the rejected model output is never exposed.
            raise RagAnswerError(
                RagAnswerErrorCode.CORPUS_WIDE_NEGATIVE,
                "回答包含超出本次检索返回范围的全称否定断言，拒绝显示。",
            )
        answer_citations = _validate_answer_citations(guarded_text, package)
        declared_insufficient = (
            not answer_citations
            and _is_sanctioned_insufficiency_statement(guarded_text)
        )
        # Presentation-only normalization applied after every fail-closed check
        # above (citation, corpus-wide-negative, truncation) has run against the
        # raw model output. It only inserts line breaks so enumerated items are
        # not flattened into one run-on paragraph; citations and page refs are
        # whitespace-independent and survive unchanged. The audit keeps the raw
        # model output length in ``output_chars``.
        answer = normalize_numbered_list_lines(guarded_text)
        return AuditedAIOutput(
            output_id=str(uuid.uuid4()),
            query=query.strip(),
            context_package_id=package.package_uuid,
            provider=(
                "mock"
                if isinstance(provider, MockCompletionProvider)
                else str(getattr(provider, "provider_id", None) or "configured")
            ),
            model=result.model,
            generated_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            answer=answer,
            citations=package.citations,
            answer_citations=answer_citations,
            warnings=warnings,
            token_usage=result.usage,
            context_stable_ids=target_refs,
            excluded=tuple(
                (item.stable_id, item.reason) for item in package.excluded
            ),
            confidence=None,
            finish_reason=result.finish_reason,
            output_chars=len(result.text),
            declared_insufficient=declared_insufficient,
        )


def _contains_corpus_wide_negative(text: str) -> bool:
    """True when the answer asserts absence beyond the retrieved window."""

    lowered = (text or "").casefold()
    return any(pattern in lowered for pattern in _CORPUS_WIDE_NEGATIVE_PATTERNS)


def _normalize_choice_conflict_conclusion(text: str) -> str:
    """Remove a contradictory leading verdict from an admitted source conflict.

    Some providers follow the generic leading-question rule first (``不是D``)
    and only then follow the stricter source-conflict rule (``无法确认``).  Once
    the answer itself explicitly acknowledges that the source's option letter
    and explanation disagree, presenting that leading verdict is unsafe.  This
    narrow presentation guard replaces only the first sentence and leaves the
    cited evidence untouched.  It never tries to discover a conflict on its
    own and therefore cannot invent one.
    """

    source = (text or "").strip()
    if not source:
        return source
    conflict_admitted = (
        "字母与解释不一致" in source
        and "无法确认哪个是命题方原意" in source
    )
    if not conflict_admitted:
        return source
    first_match = _FIRST_SENTENCE.match(source)
    first = first_match.group(1).strip() if first_match else source
    if "无法确认" in first or not _CHOICE_ASSERTION.search(first):
        return source
    safe = "结论：来源的选项字母与文字解释不一致，无法确认哪个选项是命题方原意。"
    remainder = source[first_match.end() :].lstrip() if first_match else ""
    return f"{safe}\n{remainder}" if remainder else safe


def _validate_answer_citations(
    text: str, package: KnowledgeContextPackage
) -> tuple[str, ...]:
    """Validate every citation in the AI answer against the context package.

    Citation markers may be either ``#N`` numbers (mapped through the
    package citation list) or raw ``<kb_uuid>:<type>:<id>`` stable ids. Any
    unknown, forged, blank or malformed citation fails closed; a valid
    citation set is deduplicated while preserving first-seen order.
    """

    package_stable_ids = {item.stable_id for item in package.items}
    citation_by_number: dict[int, str] = {}
    for stable_id, number in package.citations:
        if number.startswith("#") and number[1:].isdigit():
            citation_by_number[int(number[1:])] = stable_id

    found: list[str] = []
    for match in _STABLE_ID_TOKEN.finditer(text):
        token = match.group(0)
        if token not in package_stable_ids:
            raise RagAnswerError(
                RagAnswerErrorCode.CITATION_INVALID,
                f"引用校验失败：回答包含未知或非法的引用 {token}，拒绝显示。",
            )
        if token not in found:
            found.append(token)
    for match in _CITATION_NUMBER_TOKEN.finditer(text):
        number = int(match.group(1))
        if number not in citation_by_number:
            raise RagAnswerError(
                RagAnswerErrorCode.CITATION_INVALID,
                f"引用校验失败：回答引用了不存在的来源编号 #{number}，拒绝显示。",
            )
        stable_id = citation_by_number[number]
        if stable_id not in found:
            found.append(stable_id)
    if not found and _is_sanctioned_insufficiency_statement(text):
        # Rule 7 of the prompt mandates this exact honesty form when the
        # context cannot answer the question. An answer that says so — and
        # that carries no citation tokens at all (any invalid token already
        # raised above) — is a legitimate refusal, not a grounding failure
        # (v0.8.1 FAIL-013 mitigation).
        return ()
    if not found:
        raise RagAnswerError(
            RagAnswerErrorCode.CITATION_INVALID,
            "引用校验失败：AI 回答未包含任何合法引用，拒绝作为有依据回答显示。",
        )
    return tuple(found)


def _is_sanctioned_insufficiency_statement(text: str) -> bool:
    """True only for a short, whole-answer insufficiency refusal.

    The mandated phrase is a fail-closed escape hatch for a response that has
    no answerable content.  Treating the phrase as an anywhere-in-the-output
    substring lets a long, partially answered response bypass citation
    validation merely by using it in one subsection.  Keep the escape hatch
    deliberately narrow: it must appear near the start, remain concise, and
    must not contain a citation-looking numbered source in another syntax.
    """

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized or len(normalized) > 600:
        return False
    if re.search(r"来源\s*[\[\u3010#（(]*\s*\d", normalized):
        return False
    prefix = normalized[:160]
    return bool(re.search(r"知识上下文\s*[，,:\uff1a]?\s*信息不足", prefix))
