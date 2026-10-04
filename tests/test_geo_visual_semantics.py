"""Geography G2-A: visual semantics — completeness split, binding, layer-1 material."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateError,
    QuestionCandidateStore,
    parse_candidates_payload,
    stem_confidence_for,
)


def _store(tmp_path: Path) -> QuestionCandidateStore:
    return QuestionCandidateStore(tmp_path / "question-candidates")


# ------------------------------------------------- text vs visual semantics
def test_text_complete_with_required_visual_is_not_incomplete() -> None:
    """G1-GEO-02 core rule: 需结合图表 ≠ 题干不完整."""

    raw = json.dumps(
        {
            "candidates": [
                {
                    "number": "2",
                    "stem": "人口分布状况一直比较稳定，其主导因素是 "
                    "A.自然环境 B.经济格局 C.河流分布 D.国家政策",
                    "completeness": "complete",
                    "visual_dependency": "required",
                    "visual_notes": "共享材料：江苏省各区域人口密度分布示意图",
                    "figure_refs": [],
                }
            ]
        },
        ensure_ascii=False,
    )
    candidates = parse_candidates_payload(raw)
    assert candidates[0].completeness == "complete"
    assert candidates[0].visual_dependency == "required"
    assert candidates[0].needs_visual


def test_pure_text_question_has_no_visual() -> None:
    raw = json.dumps(
        {
            "candidates": [
                {
                    "number": "6",
                    "stem": "缓解人口分布不均的合理措施是 A.放开生育政策 B.完善交通网络",
                    "completeness": "complete",
                    "visual_dependency": "none",
                    "figure_refs": [],
                }
            ]
        },
        ensure_ascii=False,
    )
    candidates = parse_candidates_payload(raw)
    assert candidates[0].visual_dependency == "none"
    assert not candidates[0].needs_visual


def test_legacy_misjudgement_reason_becomes_dependency(tmp_path: Path) -> None:
    """Stored G1 records with 「题干依赖的地图…未完整提供」 heal on read."""

    store = _store(tmp_path)
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="1",
                stem="甲地区人口密度低的主要原因是 A.地势较高 B.台风多发 C.滩涂广布 D.淡水短缺",
                completeness="incomplete",
                incomplete_reason="题干依赖的地图（江苏省各区域人口密度分布示意图）未完整提供，无法确定甲地区位置",
            )
        ],
    )
    healed = store.page_candidates(1)[0]
    assert healed.completeness == "complete"
    assert healed.incomplete_reason == ""
    assert healed.visual_dependency == "required"
    assert "江苏省各区域人口密度分布示意图" in healed.visual_notes
    assert healed.binding_confirmed is False  # AI 判断永不自动升级为用户确认


def test_genuinely_truncated_text_stays_incomplete() -> None:
    raw = json.dumps(
        {
            "candidates": [
                {
                    "number": "9",
                    "stem": "读图回答",
                    "completeness": "incomplete",
                    "incomplete_reason": "题干未拍全，只有图 2-74",
                    "visual_dependency": "required",
                    "figure_refs": ["图 2-74"],
                }
            ]
        },
        ensure_ascii=False,
    )
    candidates = parse_candidates_payload(raw)
    assert candidates[0].completeness == "incomplete"
    assert candidates[0].visual_dependency == "required"


def test_stem_confidence_ignores_visual_dependency() -> None:
    """stem_confidence 只反映文字把握；需结合图表的完整文字题是 probable."""

    assert stem_confidence_for("complete") == "probable"
    assert stem_confidence_for("incomplete") == "uncertain"


# ------------------------------------------------- binding provenance
def test_confirm_visual_binding_is_user_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="2",
                stem="题干",
                completeness="complete",
                visual_dependency="required",
                visual_notes="共享材料：示意图",
            )
        ],
    )
    assert store.page_candidates(1)[0].binding_confirmed is False
    store.confirm_visual_binding(1, "2")
    healed = store.page_candidates(1)[0]
    assert healed.binding_confirmed is True
    assert healed.visual_dependency == "required"


def test_confirm_visual_binding_missing_number_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(QuestionCandidateError):
        store.confirm_visual_binding(1, "99")


# ------------------------------------------------- shared material lifecycle
def test_shared_material_survives_one_reference_delete(tmp_path: Path) -> None:
    """G2-A §20: 删除 Q2 的引用不删除共享图——Q1/Q3 仍引用同一材料说明。"""

    store = _store(tmp_path)
    material = "共享材料：江苏省各区域人口密度分布示意图（第1～3题共用）"
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(number=n, stem=f"第{n}题", completeness="complete",
                              visual_dependency="required", visual_notes=material)
            for n in ("1", "2", "3")
        ],
    )
    # Q2 被删除（删除候选记录中的引用语义：加入库后由题目删除承担）
    store.mark_status(1, "2", "ignored")
    remaining = store.page_candidates(1)
    kept = [c for c in remaining if c.status == "pending"]
    assert [c.number for c in kept] == ["1", "3"]
    # 共享材料描述仍在两题上（引用同一份说明，无复制图片字节）
    assert all(c.visual_notes == material for c in kept)


def test_visual_material_block_references_page_not_copy() -> None:
    """visual_material 只带 page 引用与说明，绝不含图像字节/复制路径。"""

    from types import SimpleNamespace

    from src.learning_entry_ui import _visual_material_block

    page = SimpleNamespace(id=579)
    candidate = QuestionCandidate(
        number="2", stem="s", completeness="complete",
        visual_dependency="required",
        visual_notes="共享材料：示意图（第1～3题共用）",
        binding_confirmed=True,
    )
    block = _visual_material_block(candidate, page)
    assert block["source_page_id"] == 579
    assert block["binding_confirmed"] is True
    assert block["binding_provenance"] == "USER CONFIRMED BINDING"
    raw = json.dumps(block, ensure_ascii=False)
    assert ".png" not in raw and ".jpg" not in raw and "base64" not in raw


def test_atomic_leaf_inherits_parent_visual_material() -> None:
    """A text-only leaf still sees the composite parent's required flowchart."""

    from types import SimpleNamespace

    from src.learning_entry_ui import _visual_material_block

    parent = QuestionCandidate(
        number="16",
        stem="共享工艺流程",
        completeness="complete",
        visual_dependency="required",
        visual_notes="共享材料：CaO₂ 制备流程与热抽滤装置",
        figure_refs=["题16图"],
        children=[
            QuestionCandidate(
                number="(3)",
                stem="写出转化的离子方程式",
                completeness="complete",
                visual_dependency="none",
            )
        ],
        question_kind="composite",
    )
    leaf = parent.children[0]

    block = _visual_material_block(
        leaf, SimpleNamespace(id=93), ancestors=(parent,)
    )

    assert block["dependency"] == "required"
    assert block["material_notes"] == "共享材料：CaO₂ 制备流程与热抽滤装置"
    assert block["figure_refs"] == ["题16图"]
    assert block["binding_confirmed"] is False
    assert block["source_page_id"] == 93
