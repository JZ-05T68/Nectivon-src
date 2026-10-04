"""Scan-artifact filtering: scanner watermarks never enter knowledge text.

Real scanned exam papers carry branding left by the scanning app —
「扫码使用」「夸克扫描王」「扫描全能王」, QR-code promo slogans and similar
lines.  These are artifacts of the *tooling*, not of the *document*, and they
used to pollute every knowledge layer: OCR search text (FTS hits for
「夸克」), search snippets shown to the user, and Agent reading inputs
(brand names landed in ``summary`` / ``key_facts`` / ``keywords``).

Hard product rule (V086-309): the paper's OWN printed page numbers and
document footers — 「高三数学试卷（九） 第1页（共6页）」 — are part of the
original layout and must be preserved and recognisable.  Blanket strategies
("cut the bottom 15%", "drop every footer line") are therefore forbidden by
design; classification is line-based with explicit protection.

Layered semantics:

* the raw OCR text and the source PDF/page images are NEVER modified —
  filtering applies only to derived knowledge surfaces (FTS search columns,
  Agent reading input, printed-page-number extraction);
* a user's manual correction (``pages.markdown_content``) is user data and
  is never filtered by this module;
* classification is extensible: brand/branding token table + promo-shape
  rules + an explicit printed-page-number protection pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------- protection
#: Printed page-number / document-footer pattern. Both full-width and
#: half-width parentheses appear in real OCR output (「第1页(共6页)」),
#: optional spaces and 「第 X 页」 variants are accepted.
PRINTED_PAGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"第\s*\d+\s*页\s*[（(]\s*共\s*\d+\s*页\s*[）)]"
)
#: Simpler printed-page form without the total (「第 12 页」 alone). Only
#: used for extraction when the total form is absent.
PRINTED_PAGE_ONLY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"第\s*(\d+)\s*页(?!\s*[（(])"
)

# ------------------------------------------------------------- artifact rules
#: Scanner-app brand tokens (case-insensitive where latin). Extensible table:
#: add a token here and every layer (FTS, snippets, Agent input) stops seeing
#: it without any other code change.
BRAND_TOKENS: Final[tuple[str, ...]] = (
    "夸克扫描王",
    "扫描全能王",
    "全能扫描王",
    "扫描王",
    "camscanner",
    "扫描宝",
    "cs扫描",
    "夸克扫描",
)
#: Promo slogans / QR-call-to-action shapes commonly stamped by scanner apps.
PROMO_TOKENS: Final[tuple[str, ...]] = (
    "扫码使用",
    "扫码关注",
    "扫码体验",
    "扫码获取",
    "扫码下载",
    "扫码看解析",
    "扫码看视频",
    "微信扫码",
    "极速扫描",
    "更多精品",
)
#: Answer-sheet template boilerplate / identity fields (V0.8.7 contract,
#: filtered already at the knowledge-text layer so it can never become
#: learning content: answer sheets are scanning logistics, not paper
#: content; and identity fields must not be searchable knowledge).
#: Deliberately conservative complete-line patterns only.
BOILERPLATE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"请在?各题(的)?答题区域(内)?作答"),
    re.compile(r"超出黑色矩形边框"),
    re.compile(r"^正确填涂$"),
    re.compile(r"^缺[考疑]标记"),
    re.compile(r"^贴条形码"),
    re.compile(r"^\d*\s*准考证号"),
    re.compile(r"^姓名[:：_]"),
    re.compile(r"^条码?粘贴区$"),
)
_BRAND_MATCHER: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(token) for token in BRAND_TOKENS), re.IGNORECASE
)
_PROMO_MATCHER: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(token) for token in PROMO_TOKENS)
)
#: A "扫码…" call to action of any wording (二维码 promo lines) — only
#: filtered when the line is short, so a long legitimate sentence that merely
#: mentions a code somewhere is never dropped.
_SCAN_CTA_SHAPE: Final[re.Pattern[str]] = re.compile(r"扫码")
_MAX_CTA_LINE_CHARS: Final[int] = 24


@dataclass(frozen=True, slots=True)
class ArtifactFilterReport:
    """What one filtering pass removed — auditable, never silent."""

    filtered_text: str
    removed_lines: tuple[str, ...]
    kept_page_footer: bool


@dataclass(frozen=True, slots=True)
class PrintedPageInfo:
    """Printed page-number facts parsed from the paper's own footer."""

    printed_page_number: int | None
    printed_total_pages: int | None
    footer_text: str


def _is_artifact_line(line: str) -> bool:
    """True when the whole line is scanner branding / QR promo / sheet boilerplate."""
    candidate = line.strip()
    if not candidate:
        return False
    # The paper's own footer is always protected, even when a brand token
    # shares the line (「第1页(共6页) 夸克扫描王」 keeps the page number).
    if PRINTED_PAGE_PATTERN.search(candidate):
        return False
    if any(pattern.search(candidate) for pattern in BOILERPLATE_PATTERNS):
        return True
    if _BRAND_MATCHER.search(candidate) or _PROMO_MATCHER.search(candidate):
        return True
    return bool(_SCAN_CTA_SHAPE.search(candidate) and len(candidate) <= _MAX_CTA_LINE_CHARS)


def _strip_brand_tokens(line: str) -> str:
    """Remove brand/promo fragments from a protected line, keep the rest."""
    cleaned = _BRAND_MATCHER.sub(" ", line)
    cleaned = _PROMO_MATCHER.sub(" ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def filter_knowledge_text(text: str) -> ArtifactFilterReport:
    """Return knowledge-safe text: scanner artifacts out, page footers in.

    Line-level classification with explicit footer protection; brand tokens
    sharing a protected footer line are stripped while the page number is
    kept verbatim. Never raises on odd input — the input text is returned
    unchanged when nothing matches.
    """
    if not text:
        return ArtifactFilterReport(filtered_text="", removed_lines=(), kept_page_footer=False)
    kept_lines: list[str] = []
    removed: list[str] = []
    kept_page_footer = False
    modified = False
    for line in text.splitlines():
        if _is_artifact_line(line):
            removed.append(line.strip())
            modified = True
            continue
        if PRINTED_PAGE_PATTERN.search(line):
            kept_page_footer = True
            cleaned = _strip_brand_tokens(line)
            if cleaned != line.strip():
                modified = True
            kept_lines.append(cleaned if cleaned else line)
            continue
        kept_lines.append(line)
    filtered = "\n".join(kept_lines)
    if modified:
        # Collapse runs of blank lines left by dropped artifacts.
        filtered = re.sub(r"\n{3,}", "\n\n", filtered).strip()
    else:
        filtered = text
    return ArtifactFilterReport(
        filtered_text=filtered,
        removed_lines=tuple(removed),
        kept_page_footer=kept_page_footer,
    )


def extract_printed_page_info(text: str) -> PrintedPageInfo:
    """Parse the paper's own printed page number from kept footer lines.

    PDF page index and printed page number are different concepts (a paper
    may print 「第3页（共6页）」 on its 3rd scanned image, but covers/inserts
    break that alignment); both must stay traceable, so this reads only the
    document's own footer text and never derives from the row id.
    """
    if not text:
        return PrintedPageInfo(None, None, "")
    for line in text.splitlines():
        match = PRINTED_PAGE_PATTERN.search(line)
        if match:
            digits = re.findall(r"\d+", match.group(0))
            if len(digits) >= 2:
                return PrintedPageInfo(
                    printed_page_number=int(digits[0]),
                    printed_total_pages=int(digits[1]),
                    footer_text=line.strip(),
                )
    for line in text.splitlines():
        match = PRINTED_PAGE_ONLY_PATTERN.search(line)
        if match:
            return PrintedPageInfo(
                printed_page_number=int(match.group(1)),
                printed_total_pages=None,
                footer_text=line.strip(),
            )
    return PrintedPageInfo(None, None, "")


__all__ = [
    "ArtifactFilterReport",
    "BOILERPLATE_PATTERNS",
    "BRAND_TOKENS",
    "PRINTED_PAGE_PATTERN",
    "PrintedPageInfo",
    "PROMO_TOKENS",
    "extract_printed_page_info",
    "filter_knowledge_text",
]
