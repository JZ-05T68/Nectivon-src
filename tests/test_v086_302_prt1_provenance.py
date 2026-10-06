"""V086-302-PRT1 contract: Stage-1 provenance must be user-visible.

The database already records visual_detection_status/method/at, but the real
8511 UI retest showed no surface exposed them, so「检测过但结果不确定」was
indistinguishable from「从来没跑」. This pins the shared formatter contract
that 待整理页面 / 浏览资料 / 我的资料 all reuse.
"""

from __future__ import annotations

import pytest

from src.visual_provenance import (
    format_visual_detection_provenance,
    visual_detection_method_label,
    visual_detection_provenance_fields,
)


def _state(
    *,
    status: str = "not_checked",
    method: str | None = "",
    detected_at: str | None = "",
    has_handwriting: int = 0,
    has_visual_content: int = 0,
) -> dict[str, object]:
    return {
        "has_handwriting": has_handwriting,
        "has_visual_content": has_visual_content,
        "visual_detection_status": status,
        "visual_detected_at": detected_at,
        "visual_detection_method": method,
        "interpretations": [],
    }


def test_never_checked_is_explicitly_not_checked() -> None:
    fields = visual_detection_provenance_fields(_state())
    assert fields["已检测"] == "否"
    assert fields["检测状态"] == "未检测"
    line = format_visual_detection_provenance(_state())
    assert "已检测：否" in line


def test_ran_but_uncertain_is_distinguishable_from_never_ran() -> None:
    """The exact confusion the 8511 retest reported: uniform defaults."""
    fields = visual_detection_provenance_fields(
        _state(
            status="uncertain",
            method="pymupdf_structure",
            detected_at="2026-09-25T16:09:01+00:00",
        )
    )
    assert fields["已检测"] == "是"
    assert fields["检测状态"].startswith("不确定")
    assert fields["检测方式"] == "本地结构检测"
    assert fields["检测时间"] == "2026-09-26 00:09:01"
    line = format_visual_detection_provenance(_state())
    assert "已检测：否" in line


def test_failed_detection_is_honest_not_silent() -> None:
    fields = visual_detection_provenance_fields(
        _state(
            status="uncertain",
            method="failed",
            detected_at="2026-09-25T16:09:01+00:00",
        )
    )
    assert fields["已检测"] == "是"
    assert fields["检测方式"] == "检测失败"
    assert fields["检测方式"] != "未检测"


def test_image_source_method_gets_human_label() -> None:
    fields = visual_detection_provenance_fields(
        _state(
            status="checked",
            method="image_source",
            detected_at="2026-09-25T16:09:01+00:00",
        )
    )
    assert fields["检测方式"] == "图片来源（导入时记录）"
    assert "pymupdf" not in format_visual_detection_provenance(fields)


def test_checked_status_without_timestamp_still_counts_as_ran() -> None:
    fields = visual_detection_provenance_fields(_state(status="checked"))
    assert fields["已检测"] == "是"
    assert fields["检测时间"] == "—"


def test_internal_enum_never_leaks_into_user_line() -> None:
    line = format_visual_detection_provenance(
        _state(
            status="uncertain",
            method="pymupdf_structure",
            detected_at="2026-09-25T16:09:01+00:00",
        )
    )
    assert "本地结构检测" in line
    assert "pymupdf_structure" not in line


def test_unknown_method_falls_back_to_honest_label() -> None:
    assert visual_detection_method_label("") == "未知方式"
    assert visual_detection_method_label("future_method") == "future_method"


@pytest.mark.parametrize("bad_state", [{}, {"visual_detection_status": None}])
def test_degenerate_states_do_not_raise(bad_state: dict) -> None:
    fields = visual_detection_provenance_fields(bad_state)
    assert "已检测" in fields
