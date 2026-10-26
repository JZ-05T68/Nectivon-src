"""Global figure index across every imported photo (round-2 issue 2).

A question's 原理图 often lives in the textbook body, photographed as a
separate page in a separate import batch.  This module keeps one global,
deterministic index — {figure name → where it physically lives} — updated
whenever a page is read, and hands back cross-page "virtual regions" so any
question view can crop and display the figure from ITS page.  No adjacency
assumption, no per-subject rules; one figure may serve many questions.

Storage is application metadata (``data/figures-index.json`` beside the
candidate store), never user material.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)

INDEX_FILENAME = "figures-index.json"
_MAX_DESCRIPTION = 200


def _figure_key(value: str) -> str:
    return re.sub(r"[\s，。、．.·()（）]", "", str(value or "")).lower()


def index_path(store_root: Path | str) -> Path:
    return Path(store_root) / INDEX_FILENAME


def read_index(store_root: Path | str) -> dict[str, list[dict]]:
    path = index_path(store_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("全局图形索引无法读取，按空索引处理：%s", path)
        return {}
    figures = payload.get("figures") if isinstance(payload, dict) else None
    if not isinstance(figures, dict):
        return {}
    cleaned: dict[str, list[dict]] = {}
    for key, entries in figures.items():
        if isinstance(key, str) and isinstance(entries, list):
            cleaned[key] = [e for e in entries if isinstance(e, dict)]
    return cleaned


def _write_index(store_root: Path | str, figures: dict[str, list[dict]]) -> None:
    path = index_path(store_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"figures": figures}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    temporary.replace(path)


def update_page_figures(
    store_root: Path | str,
    *,
    document_id: int,
    page_id: int,
    page_number: int,
    document_title: str,
    figures: list[dict],
) -> int:
    """Refresh one page's entries in the global index; returns entry count."""

    index = read_index(store_root)
    for key, entries in list(index.items()):
        index[key] = [e for e in entries if e.get("page_id") != page_id]
        if not index[key]:
            del index[key]
    added = 0
    for figure in figures or []:
        name = str(figure.get("name", "")).strip()
        if not name:
            continue
        bbox = figure.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        key = _figure_key(name)
        index.setdefault(key, []).append(
            {
                "name": name[:80],
                "description": str(figure.get("description", ""))[:_MAX_DESCRIPTION],
                "document_id": document_id,
                "page_id": page_id,
                "page_number": page_number,
                "document_title": str(document_title)[:120],
                "bbox": [float(v) for v in bbox],
            }
        )
        added += 1
    _write_index(store_root, index)
    return added


def find_figures(store_root: Path | str, reference: str) -> list[dict]:
    """Entries whose indexed name matches a figure reference like 图 6-8a."""

    key = _figure_key(reference)
    if not key:
        return []
    index = read_index(store_root)
    exact = index.get(key)
    if exact:
        return list(exact)
    hits: list[dict] = []
    for name_key, entries in index.items():
        if key in name_key or name_key in key:
            hits.extend(entries)
    return hits


def list_all_figures(store_root: Path | str) -> list[dict]:
    """Flat list for the manual picker, ordered by document/page."""

    flat = [
        {**entry, "key": key}
        for key, entries in read_index(store_root).items()
        for entry in entries
    ]
    return sorted(
        flat, key=lambda e: (str(e.get("document_title")), e.get("page_number", 0))
    )


def virtual_region(entry: dict, *, description: str = "") -> dict:
    """Build a cross-page region dict that crops from the figure's own page."""

    label = description or str(entry.get("name", ""))
    source = str(entry.get("document_title", ""))
    page_number = entry.get("page_number", "?")
    return {
        "role": "shared",
        "bbox": [float(v) for v in entry.get("bbox", [0, 0, 1000, 1000])],
        "description": f"{label}（来自《{source}》第 {page_number} 页照片）",
        "source_page_id": int(entry.get("page_id", 0)),
        "source_document_id": int(entry.get("document_id", 0)),
        "source_label": f"《{source}》第 {page_number} 页",
    }


def refresh_automatic_figure_clips(
    store_root: Path, regions: list[dict], *, page_id: int,
) -> list[dict]:
    """Recompute old automatic hard stops from the actual source page registry.

    This is a read-only compatibility repair for already imported figures;
    saved human pixel boxes are never expanded. No recognition is repeated.
    """

    from types import SimpleNamespace

    from src.cross_page_linkage import clip_regions_to_siblings

    result = []
    for supplied in regions:
        region = dict(supplied)
        if region.get("manual_bounds") or region.get("region_id"):
            result.append(region)
            continue
        physical_page = region.get("source_page_id", region.get("page_id", page_id))
        path = store_root / f"figures_page_{physical_page}.json"
        if region.get("role") in ("stem", "shared") and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("page_id") != physical_page:
                    raise ValueError("Figure registry source mismatch")
                figures = payload["figures"]
                if not isinstance(figures, list):
                    raise ValueError("Invalid figure registry")
                region.pop("sibling_clip", None)
                node = SimpleNamespace(visual_regions=[region], figure_refs=[], children=[])
                clip_regions_to_siblings([node], figures)
            except (OSError, ValueError, KeyError, TypeError):
                LOGGER.exception("无法核对图形间距，保留已记录边界：page=%s", physical_page)
                region = dict(supplied)
        result.append(region)
    return result
