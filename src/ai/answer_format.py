"""Deterministic numbered-list normalization for AI answer text.

Models sometimes return an enumerated answer as one long paragraph
(``1. 类型A 2. 类型B 3. 类型C``), which the Markdown renderer then shows as a
single run-on line instead of one item per line.

This module only inserts line breaks. It never rewrites, reorders, renumbers
or deletes answer content, and it is deliberately conservative:

- a break is inserted only inside a run of markers valued ``1, 2, 3, …``;
- every marker of a run must sit on the same source line as the previous one,
  so an already well-formed list (one item per line) is left untouched;
- a marker must be at a line start or follow whitespace, and must not be
  followed by another digit or separator;
- fenced code blocks are skipped entirely.

Anything ambiguous is therefore left byte-for-byte unchanged (fail-safe).
Numeric literals that merely look like list markers survive untouched:
version numbers (``0.8.4``), interpreter versions (``3.11``), decimal values
(``1.25``), section references (``第2.3节``) and identifiers (``v1.0``).
"""

from __future__ import annotations

import re

__all__ = ["normalize_numbered_list_lines"]

#: A fenced code block. Its content is never touched: ``1.`` inside a code
#: sample is program text, not an enumerated answer item. An unterminated
#: fence swallows the remainder, which is the safe direction.
_CODE_FENCE = re.compile(
    r"^(```|~~~)[^\n]*\n.*?(?:^\1[^\n]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)

#: One candidate list marker: a 1-2 digit number at a line start, after
#: whitespace or after a colon (``答：1. …``), followed by ``.`` / ``)`` /
#: ``、``, and then something that is neither a digit nor a second separator.
#: The restricted prefix and that trailing class are what keep ``0.8.4``,
#: ``3.11``, ``1.25``, ``第2.3节`` and ``v1.0`` out of scope.
_LIST_MARKER = re.compile(
    r"(?:^|(?<=\s)|(?<=[：:]))(\d{1,2})([.)、])(?=\s*[^\d\s.)、])",
    re.MULTILINE,
)

#: Refuse to restructure a run longer than this. A genuine enumerated answer
#: is short; an unexpectedly long run is more likely coincidental prose digits.
_MAX_ITEMS = 30


def normalize_numbered_list_lines(text: str) -> str:
    """Return ``text`` with every enumerated item on its own line.

    Only whitespace is ever added. When no unambiguous ``1, 2, 3…`` run exists,
    the input is returned unchanged.
    """

    if not isinstance(text, str) or not text:
        return text
    if not _LIST_MARKER.search(text):
        return text

    pieces: list[str] = []
    cursor = 0
    for fence in _CODE_FENCE.finditer(text):
        pieces.append(_normalize_segment(text[cursor : fence.start()]))
        pieces.append(fence.group(0))
        cursor = fence.end()
    pieces.append(_normalize_segment(text[cursor:]))
    return "".join(pieces)


def _normalize_segment(segment: str) -> str:
    """Insert line breaks before the markers of one non-code segment."""

    markers = list(_LIST_MARKER.finditer(segment))
    if len(markers) < 2:
        return segment

    offsets = _list_break_offsets(segment, markers)
    if not offsets:
        return segment

    rebuilt: list[str] = []
    previous = 0
    for offset in offsets:
        rebuilt.append(segment[previous:offset].rstrip())
        rebuilt.append("\n")
        previous = offset
    rebuilt.append(segment[previous:])
    return "".join(rebuilt)


def _list_break_offsets(segment: str, markers: list[re.Match[str]]) -> list[int]:
    """Return the offsets in ``segment`` that need a preceding line break.

    Markers are grouped into maximal runs of consecutive values starting at 1
    that share one source line. A run needs at least two markers to be acted
    on, so a lone ``1.`` in prose is never treated as a list.
    """

    offsets: list[int] = []
    run: list[tuple[int, int]] = []
    expected = 0
    previous_line = -1

    def close_run() -> None:
        if len(run) >= 2:
            offsets.extend(start for _, start in run[1:])
            first_start = run[0][1]
            if not _starts_own_line(segment, first_start):
                offsets.append(first_start)
        run.clear()

    for match in markers:
        value = int(match.group(1))
        start = match.start()
        line = segment.count("\n", 0, start)
        continues = (
            bool(run)
            and value == expected + 1
            and line == previous_line
            and len(run) < _MAX_ITEMS
        )
        if continues:
            run.append((value, start))
            expected = value
        else:
            close_run()
            if value == 1:
                run.append((value, start))
                expected = 1
            else:
                expected = 0
        previous_line = line

    close_run()
    return sorted(set(offsets))


def _starts_own_line(segment: str, start: int) -> bool:
    """True when nothing but whitespace precedes ``start`` on its line."""

    line_start = segment.rfind("\n", 0, start) + 1
    return not segment[line_start:start].strip()
