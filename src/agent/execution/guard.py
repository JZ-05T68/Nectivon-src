"""Deterministic ANSWER_DIRECTLY corpus guard (FAIL-020 + Morning M1/M2).

Night 1/1B evidence: bare short concept questions ("X 是什么？" "X 有什么缺点？")
whose topics live in LLM internal knowledge are systematically routed to
ANSWER_DIRECTLY, which the Final Answer stage must render as a canned refusal
— an honest omission even though the user's own corpus contains the concept.
Adding a document-name anchor flips routing 3/3, so the missing signal is
"does the user's corpus mention this concept at all", not the topic domain.

Morning cross-domain run (HBV2-MORNING-20260909) added two more faces of the
same missing signal:

- M2 TRIVIAL_COMPUTATION_ROUTE_TO_ANSWER_DIRECTLY: questions the model could
  compute itself (physics homework in the user's 错题本) were routed to
  ANSWER_DIRECTLY even though the corpus holds the original problem and its
  answer — the user-asset-first principle requires retrieval first;
- the same canned refusal followed questions that explicitly point at the
  user's own materials ("我有一本错题本，里面就有这道题").

This guard supplies the corpus signal deterministically after the model
decision:

- it only reconsiders ANSWER_DIRECTLY decisions (every CALL_TOOL passes
  through untouched, so no existing routing changes);
- it triggers on generic linguistic shapes only — no domain, topic or keyword
  lists: concept/definition shapes (Night 2), user-asset references
  ("我的资料/我上传的/之前保存的/这道题…"， Morning M2), and quantity or
  verification shapes ("多少/等于几/对吗…");
- it runs one lexical search for the quoted content terms; a hit means the
  corpus may contain relevant material, so the question goes to
  ``page_search`` and the retrieval result decides (honest "信息不足" stays
  possible when the hit is irrelevant). A hit driven only by low-information
  fragments (single characters, bare digits) does not reroute — the cleaned
  question must keep at least one substantive ranking term (≥2-char CJK or
  letter-bearing token);
- no hit (or any guard failure) keeps ANSWER_DIRECTLY: corpus-absent
  questions keep their honest boundary and meta/chit-chat questions are
  unaffected.

It never calls the model a second time and never fabricates content.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Final

from src.agent.execution.contracts import AgentDecision, AgentDecisionKind
from src.search_service import SearchService
from src.text_utils import partition_query_terms

LOGGER = logging.getLogger(__name__)

GUARD_SOURCE = "answer_directly_corpus_guard"

#: Generic concept-question patterns over the *quoted* question text. They
#: describe linguistic shape only; no topic or domain vocabulary appears here.
_CONCEPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(是什么|是啥|啥是|什么是|什么叫|什么是叫|的定义|定义是)"),
    re.compile(r"(有什么缺点|有什么优点|有什么问题|有什么局限|有哪些缺点|有哪些优点)"),
    re.compile(r"(怎么工作|怎么运作|工作原理|原理是什么|原理是|是怎么工作)"),
    re.compile(r"(有什么作用|有什么用途|用来做什么|用来干嘛)"),
    re.compile(r"(差在哪|区别是什么|有什么区别|有什么差异|差异在哪|有什么不同)"),
    re.compile(r"(快还是|好还是|适合还是)"),
    re.compile(r"^那[^，。？！?]{1,20}呢[？?]?$"),
    # generic "A 还是 B" selection shape (B2-T6: 是钢还是铸铁)
    re.compile(r"还是"),
)

#: Morning M2: the user points at their own materials/records. Pure
#: possession/reference shapes — no topic or domain vocabulary.
_ASSET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(我的资料|我的文档|我的笔记|我的记录|我的经验|我的错题本|错题本|"
        r"我上传的|我保存的|我导入的|我整理的|我记录的|"
        r"之前保存的|上次保存的|以前保存的|"
        r"上传的文档|上传的文件|导入的文档|导入的文件|"
        r"这个文件|这个文档|这份资料|这份文档|这个(?i:pdf)|"
        r"这道题|笔记里)"
    ),
)

#: Morning M2: quantity/verification shapes the model may believe it can
#: answer from internal knowledge (plain computation or "the teacher said X,
#: right?"). Grammar shapes only.
_COMPUTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(多少|等于几|算一算|算一下|算算|求出|计算出|"
        r"多远|多大|多久|多长|多高|多快|多重|"
        r"对吗|对不对|是不是|正确吗|说得对吗|说得对不对)"
    ),
)

#: M1-RESIDUAL (HBV2 Phase 2 RUN2, 2026-09-09): referential fragments that
#: carry concrete content — digits or letter-bearing tokens (「那个20米每秒的
#: 车」「20m每秒那个车」) — are not pure anaphora: the fragment itself can
#: anchor corpus content, so an ANSWER_DIRECTLY decision must be reconsidered
#: against the corpus. Pure arithmetic stays protected by the substantive
#: ranking-term check (bare digits never count as substantive), and truly
#: corpus-absent questions still keep their honest boundary because the
#: reroute additionally requires a literal corpus hit.
_DIGIT_OR_LETTER: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z]")

# interrogatives/fillers removed from the search terms (shape words, not
# domain words)
_STRIP_WORDS = (
    "是什么",
    "是啥",
    "啥是",
    "什么是",
    "什么叫",
    "的定义",
    "定义是",
    "有什么缺点",
    "有什么优点",
    "有什么问题",
    "有什么局限",
    "有哪些缺点",
    "有哪些优点",
    "怎么工作",
    "怎么运作",
    "工作原理",
    "原理是什么",
    "原理是",
    "是怎么工作",
    "有什么作用",
    "有什么用途",
    "用来做什么",
    "用来干嘛",
    "差在哪",
    "区别是什么",
    "有什么区别",
    "有什么差异",
    "差异在哪",
    "有什么不同",
    "请问",
    "一下",
    "到底",
    "究竟",
    "大概",
    "分别",
    # Morning M1/M2: interrogative and quantity shape words must not leak
    # into the rerouted page_search query either.
    "多少",
    "哪个",
    "哪些",
    "哪里",
    "什么",
    "怎么",
    "怎样",
    "为什么",
    "为何",
    "如何",
    "还是",
    "等于几",
    "对吗",
    "对不对",
    "是不是",
    "正确吗",
    "说得对吗",
    "说得对不对",
    "多远",
    "多大",
    "多久",
    "多长",
    "多高",
    "多快",
    "多重",
    "告诉",
    "记得",
    "老师",
    "啊",
    "呀",
    "呢",
    "吧",
    "吗",
)

_PUNCT_RE = re.compile(r"[\s，。？！?！、,.;；:：\"'“”‘’()（）\[\]【】<>《》…\-—_]+")


def looks_like_concept_query(text: str) -> bool:
    """True when ``text`` matches a generic definition/comparison pattern."""

    stripped = text.strip()
    return any(pattern.search(stripped) for pattern in _CONCEPT_PATTERNS)


def references_user_asset(text: str) -> bool:
    """True when ``text`` points at the user's own materials or records."""

    stripped = text.strip()
    return any(pattern.search(stripped) for pattern in _ASSET_PATTERNS)


def asks_quantity_or_verification(text: str) -> bool:
    """True when ``text`` asks for a quantity or verifies a stated value."""

    stripped = text.strip()
    return any(pattern.search(stripped) for pattern in _COMPUTATION_PATTERNS)


def carries_concrete_content_fragments(text: str) -> bool:
    """True when ``text`` embeds digit- or letter-bearing content fragments.

    M1-RESIDUAL shape (HBV2 Phase 2 RUN2): 「那个20米每秒的车」 carries no
    question word at all, yet its numbers/units can anchor the exact corpus
    page. Generic digits/letters only — no domain vocabulary.
    """

    return bool(_DIGIT_OR_LETTER.search(text.strip()))


def looks_like_guard_trigger(text: str) -> bool:
    """True for any generic shape the guard reconsiders against the corpus."""

    return (
        looks_like_concept_query(text)
        or references_user_asset(text)
        or asks_quantity_or_verification(text)
        or carries_concrete_content_fragments(text)
    )


def concept_search_terms(text: str) -> str:
    """Extract the quoted concept terms from a concept-style question."""

    cleaned = _PUNCT_RE.sub(" ", text)
    for word in _STRIP_WORDS:
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"^那\s*|\s*呢\b", " ", cleaned)
    terms = " ".join(part for part in cleaned.split() if part)
    return terms[:200]


def _has_substantive_ranking_terms(terms_text: str) -> bool:
    """True when the cleaned query keeps a content-bearing ranking term.

    A reroute must never be driven by single characters or bare digits; the
    cleaned question has to retain at least one ≥2-char CJK or letter-bearing
    term after demotion (HBV2-MORNING-20260909 M1/M2 discipline).
    """

    from src.text_utils import extract_search_terms

    ranking, _ = partition_query_terms(extract_search_terms(terms_text), terms_text)
    for term in ranking:
        if re.search(r"[A-Za-z]", term):
            return True
        if len(term) >= 2 and re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", term):
            return True
    return False


def answer_directly_corpus_guard(
    request_text: str,
    decide: Callable[[], AgentDecision],
    service: SearchService,
) -> AgentDecision:
    """Return the model decision, reconsidered against the user corpus.

    ``decide`` is called exactly once (the model decision is never repeated);
    the guard adds at most one local lexical search and only for ANSWER_DIRECTLY
    questions matching a guarded shape (concept / user-asset / quantity or
    verification). Any guard-internal failure fails open to the original
    decision with a logged warning.
    """

    decision = decide()
    if decision.kind is AgentDecisionKind.CALL_TOOL:
        return decision
    if decision.kind is not AgentDecisionKind.ANSWER_DIRECTLY:
        return decision
    if not looks_like_guard_trigger(request_text):
        return decision
    terms = concept_search_terms(request_text)
    if not terms or not _has_substantive_ranking_terms(terms):
        # Meta/chit-chat or pure-fragment questions: retrieval cannot help.
        return decision
    try:
        # The guard asks a *decision* question — does the corpus plausibly
        # mention this topic — not a recall question, so the zero-recall
        # widening retry must stay off: fragments of a corpus-absent topic
        # (是/多/里…) would otherwise fake a hit and flip an honest
        # ANSWER_DIRECTLY into a junk retrieval (M1R-A second residual).
        hits = service.search(terms, limit=1, widen_on_zero_recall=False)
    except Exception:
        LOGGER.warning(
            "ANSWER_DIRECTLY corpus 守卫检索失败，按原决策继续：terms=%s",
            terms,
            exc_info=True,
        )
        return decision
    if hits:
        LOGGER.info(
            "问法被判定无需工具但语料存在匹配，改路由 page_search：terms=%s hit=%s",
            terms,
            hits[0].document_title,
        )
        return AgentDecision(
            kind=AgentDecisionKind.CALL_TOOL,
            tool_name="page_search",
            arguments={"query": terms},
            finish_reason=decision.finish_reason,
            output_chars=decision.output_chars,
            output_tokens=decision.output_tokens,
        )
    return decision


__all__ = [
    "GUARD_SOURCE",
    "answer_directly_corpus_guard",
    "asks_quantity_or_verification",
    "carries_concrete_content_fragments",
    "concept_search_terms",
    "looks_like_concept_query",
    "looks_like_guard_trigger",
    "references_user_asset",
]
