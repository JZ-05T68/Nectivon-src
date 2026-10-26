"""The same question/option figures in review, learning and teach-back views."""

from __future__ import annotations

import io
import re
from pathlib import Path

import streamlit as st

from src.figure_scan_service import looks_like_text_block
from src.math_display import format_multiple_choice_lines, render_question_math_markdown
from src.question_visual_regions import crop_region, normalize_regions


def question_regions(
    image_path: Path | str | None, number: str, regions: list[dict], *, page_id: int,
    committed_only: bool = False,
) -> list[dict]:
    """Use committed per-question crops consistently in every question view."""

    from src.question_region_store import resolve_question_regions

    resolved = resolve_question_regions(
        image_path, number, _coalesce_overlapping_figures(regions), page_id=page_id,
        committed_only=committed_only,
    )
    if image_path is not None and len(Path(image_path).resolve().parents) >= 3:
        from src.figure_index_service import refresh_automatic_figure_clips

        metadata = Path(image_path).resolve().parents[2] / "question-candidates"
        resolved = refresh_automatic_figure_clips(metadata, resolved, page_id=page_id)
    return resolved


def _region_image_path(image_path: Path | str | None, region: dict) -> Path | str | None:
    """Cross-page figures crop from the photo that physically holds them."""

    if "source_page_id" not in region:
        return image_path
    try:
        from src.question_figure_service import resolve_figure_source
        from src.runtime import application_database, application_pages_dir

        return resolve_figure_source(
            application_database(), application_pages_dir(), int(region["source_page_id"]), region,
        ).path
    except (OSError, ValueError, RuntimeError):
        return None


def _text_guard_probe(source_path, region: dict) -> bytes | None:
    """Raw-bbox crop for the text-block guard.

    The display crop goes through ink-boundary completion, which can pull
    in neighbouring handwriting/figures and break the line statistics; the
    guard judges the region the model actually proposed.
    """

    from PIL import Image as _Image

    try:
        with _Image.open(source_path) as image:
            width, height = image.size
            x1, y1, x2, y2 = region["bbox"]
            box = (
                max(0, int(x1 / 1000 * width)), max(0, int(y1 / 1000 * height)),
                min(width, int(x2 / 1000 * width)), min(height, int(y2 / 1000 * height)),
            )
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                return None
            crop = image.crop(box)
            buffer = io.BytesIO()
            crop.convert("RGB").save(buffer, format="PNG")
            return buffer.getvalue()
    except (OSError, KeyError, TypeError, ValueError):
        return None


def render_region_images(
    image_path: Path | str | None, regions: list[dict],
) -> bool:
    """Render one figure region with two on-demand derived views (round 4.1).

    Each figure shows two collapsed toggles — 「AI复刻图」(structured replica)
    and 「黑白清稿图」(round-3 binary cleanup).  Expanding is a pure local
    read: no cloud call, no silent generation; a missing artifact shows an
    explicit notice.  Both views derive from the SAME content-addressed crop
    pixels, so a re-saved source/box automatically yields fresh images and
    stale ones are never shown.  The raw photograph stays behind 「修改图片」.
    """

    import hashlib

    from src.figure_replica_service import load_replica, replica_png
    from src.figure_scan_service import enhanced_crop

    shown = False
    hidden_text_blocks = 0
    figures_present = any(
        region.get("role") in ("stem", "shared") for region in regions
    )
    for region in _coalesce_overlapping_figures(regions):
        source_path = _region_image_path(image_path, region)
        data = crop_region(source_path, region) if source_path is not None else None
        if data is None:
            st.warning("这处图像暂时无法裁切，请对照原页核对。")
            continue
        role = region.get("role")
        # A whole-question crop is only an honest fallback while no real
        # figure region exists; and even then it must not BE prose.
        if role == "question" and figures_present:
            continue
        if role in ("stem", "shared", "question"):
            probe = _text_guard_probe(source_path, region)
            if probe is not None and looks_like_text_block(probe):
                hidden_text_blocks += 1
                continue
        caption = str(region.get("source_label") or "").strip() or None
        digest = hashlib.sha256(data).hexdigest()[:16]
        replica = load_replica(source_path, data)
        status = replica.get("status") if replica else None

        # Native expanders: collapsed by default, independently toggleable,
        # auto-keyed (the same figure may render in two views in one run —
        # explicit keys would collide and crash the page).
        left, right = st.columns(2)
        show_replica = left.expander("AI复刻图", expanded=False)
        show_clean = right.expander("黑白清稿图", expanded=False)
        with show_replica:
            if status in ("verified", "accepted", "failed_visual"):
                png = replica_png(source_path, data)
                if png:
                    label = {
                        "verified": "教材插图复刻（已通过自动结构核验）",
                        "accepted": "教材插图复刻（你已人工核验通过）",
                        "failed_visual": "教材插图复刻（待人工核验，注意下方差异）",
                    }[status]
                    st.image(png, width="stretch", caption=caption or label)
                    if caption:
                        st.caption(label)
                    if status == "failed_visual":
                        verification = replica.get("verification") or {}
                        for field, name in (("missing", "缺失"), ("extra", "多余"),
                                            ("wrong", "差异"), ("text_errors", "文字")):
                            for item in verification.get(field, []):
                                st.caption(f"{name}: {item}")
                else:
                    st.warning("复刻记录存在但图片文件缺失，请重新生成。")
            elif status == "unsupported":
                st.info("该插图属于照片类或复杂图像，不适合结构化复刻；未生成复刻图。")
            elif status == "failed_parse":
                st.info("上次复刻生成失败（模型输出无效）；可点「📐 生成复刻图」重试。")
            else:
                st.info(
                    "尚未生成复刻图；点「✏️ 修改图片」旁的「📐 生成复刻图」生成"
                    "（展开查看不会自动触发）。"
                )
        with show_clean:
            try:
                clean = enhanced_crop(data, source_key=digest)
            except (OSError, ValueError) as exc:
                st.warning(
                    f"黑白清稿图生成失败：{exc}。可点「✏️ 修改图片」核对原始照片。"
                )
            else:
                st.image(
                    clean, width="stretch",
                    caption=(caption + " · 白底黑线清稿") if caption else "白底黑线清稿",
                )
        shown = True
    if hidden_text_blocks:
        st.info(
            f"已隐藏 {hidden_text_blocks} 处被判定为题干文字的区域（未作为原理图显示）。"
            "如需修正图片范围，请用「修改图片」或「查看原始扫描大图／修改截图方框」。"
        )
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
                        or earlier.get("source_page_id") != region.get("source_page_id")
                        or earlier.get("source_document_id") != region.get("source_document_id")
                        or earlier.get("manual_bounds") or region.get("manual_bounds")
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
        image_path, [r for r in regions if r["role"] == "question"
                     and not any(x["role"] in ("stem", "shared") for x in regions)],
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
