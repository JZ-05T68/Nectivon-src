"""Stage-1 visual detection provenance: one shared, honest presentation.

V086-302-PRT1: the database already records whether stage-1 (visual
presence detection) ran, how, when, and what it concluded — but the real
8511 UI retest showed users could not see any of it, so「检测过但结果
不确定」was indistinguishable from「从来没跑」. This module is the single
formatter every surface (待整理页面 / 浏览资料 / 我的资料) reuses; no page
may assemble its own provenance strings.

Presentation contract (V086-302-PRT1 §7.3):

* ``not_checked`` with no detection timestamp → the detector never ran;
* ``method == "failed"`` → honestly shown as 检测失败, never as 未检测;
* ``uncertain`` → 检测过，结果不确定（扫描件的诚实输出）;
* internal enums (``pymupdf_structure``) are shown as human labels;
  the raw enum stays available for debug surfaces.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.page_visual_service import PageVisualService

#: Human-readable labels for the recorded detection methods.
METHOD_LABELS: Mapping[str, str] = {
    "pymupdf_structure": "本地结构检测",
    "image_source": "图片来源（导入时记录）",
    "failed": "检测失败",
}

#: Human-readable labels for the recorded detection statuses.
STATUS_LABELS: Mapping[str, str] = {
    "not_checked": "未检测",
    "checked": "已检测",
    "uncertain": "不确定（以实际读取为准）",
}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def visual_detection_method_label(method: object) -> str:
    """Human-readable label for one detection method value."""

    normalized = _clean_text(method)
    if not normalized:
        return "未知方式"
    return METHOD_LABELS.get(normalized, normalized)


def visual_detection_status_label(status: object) -> str:
    """Human-readable label for one detection status value."""

    normalized = _clean_text(status)
    return STATUS_LABELS.get(normalized, normalized or "未知状态")


def visual_detection_provenance_fields(
    state: Mapping[str, object],
) -> dict[str, str]:
    """Labeled provenance fields: 已检测 / 检测状态 / 检测方式 / 检测时间.

    The three-way distinction the UI retest demanded — never ran vs ran-but-
    uncertain vs ran-but-failed — is decided here, once, from the recorded
    row values. ``state`` is the dict returned by
    :meth:`PageVisualService.get_page_visual_state` (or an equivalent row
    mapping); unknown shapes degrade to honest「未知」labels instead of
    raising.
    """

    status = _clean_text(state.get("visual_detection_status")) or "not_checked"
    method = _clean_text(state.get("visual_detection_method"))
    detected_at = _clean_text(state.get("visual_detected_at"))
    ran = bool(detected_at) or status in {"checked", "uncertain"} or bool(method)
    fields: dict[str, str] = {
        "已检测": "是" if ran else "否",
        "检测状态": visual_detection_status_label(status),
    }
    if not ran:
        fields["检测方式"] = "—"
        fields["检测时间"] = "—"
        return fields
    if method == "failed":
        # Honest failure: attempted, not trustworthy, never "未检测".
        fields["检测方式"] = "检测失败"
    else:
        fields["检测方式"] = visual_detection_method_label(method)
    fields["检测时间"] = detected_at[:19].replace("T", " ") if detected_at else "—"
    return fields


def format_visual_detection_provenance(state: Mapping[str, object]) -> str:
    """One-line provenance summary, e.g.

    ``视觉预检：已检测 · 状态：不确定（以实际读取为准） · 方式：本地结构检测 ·
    时间：2026-09-25 16:09:01``
    """

    fields = visual_detection_provenance_fields(state)
    return "视觉预检：" + " · ".join(
        f"{key}：{value}" for key, value in fields.items()
    )


def load_stage1_state(database: object, page_id: int) -> dict[str, object] | None:
    """Fail-safe stage-1 state read for display surfaces.

    Returns ``None`` when the state cannot be read (missing columns in an
    older database, locked file, ...) — callers then simply skip the
    provenance line instead of breaking the page. Read-only: this never
    triggers detection.
    """

    try:
        service = PageVisualService(database, minimum_text_length=0)  # type: ignore[arg-type]
        return service.get_page_visual_state(page_id)
    except Exception:  # noqa: BLE001 - display must never break the page
        return None


__all__ = [
    "METHOD_LABELS",
    "STATUS_LABELS",
    "format_visual_detection_provenance",
    "load_stage1_state",
    "visual_detection_method_label",
    "visual_detection_provenance_fields",
    "visual_detection_status_label",
]
