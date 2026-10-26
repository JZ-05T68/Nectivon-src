"""Deterministic cross-page linkage for photographed exercise sets (P0-1).

Merged multi-photo documents make one exercise span two photos.  The model
is instructed never to drop a leading continuation line (CONTINUATION_RULES);
this module then closes the loop locally, without any AI call:

- :func:`merge_cross_page_continuations` — attach a page's leading
  「（接上页）」candidates to the previous page's last incomplete leaf.
- :func:`link_document_figures` — when a candidate references 图 X that is
  registered (page_figures) on a sibling page, record where the figure
  lives so the student can find the original drawing.

Both functions only append information (stem text / visual notes) and log a
stem revision; they never delete user content and never re-bill the AI.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database
    from src.question_candidate_service import QuestionCandidateStore

LOGGER = logging.getLogger(__name__)

CONTINUATION_PREFIX = "（接上页）"
_CROSS_PAGE_NOTE = re.compile(r"位于本资料第 \d+ 页")
_EDITORIAL_RESIDUE = re.compile(r"（?注[:：].*$")


def sanitize_region_description(description: str) -> str:
    """Strip model editorial residue; keep only the caption itself."""

    return _EDITORIAL_RESIDUE.sub("", str(description or "")).strip()


def clip_regions_to_siblings(
    candidates: list, page_figures: list[dict], text_blocks: list[dict] | None = None,
) -> int:
    """Bound each figure region between its same-page neighbours (P0-2/P1-3).

    The model's boxes separate neighbouring figures and prose columns
    correctly, but local ink completion can walk across the narrow gutter
    between them.  When a neighbour shares the row band on one side, that
    side gets a hard horizontal stop in the gutter between the two boxes.
    Model edges can clip detached labels and output arrows; the midpoint
    allows recovery without reaching the neighbouring figure. Prose still
    clips at the figure's own edge, since text can occupy the whole gutter.
    Siblings are same-page figure registry entries, other candidates'
    stem/shared regions, and text blocks from the page's content blocks.
    """

    from src.question_candidate_service import iter_question_nodes

    def boxes_overlap_y(a: list, b: list) -> bool:
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        return overlap > 0.3 * min(a[3] - a[1], b[3] - b[1])

    def owns(region: dict, node, figure: dict) -> bool:
        name = _figure_key(figure.get("name", ""))
        description = _figure_key(region.get("description", ""))
        refs = {_figure_key(ref) for ref in (getattr(node, "figure_refs", []) or [])}
        return (
            name in description
            or description in name
            or name in refs
            or not figure.get("bbox")
        )

    def valid(box: object) -> list[float] | None:
        if (isinstance(box, list) and len(box) == 4
                and all(isinstance(v, (int, float)) for v in box)
                and 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
            return [float(v) for v in box]
        return None

    regions_with_owner: list[tuple[dict, object]] = []
    for node in iter_question_nodes(candidates):
        for region in getattr(node, "visual_regions", []) or []:
            if region.get("role") in ("stem", "shared") and region.get("bbox"):
                regions_with_owner.append((region, node))
    changed = 0
    for region, node in regions_with_owner:
        bbox = valid(region.get("bbox"))
        if bbox is None:
            continue
        left_cut, right_cut = 0.0, 1000.0
        for provider, prose in ((page_figures or [], False), (text_blocks or [], True)):
            for item in provider:
                if item in (page_figures or []) and owns(region, node, item):
                    continue
                sibling = valid(item.get("bbox"))
                if sibling is None or not boxes_overlap_y(bbox, sibling):
                    continue
                if sibling[2] <= bbox[0]:
                    left_cut = max(left_cut, bbox[0] if prose else (sibling[2] + bbox[0]) / 2)
                elif sibling[0] >= bbox[2]:
                    right_cut = min(right_cut, bbox[2] if prose else (bbox[2] + sibling[0]) / 2)
        for other, _owner in regions_with_owner:
            if other is region:
                continue
            if _figure_key(other.get("description", "")) == _figure_key(
                region.get("description", "")
            ) and other.get("description"):
                continue
            sibling = valid(other.get("bbox"))
            if sibling is None or not boxes_overlap_y(bbox, sibling):
                continue
            if sibling[2] <= bbox[0]:
                left_cut = max(left_cut, (sibling[2] + bbox[0]) / 2)
            elif sibling[0] >= bbox[2]:
                right_cut = min(right_cut, (bbox[2] + sibling[0]) / 2)
        if left_cut > 0 or right_cut < 1000:
            region["sibling_clip"] = [left_cut, bbox[1], right_cut, bbox[3]]
            changed += 1
    return changed


def _is_continuation(candidate) -> bool:
    return (
        not candidate.children
        and candidate.status == "pending"
        and (not candidate.number.strip() or candidate.stem.startswith(CONTINUATION_PREFIX))
    )


_BARE_SUBQUESTION = re.compile(r"^[（(]\s*([0-9一二三四五六七八九十]+)\s*[）)]")


def merge_cross_page_continuations(
    store: QuestionCandidateStore, database: Database, page_id: int
) -> list[dict[str, str]]:
    """Fold this page's leading continuation candidates into the prior page.

    A continuation that opens with a bare sub-question label (``(2) …``)
    joins the previous page's last incomplete composite ROOT as a new
    child; plain trailing text appends to that root's / the last
    incomplete leaf's stem.  Returns one record per merge.  No merge
    happens without an incomplete receiver — an orphan continuation stays
    visible on its own page instead of being glued to an unrelated
    question.
    """

    from src.question_candidate_service import (
        QuestionCandidate,
        _append_stem_revision,
        _utc_now,
        canonical_question_number,
        iter_atomic_leaves,
    )

    page = database.get_page(page_id)
    if page is None or page.page_number <= 1:
        return []
    previous_pages = [
        item for item in database.list_pages(page.document_id)
        if item.page_number < page.page_number
    ]
    if not previous_pages:
        return []
    previous = max(previous_pages, key=lambda item: item.page_number)
    current = store.page_candidates(page_id) or []
    leading: list = []
    rest = list(current)
    while rest and _is_continuation(rest[0]):
        leading.append(rest.pop(0))
    if not leading:
        return []
    previous_candidates = store.page_candidates(previous.id) or []
    added_text = "\n".join(
        node.stem.removeprefix(CONTINUATION_PREFIX).strip() for node in leading
    ).strip()
    if not added_text:
        return []

    # A leading continuation can only belong to the LAST question of the
    # previous page; an incomplete question in the middle stays untouched.
    last_root = previous_candidates[-1] if previous_candidates else None
    incomplete_root = (
        last_root if last_root is not None and last_root.completeness == "incomplete" else None
    )
    label = _BARE_SUBQUESTION.match(added_text.lstrip())
    merged_number = ""
    if incomplete_root is not None and incomplete_root.children and label:
        parent_number = canonical_question_number(incomplete_root.number)
        child = QuestionCandidate(
            number=f"{parent_number}({label.group(1)})",
            stem=added_text,
            completeness=leading[-1].completeness,
            incomplete_reason=leading[-1].incomplete_reason,
            extracted_at=_utc_now(),
            split_source="ai_inference",
            split_confidence="medium",
        )
        child.validate()
        incomplete_root.children.append(child)
        if (
            leading[-1].completeness == "complete"
            and all(node.completeness == "complete" for node in incomplete_root.children)
        ):
            incomplete_root.completeness = "complete"
            incomplete_root.incomplete_reason = ""
        receiver = incomplete_root
        merged_number = child.number
    elif incomplete_root is not None and not incomplete_root.children:
        incomplete_root.stem = f"{incomplete_root.stem}\n{added_text}".strip()
        if leading[-1].completeness == "complete":
            incomplete_root.completeness = "complete"
            incomplete_root.incomplete_reason = ""
        receiver = incomplete_root
        merged_number = incomplete_root.number
    else:
        receiver = next(
            (
                leaf
                for _root, leaf, _path, _ancestors in reversed(
                    list(iter_atomic_leaves(previous_candidates))
                )
                if leaf.completeness == "incomplete"
            ),
            None,
        )
        if receiver is None:
            return []
        receiver.stem = f"{receiver.stem}\n{added_text}".strip()
        if leading[-1].completeness == "complete":
            receiver.completeness = "complete"
            receiver.incomplete_reason = ""
        merged_number = receiver.number
    _append_stem_revision(
        receiver,
        kind="cross_page_merge",
        detail=f"并入第 {page.page_number} 页开头的延续内容：{added_text[:180]}",
    )
    store.save_page_candidates(previous.id, previous_candidates)
    store.save_page_candidates(page_id, rest)
    LOGGER.info(
        "跨页延续已合并：page %s → page %s 题号 %s",
        page.page_number, previous.page_number, merged_number or "（无题号）",
    )
    return [
        {
            "number": merged_number or "（无题号）",
            "from_page": str(page.page_number),
            "added": added_text[:120],
        }
    ]


def _figure_key(value: str) -> str:
    """Compare figure names across spacing/punctuation variants."""

    return re.sub(r"[\s，。、．.·()（）]", "", str(value or ""))


_FIGURE_MENTION = re.compile(r"图\s*[0-9０-９]+[-－—–][0-9０-９]+[A-Za-z]?[0-9]?")

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _referenced_figures(node) -> list[str]:
    """Figure references from BOTH figure_refs and visual_notes mentions.

    Round-2 evidence: the model often writes 「本题所需图 6-8a 不在本页」only
    in visual_notes while leaving figure_refs empty; harvesting both keeps
    the cross-page link deterministic.
    """

    references = [str(item) for item in (getattr(node, "figure_refs", []) or [])]
    for match in _FIGURE_MENTION.finditer(str(getattr(node, "visual_notes", "") or "")):
        mention = match.group(0).translate(_FULLWIDTH_DIGITS)
        if mention not in references:
            references.append(mention)
    return references


def link_document_figures(
    store: QuestionCandidateStore, database: Database, document_id: int
) -> list[str]:
    """Attach referenced figures to questions — same page, same document,
    or ANY imported photo via the global figure index (round-2 issue 2).

    A cross-page/global hit both records a visual note and injects a
    cross-page virtual region, so every question view can crop the real
    figure from the page that physically holds it.  Deterministic caption
    matching only — no AI call, no OCR, no adjacency assumption.
    """

    from src.figure_index_service import find_figures, virtual_region
    from src.question_candidate_service import iter_question_nodes

    pages = sorted(database.list_pages(document_id), key=lambda item: item.page_number)
    registry: dict[int, list[dict]] = {}
    for page in pages:
        registry[page.id] = store.page_figures(page.id) or []
    notes: list[str] = []
    for page in pages:
        local_keys = {_figure_key(figure.get("name", "")) for figure in registry[page.id]}
        candidates = store.page_candidates(page.id)
        if not candidates:
            continue
        changed = False
        for node in iter_question_nodes(candidates):
            if getattr(node, "binding_confirmed", False):
                continue
            for reference in _referenced_figures(node):
                reference_key = _figure_key(reference)
                if not reference_key or reference_key in local_keys:
                    continue
                # Same-document sibling pages first, then the global index
                # (separately photographed textbook figures, any batch).
                hit = None
                source_page = None
                for other in pages:
                    if other.id == page.id:
                        continue
                    hit = next(
                        (
                            figure for figure in registry[other.id]
                            if _figure_key(figure.get("name", ""))
                            and (
                                reference_key in _figure_key(figure.get("name", ""))
                                or _figure_key(figure.get("name", "")) in reference_key
                            )
                        ),
                        None,
                    )
                    if hit is not None:
                        source_page = other
                        break
                global_hit = None
                if hit is None:
                    for entry in find_figures(store.root, reference):
                        if entry.get("page_id") == page.id:
                            continue
                        global_hit = entry
                        break
                if hit is None and global_hit is None:
                    continue
                if hit is not None:
                    where = f"本资料第 {source_page.page_number} 页"
                    region = {
                        "role": "shared",
                        "bbox": [float(v) for v in hit.get("bbox", [])],
                        "description": f"{reference}（{where}，跨页插图）",
                        "source_page_id": source_page.id,
                        "source_document_id": document_id,
                        "source_label": f"本资料第 {source_page.page_number} 页",
                    }
                else:
                    title = str(global_hit.get("document_title", ""))
                    number = global_hit.get("page_number", "?")
                    where = f"《{title}》第 {number} 页"
                    region = virtual_region(global_hit, description=reference)
                note = f"{reference} 位于{where}（已跨照片关联）"
                if reference in (node.visual_notes or "") and any(
                    r.get("source_page_id") == region.get("source_page_id")
                    for r in node.visual_regions
                ):
                    continue
                node.visual_notes = (
                    f"{node.visual_notes}；{note}" if node.visual_notes else note
                )
                if node.visual_dependency == "none":
                    node.visual_dependency = "required"
                node.visual_regions.append(region)
                notes.append(
                    f"第 {page.page_number} 页 {node.number or '（无题号）'}：{note}"
                )
                changed = True
        if changed:
            store.save_page_candidates(page.id, candidates)
    return notes
