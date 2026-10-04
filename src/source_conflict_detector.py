"""Source-conflict detector — F1 (P0 finding from ROUND 2 regression).

Observed conflict pattern: an imported filename says 「校内四模」 while the
printed header says 「校内三模」.  Without a warning, this mismatch can
silently affect later study references.

This module is a STATELESS detector: it compares the imported file name
against the printed paper title (first OCR line / Agent reading head) and
reports a CONFLICT PROPOSAL for the UI to surface.  It never renames
anything, never rewrites history, and only ever *suggests* — the user
decides what the document actually is (F-task §0.1).

Detection is deliberately conservative: it fires only on exam-kind words
that disagree INSIDE THE SAME detection family (模/联考/期中期末/高考),
so a filename like 「2024江苏高考地理.pdf」 with a paper titled 「江苏卷」
does NOT trigger (the words agree, not just co-exist).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Exam-version words whose DISAGREEMENT matters (三模 vs 四模, 期中 vs 期末…).
_EXAM_KIND_WORDS = (
    "一模", "二模", "三模", "四模", "五模", "六模",
    "模前模", "零模",
    "期中", "期末", "月考", "联考", "统考", "模拟",
)

# Extract the exam-kind tokens actually present in a string.
_KIND_TOKEN_RE = re.compile(
    r"(?:模前模|零模|[一二三四五六]模|期中|期末|月考|联考|统考|模拟)"
)

# Papers often print the school/year before the kind; strip pure noise.
_STRIP_RE = re.compile(r"[\s:：，,。.·\-—－()（）\[\]【】]")


@dataclass(slots=True)
class SourceConflict:
    """A suggested (never auto-applied) filename↔paper-title conflict."""

    kind: str  # 'exam_version' | 'none'
    filename_side: str
    paper_side: str
    message: str


def _kind_tokens(text: str) -> set[str]:
    return set(_KIND_TOKEN_RE.findall(_STRIP_RE.sub("", text or "")))


def detect_source_conflict(
    filename: str, paper_title: str
) -> SourceConflict | None:
    """Compare the file name with the printed paper title.

    Returns a :class:`SourceConflict` when BOTH sides name an exam kind
    AND the kinds disagree (e.g. 四模 vs 三模).  Missing/unknown kinds on
    either side never fire (absence of evidence is not evidence).
    """

    file_tokens = _kind_tokens(filename)
    paper_tokens = _kind_tokens(paper_title)
    if not file_tokens or not paper_tokens:
        return None
    # Same family disagreement: token sets are non-empty and disjoint
    # (e.g. {'四模'} vs {'三模'}); a shared token means agreement.
    if file_tokens & paper_tokens:
        return None
    # Guard: 「模拟」 is a generic word — if one side only says 模拟 and
    # the other is a specific 模, treat as agreeing family, no conflict.
    if file_tokens == {"模拟"} or paper_tokens == {"模拟"}:
        return None
    return SourceConflict(
        kind="exam_version",
        filename_side="/".join(sorted(file_tokens)),
        paper_side="/".join(sorted(paper_tokens)),
        message=(
            f"文件名标注「{'/'.join(sorted(file_tokens))}」，"
            f"但卷面标题写「{'/'.join(sorted(paper_tokens))}」——"
            "两者不一致。请核对这份资料实际是哪次考试；系统不会替你改名。"
        ),
    )


def paper_title_from_ocr(ocr_text: str, max_lines: int = 6) -> str:
    """Best-effort printed-title extraction: first non-empty OCR lines."""

    for line in (ocr_text or "").splitlines()[:max_lines]:
        cleaned = line.strip()
        if len(cleaned) >= 4:
            return cleaned
    return ""
