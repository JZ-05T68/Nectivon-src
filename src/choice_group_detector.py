"""Choice question-group detector — F3 (P0 fix, GEOGRAPHY ROUND 2).

Round 2 found the real defect: a printed choice question-group
("1～2题共用材料") was split into N candidates that each carried the SAME
full shared material text — duplicate assets instead of one shared group.

This module provides a DETERMINISTIC (non-AI, non-hardcoded) detector that
runs over AI-split candidates plus the raw page text and, when the signals
agree, proposes a choice question GROUP:

    one shared material → one group → several independent sub-questions

Design constraints (F3 spec §0.1/§4/§5):

* No single fixed regex: the detector combines (a) cross-candidate common
  text blocks (difflib), (b) question-number continuity, (c) group-hint
  phrases ("完成下面小题" / "完成1～2题" / "据此完成…4～6题" variants),
  (d) option-block presence, (e) per-question remaining stem length.
* Under-confident input must NOT be forced into a group (宁可 unknown /
  待确认): every returned draft carries its signals and a confidence, and
  the UI keeps the user in charge of confirming or splitting.
* No content is deleted: shared text is *identified*, the AI's original
  candidate stems stay in the store; grouping only changes how the review
  page presents them and how they join the knowledge base.
* Generic: works on any corpus, no doc172/P7 special-casing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Group-hint phrases seen in real exam papers.  Multiple shapes, kept
# deliberately broad — they only RAISE confidence, never create a group
# alone (a single hint with one question is not a group).
_GROUP_HINT_RE = re.compile(
    r"(?:完成|回答|做)\s*(?:下面|下列|以下)?\s*(?:的)?\s*"
    r"(?:第)?\s*\d+\s*[～~\-—－至到]\s*\d+\s*(?:小)?题"
    r"|(?:完成|回答)\s*(?:下面|下列|以下)?\s*(?:的)?\s*小题"
    r"|据此\s*(?:完成|回答)"
    r"|(?:完成|回答)\s*(?:下面|下列|以下)?\s*(?:的)?\s*\d+\s*[～~\-—－至到]\s*\d+\s*题"
)

# F6: the same hint shapes but CAPTURING the printed number range, so the
# detector can tell whether a group's sub-questions span page boundaries.
_HINT_RANGE_RE = re.compile(
    r"(?:完成|回答|做)\s*(?:下面|下列|以下)?\s*(?:的)?\s*(?:第)?\s*"
    r"(\d{1,3})\s*[～~\-—－至到]\s*(\d{1,3})\s*(?:小)?题"
    r"|(?:完成|回答)\s*(?:下面|下列|以下)?\s*(?:的)?\s*(?:第)?\s*"
    r"(\d{1,3})\s*[～~\-—－至到]\s*(\d{1,3})\s*题"
)

_OPTION_BLOCK_RE = re.compile(r"[ABCD][.．、]")

_FIRST_OPTION_RE = re.compile(r"[ABCD][.．、]")


def _material_part(stem: str) -> str:
    """The pre-option part of a stem (shared material + question preamble).

    Choice options ("A. … B. …") repeat across EVERY question on a page and
    must never count as shared material — this exact shape caused the
    Round-2 false grouping.  Everything from the first option marker on is
    excluded from similarity computations.
    """

    match = _FIRST_OPTION_RE.search(stem or "")
    return stem[: match.start()] if match else (stem or "")

# A shared block must be at least this long (chars) to count as material.
_MIN_SHARED_CHARS = 40
# ...and must cover at least this fraction of the SHORTEST member stem.
_MIN_SHARED_RATIO = 0.35
# Two candidates with a common block of at least this ratio are grouped.
_PAIR_LINK_RATIO = 0.55


@dataclass(slots=True)
class ChoiceGroupDraft:
    """One detected choice question-group (a PROPOSAL, never auto-applied)."""

    member_numbers: list[str]
    shared_text: str
    unique_stems: dict[str, str] = field(default_factory=dict)
    display_unique_stems: dict[str, str] = field(default_factory=dict)
    confidence: str = "uncertain"  # probable | uncertain
    signals: list[str] = field(default_factory=list)

    @property
    def group_number(self) -> str:
        """Verbatim-style group label: first~last member number."""

        return f"{self.member_numbers[0]}~{self.member_numbers[-1]}题组"


def _number_key(number: str) -> int | None:
    """Parse '12' / '12.' / '12、' style numbers; None when not numeric."""

    match = re.match(r"^\s*(\d{1,3})", number or "")
    return int(match.group(1)) if match else None


def _common_blocks(a: str, b: str, min_size: int) -> list[str]:
    """Long common blocks between two stems (>= min_size chars)."""

    matcher = SequenceMatcher(None, a, b, autojunk=False)
    blocks = []
    for block in matcher.get_matching_blocks():
        size = block.size
        if size >= min_size:
            blocks.append(a[block.a : block.a + size])
    # merge adjacent/overlapping blocks produced by matcher splits
    merged: list[str] = []
    for block in blocks:
        if merged and block in merged[-1]:
            continue
        merged.append(block)
    return merged


def _link_ratio(a: str, b: str) -> float:
    """Ratio of the longer MATERIAL part shared with the other stem.

    Options are excluded via :func:`_material_part` — option blocks repeat
    across all choice questions and must not link unrelated questions.
    """

    a_material = _material_part(a)
    b_material = _material_part(b)
    if not a_material or not b_material:
        return 0.0
    matcher = SequenceMatcher(None, a_material, b_material, autojunk=False)
    shared = sum(block.size for block in matcher.get_matching_blocks())
    return shared / max(len(a_material), len(b_material))


def _extract_unique_stem(stem: str, shared_text: str) -> str:
    """Remove the shared material occurrence from one stem, honestly.

    STRICTLY LITERAL: the shared text is removed ONLY when it occurs as
    an exact contiguous substring.  Any other case (OCR reordering, noise,
    partial drift) keeps the ORIGINAL stem untouched — the database never
    stores a fabricated cut.  A display-only fuzzy variant lives in
    :func:`display_unique_stem` for the review page.
    """

    candidate = stem
    if shared_text and shared_text in candidate:
        candidate = candidate.replace(shared_text, " ", 1)
    return re.sub(r"\s{2,}", " ", candidate).strip()


def display_unique_stem(stem: str, shared_text: str) -> str:
    """DISPLAY-ONLY fuzzy residual for the review page (never stored).

    Removes every common block (>= 12 chars) between the stem and the
    shared text; what remains is shown to the user as "this sub-question's
    own content" next to the shared material.  The stored stem keeps the
    verbatim source (see :func:`_extract_unique_stem`).
    """

    if not shared_text or not stem:
        return (stem or "").strip()
    matcher = SequenceMatcher(None, stem, shared_text, autojunk=False)
    residual = stem
    for block in matcher.get_matching_blocks():
        if block.size < 12:
            continue
        segment = residual[block.a : block.a + block.size]
        if segment in residual:
            residual = residual.replace(segment, " ", 1)
    return re.sub(r"\s{2,}", " ", residual).strip()


def detect_choice_groups(
    candidates: list,
    page_text: str = "",
) -> list[ChoiceGroupDraft]:
    """Propose choice question-groups from AI-split candidates.

    ``candidates`` are objects with ``number`` / ``stem`` (the AI candidate
    dataclass).  Only PENDING-style members should be passed by the caller
    (already-ignored ones are not this module's business).
    """

    members = [c for c in candidates if (c.stem or "").strip()]
    if len(members) < 2:
        return []
    members = sorted(
        members,
        key=lambda c: (_number_key(c.number) is None, _number_key(c.number) or 0),
    )
    numbers = [_number_key(c.number) for c in members]
    all_numeric = all(n is not None for n in numbers)

    # ---- greedy segmentation: adjacent candidates with a strong common
    # block join the same group; a break starts a new segment (Case D).
    segments: list[list[int]] = []
    current = [0]
    for i in range(1, len(members)):
        link = _link_ratio(members[i - 1].stem, members[i].stem)
        if all_numeric and numbers[i] is not None and numbers[i - 1] is not None:
            consecutive = numbers[i] - numbers[i - 1] == 1
        else:
            consecutive = False
        if link >= _PAIR_LINK_RATIO or (link >= 0.35 and consecutive):
            current.append(i)
        else:
            segments.append(current)
            current = [i]
    segments.append(current)

    hint_hit = bool(_GROUP_HINT_RE.search(page_text or ""))
    drafts: list[ChoiceGroupDraft] = []
    for segment in segments:
        if len(segment) < 2:
            continue
        seg_members = [members[i] for i in segment]
        # Similarity runs on MATERIAL parts only — option blocks repeat on
        # every choice question and must never form the shared text.
        stems = [_material_part(m.stem) for m in seg_members]
        shortest = min(stems, key=len)
        if any(len(s.strip()) < _MIN_SHARED_CHARS for s in stems):
            # a member has (almost) no material part → not a shared-material
            # group; grouping it would fabricate a link.
            continue
        blocks = _common_blocks(stems[0], stems[1], _MIN_SHARED_CHARS)
        for other in stems[2:]:
            refined = [b for b in blocks if b in other]
            if refined:
                blocks = refined
        shared = max(blocks, key=len) if blocks else ""
        if not shared:
            # try the union of smaller pairwise blocks against every member
            fallback = _common_blocks(stems[0], shortest, _MIN_SHARED_CHARS)
            shared = max(fallback, key=len) if fallback else ""
        if not shared or len(shared) < _MIN_SHARED_CHARS:
            continue
        if len(shared) < _MIN_SHARED_RATIO * len(shortest):
            continue
        seg_numbers = [m.number for m in seg_members]
        seg_keys = [_number_key(n) for n in seg_numbers]
        consecutive = (
            all(k is not None for k in seg_keys)
            and all(seg_keys[i + 1] - seg_keys[i] == 1 for i in range(len(seg_keys) - 1))
        )
        option_ok = all(_OPTION_BLOCK_RE.search(m.stem) for m in seg_members)
        signals: list[str] = []
        if consecutive:
            signals.append("题号连续")
        if hint_hit:
            signals.append("页面含题组提示语")
        if option_ok:
            signals.append("各小题含选项块")
        signals.append(f"共享材料 {len(shared)} 字")
        confidence = "probable" if (consecutive or hint_hit) else "uncertain"
        drafts.append(
            ChoiceGroupDraft(
                member_numbers=seg_numbers,
                shared_text=shared,
                unique_stems={
                    m.number: _extract_unique_stem(m.stem, shared)
                    for m in seg_members
                },
                display_unique_stems={
                    m.number: display_unique_stem(m.stem, shared)
                    for m in seg_members
                },
                confidence=confidence,
                signals=signals,
            )
        )
    return drafts


# --- F6: cross-page question groups ------------------------------------------


@dataclass(slots=True)
class CrossPageGroupDraft:
    """A detected group whose printed number range SPANS page boundaries.

    Real exam papers put the shared material + first sub-questions at the
    bottom of one page and continue on the next; a single-page detector
    can never see them (F6 audit gap #3).  A draft is still only a
    PROPOSAL — confirmation stays a user action.
    """

    member_numbers: list[str]
    page_numbers: list[int]  # document-local, ascending
    shared_text: str
    confidence: str = "uncertain"
    signals: list[str] = field(default_factory=list)

    @property
    def group_number(self) -> str:
        return f"{self.member_numbers[0]}~{self.member_numbers[-1]}题组"


def _extract_tail_material(page_text: str, hint_start: int) -> str:
    """Material block just BEFORE the hint at ``hint_start``.

    Exam layout: previous question's options → shared material → hint.
    So walk back from the hint and cut after the LAST option marker
    ("D." / "D．") inside the lookback window; without one, keep the
    whole window.  Honest heuristic: returns "" when nothing plausible
    is found instead of fabricating a material block.
    """

    window_start = max(0, hint_start - 300)
    window = page_text[window_start:hint_start]
    last_option = None
    for marker in ("D．", "D.", "D、"):
        pos = window.rfind(marker)
        if pos != -1 and (last_option is None or pos > last_option):
            last_option = pos
    if last_option is not None:
        # cut AFTER the option's own LINE, not after the marker —
        # cutting at the marker kept the option's text ("光照充足…")
        # inside the material (F6-04, browser-verified).
        line_end = window.find("\n", last_option)
        window = (
            window[line_end + 1:] if line_end != -1 else window[last_option:]
        )
    return window.strip()


def detect_cross_page_groups(
    candidates_by_page: dict[int, list],
    page_texts: dict[int, str],
) -> list[CrossPageGroupDraft]:
    """Propose groups whose hint number range spans page boundaries.

    ``candidates_by_page`` maps DOCUMENT-LOCAL page numbers to that page's
    candidates; ``page_texts`` maps the same page numbers to page text.
    Signal (generic, no hardcoding): a printed hint range "完成 X～Y 小题"
    whose members are SPLIT across consecutive pages — part on page N,
    the rest on page N+1 (or the next page holding group members).

    Membership must exactly cover the printed range across exactly these
    pages: missing or extra members mean the split guess is wrong and NO
    draft is produced (宁可漏报，不可错报).
    """

    numbers_by_page: dict[int, set[int]] = {}
    for page_number, candidates in candidates_by_page.items():
        keys = {
            key
            for key in (_number_key(c.number) for c in candidates)
            if key is not None
        }
        if keys:
            numbers_by_page[page_number] = keys
    if not numbers_by_page:
        return []

    page_order = sorted(page_texts)
    drafts: list[CrossPageGroupDraft] = []
    for page_number in page_order:
        text = page_texts.get(page_number) or ""
        for match in _HINT_RANGE_RE.finditer(text):
            groups = match.groups()
            start = int(groups[0] if groups[0] is not None else groups[2])
            end = int(groups[1] if groups[1] is not None else groups[3])
            if end <= start or end - start > 8:
                continue
            wanted = set(range(start, end + 1))
            here = numbers_by_page.get(page_number, set()) & wanted
            if not here or here == wanted:
                continue
            for later in [p for p in page_order if p > page_number][:3]:
                there = numbers_by_page.get(later, set()) & wanted
                if not there:
                    continue
                if here | there != wanted or here & there:
                    continue
                member_keys = sorted(here | there)
                member_numbers = [str(k) for k in member_keys]
                shared = _extract_tail_material(text, match.start())
                signals = [
                    f"第 {page_number} 页题组提示语 {start}～{end} 题跨页",
                    "小题分布于相邻两页且恰好覆盖提示语范围",
                ]
                if shared:
                    signals.append(f"共享材料 {len(shared)} 字")
                drafts.append(
                    CrossPageGroupDraft(
                        member_numbers=member_numbers,
                        page_numbers=[page_number, later],
                        shared_text=shared,
                        confidence="probable",
                        signals=signals,
                    )
                )
                break
    return drafts
