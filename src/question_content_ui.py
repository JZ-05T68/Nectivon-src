"""The same question/option figures in review, learning and teach-back views."""

from __future__ import annotations

import io
import re
from pathlib import Path

import streamlit as st
from PIL import Image

from src.math_display import format_multiple_choice_lines, render_question_math_markdown
from src.question_visual_regions import crop_region, normalize_regions


def question_regions(
    image_path: Path | str | None, number: str, regions: list[dict], *, page_id: int,
    committed_only: bool = False,
) -> list[dict]:
    """Use committed per-question crops consistently in every question view."""

    from src.question_region_store import resolve_question_regions

    return resolve_question_regions(
        image_path, number, _coalesce_overlapping_figures(regions), page_id=page_id,
        committed_only=committed_only,
    )


def render_region_images(
    image_path: Path | str | None, regions: list[dict],
) -> bool:
    """Render the same original/manually revised display crops in every view."""

    shown = False
    for region in _coalesce_overlapping_figures(regions):
        data = crop_region(image_path, region) if image_path is not None else None
        if data is None:
            st.warning("这处图像暂时无法裁切，请对照原页核对。")
            continue
        with Image.open(io.BytesIO(data)) as image:
            width = min(650, max(260, image.width))
        st.image(data, width=width)
        shown = True
    return shown


def _coalesce_overlapping_figures(regions: list[dict]) -> list[dict]:
    """Display overlapping crops of one figure group once; keep options separate."""

    merged: list[dict] = []
    for supplied in normalize_regions(regions):
        region = dict(supplied)
        changed = True
        while changed and region["role"] in ("stem", "shared"):
            changed = False
            for earlier in merged:
                if (earlier["role"] != region["role"]
                        or earlier.get("region_id") or region.get("region_id")
                        or earlier.get("page_id") != region.get("page_id")
                        or earlier.get("image_sha256") != region.get("image_sha256")):
                    continue
                a, b = earlier["bbox"], region["bbox"]
                intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                    0, min(a[3], b[3]) - max(a[1], b[1])
                )
                area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if intersection <= area * .2:
                    continue
                region["bbox"] = [min(a[0], b[0]), min(a[1], b[1]),
                                  max(a[2], b[2]), max(a[3], b[3])]
                merged.remove(earlier)
                changed = True
                break
        merged.append(region)
    return merged


def render_question_content(
    text: str, *, image_path: Path | str | None = None, regions: list[dict] | None = None,
    math_notation: bool = True,
) -> bool:
    """Place each original option diagram immediately below its own label/text."""

    regions = normalize_regions(regions or [])
    render_text = render_question_math_markdown if math_notation else st.markdown
    if not regions:
        render_text(text)
        return False
    parts = re.split(r"(?m)^(?=[A-Z][.．、]\s)", format_multiple_choice_lines(text))
    option_parts: list[tuple[str, str]] = []
    stem_parts: list[str] = []
    for part in parts:
        match = re.match(r"^([A-Z])[.．、]\s", part)
        if match:
            option_parts.append((match[1], part.strip()))
        elif part.strip():
            stem_parts.append(part.strip())
    if stem_parts:
        render_text("\n\n".join(stem_parts))
    shown = render_region_images(image_path, [r for r in regions if r["role"] == "stem"])
    for label, part in option_parts:
        render_text(part)
        shown = render_region_images(
            image_path, [r for r in regions
                         if r["role"] == "option" and r["option_label"] == label],
        ) or shown
    present_labels = {label for label, _ in option_parts}
    for label in sorted({r["option_label"] for r in regions if r["role"] == "option"}
                        - present_labels):
        # Editing only the sentence must not silently discard the original
        # graphical options, which are stored separately from editable text.
        render_text(f"{label}. （见原图）")
        shown = render_region_images(
            image_path, [r for r in regions
                         if r["role"] == "option" and r["option_label"] == label],
        ) or shown
    # A whole-question crop is an honest fallback if an individual option box
    # was missing/invalid. It keeps the graph visible without invented geometry.
    shown = render_region_images(
        image_path, [r for r in regions if r["role"] == "question"],
    ) or shown
    return shown


def render_training_question(question) -> None:
    """Resolve a verified local question's figures by its exact organized item."""

    from src.learning_workflow_service import QuestionService
    from src.runtime import application_database

    if question.question_item_id is None or question.page_id is None:
        render_question_content(question.question_text)
        return
    database = application_database()
    try:
        item = QuestionService(database).get_question_item(question.question_item_id)
        page = database.get_page(question.page_id)
    except (ValueError, RuntimeError):
        render_question_content(question.question_text)
        st.warning("原题图像来源暂时不可用，请通过题目来源核对。")
        return
    if (page is None or item.page_id != question.page_id
            or item.document_id != question.document_id or not item.source_available):
        render_question_content(question.question_text)
        return
    material = (item.ai_draft or {}).get("visual_material", {})
    if not isinstance(material, dict) or material.get("dependency") == "none":
        render_question_content(question.question_text)
        return
    regions = [r for r in normalize_regions(material.get("regions", []))
               if r.get("page_id", page.id) == page.id]
    regions = question_regions(page.image_path, item.question_number, regions, page_id=page.id)
    shared_shown = render_region_images(
        page.image_path, [r for r in regions if r["role"] == "shared"],
    )
    shown = render_question_content(
        question.question_text, image_path=page.image_path, regions=regions,
    ) or shared_shown
    if not shown and material.get("dependency") in ("required", "uncertain"):
        st.caption("本题截图尚待核对，可在下方原始大图上修改截图方框。")
    from src.question_image_editor import render_question_crop_editor, render_question_image_editor

    render_question_image_editor(
        page.image_path, page_id=page.id,
        number=item.question_number, regions=regions,
        key=f"training_image_{getattr(question, 'id', question.question_item_id)}",
    )
    render_question_crop_editor(
        page.image_path, page_id=page.id, number=item.question_number, regions=regions,
        key=f"training_crop_{getattr(question, 'id', question.question_item_id)}",
    )
