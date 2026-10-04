"""Handwriting presence gate tests (human-review fix round 2026-09-27).

Covers the §18/§19/§20 fix contract:

1. The handwriting reading prompt requires a first-line
   ``HANDWRITING_PRESENCE`` declaration and per-item position pointers.
2. Presence declarations parse out of model output; undeclared output
   degrades conservatively (never to confirmed).
3. The cross-channel consistency check flags a handwriting ``confirmed``
   claim against an image-region ``none`` observation.
4. The learning-entry source resolver refuses handwriting readings unless
   the model declared ``confirmed`` or the user saved the reading.

No network and no AI calls anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.learning_entry_ui import (
    _page_has_unconfirmed_visual_draft,
    _resolve_page_source,
)
from src.page_visual_service import PageVisualService
from src.visual_reading_ui import (
    _INTERPRET_VISUAL_PROMPT,
    _READ_HANDWRITING_PROMPT,
    _presence_badge,
    _split_presence_declaration,
)


# --------------------------------------------------------------------------
# prompt contract
# --------------------------------------------------------------------------
def test_handwriting_prompt_requires_presence_declaration_first() -> None:
    assert "HANDWRITING_PRESENCE:" in _READ_HANDWRITING_PROMPT
    assert "none" in _READ_HANDWRITING_PROMPT
    assert "possible" in _READ_HANDWRITING_PROMPT
    assert "confirmed" in _READ_HANDWRITING_PROMPT
    # §19: position pointers are required for confirmed transcriptions.
    assert "位置" in _READ_HANDWRITING_PROMPT
    # The prompt must not unconditionally ask for transcription anymore.
    assert "读取手写内容（解题过程" not in _READ_HANDWRITING_PROMPT


def test_image_region_prompt_declares_presence_seen_line() -> None:
    assert "HANDWRITING_PRESENCE_SEEN:" in _INTERPRET_VISUAL_PROMPT


# --------------------------------------------------------------------------
# declaration parsing
# --------------------------------------------------------------------------
def test_split_presence_extracts_level_and_body() -> None:
    content = (
        "HANDWRITING_PRESENCE: none\n\n"
        "页面主体为印刷教材页，未发现任何手写演算或批注。"
    )
    level, body = _split_presence_declaration(
        content, __import__("re").compile(
            r"(?im)^\s*HANDWRITING_PRESENCE\s*[:：]\s*(none|possible|confirmed)\s*$"
        )
    )
    assert level == "none"
    assert "HANDWRITING_PRESENCE" not in body
    assert "印刷教材页" in body


def test_split_presence_undeclared_returns_none_level() -> None:
    level, body = _split_presence_declaration(
        "（手写内容，按出现顺序整理如下）：\n- 手写：i_C",
        __import__("re").compile(r"(?im)^\s*HANDWRITING_PRESENCE\s*[:：]\s*(none|possible|confirmed)\s*$"),
    )
    assert level is None
    assert body.startswith("（手写内容")


def test_presence_badge_never_invents_confirmation() -> None:
    assert _presence_badge("none") != _presence_badge("confirmed")
    assert "需人工核对" in _presence_badge(None)


# --------------------------------------------------------------------------
# service-level cross-channel consistency
# --------------------------------------------------------------------------
@pytest.fixture()
def visual_workspace(tmp_path: Path) -> PageVisualService:
    database = PageVisualService.__new__(PageVisualService)
    from src.database import Database

    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    db.create_document(
        title="一致性夹具",
        filename="fixture.pdf",
        source_path=tmp_path / "data" / "raw" / "fixture.pdf",
        sha256="0" * 64,
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=tmp_path / "data" / "pages" / "1" / "page-1.png",
        extracted_text="printed",
        status="ready",
    )
    database._database = db
    return database


def test_consistency_flags_confirmed_vs_none(visual_workspace) -> None:
    service = visual_workspace
    service.record_interpretation(
        1,
        provenance="IMAGE_REGION",
        content="所有标注均为可见文本",
        region_json={"handwriting_presence": "none"},
    )
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="（手写内容）：i_C …",
        region_json={"handwriting_presence": "confirmed"},
    )
    result = service.check_handwriting_consistency(1)
    assert result["conflict"] is True
    assert result["handwriting_presence"] == "confirmed"
    assert result["image_region_presence"] == "none"


def test_consistency_quiet_when_channels_agree(visual_workspace) -> None:
    service = visual_workspace
    service.record_interpretation(
        1,
        provenance="IMAGE_REGION",
        content="印刷元素描述",
        region_json={"handwriting_presence": "none"},
    )
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="页面为纯印刷内容，未发现手写。",
        region_json={"handwriting_presence": "none"},
    )
    result = service.check_handwriting_consistency(1)
    assert result["conflict"] is False


def test_consistency_missing_declarations_reported_not_invented(
    visual_workspace,
) -> None:
    service = visual_workspace
    service.record_interpretation(1, provenance="IMAGE_REGION", content="x")
    result = service.check_handwriting_consistency(1)
    assert result["handwriting_presence"] is None
    assert result["image_region_presence"] is None
    assert result["conflict"] is False


# --------------------------------------------------------------------------
# learning-entry source gate
# --------------------------------------------------------------------------
@pytest.fixture()
def page_with_visual(tmp_path: Path) -> tuple[object, PageVisualService]:
    from src.database import Database

    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    db.create_document(
        title="来源门夹具",
        filename="fixture.pdf",
        source_path=tmp_path / "data" / "raw" / "fixture.pdf",
        sha256="1" * 64,
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=tmp_path / "data" / "pages" / "1" / "page-1.png",
        extracted_text="printed stem 2-1",
        status="ready",
    )
    page = db.get_page(1)
    assert page is not None
    return page, PageVisualService(db)


def test_none_reading_is_never_a_source(page_with_visual) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="未检测到可辨识的手写内容。",
        region_json={"handwriting_presence": "none"},
    )
    content, label, extra = _resolve_page_source(page, visual_service=service)
    # Falls through to the next available source (OCR/extracted text).
    assert label != "AI 视觉识别草稿"
    assert extra.get("origin") != "stage2_visual_draft"


def test_legacy_undeclared_reading_is_not_a_source(page_with_visual) -> None:
    """A pre-fix reading without a presence declaration must not flow in."""

    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="（手写内容，按出现顺序整理如下）：\n- 手写：i_C",
    )
    content, label, extra = _resolve_page_source(page, visual_service=service)
    assert extra.get("origin") != "stage2_visual_draft"


def test_confirmed_reading_is_a_source_with_pointer(page_with_visual) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="- 手写：u_i = i_1 R_1 + u_o（位置：图(a) 下方）",
        region_json={"handwriting_presence": "confirmed"},
    )
    content, label, extra = _resolve_page_source(page, visual_service=service)
    assert extra.get("origin") == "stage2_visual_draft"
    assert extra.get("handwriting_presence") == "confirmed"
    assert "已判定存在手写" in label
    assert "u_i" in content


def test_user_saved_reading_overrides_missing_declaration(
    page_with_visual,
) -> None:
    page, service = page_with_visual
    reading_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="用户核对后的手写转录",
    )
    service.update_interpretation(reading_id, content="用户核对后的手写转录 v2")
    content, label, extra = _resolve_page_source(page, visual_service=service)
    assert extra.get("origin") == "stage2_visual_draft"
    assert extra.get("user_confirmed") is True
    assert "已保存确认" in label
    assert "v2" in content


def test_possible_reading_is_skipped_until_user_confirms(
    page_with_visual,
) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="疑似手写【待核对】",
        region_json={"handwriting_presence": "possible"},
    )
    content, label, extra = _resolve_page_source(page, visual_service=service)
    assert extra.get("origin") != "stage2_visual_draft"


def test_region_json_roundtrip_keeps_presence(page_with_visual) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="x",
        region_json={"handwriting_presence": "possible"},
    )
    state = service.get_page_visual_state(1)
    raw = state["interpretations"][0]["region_json"]
    assert json.loads(raw)["handwriting_presence"] == "possible"


# --------------------------------------------------------------------------
# unconfirmed-draft hint (redteam R2-02: explain the OCR fallback)
# --------------------------------------------------------------------------
def test_unconfirmed_draft_hint_true_for_gate_skipped_reading(
    page_with_visual,
) -> None:
    """A possible-presence draft is skipped by the gate → hint must fire."""

    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="疑似手写【待核对】",
        region_json={"handwriting_presence": "possible"},
    )
    assert _page_has_unconfirmed_visual_draft(page, visual_service=service)


def test_unconfirmed_draft_hint_true_for_undeclared_reading(
    page_with_visual,
) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="（手写内容，按出现顺序整理如下）",
    )
    assert _page_has_unconfirmed_visual_draft(page, visual_service=service)


def test_unconfirmed_draft_hint_false_when_user_saved(page_with_visual) -> None:
    page, service = page_with_visual
    reading_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="用户核对后的手写转录",
    )
    service.update_interpretation(reading_id, content="用户核对后的手写转录 v2")
    assert not _page_has_unconfirmed_visual_draft(page, visual_service=service)


def test_unconfirmed_draft_hint_false_when_presence_confirmed(
    page_with_visual,
) -> None:
    page, service = page_with_visual
    service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="- 手写：u_i = i_1 R_1",
        region_json={"handwriting_presence": "confirmed"},
    )
    assert not _page_has_unconfirmed_visual_draft(page, visual_service=service)


def test_unconfirmed_draft_hint_false_without_drafts(page_with_visual) -> None:
    page, _service = page_with_visual
    assert not _page_has_unconfirmed_visual_draft(page, visual_service=_service)

    class _NoDrafts:
        def get_page_visual_state(self, _page_id):
            return {"interpretations": []}

    assert not _page_has_unconfirmed_visual_draft(
        page, visual_service=_NoDrafts()
    )

    class _Boom:
        def get_page_visual_state(self, _page_id):
            raise RuntimeError("db down")

    assert not _page_has_unconfirmed_visual_draft(page, visual_service=_Boom())
