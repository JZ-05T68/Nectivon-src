"""Reusable, safe text normalization, excerpt, and highlighting helpers."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from typing import Final

import jieba

_SEARCHABLE_GROUP: Final[re.Pattern[str]] = re.compile(
    r"[\w\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE
)
_ONLY_CJK: Final[re.Pattern[str]] = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_MARKDOWN_IMAGE: Final[re.Pattern[str]] = re.compile(r"!\[[^]]*]\([^)]*\)")
_MARKDOWN_LINK: Final[re.Pattern[str]] = re.compile(r"\[([^]]+)]\([^)]*\)")
_MARKDOWN_MARKER: Final[re.Pattern[str]] = re.compile(
    r"(?m)^(?:#{1,6}|[-*+] |>+)\s*"
)
_FTS_OPERATORS: Final[frozenset[str]] = frozenset({"and", "or", "not", "near"})

#: Interrogative stems whose fragments must never dominate FTS5 OR ranking
#: (HBV2-MORNING-20260909 M1). Pure linguistic shape words — no domain or
#: topic vocabulary. Multi-char stems may appear *inside* segmentation debris
#: (e.g. "兆帕下推力是多少"), so containment is used for them; single-char
#: stems only match exactly to avoid harming legitimate words (几乎, 哪里…).
_INTERROGATIVE_CONTAIN: Final[tuple[str, ...]] = (
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
)
_INTERROGATIVE_EXACT: Final[frozenset[str]] = frozenset(
    {
        "多远",
        "多大",
        "多久",
        "多长",
        "多高",
        "多快",
        "多重",
        "多宽",
        "多深",
        "几",
        "哪",
        "啥",
        "吗",
        "呢",
        "吧",
        "啊",
        "呀",
        "嘛",
    }
)
_PURE_DIGIT: Final[re.Pattern[str]] = re.compile(r"^\d+$")
#: Compact identifier runs: alphanumeric segments optionally joined by -/_.
#: A run that mixes letters and digits (VFD-600, P0-07, S7-1200) anchors its
#: digit fragments as high-information model/code tokens.
_IDENTIFIER_RUN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*"
)
_LETTER: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]")
_DIGIT: Final[re.Pattern[str]] = re.compile(r"\d")
#: Compact token mixing letters and digits with no separator (20m, STM32H750).
#: The lookaheads require at least one letter and one digit.
_MIXED_ALNUM_RUN: Final[re.Pattern[str]] = re.compile(
    r"(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]+"
)
_DIGIT_RUN: Final[re.Pattern[str]] = re.compile(r"\d+")
#: Explicit question-number references need punctuation-bearing anchors. A
#: bare digit is deliberately demoted by the general ranker, while source
#: papers normally write a question as ``16．`` / ``16.`` / ``16、``.
_QUESTION_NUMBER_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"(?:第\s*)?(\d{1,3})\s*(?:题|問|问)|\bq\s*(\d{1,3})\b",
    re.IGNORECASE,
)
# ``14（2）①`` / ``17(2)④`` are equally explicit exam references.  The
# leading number is the root question; the parenthesized/circled suffix is
# deliberately not turned into another root marker.
_QUESTION_HIERARCHY_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(\d{1,3})\s*[\(（]\s*[0-9一二三四五六七八九十]+\s*[\)）]"
)
#: Chinese numeral runs for the zero-recall widening retry (M1R-A second
#: residual): users say 二十米/五公斤/两米 while sources write 20 m/s / 5 千克 /
#: 2 米, so the only literal bridge is the converted Arabic digit.
_CN_NUMERAL_RUN: Final[re.Pattern[str]] = re.compile(r"[零一二两三四五六七八九十]+")
#: Single Chinese numeral characters mapped to their Arabic digit. Compound
#: runs are resolved below; 十 carries positional meaning and is handled there.
_CN_NUMERAL_DIGIT: Final[dict[str, int]] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def build_agent_page_text(
    *, extracted_text: str, ocr_text: str, manual_text: str = ""
) -> tuple[str, str]:
    """Combine immutable source text with an explicitly labelled correction.

    V086-309: the source text handed to the Agent is knowledge text —
    scanner branding (「夸克扫描王」 watermarks, QR promo lines) is filtered
    out before it can reach summaries/keywords, while the paper's own
    printed footers stay. The user's manual correction is user data and is
    never filtered.
    """

    from src.scan_artifact_filter import filter_knowledge_text

    if ocr_text.strip():
        original = filter_knowledge_text(ocr_text.strip()).filtered_text
        source_kind = "ocr_text"
    elif extracted_text.strip():
        original = filter_knowledge_text(extracted_text.strip()).filtered_text
        source_kind = "pdf_text"
    else:
        original = ""
        source_kind = "none"
    correction = manual_text.strip()
    if original and correction:
        return (
            f"【原始页面文字】\n{original}\n\n"
            f"【用户人工校对或补充】\n{correction}",
            f"{source_kind}+manual",
        )
    if correction:
        return f"【用户人工校对或补充】\n{correction}", "manual"
    return original, source_kind


def literal_match_spans(text: str, terms: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Return non-overlapping literal matches, preferring longer overlapping terms."""

    unique_terms = sorted(
        {term for term in terms if term},
        key=lambda value: (-len(value), value.casefold()),
    )
    if not text or not unique_terms:
        return ()
    pattern = re.compile(
        "|".join(re.escape(term) for term in unique_terms),
        flags=re.IGNORECASE,
    )
    return tuple((match.start(), match.end()) for match in pattern.finditer(text))


def build_context_excerpts(
    content: str,
    terms: Sequence[str],
    *,
    max_chars: int = 180,
    max_excerpts: int = 3,
) -> tuple[str, ...]:
    """Build distinct excerpts around literal matches without interpreting markup."""

    text = to_plain_text(content)
    if not text or max_chars < 1 or max_excerpts < 1:
        return ()
    spans = literal_match_spans(text, terms)
    if not spans:
        fallback = build_context_excerpt(text, terms, max_chars=max_chars)
        return (fallback,) if fallback else ()

    excerpts: list[str] = []
    normalized_excerpts: list[str] = []
    for match_start, _ in spans:
        start = max(0, match_start - max_chars // 3)
        end = min(len(text), start + max_chars)
        if end - start < max_chars:
            start = max(0, end - max_chars)
        excerpt = text[start:end].strip()
        if start > 0:
            excerpt = f"…{excerpt}"
        if end < len(text):
            excerpt = f"{excerpt}…"
        normalized = _WHITESPACE.sub(" ", excerpt).casefold()
        if any(
            normalized in existing or existing in normalized
            for existing in normalized_excerpts
        ):
            continue
        excerpts.append(excerpt)
        normalized_excerpts.append(normalized)
        if len(excerpts) >= max_excerpts:
            break
    return tuple(excerpts)


def extract_search_terms(
    query: str,
    *,
    max_terms: int = 16,
    max_term_chars: int = 128,
) -> tuple[str, ...]:
    """Return deduplicated literal terms from free-form Chinese or English input.

    Punctuation and FTS5 grammar characters never survive into the returned
    terms. A continuous Chinese group is retained before jieba-derived tokens so
    exact phrases remain discoverable without weakening existing token search.
    """

    return _extract_search_terms_impl(
        query,
        max_terms=max_terms,
        max_term_chars=max_term_chars,
        keep_single_cjk=False,
    )


def question_number_marker_fragments(query: str) -> tuple[str, ...]:
    """Return precise list markers for explicit question-number references.

    The function is deliberately limited to ``第16题`` / ``Q16``-shaped
    references. Years and ordinary numeric parameters therefore keep the
    existing low-information-number behaviour.
    """

    if not isinstance(query, str):
        return ()
    normalized = unicodedata.normalize("NFKC", query)
    fragments: list[str] = []
    for match in _QUESTION_NUMBER_REFERENCE.finditer(normalized):
        number = match.group(1) or match.group(2)
        if not number:
            continue
        for marker in (
            f"{number}．",
            f"{number}.",
            f"{number}、",
            f"{number}：",
            f"{number}:",
        ):
            if marker not in fragments:
                fragments.append(marker)
    for match in _QUESTION_HIERARCHY_REFERENCE.finditer(normalized):
        number = match.group(1)
        for marker in (
            f"{number}．",
            f"{number}.",
            f"{number}、",
            f"{number}：",
            f"{number}:",
        ):
            if marker not in fragments:
                fragments.append(marker)
    return tuple(fragments)


def extract_relaxed_search_terms(
    query: str,
    *,
    max_terms: int = 24,
    max_term_chars: int = 128,
) -> tuple[str, ...]:
    """Return extraction identical to :func:`extract_search_terms` but keeping
    single-character CJK terms.

    M1R-A second residual (HY4 independent retest, 2026-09-09): colloquial
    queries such as 「铜 铝 导电」 lose their only corpus hooks (铜/铝) to the
    de-noise filter that drops one-character CJK terms whenever a longer CJK
    term exists. The relaxed variant is used exclusively by the zero-recall
    widening retry in :class:`~src.search_service.SearchService` — it never
    changes the behaviour of a query that already recalls pages.
    """

    return _extract_search_terms_impl(
        query,
        max_terms=max_terms,
        max_term_chars=max_term_chars,
        keep_single_cjk=True,
    )


def _extract_search_terms_impl(
    query: str,
    *,
    max_terms: int,
    max_term_chars: int,
    keep_single_cjk: bool,
) -> tuple[str, ...]:
    """Shared extraction for the strict and relaxed term extractors."""

    if not isinstance(query, str) or max_terms < 1 or max_term_chars < 1:
        return ()
    normalized = unicodedata.normalize("NFKC", query).strip()
    if not normalized:
        return ()

    terms: list[str] = []
    seen: set[str] = set()

    def append(value: str) -> bool:
        term = value.casefold().strip("_")[:max_term_chars]
        if not term or term in _FTS_OPERATORS or term in seen:
            return False
        seen.add(term)
        terms.append(term)
        return len(terms) >= max_terms

    for group_match in _SEARCHABLE_GROUP.finditer(normalized):
        group = group_match.group(0)
        if _ONLY_CJK.fullmatch(group) and len(group) > 1 and append(group):
            break
        for jieba_token in jieba.cut_for_search(group):
            for token_match in _SEARCHABLE_GROUP.finditer(jieba_token):
                if append(token_match.group(0)):
                    break
            if len(terms) >= max_terms:
                break
        if len(terms) >= max_terms:
            break
    if not keep_single_cjk and any(
        _ONLY_CJK.fullmatch(term) and len(term) > 1 for term in terms
    ):
        # Multi-term OR queries should not be dominated by low-information
        # segmentation fragments such as “的” or “不”. A deliberate one-character
        # Chinese query remains supported because it has no longer CJK companion.
        terms = [
            term
            for term in terms
            if not (_ONLY_CJK.fullmatch(term) and len(term) == 1)
        ]
    return tuple(terms)


def chinese_numeral_digit_fragments(query: str) -> tuple[str, ...]:
    """Return Arabic digit fragments for Chinese numeral runs in ``query``.

    M1R-A second residual: 「车一秒钟跑二十米」 shares no literal token with the
    source 「20 m/s 匀速行驶」 — the numeral is the only bridge. Runs are read
    positionally (二十→20, 十五→15, 两→2); exotic formats (万/亿, decimals) are
    intentionally not interpreted and yield nothing rather than a wrong digit.
    Used only by the zero-recall widening retry.
    """

    if not isinstance(query, str):
        return ()
    normalized = unicodedata.normalize("NFKC", query)
    fragments: list[str] = []
    for run_match in _CN_NUMERAL_RUN.finditer(normalized):
        run = run_match.group(0)
        if "十" in run:
            head, _, tail = run.partition("十")
            tens = _CN_NUMERAL_DIGIT.get(head, 1) if head else 1
            if tens > 9:
                continue
            ones = _CN_NUMERAL_DIGIT.get(tail, 0) if tail else 0
            fragments.append(str(tens * 10 + ones))
            continue
        if len(run) == 1:
            if run in _CN_NUMERAL_DIGIT:
                fragments.append(str(_CN_NUMERAL_DIGIT[run]))
            continue
        # Multi-digit runs without a positional marker (e.g. 一二) are
        # ambiguous — emit each digit instead of guessing one number.
        for char in run:
            if char in _CN_NUMERAL_DIGIT:
                fragments.append(str(_CN_NUMERAL_DIGIT[char]))
    return tuple(dict.fromkeys(fragments))


def cjk_char_fragments(terms: Sequence[str]) -> tuple[str, ...]:
    """Return individual characters of multi-character CJK terms.

    Ranking connects a rewritten query to pages through field boosts and bm25;
    when the user's wording differs from the source (「五公斤」 vs 「5 千克」,
    「保险丝」 vs 「熔断器」), whole terms match nothing while their characters
    (米, 选, 闸…) still substring-match the right pages. Used only by the
    zero-recall widening retry, so it can never change a query that already
    recalls pages.
    """

    fragments: list[str] = []
    for term in terms:
        if len(term) > 1 and _ONLY_CJK.fullmatch(term):
            for char in term:
                if char not in fragments:
                    fragments.append(char)
    return tuple(fragments)


def extract_fts_search_terms(
    query: str,
    *,
    max_terms: int = 16,
    max_term_chars: int = 128,
) -> tuple[str, ...]:
    """Return deduplicated FTS5 tokens for knowledge-scope FTS recall.

    Unlike :func:`extract_search_terms`, a continuous CJK group is **never**
    kept whole: the knowledge shadow columns are jieba token sequences, so a
    whole-group literal would not exist in the FTS index. Every searchable
    group is therefore segmented with ``jieba.cut_for_search`` and each token
    is cleaned (lowercase, ``_FTS_OPERATORS`` removed, deduplicated, bounded)
    exactly like the page-search term path. Page-search semantics are not
    changed: ``extract_search_terms`` is untouched.
    """

    if not isinstance(query, str) or max_terms < 1 or max_term_chars < 1:
        return ()
    normalized = unicodedata.normalize("NFKC", query).strip()
    if not normalized:
        return ()

    terms: list[str] = []
    seen: set[str] = set()

    def append(value: str) -> bool:
        term = value.casefold().strip("_")[:max_term_chars]
        if not term or term in _FTS_OPERATORS or term in seen:
            return False
        seen.add(term)
        terms.append(term)
        return len(terms) >= max_terms

    for group_match in _SEARCHABLE_GROUP.finditer(normalized):
        group = group_match.group(0)
        for jieba_token in jieba.cut_for_search(group):
            for token_match in _SEARCHABLE_GROUP.finditer(jieba_token):
                if append(token_match.group(0)):
                    break
            if len(terms) >= max_terms:
                break
        if len(terms) >= max_terms:
            break
    return tuple(terms)


def _anchored_digit_fragments(query: str) -> frozenset[str]:
    """Return digit fragments that belong to letter-mixed identifier runs.

    ``VFD-600``, ``P0-07``, ``S7-1200`` and ``STM32H750`` carry digits that
    identify a model or parameter code; the same digits standing alone in a
    sentence (缸径 50 毫米) are low-information. A digit fragment is anchored
    only when it sits inside a run that also contains a letter — either
    directly adjacent (``P0``, ``H750``) or joined through ``-``/``_``
    (``VFD-600``). Decimal points never anchor by themselves: ``0.63 兆帕``
    stays low-information while ``0.63MPa`` keeps only the directly adjacent
    fragment anchored.
    """

    anchored: set[str] = set()
    for run_match in _IDENTIFIER_RUN.finditer(query):
        run = run_match.group(0)
        if not (_LETTER.search(run) and _DIGIT.search(run)):
            continue
        for segment in re.split(r"[-_]", run):
            has_letter = bool(_LETTER.search(segment))
            has_digit = bool(_DIGIT.search(segment))
            if not has_digit:
                continue
            if has_letter:
                # letter-digit mix inside one segment (P0, H750, 1200V)
                anchored.update(re.findall(r"\d+", segment))
            elif _LETTER.search(run) and re.search(r"[-_]", run):
                # digit segment joined to a letter segment by - or _ (VFD-600)
                anchored.update(re.findall(r"\d+", segment))
    return frozenset(anchored)


def is_low_information_term(term: str, anchored_digits: frozenset[str]) -> bool:
    """True when ``term`` must not dominate FTS5 OR ranking (M1 family).

    Two generic shape rules only — no domain, topic or keyword vocabulary:

    1. interrogative/question-shape fragments (多少/哪个/还是/…) — they appear
       in nearly every natural question and must never be a main recall
       factor;
    2. isolated pure digits — number-dense pages (price lists, parameter
       tables, maintenance plans) match them wholesale and flood the ranking.
       Digits inside letter-mixed identifiers (VFD-600 / P0-07) are exempt.
    """

    if not term:
        return False
    if any(stem in term for stem in _INTERROGATIVE_CONTAIN):
        return True
    if term in _INTERROGATIVE_EXACT:
        return True
    if _PURE_DIGIT.fullmatch(term):
        return term not in anchored_digits
    return False


def partition_query_terms(
    terms: Sequence[str], query: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split extracted query terms into (ranking terms, demoted terms).

    Ranking terms keep full weight in FTS5 OR ranking; demoted terms stay in
    literal recall and highlighting but cannot crowd the ranking. When every
    term is demoted the caller must fall back to the original terms so a
    numbers-only query still works. Generic linguistic shapes only.
    """

    term_list = [term for term in terms if term]
    if not term_list:
        return (), ()
    anchored = _anchored_digit_fragments(
        unicodedata.normalize("NFKC", query) if isinstance(query, str) else ""
    )
    ranking = [term for term in term_list if not is_low_information_term(term, anchored)]
    if not ranking:
        return tuple(term_list), ()
    return tuple(ranking), tuple(term for term in term_list if term not in ranking)


def identifier_digit_fragments(terms: Sequence[str]) -> tuple[str, ...]:
    """Return the digit fragments hidden inside compact letter+digit tokens.

    A glued token such as ``20m`` or ``STM32H750`` survives term extraction
    whole and then fails *literal* recall when no page contains that exact
    run (「20 m/s」 is written with a space). The token's digit fragments
    (``20``; ``32``/``750``) are the recallable pieces. Only the digit parts
    are returned — single-letter fragments would match nearly every page and
    carry no information. Callers use this exclusively as a widening retry
    for zero-recall queries, so adding fragments can never change a query
    that already recalls pages.
    """

    fragments: list[str] = []
    for term in terms:
        if not term or not _MIXED_ALNUM_RUN.fullmatch(term):
            continue
        for digits in _DIGIT_RUN.findall(term):
            if digits not in fragments:
                fragments.append(digits)
    return tuple(fragments)


def to_plain_text(value: str) -> str:
    """Remove common Markdown presentation markers without interpreting HTML."""

    text = value or ""
    text = _MARKDOWN_IMAGE.sub(" ", text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _MARKDOWN_MARKER.sub("", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return _WHITESPACE.sub(" ", text).strip()


# --------------------------------------------------------------- plain UI math
#: LaTeX command → human-readable plain text (R5.1 plain-UI contract).
_LATEX_PLAIN_SYMBOLS: Final[dict[str, str]] = {
    "to": "→",
    "rightarrow": "→",
    "Rightarrow": "⇒",
    "leftarrow": "←",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "infty": "∞",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "cup": "∪",
    "cap": "∩",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "Δ",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "because": "∵",
    "therefore": "∴",
    "angle": "∠",
    "triangle": "△",
    "perp": "⊥",
    "parallel": "∥",
    "degree": "°",
    "circ": "∘",
    "partial": "∂",
    "nabla": "∇",
    "forall": "∀",
    "exists": "∃",
    "ldots": "…",
    "cdots": "…",
    "dots": "…",
    "prime": "′",
    "log": "log",
    "ln": "ln",
    "lg": "lg",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "cot": "cot",
    "sec": "sec",
    "csc": "csc",
    "arcsin": "arcsin",
    "arccos": "arccos",
    "arctan": "arctan",
    "max": "max",
    "min": "min",
    "hbar": "ℏ",
    "ell": "ℓ",
    "Re": "Re",
    "Im": "Im",
    "quad": " ",
    "qquad": "  ",
    "limits": "",
    "displaystyle": "",
    "left": "",
    "right": "",
    "big": "",
    "Big": "",
    "bigl": "",
    "bigr": "",
    "operatorname": "",
    "mathrm": "",
    "mathbf": "",
    "text": "",
    "boldsymbol": "",
}

_SUPERSCRIPT_SAFE: Final[str] = "0123456789+-=()ni"
_SUPERSCRIPT_TABLE: Final[str] = str.maketrans(
    "0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"
)
_SUBSCRIPT_SAFE: Final[str] = "0123456789+-=()nijkm"
_SUBSCRIPT_TABLE: Final[str] = str.maketrans(
    "0123456789+-=()nijkm", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₙᵢⱼₖₘ"
)
_DOUBLE_STRUCK: Final[dict[str, str]] = {
    "R": "ℝ",
    "N": "ℕ",
    "Z": "ℤ",
    "Q": "ℚ",
    "C": "ℂ",
    "F": "𝔽",
}

_LATEX_COMMAND: Final[re.Pattern[str]] = re.compile(r"\\([a-zA-Z]+)")

#: A plain ASCII apostrophe right after a token is a derivative prime
#: (``f'(1)`` → ``f′(1)``); quotes after spaces or punctuation stay.
_PRIME_AFTER_TOKEN: Final[re.Pattern[str]] = re.compile(r"(?<=[A-Za-z0-9\)\]])'")


class _MalformedMath(Exception):
    """Raised inside one math segment; the caller degrades that segment only."""


def _read_braced_group(text: str, index: int) -> tuple[str, int]:
    """Read one ``{...}`` group at ``index``; return (content, next_index)."""

    if index >= len(text) or text[index] != "{":
        raise _MalformedMath()
    depth = 0
    cursor = index
    length = len(text)
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[index + 1 : cursor], cursor + 1
        cursor += 1
    raise _MalformedMath()


def _read_script_argument(text: str, index: int) -> tuple[str, int]:
    """Read the argument of ``^`` / ``_``: a braced group, command, or char."""

    if index >= len(text):
        raise _MalformedMath()
    if text[index] == "{":
        return _read_braced_group(text, index)
    match = _LATEX_COMMAND.match(text, index)
    if match:
        return match.group(0), match.end()
    return text[index], index + 1


def _apply_script(arg: str, *, superscript: bool) -> str:
    """Convert one ^/_ argument to Unicode script text or a bracket fallback."""

    if arg == "\\circ":
        return "°"
    converted = _latex_to_plain(arg)
    table = _SUPERSCRIPT_TABLE if superscript else _SUBSCRIPT_TABLE
    safe = _SUPERSCRIPT_SAFE if superscript else _SUBSCRIPT_SAFE
    if converted and all(char in safe for char in converted):
        return converted.translate(table)
    bracket = "^" if superscript else "_"
    return f"{bracket}({converted})"


def _format_fraction(numerator: str, denominator: str) -> str:
    """``\\frac{A}{B}`` → ``A/B`` with brackets only when meaning requires."""

    def wrap(part: str) -> str:
        stripped = part.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            return stripped
        if any(op in stripped for op in ("+", "-", "±", "∓")):
            return f"[{stripped}]"
        return stripped

    return f"{wrap(numerator)}/{wrap(denominator)}"


def _read_bounds(text: str, index: int) -> tuple[str, str, int]:
    """Read optional ``_{a}`` / ``^{b}`` bounds in either order."""

    lower = ""
    upper = ""
    cursor = index
    for _ in range(2):
        if cursor < len(text) and text[cursor] in "_^":
            is_lower = text[cursor] == "_"
            arg, cursor = _read_script_argument(text, cursor + 1)
            if is_lower:
                lower = arg
            else:
                upper = arg
        else:
            break
    return lower, upper, cursor


def _apply_big_operator(
    name: str, text: str, index: int
) -> tuple[str, int]:
    """Convert \\int / \\sum / \\prod with their optional bounds."""

    lower, upper, cursor = _read_bounds(text, index)
    symbol = {"int": "∫", "oint": "∮", "sum": "Σ", "prod": "∏"}[name]
    if name == "int":
        lower_plain = _latex_to_plain(lower) if lower else ""
        upper_plain = _latex_to_plain(upper) if upper else ""
        if lower_plain and upper_plain and all(
            char in _SUBSCRIPT_SAFE for char in lower_plain
        ) and all(char in _SUPERSCRIPT_SAFE for char in upper_plain):
            return symbol + lower_plain.translate(_SUBSCRIPT_TABLE) + upper_plain.translate(
                _SUPERSCRIPT_TABLE
            ), cursor
        if lower_plain or upper_plain:
            return f"{symbol}({lower_plain}→{upper_plain})", cursor
        return symbol, cursor
    if lower:
        lower_plain = _latex_to_plain(lower)
        upper_plain = _latex_to_plain(upper) if upper else ""
        return f"{symbol}({lower_plain}→{upper_plain})", cursor
    return symbol, cursor


def _read_environment(
    text: str, index: int, environment: str
) -> tuple[str, int]:
    """Read the body of ``\\begin{env}...\\end{env}`` starting after the name."""

    end_token = f"\\end{{{environment}}}"
    end_position = text.find(end_token, index)
    if end_position < 0:
        raise _MalformedMath()
    body = text[index:end_position]
    # Alignment rows commonly write ``x^2,&x\ge0`` — the trailing comma
    # before ``&`` would double up with the comma we render for ``&``.
    body = body.replace(",&", "&").replace("，&", "&")
    rows = [row for row in body.split("\\\\") if row.strip()]
    joined = "；".join(_latex_to_plain(row) for row in rows)
    label = "分段：" if environment == "cases" else ""
    return f"{label}{joined}", end_position + len(end_token)


def _latex_to_plain(src: str) -> str:
    """Convert one LaTeX source string to human-readable plain text.

    Deterministic, best-effort, never fabricates meaning: structured
    commands (frac/lim/sqrt/int/sum/cases/...) get faithful plain forms,
    unknown commands keep their name without the backslash, and a
    structurally broken formula raises :class:`_MalformedMath` so the
    caller can degrade that segment honestly (R5.1 §19/§20).
    """

    out: list[str] = []
    index = 0
    length = len(src)
    while index < length:
        char = src[index]
        if char == "\\":
            match = _LATEX_COMMAND.match(src, index)
            if match is None:
                nxt = src[index + 1] if index + 1 < length else ""
                if nxt == "$":
                    out.append("＄")
                elif nxt == "\\":
                    out.append("；")
                elif nxt in ",;:! ":
                    out.append(" ")
                elif nxt:
                    out.append(nxt)
                index += 2
                continue
            name = match.group(1)
            after = match.end()
            if name in ("frac", "dfrac", "tfrac"):
                numerator, cursor = _read_braced_group(src, after)
                denominator, cursor = _read_braced_group(src, cursor)
                out.append(
                    _format_fraction(
                        _latex_to_plain(numerator), _latex_to_plain(denominator)
                    )
                )
                index = cursor
                continue
            if name == "sqrt":
                argument, cursor = _read_braced_group(src, after)
                plain = _latex_to_plain(argument)
                out.append(f"√({plain})" if len(plain) > 1 else f"√{plain}")
                index = cursor
                continue
            if name in ("lim", "max", "min"):
                if after < length and src[after] == "_":
                    bound, cursor = _read_script_argument(src, after + 1)
                    out.append(f"{name}({_latex_to_plain(bound)})")
                    index = cursor
                else:
                    out.append(name)
                    index = after
                continue
            if name in ("int", "oint", "sum", "prod"):
                piece, index = _apply_big_operator(name, src, after)
                out.append(piece)
                continue
            if name == "begin":
                environment, cursor = _read_braced_group(src, after)
                piece, index = _read_environment(src, cursor, environment)
                out.append(piece)
                continue
            if name in ("end",):
                raise _MalformedMath()
            if name in ("mathbb", "mathds"):
                argument, index = _read_braced_group(src, after)
                inner = _latex_to_plain(argument)
                out.append(_DOUBLE_STRUCK.get(inner, inner))
                continue
            if name in (
                "mathcal",
                "mathsf",
                "mathit",
                "mathfrak",
                "hat",
                "bar",
                "vec",
                "tilde",
                "widehat",
                "overline",
                "underline",
            ):
                argument, index = _read_braced_group(src, after)
                out.append(_latex_to_plain(argument))
                continue
            if name in _LATEX_PLAIN_SYMBOLS:
                out.append(_LATEX_PLAIN_SYMBOLS[name])
                index = after
                continue
            out.append(name)
            index = after
            continue
        if char == "^":
            argument, index = _read_script_argument(src, index + 1)
            out.append(_apply_script(argument, superscript=True))
            continue
        if char == "_":
            argument, index = _read_script_argument(src, index + 1)
            out.append(_apply_script(argument, superscript=False))
            continue
        if char == "{":
            content, index = _read_braced_group(src, index)
            out.append(_latex_to_plain(content))
            continue
        if char == "}":
            raise _MalformedMath()
        if char == "&":
            out.append("，")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


_MATH_SEGMENT: Final[re.Pattern[str]] = re.compile(r"\$\$[^$]*\$\$|\$[^$]*\$|\$")


def _convert_math_segments(text: str) -> str:
    """Convert every ``$...$`` / ``$$...$$`` segment; degrade broken ones."""

    out: list[str] = []
    cursor = 0
    for match in _MATH_SEGMENT.finditer(text):
        out.append(text[cursor : match.start()])
        segment = match.group(0)
        if segment == "$":
            # Unclosed delimiter: treat the remainder as math, best effort.
            out.append(_degrade_math(text[match.start() + 1 :]))
            cursor = len(text)
            break
        inner = segment[2:-2] if segment.startswith("$$") else segment[1:-1]
        out.append(_degrade_math(inner))
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def _degrade_math(inner: str) -> str:
    """Convert one formula body; broken input becomes an honest placeholder."""

    try:
        plain = _latex_to_plain(inner)
    except _MalformedMath:
        return "公式(可能有误) "
    plain = _PRIME_AFTER_TOKEN.sub("′", plain)
    return plain if plain.strip() else "公式"


def ui_plaintext_digest(text: str, limit: int | None = None) -> str:
    """Markdown + LaTeX source → human-readable plain text for plain UI.

    Nectivon content-rendering contract (R5.1, 2026-09-27): rich READ
    surfaces render Markdown + LaTeX via KaTeX, EDIT surfaces keep the raw
    source, and every *plain* short-string UI (selectors, card titles,
    history titles, candidate previews) must show this digest instead of
    source.  The pipeline is convert-then-truncate: Markdown markers are
    stripped, each math segment becomes faithful plain math (``$x^2$`` →
    ``x²``, ``\\frac{a}{b}`` → ``a/b``, ``\\lim_{h\\to0}`` → ``lim(h→0)``),
    and only then is the result truncated to ``limit`` with an ellipsis.
    Malformed LaTeX degrades to ``公式(可能有误)`` — never an exception,
    never fabricated mathematics.
    """

    value = str(text or "")
    if not value.strip():
        return ""
    # An escaped \\$ is a literal price sign, not a LaTeX delimiter: swap it
    # to the full-width form first so segment pairing can never mistake it.
    value = value.replace("\\$", "＄")
    plain = _convert_math_segments(to_plain_text(value))
    plain = _WHITESPACE.sub(" ", plain).strip()
    plain = plain.replace("（ ", "（").replace(" ）", "）")
    if limit is not None and limit > 0 and len(plain) > limit:
        return plain[:limit].rstrip() + "…"
    return plain


def contains_raw_markup(text: str) -> bool:
    """True when student-visible plain text still carries Markdown/LaTeX source.

    Used by tests as the plain-UI lint. A single literal ``＄`` (the
    full-width price sign produced from escaped ``\\$``) and a lone ASCII
    ``$`` directly followed by a digit (prices such as ``$5``) are not
    markup; pairs of ``$`` and backslash commands are.
    """

    value = str(text or "")
    if _LATEX_COMMAND.search(value):
        return True
    if "$$" in value:
        return True
    dollar_positions = [
        position
        for position, char in enumerate(value)
        if char == "$" and (position == 0 or value[position - 1] != "\\")
    ]
    if len(dollar_positions) >= 2:
        return True
    if "**" in value or re.search(r"(?m)^#{1,6}\s", value):
        return True
    return False


def math_safe_prefix(text: str, limit: int) -> str:
    """Return at most ``limit`` characters without cutting inside math.

    Closure R5 (2026-09-27) surface rule: a truncated stem/summary must
    never end with a dangling ``$``, half a ``$$`` pair, or a broken
    backslash command (``\\frac`` cut to ``\\fr``).  The scan keeps
    ``$...$`` / ``$$...$$`` segments intact (escaped ``\\$`` respected);
    when the natural cut lands inside a formula the cut retreats to the
    start of that formula, so a selector shows plain text plus a whole
    formula or none of it — never half of one.  An unclosed ``$`` is
    treated as a plain character (malformed input degrades, never hangs).
    """

    value = str(text or "")
    if limit < 1:
        return ""
    if len(value) <= limit:
        return value

    cut = limit
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\":
            # Keep escape pairs and command heads atomic; if the natural cut
            # splits one, move the cut back before the backslash.
            if index < limit <= index + 1:
                cut = index
                break
            index += 2
            continue
        if char == "$":
            delimiter = 2 if value.startswith("$$", index) else 1
            cursor = index + delimiter
            closed = False
            while cursor < length:
                if value[cursor] == "\\":
                    cursor += 2
                    continue
                if value[cursor] == "$":
                    if delimiter == 2:
                        if value.startswith("$$", cursor):
                            cursor += 2
                            closed = True
                            break
                        cursor += 1
                        continue
                    cursor += 1
                    closed = True
                    break
                cursor += 1
            if closed:
                # A formula spanning the cut forces the cut before it.
                if index < limit < cursor:
                    cut = index
                    break
                index = cursor
                continue
            index += 1
            continue
        index += 1

    prefix = value[:cut]
    # A trailing lone backslash or an unpaired opening ``$`` reads broken.
    while prefix and prefix[-1] == "\\":
        prefix = prefix[:-1]
    dollar_positions: list[int] = []
    scan = 0
    while scan < len(prefix):
        if prefix[scan] == "\\":
            scan += 2
            continue
        if prefix[scan] == "$":
            dollar_positions.append(scan)
        scan += 1
    if len(dollar_positions) % 2 == 1:
        # The kept prefix holds an unmatched (unescaped) ``$``; trim back
        # before it instead of showing a dangling delimiter.
        prefix = prefix[: dollar_positions[-1]]
    return prefix.rstrip()


def short_math_digest(text: str, limit: int) -> str:
    """``math_safe_prefix`` plus an ellipsis only when content was dropped."""

    value = str(text or "")
    if len(value) <= limit:
        return value
    prefix = math_safe_prefix(value, limit)
    if not prefix:
        prefix = value[:limit]
    return prefix + "…"


def build_context_excerpt(
    content: str,
    terms: Sequence[str],
    *,
    max_chars: int = 180,
) -> str:
    """Build a stable excerpt centred on the earliest literal term match."""

    text = to_plain_text(content)
    if not text or max_chars < 1:
        return ""
    if len(text) <= max_chars:
        return text

    positions: list[int] = []
    folded_text = text.casefold()
    for term in terms:
        if term:
            position = folded_text.find(term.casefold())
            if position >= 0:
                positions.append(position)
    match_position = min(positions, default=0)

    start = max(0, match_position - max_chars // 3)
    end = min(len(text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = f"…{excerpt}"
    if end < len(text):
        excerpt = f"{excerpt}…"
    return excerpt


def highlight_html(text: str, terms: Sequence[str]) -> str:
    """Escape all source text and wrap literal matches in safe ``mark`` tags.

    Removing the generated ``mark`` tags and HTML-unescaping the result always
    reconstructs ``text`` exactly. No page content is inserted as executable
    HTML.
    """

    unique_terms = sorted(
        {term for term in terms if term},
        key=lambda value: (-len(value), value.casefold()),
    )
    if not unique_terms:
        return html.escape(text, quote=True)
    pattern = re.compile(
        "|".join(re.escape(term) for term in unique_terms),
        flags=re.IGNORECASE,
    )
    parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        parts.append(html.escape(text[cursor : match.start()], quote=True))
        parts.append("<mark>")
        parts.append(html.escape(match.group(0), quote=True))
        parts.append("</mark>")
        cursor = match.end()
    parts.append(html.escape(text[cursor:], quote=True))
    return "".join(parts)


__all__ = [
    "build_agent_page_text",
    "build_context_excerpt",
    "build_context_excerpts",
    "contains_raw_markup",
    "extract_fts_search_terms",
    "extract_search_terms",
    "highlight_html",
    "identifier_digit_fragments",
    "is_low_information_term",
    "literal_match_spans",
    "math_safe_prefix",
    "partition_query_terms",
    "question_number_marker_fragments",
    "short_math_digest",
    "to_plain_text",
    "ui_plaintext_digest",
]
