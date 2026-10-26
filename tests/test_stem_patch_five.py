"""STEM patch unit tests: math input, region re-recognition parsing, goals,
candidate stem revisions and question revision history (P0-1/2/3/4/5).

No network, no AI provider, no OCR — pure local contracts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.learning_goal_service import (
    GOALS,
    LearningGoalError,
    goal_for,
    layer_guidance,
    read_goals,
    recommended_layer,
    save_goal,
)
from src.learning_workflow_service import QuestionService
from src.math_input_service import plain_math_to_latex, wrap_inline
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateStore,
)
from src.region_recognition_service import (
    RegionRecognitionError,
    parse_region_payload,
)


# --------------------------------------------------------------------- P1-3
@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("omega_n", r"\omega_{n}"),
        ("alpha_1", r"\alpha_{1}"),
        ("Delta_p", r"\Delta_{p}"),
        ("K_p", r"K_{p}"),
        ("x^2", "x^{2}"),
        ("10^-3", "10^{-3}"),
        ("3.2e-3", r"3.2\times10^{-3}"),
        ("3.2×10^-3", r"3.2\times10^{-3}"),
        ("sqrt(p1-p2)", r"\sqrt{p1-p2}"),
        ("ω_n", r"\omega _{n}".replace(" _", "_")),
        ("\\omega_n", r"\omega_{n}"),
        ("sin(wt)", r"\sin (wt)"),
        ("d/dt", r"\dfrac{d}{dt}"),
        ("(d^2x)/(dt^2)", r"\dfrac{d^{2}x}{dt^{2}}"),
        ("20 kΩ", r"20\,\mathrm{k\Omega}"),
    ],
)
def test_math_fragment_conversion(fragment: str, expected: str) -> None:
    assert plain_math_to_latex(fragment).latex == expected


def test_math_conversion_keeps_prose_untouched() -> None:
    conversion = plain_math_to_latex("system response is stable")
    assert conversion.latex == "system response is stable"
    assert "已知" in plain_math_to_latex("已知系统稳定，求 omega_n").latex


def test_math_conversion_ambiguous_subscript_offers_candidates() -> None:
    conversion = plain_math_to_latex("x_i+1")
    assert conversion.ambiguous
    assert conversion.candidates[0] == "x_{i}+1"
    assert conversion.candidates[1] == "x_{i+1}"


def test_math_conversion_pasted_latex_passthrough() -> None:
    pasted = r"$\frac{d^2x}{dt^2}$ 已核对"
    conversion = plain_math_to_latex(pasted)
    assert conversion.latex == pasted
    assert plain_math_to_latex(r"\omega_n").latex == r"\omega_{n}"


def test_wrap_inline_never_double_wraps() -> None:
    assert wrap_inline(r"\omega_n") == r"$\omega_n$"
    assert wrap_inline("$x$") == "$x$"
    assert wrap_inline("") == ""


# --------------------------------------------------------------------- P0-2
def test_region_payload_parses_candidates_and_warnings() -> None:
    payload = json.dumps(
        {
            "texts": [r"求 $\omega_n$ 的值", r"求 $\omega_n$ 的值"],
            "warnings": ["第一处 0/o 存在多义"],
        },
        ensure_ascii=False,
    )
    texts, warnings = parse_region_payload(f"```json\n{payload}\n```")
    assert texts == [r"求 $\omega_n$ 的值"]
    assert warnings == ["第一处 0/o 存在多义"]


def test_region_payload_rejects_unusable_output() -> None:
    with pytest.raises(RegionRecognitionError):
        parse_region_payload("我认为这道题的答案是……")
    with pytest.raises(RegionRecognitionError):
        parse_region_payload(json.dumps({"texts": []}))


def test_region_payload_repairs_latex_escapes() -> None:
    raw = '{"texts": ["压差 $\\\\Delta p$ 为 2 MPa"], "warnings": []}'
    texts, _ = parse_region_payload(raw)
    assert texts == [r"压差 $\Delta p$ 为 2 MPa"]


# --------------------------------------------------------------------- P1-4
def test_learning_goal_roundtrip_and_defaults(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "database" / "knowledge.db"
    assert read_goals(database_path) == {}
    save_goal(database_path, "自动控制原理", "考研重点")
    save_goal(database_path, "液压与气动", "课程达标")
    assert goal_for(database_path, "自动控制原理") == "考研重点"
    assert goal_for(database_path, "液压与气动") == "课程达标"
    assert goal_for(database_path, "未设置课程") == "系统掌握"
    assert recommended_layer("课程达标") == 1
    assert recommended_layer("系统掌握") == 2
    assert recommended_layer("考研重点") == 3
    assert "第三层" in layer_guidance("考研重点")


def test_learning_goal_rejects_invalid_values(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "database" / "knowledge.db"
    with pytest.raises(LearningGoalError):
        save_goal(database_path, "课程", "及格就行")
    with pytest.raises(LearningGoalError):
        save_goal(database_path, "  ", GOALS[0])


# --------------------------------------------------------------------- P1-5
@pytest.fixture()
def candidate_store(tmp_path: Path) -> QuestionCandidateStore:
    store = QuestionCandidateStore(tmp_path / "question-candidates")
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="3-2",
                stem="求系统的单位阶跃响应",
                completeness="complete",
                extracted_at="2026-10-24T00:00:00+00:00",
            )
        ],
    )
    return store


def test_candidate_stem_edit_records_revision(candidate_store: QuestionCandidateStore) -> None:
    candidate_store.update_candidate_at_path(
        1, "0", stem="求系统的单位阶跃响应 $u_o(t)$"
    )
    (candidate,) = candidate_store.page_candidates(1)
    assert candidate.user_edited is True
    assert len(candidate.stem_revisions) == 1
    assert "题干修改" in candidate.stem_revisions[0]["detail"]


def test_candidate_unchanged_stem_writes_no_revision(
    candidate_store: QuestionCandidateStore,
) -> None:
    candidate_store.update_candidate_at_path(1, "0", stem="求系统的单位阶跃响应")
    (candidate,) = candidate_store.page_candidates(1)
    assert candidate.stem_revisions == []


def test_apply_recognition_result_keeps_full_chain(
    candidate_store: QuestionCandidateStore,
) -> None:
    readings = [r"求 $u_{o}(t)$ 及 $t_{1}$", r"求 $u_{o}(t)$ 与 $t_{1}$"]
    candidate_store.apply_recognition_result(
        1, "0", readings[0], region_role="stem", candidates_readings=readings
    )
    (candidate,) = candidate_store.page_candidates(1)
    assert candidate.stem == readings[0]
    entry = candidate.stem_revisions[0]
    assert entry["kind"] == "region_re_recognition"
    assert readings[1] in entry["detail"]
    assert "替换前题干" in entry["detail"]


def test_question_item_revision_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "教材.jpg"
    raw.write_bytes(b"jpg")
    document = database.create_document(
        title="液压与气动",
        filename="教材.jpg",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=raw,
        extracted_text="",
        status="pending",
    )
    service = QuestionService(database)
    question = service.create_question_item(
        subject="液压与气动",
        stem_text="已知流量 q 和面积 A，求速度",
        document_id=document.id,
        page_id=1,
        question_kind="error",
    )
    service.update_question_item(
        question.id,
        stem_text="已知流量 $q$ 和面积 $A$，求速度 $v=q/A$",
        change_source="user_edit",
    )
    revisions = service.list_content_revisions(question.id)
    assert len(revisions) == 1
    assert revisions[0]["change_source"] == "user_edit"
    assert revisions[0]["old_value"].startswith("已知流量 q")
    assert "$q$" in revisions[0]["new_value"]
    service.update_question_item(question.id, stem_text=None)
    assert len(service.list_content_revisions(question.id)) == 1


def test_migration_adds_revision_table(tmp_path: Path) -> None:
    from src.migrations import SCHEMA_VERSION, migrate_database

    database_path = tmp_path / "knowledge.db"
    migrate_database(database_path)
    assert SCHEMA_VERSION >= 38
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(question_content_revisions)"
            )
        }
    assert {"question_id", "field", "old_value", "new_value", "change_source"} <= columns


# --------------------------------------------------------- P0-1 cross-page
@pytest.fixture()
def linked_pages(tmp_path: Path):
    from src.cross_page_linkage import (
        link_document_figures,
        merge_cross_page_continuations,
    )

    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "自控.jpg"
    raw.write_bytes(b"jpg")
    document = database.create_document(
        title="自动控制原理",
        filename="自控.jpg",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=2,
        import_status="completed",
    )
    page1 = database.create_page(
        document_id=document.id, page_number=1, image_path=raw,
        extracted_text="", status="pending",
    )
    page2 = database.create_page(
        document_id=document.id, page_number=2, image_path=raw,
        extracted_text="", status="pending",
    )
    store = QuestionCandidateStore(tmp_path / "question-candidates")
    store.save_page_candidates(
        page1.id,
        [
            QuestionCandidate(
                number="3-5", stem="已知系统如图，试求：", completeness="incomplete",
                incomplete_reason="下一小问在下一页", figure_refs=["图 3-55", "图 3-56"],
                extracted_at="2026-10-24T00:00:00+00:00",
            ),
            QuestionCandidate(
                number="3-6", stem="另一道题", completeness="complete",
                extracted_at="2026-10-24T00:00:00+00:00",
            ),
        ],
    )
    store.save_page_candidates(
        page2.id,
        [
            QuestionCandidate(
                number="", stem="（接上页）(2) 0.04 $\\frac{d^2c(t)}{dt^2}$ 求脉冲响应",
                completeness="complete", extracted_at="2026-10-24T00:00:00+00:00",
            ),
            QuestionCandidate(
                number="3-7", stem="新题", completeness="complete",
                extracted_at="2026-10-24T00:00:00+00:00",
            ),
        ],
    )
    store.save_page_figures(
        page2.id, [{"name": "图 3-55", "description": "题 3-10 图", "bbox": [1, 2, 3, 4]}]
    )
    merges = merge_cross_page_continuations(store, database, page2.id)
    notes = link_document_figures(store, database, document.id)
    return store, page1, page2, merges, notes


def test_continuation_merges_into_previous_incomplete_leaf(linked_pages) -> None:
    store, page1, page2, merges, _notes = linked_pages
    assert len(merges) == 1
    assert merges[0]["number"] == "3-5"
    previous = store.page_candidates(page1.id)
    assert "0.04" in previous[0].stem
    assert previous[0].completeness == "complete"
    assert previous[0].stem_revisions[-1]["kind"] == "cross_page_merge"
    remaining = store.page_candidates(page2.id)
    assert [candidate.number for candidate in remaining] == ["3-7"]


def test_cross_page_figure_link_points_to_sibling_page(linked_pages) -> None:
    store, _page1, _page2, _merges, notes = linked_pages
    assert any("图 3-55" in note and "第 2 页" in note for note in notes)
    previous = store.page_candidates(_page1.id)
    assert "图 3-55 位于本资料第 2 页" in previous[0].visual_notes
    # 图 3-56 has no registry entry anywhere: no invented note.
    assert "图 3-56" not in previous[0].visual_notes


def test_continuation_without_incomplete_target_is_kept_visible(tmp_path: Path) -> None:
    from src.cross_page_linkage import merge_cross_page_continuations

    database = Database(tmp_path / "data2" / "database" / "knowledge.db")
    (tmp_path / "data2" / "raw").mkdir(parents=True)
    raw = tmp_path / "data2" / "raw" / "m.jpg"
    raw.write_bytes(b"jpg")
    document = database.create_document(
        title="m", filename="m.jpg", source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=2, import_status="completed",
    )
    page1 = database.create_page(
        document_id=document.id, page_number=1, image_path=raw,
        extracted_text="", status="pending",
    )
    page2 = database.create_page(
        document_id=document.id, page_number=2, image_path=raw,
        extracted_text="", status="pending",
    )
    store = QuestionCandidateStore(tmp_path / "question-candidates")
    store.save_page_candidates(
        page1.id,
        [QuestionCandidate(number="1", stem="完整题", completeness="complete")],
    )
    store.save_page_candidates(
        page2.id,
        [QuestionCandidate(number="", stem="（接上页）悬空延续", completeness="complete")],
    )
    assert merge_cross_page_continuations(store, database, page2.id) == []
    assert len(store.page_candidates(page2.id)) == 1


def test_region_description_editorial_residue_is_stripped() -> None:
    from src.cross_page_linkage import sanitize_region_description

    assert sanitize_region_description("图 6-8 注：bbox 待调整，暂用文字描述") == "图 6-8"
    assert sanitize_region_description("图 3-53 题 3-7 图") == "图 3-53 题 3-7 图"


def test_parse_candidates_strips_residue_and_keeps_continuation() -> None:
    from src.question_candidate_service import parse_candidates_payload

    payload = json.dumps(
        {
            "candidates": [
                {
                    "number": "",
                    "stem": "（接上页）(2) 续问",
                    "completeness": "complete",
                    "visual_regions": [
                        {
                            "role": "stem",
                            "bbox": [10, 20, 30, 40],
                            "description": "图 6-8 注：位置待核对",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    (candidate,) = parse_candidates_payload(payload)
    assert candidate.number == ""
    assert candidate.stem.startswith("（接上页）")
    assert candidate.visual_regions[0]["description"] == "图 6-8"


def test_page_figures_registry_roundtrip(tmp_path: Path) -> None:
    store = QuestionCandidateStore(tmp_path / "figures")
    store.save_page_figures(
        9, [{"name": " 图 6-9 ", "description": "回油节流调速回路", "bbox": [1, 2, 3, 4]},
            {"name": "", "description": "无名"}, {"unexpected": True}]
    )
    registry = store.page_figures(9)
    assert [figure["name"] for figure in registry] == ["图 6-9"]
    assert store.page_figures(123) == []


def test_sibling_clip_uses_figure_gutter_but_stops_before_prose() -> None:
    from src.cross_page_linkage import clip_regions_to_siblings

    class Node:
        def __init__(self, number, regions, figure_refs=None):
            self.number, self.visual_regions = number, regions
            self.figure_refs = figure_refs or []
            self.children = []

    figure_box = [415.2, 240.0, 777.1, 454.3]
    node = Node(
        "3-7",
        [{"role": "stem", "bbox": list(figure_box), "description": "图 3-53 题 3-7 图"}],
    )
    neighbour = {"name": "图 3-52", "description": "图 3-52 题 3-2 图",
                 "bbox": [62.9, 204.3, 396.2, 454.3]}
    own = {"name": "图 3-53", "description": "图 3-53 题 3-7 图", "bbox": list(figure_box)}
    changed = clip_regions_to_siblings([node], [neighbour, own])
    clip = node.visual_regions[0]["sibling_clip"]
    assert changed == 1
    assert clip[0] == pytest.approx((396.2 + figure_box[0]) / 2)
    assert clip[2] == 1000.0  # no neighbour on the right → free growth

    # A prose column beside the figure clips too (text blocks from blocks).
    prose_node = Node(
        "6-6",
        [{"role": "shared", "bbox": [681.3, 311.4, 972.9, 607.9],
          "description": "图 6-34 题 6-6 图"}],
    )
    prose = [{"kind": "text", "bbox": [50.0, 300.0, 670.0, 620.0]}]
    clip_regions_to_siblings(
        [prose_node], [{"name": "图 6-34", "bbox": [681.3, 311.4, 972.9, 607.9]}], prose,
    )
    assert prose_node.visual_regions[0]["sibling_clip"][0] == pytest.approx(681.3)
