"""Version-family aware retrieval expansion for read-only page tools.

FAIL-014 (v0.8.1): when a document family exists in several versions
(``泵维护视觉手册`` / ``_v1.0`` / ``_v1.1`` / ``_v2.0``) or under the same
title with different content, the default top-``limit`` lexical hits may all
come from one or two family members. The agent then answers from a partial
view of the family and can wrongly claim a value "is not in the corpus".

Minimal general fix, data-driven on the retrieval pool only:

- fetch a slightly wider (free, lexical) pool than the caller asked for;
- group pool hits by normalized title family (version tokens, years and
  publishing-status words stripped — natural naming variants collapse);
- when the pool itself already spans two or more documents of one family,
  append the missing family members after the caller's original top hits,
  so the vision step reads the whole family instead of part of it;
- because a family member with a thin text layer can rank below the pool
  window for one query's terms, the caller may also run one co-query with an
  anchor-family member's document title (title words are family words) and
  merge that pool in before selection — see :func:`select_family_expanded`;
- everything else keeps the exact pre-existing behaviour, and the expansion
  only ever adds same-family pages — it never reorders, replaces, or pulls
  in unrelated documents (precision guard, tested alongside recall).

The detection deliberately ignores the query text: whether the family
matters is decided by evidence in the pool, not by keyword guessing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.models import SearchResult

FAMILY_POOL_LIMIT = 8
MAX_FAMILY_PAGES = 4
#: FAIL-018/E-1: one document may contribute a second page when the visual
#: artifact index surfaces a *different* page of an already-pooling document
#: (e.g. the额定流量 chart page matches lexically while the趋势图 page only
#: matches through its reading artifacts). Version-family coverage stays
#: document-level; this bound just stops intra-document hits from being
#: silently deduplicated away.
MAX_PAGES_PER_DOC = 2

# Tokens that make two titles the same document family. Deliberately
# conservative: pure version numbers, years, and publishing-status words.
_VERSION_TOKEN_RE = re.compile(
    r"(?i)\bv\d+(?:\.\d+)*\b|\b\d+(?:\.\d+)+\b|\b20\d{2}\b"
)
_FAMILY_NOISE_RE = re.compile(
    r"(?i)修订版|修订|正式版|正式|草案|草稿|最终版|最新版|最新|原版|旧版|新版"
    r"|副本|复测版|复测|版本|draft|final|copy"
)
_SEPARATOR_RE = re.compile(r"[\s_\-.·（）()【】\[\]{}\"'“”‘’、，,：:；;！!？?～]+")


def normalize_family_title(title: str) -> str:
    """Collapse natural naming variants of one document family to a key.

    ``泵维护视觉手册`` / ``泵维护视觉手册_v1.1`` / ``2026 修订版泵维护视觉手册``
    all normalize to the same key, while unrelated titles stay distinct.
    """

    # Underscore is a word character, so it must become a separator *before*
    # the version-token pass for ``手册_v1.1`` to lose its ``v1.1``.
    text = title.lower().replace("_", " ")
    text = _VERSION_TOKEN_RE.sub(" ", text)
    text = _FAMILY_NOISE_RE.sub(" ", text)
    return _SEPARATOR_RE.sub("", text).strip()


def group_family_titles(titles: Sequence[str]) -> dict[str, str]:
    """Map each raw title to its normalized family key (order preserving)."""

    return {title: normalize_family_title(title) for title in titles}


def select_family_expanded(
    pool: Sequence[SearchResult],
    *,
    base_limit: int,
    max_pages: int = MAX_FAMILY_PAGES,
    visual_pages: Sequence[SearchResult] = (),
) -> tuple[list[SearchResult], dict[str, object]]:
    """Re-select pool hits so one version family is covered end to end.

    Returns the selected hits plus expansion metadata. The caller's original
    top ``base_limit`` hits are always kept first and in order; missing
    same-family members are appended afterwards, bounded by ``max_pages``.
    When the pool contains fewer than two documents of any one family the
    selection is ``pool[:base_limit]`` plus any still-unselected
    ``visual_pages`` (FAIL-018/E-1: an in-chart entity hit is a strong page
    signal even when no version family spans the pool) — i.e. non-family
    queries keep their historical top-``limit`` selection, augmented only by
    the visual artifact index.
    """

    original = list(pool[: max(base_limit, 0)])
    budget = max(max_pages, base_limit)

    def _append_visual(picked: list[SearchResult]) -> list[SearchResult]:
        """Append still-unselected visual hits (bounded, additive-only)."""

        if not visual_pages:
            return picked
        picked_keys = {(h.document_id, h.page_id) for h in picked}
        counts: dict[int, int] = {}
        for hit in picked:
            counts[hit.document_id] = counts.get(hit.document_id, 0) + 1
        for hit in visual_pages:
            if len(picked) >= budget:
                break
            key = (hit.document_id, hit.page_id)
            if key in picked_keys:
                continue
            if counts.get(hit.document_id, 0) >= MAX_PAGES_PER_DOC:
                continue
            picked.append(hit)
            picked_keys.add(key)
            counts[hit.document_id] = counts.get(hit.document_id, 0) + 1
        return picked

    if not pool or not original:
        if visual_pages:
            return _append_visual(list(original)), {
                "triggered": False,
                "family_key": None,
            }
        return original, {"triggered": False, "family_key": None}

    family_of_doc: dict[int, str] = {}
    title_of_doc: dict[int, str] = {}
    for hit in pool:
        if hit.document_id not in family_of_doc:
            family_of_doc[hit.document_id] = normalize_family_title(
                hit.document_title
            )
            title_of_doc[hit.document_id] = hit.document_title

    # Anchor to the family with the most distinct documents in the pool
    # (ties broken by best rank). A dense family is pool evidence that the
    # question concerns that family — even when the top-1 hit itself belongs
    # to a different, single-member family (cross-family lexical noise, the
    # K01-UI edge where the anchor-on-top-hit rule left v1.1 unread).
    members_by_family: dict[str, list[int]] = {}
    first_pos: dict[int, int] = {}
    for pos, hit in enumerate(pool):
        first_pos.setdefault(hit.document_id, pos)
    for doc_id, key in family_of_doc.items():
        if key:
            members_by_family.setdefault(key, []).append(doc_id)
    top_family = None
    if members_by_family:
        top_family = max(
            members_by_family,
            key=lambda key: (
                len(members_by_family[key]),
                -min(first_pos[doc_id] for doc_id in members_by_family[key]),
            ),
        )
    member_docs = members_by_family.get(top_family, [])
    if len(member_docs) < 2 or not top_family:
        return (
            _append_visual(original),
            {"triggered": False, "family_key": top_family or None},
        )

    # The co-query must use an anchor-family member's raw title (title words
    # are family words) — using the top hit's title would miss the anchor
    # family entirely when the top hit is cross-family noise.
    first_member = min(member_docs, key=lambda doc_id: first_pos[doc_id])
    coquery_title = title_of_doc[first_member]

    # Selection invariant: the top original hit is never dropped (it is the
    # strongest lexical signal — and may itself be the right page); new pages
    # beyond the caller's original window are only ever anchor-family pages;
    # the remaining original slots yield to family completeness because a
    # partially-read family is exactly the FAIL-014 defect. When the top-1
    # hit sits outside the anchor family (cross-family lexical noise), the
    # page budget grows by one so the family still fits in full — bounded,
    # never unbounded growth for larger families.
    selected = [original[0]]
    page_budget = max_pages + (0 if family_of_doc.get(original[0].document_id) == top_family else 1)
    seen_pages = {(original[0].document_id, original[0].page_id)}
    pages_per_doc: dict[int, int] = {original[0].document_id: 1}
    for hit in pool:
        if len(selected) >= page_budget:
            break
        doc_id = hit.document_id
        if family_of_doc[doc_id] != top_family:
            continue
        page_key = (doc_id, hit.page_id)
        if page_key in seen_pages:
            continue
        if pages_per_doc.get(doc_id, 0) >= MAX_PAGES_PER_DOC:
            continue
        selected.append(hit)
        seen_pages.add(page_key)
        pages_per_doc[doc_id] = pages_per_doc.get(doc_id, 0) + 1
    for hit in original[1:]:
        if len(selected) >= page_budget:
            break
        page_key = (hit.document_id, hit.page_id)
        if page_key in seen_pages:
            continue
        if pages_per_doc.get(hit.document_id, 0) >= MAX_PAGES_PER_DOC:
            continue
        selected.append(hit)
        seen_pages.add(page_key)
        pages_per_doc[hit.document_id] = pages_per_doc.get(hit.document_id, 0) + 1

    member_titles: list[str] = []
    for doc_id in family_of_doc:
        if family_of_doc[doc_id] == top_family:
            member_titles.append(title_of_doc[doc_id])

    selected = _append_visual(selected)
    return selected, {
        "triggered": True,
        "family_key": top_family,
        "family_titles": member_titles,
        "coquery_title": coquery_title,
        "pages_selected": len(selected),
    }


__all__ = [
    "FAMILY_POOL_LIMIT",
    "MAX_FAMILY_PAGES",
    "group_family_titles",
    "normalize_family_title",
    "select_family_expanded",
]
