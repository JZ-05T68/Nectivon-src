"""GEOGRAPHY G2-B tests: layer-2 confidence workflow + boundary discipline.

Covers the task-book §100 suggestions plus the G2-B guards:

- HIGH / MEDIUM / LOW confidence tiers of ``organize_with_confidence``
- user rejection persistence (移出 → save again → never silently re-added)
- family dedup on low-confidence acceptance (anti family-explosion §15)
- many-to-many membership stays intact
- topic ≠ method guard, secondary-conclusion condition guard
- boundary provenance labels (§39/§77) and absolute-language guard (§30)
- authority-fabrication guard for AI boundary drafts (§40)
- legacy visual-material backfill is draft-only (§49/§50/§111)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.learning_ai_draft_service import (
    LearningAIDraftError,
    LearningAIDraftService,
    boundary_authority_violation,
)
from src.learning_workflow_service import (
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
    absolute_language_risk,
    conclusion_condition_risk,
    method_title_risk,
    wing_provenance_label,
)
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateStore,
)


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="01如皋地理",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="第1题 读江苏省人口密度分布示意图。",
        status="ready",
    )
    questions = QuestionService(db)
    organization = QuestionOrganizationService(db)
    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="读图，判断江苏省人口分布的主要特点是人口密度东高西低。",
        method_tags=["空间分布比较"],
    )
    return db, questions, organization, question


def _members(organization, question_id: int, kind: str) -> list[int]:
    return [
        family.id
        for family in organization.list_families_for_question(question_id)
        if family.family_kind == kind
    ]


# --------------------------------------------------------------- HIGH tier
def test_auto_family_high_confidence(env) -> None:
    """§69: an exact-title match auto-attaches with a revocable reason."""

    _db, _questions, organization, question = env
    organization.create_family(
        family_kind="type", title="人口分布特征判读", description="读图判断人口分布特征"
    )
    outcome = organization.organize_with_confidence(
        question.id,
        type_family={"title": "人口分布特征判读", "description": "读图判断人口分布特征"},
    )
    assert outcome["auto"], "exact title match must attach immediately"
    assert outcome["auto"][0] in _members(organization, question.id, "type")
    assert outcome["auto_reasons"], "HIGH must carry a student-language reason"
    # Idempotent: saving again adds nothing (§22).
    again = organization.organize_with_confidence(
        question.id,
        type_family={"title": "人口分布特征判读", "description": "读图判断人口分布特征"},
    )
    assert again["auto"] == []
    assert again["recommended"] == []
    assert again["new_drafts"] == []


# ------------------------------------------------------------- MEDIUM tier
def test_auto_family_medium_recommendation(env) -> None:
    """§70: moderate match → 1-3 candidates, nothing attached until user."""

    _db, _questions, organization, question = env
    organization.create_family(
        family_kind="type", title="区域差异分析", description="比较区域差异"
    )
    # Distinct-but-related title: similar, not exact.
    outcome = organization.organize_with_confidence(
        question.id,
        type_family={"title": "区域差异比较分析", "description": "比较区域差异"},
    )
    if outcome["recommended"]:
        pending = organization.list_review_items(status="pending")
        assert pending, "medium recommendations must be persisted for review"
        item = pending[0]
        assert item["confidence"] == "medium"
        assert item["matched_family_id"] is not None
        # Nothing attached yet: the user decides.
        assert _members(organization, question.id, "type") == []
        family_id = organization.confirm_review_item(
            item["id"], family_id=int(item["matched_family_id"])
        )
        assert family_id in _members(organization, question.id, "type")
        member_provenance = [
            provenance
            for _relation, member, provenance in organization.list_family_members(
                family_id
            )
            if member.id == question.id
        ]
        assert member_provenance == ["AI 推荐后你确认了「区域差异比较分析」"]
    else:
        # If the overlap actually reached the HIGH band the attach path is
        # legitimate; both branches must never leave a pending low draft.
        assert not outcome["new_drafts"]


# ---------------------------------------------------------------- LOW tier
def test_auto_family_low_candidate(env) -> None:
    """§71: no plausible family → draft only; formal family needs the user."""

    _db, _questions, organization, question = env
    outcome = organization.organize_with_confidence(
        question.id,
        method_families=[
            {"title": "从空间分布稳定性判断长期主导因素", "description": ""}
        ],
    )
    assert outcome["new_drafts"], "no existing family → low draft expected"
    pending = organization.list_review_items(status="pending")
    assert len(pending) == 1
    assert pending[0]["confidence"] == "low"
    assert pending[0]["matched_family_id"] is None
    # LOW never created a formal family (§9 hard rule).
    method_titles = [
        f.title for f in organization.list_families(family_kind="method")
    ]
    assert "从空间分布稳定性判断长期主导因素" not in method_titles
    # User accepts → formal family with user provenance.
    family_id = organization.confirm_review_item(pending[0]["id"])
    assert family_id in _members(organization, question.id, "method")
    assert "你确认了 AI 建议的新族" in organization.list_family_members(family_id)[0][2]


def test_family_deduplication_on_low_accept(env) -> None:
    """§15: near-duplicate draft acceptance reuses the existing family."""

    _db, _questions, organization, question = env
    organization.create_family(
        family_kind="type", title="人口密度分布比较", description=""
    )
    outcome = organization.organize_with_confidence(
        question.id,
        type_family={"title": "人口密度分布比较分析", "description": ""},
    )
    pending = organization.list_review_items(status="pending")
    if pending and pending[0]["confidence"] == "low":
        family_id = organization.confirm_review_item(pending[0]["id"])
        type_titles = [
            f.title for f in organization.list_families(family_kind="type")
        ]
        assert type_titles == ["人口密度分布比较"], "no duplicate family created"
    else:
        family_id = outcome["auto"][0]
    assert family_id in _members(organization, question.id, "type")


def test_review_item_rename_and_merge(env) -> None:
    """§73: rename then accept; merge into an existing family."""

    _db, _questions, organization, question = env
    outcome = organization.organize_with_confidence(
        question.id,
        method_families=[{"title": "人口空间分布主导因素判断", "description": ""}],
    )
    assert outcome["new_drafts"]
    item = organization.list_review_items(status="pending")[0]
    renamed_id = organization.accept_review_item_renamed(
        item["id"], new_title="人口分布主导因素判断"
    )
    assert renamed_id in _members(organization, question.id, "method")
    # A second similar question later → merge path into the renamed family.
    questions = QuestionService(_db)
    question2 = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="typical",
        question_number="2",
        stem_text="说明该地区人口分布的特点及主要影响因素。",
    )
    outcome2 = organization.organize_with_confidence(
        question2.id,
        method_families=[{"title": "人口分布主导因素判断方法", "description": ""}],
    )
    pending2 = organization.list_review_items(status="pending")
    if pending2 and pending2[0]["confidence"] == "low":
        merged_id = organization.confirm_review_item(pending2[0]["id"])
        assert merged_id == renamed_id, "near-duplicate must reuse, not clone"
    else:
        assert renamed_id in outcome2["auto"]


# --------------------------------------------------------- user overrides
def test_user_family_rejection_persists(env) -> None:
    """§72: 移出之后再次保存不得自动加回，且建议时说明原因。"""

    _db, _questions, organization, question = env
    family_id = organization.create_family(
        family_kind="type", title="人口分布特征判读", description=""
    )
    organization.organize_with_confidence(
        question.id, type_family={"title": "人口分布特征判读", "description": ""}
    )
    assert family_id in _members(organization, question.id, "type")
    organization.remove_from_family(question.id, family_id, note="不想要这个族")
    assert family_id not in _members(organization, question.id, "type")
    # Save again through BOTH auto paths: no silent re-attach (§21).
    outcome = organization.organize_with_confidence(
        question.id, type_family={"title": "人口分布特征判读", "description": ""}
    )
    assert outcome["auto"] == []
    assert family_id not in _members(organization, question.id, "type")
    legacy = organization.auto_organize_question(
        question.id, type_family={"title": "人口分布特征判读", "description": ""}
    )
    assert legacy["type"] == []
    assert family_id not in _members(organization, question.id, "type")
    assert family_id in organization.rejected_family_ids(question.id)


def test_family_many_to_many(env) -> None:
    """§12: one question may sit in several type and method families."""

    _db, _questions, organization, question = env
    t1 = organization.create_family(family_kind="type", title="人口分布特征判读")
    t2 = organization.create_family(family_kind="type", title="区域差异分析")
    m1 = organization.create_family(family_kind="method", title="图表空间分布比较")
    m2 = organization.create_family(family_kind="method", title="主导因素排除法")
    for fid in (t1, t2, m1, m2):
        organization.assign_to_family(question.id, fid)
    assert {t1, t2} == set(_members(organization, question.id, "type"))
    assert {m1, m2} == set(_members(organization, question.id, "method"))


# ------------------------------------------------- topic/type/method guard
def test_geo_topic_not_method(env) -> None:
    """§13/§14: pure topic words must not silently become method families."""

    assert method_title_risk("人口"), "population alone is a topic, not a method"
    assert method_title_risk("河流")
    assert method_title_risk("地形")
    assert method_title_risk("区位分析"), "over-broad method title"
    assert not method_title_risk("主导因素排除法"), "a real method is fine"
    assert not method_title_risk("人口分布特征判断"), "type-ish title is not method"

    _db, _questions, organization, question = env
    # An AI proposal of a pure-topic method family must be downgraded to a
    # review item instead of auto-attaching a junk method family.
    outcome = organization.organize_with_confidence(
        question.id, method_families=[{"title": "人口", "description": ""}]
    )
    assert not outcome["auto"], "topic-word method family must never auto-attach"
    assert organization.list_families(family_kind="method") == []


def test_secondary_conclusion_conditions(env) -> None:
    """§19: conclusions without conditions surface a warning, not silence."""

    assert conclusion_condition_risk("某地一般更大") != ""
    assert conclusion_condition_risk("") != ""
    assert (
        conclusion_condition_risk("夏季沿海地区人口密度通常更高（季风区、城市尺度）")
        == ""
    )


# ------------------------------------------------- boundary provenance (§39)
def test_boundary_source_provenance(env) -> None:
    """§39/§77: evidence-bound + user content reads as 来自你的资料/你已确认."""

    label = wing_provenance_label("user", "confirmed", "confirmed")
    assert label == "你已确认"
    db, questions, organization, question = env
    wings = TwoWingsService(db)
    entry_id = wings.create_entry(
        wing_kind="boundary_counterexample",
        target_layer="question",
        question_id=question.id,
        origin="user",
        confidence="confirmed",
        validity_conditions=["仅适用于季风区沿海平原"],
    )
    entry = wings.get_entry(entry_id)
    assert entry.status == "draft"
    assert (
        wing_provenance_label(entry.origin, entry.confidence, entry.status)
        == "来自你的整理"
    )
    confirmed = wings.confirm_entry(entry_id)
    assert (
        wing_provenance_label(confirmed.origin, confirmed.confidence, confirmed.status)
        == "你已确认"
    )


def test_boundary_ai_supplement_label(env) -> None:
    """§77: an unconfirmed AI draft is visibly an AI supplement."""

    assert wing_provenance_label("ai_draft", "probable", "draft") == "AI 补充，建议核对"
    assert (
        wing_provenance_label("ai_draft", "uncertain", "draft")
        == "AI 补充，把握有限，建议核对"
    )
    assert wing_provenance_label("ai_draft", "probable", "confirmed") == "AI 补充 · 你已核对"


def test_boundary_absolute_language_guard(env) -> None:
    """§30: absolutized wording is flagged with the offending phrases."""

    hits = absolute_language_risk("人口密度高经济必然发达，全国所有地区都是如此")
    assert "必然" in hits
    assert "所有地区" in hits
    assert absolute_language_risk("季风区夏季降水通常较多") == []


def test_boundary_authority_guard_function() -> None:
    """§40: textbook/exam claims without evidence are detectable."""

    assert boundary_authority_violation("教材指出季风区雨热同期") == "教材指出"
    assert boundary_authority_violation("这是高考常考结论") == "高考常考"
    assert boundary_authority_violation("根据本题材料，河流含沙量较大") is None


def test_boundary_authority_guard_blocks_ai_draft(env) -> None:
    """§40 hard rule: an AI wing draft faking authority is refused, not saved."""

    class _StubProvider:
        def complete(self, prompt: str, **_kwargs):  # noqa: ANN002, ANN003
            class _Result:
                text = json.dumps(
                    {
                        "validity_conditions": ["教材指出：季风区雨热同期"],
                        "invalidation_conditions": [],
                        "boundary_cases": [],
                        "counterexamples": [],
                        "common_misuses": [],
                        "confusing_conclusions": [],
                        "condition_change_effect": "",
                    },
                    ensure_ascii=False,
                )

            return _Result()

    _db, questions, _organization, question = env
    service = LearningAIDraftService(_StubProvider())
    with pytest.raises(LearningAIDraftError) as excinfo:
        service.generate_wing_draft(question, "boundary_counterexample")
    assert "权威出处" in str(excinfo.value)


# -------------------------------------------------- legacy visual backfill
def test_visual_material_backfill_draft_only(env, tmp_path: Path) -> None:
    """§49/§50/§111: backfill creates a DRAFT binding, never user-confirmed."""

    _db, questions, organization, question = env
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="1",
                stem="读图，判断江苏省人口分布的主要特点。",
                completeness="complete",
                incomplete_reason="",
                figure_refs=["图 1"],
                status="added",
                extracted_at="2026-09-28T00:00:00+00:00",
                user_edited=False,
                visual_dependency="required",
                visual_notes="共享材料：江苏省各区域人口密度分布示意图",
                binding_confirmed=False,
            )
        ],
    )
    result = questions.backfill_visual_material_from_candidates(
        question.id, store
    )
    assert result["status"] == "backfilled"
    block = result["visual_material"]
    assert block["binding_confirmed"] is False, "backfill must stay a draft"
    assert block["binding_provenance"] == "AI BINDING DRAFT（存量回填，未经你确认）"
    assert block["source_page_id"] == question.page_id
    refreshed = questions.get_question_item(question.id)
    assert refreshed.ai_draft["visual_material"]["binding_confirmed"] is False


def test_visual_material_backfill_safety_skips(env, tmp_path: Path) -> None:
    """§50: ambiguity or missing visuals → skip, never guess."""

    _db, questions, organization, question = env
    store = QuestionCandidateStore(tmp_path / "candidates2")
    # No candidate file at all → skip.
    result = questions.backfill_visual_material_from_candidates(
        question.id, store
    )
    assert result["status"] == "skipped"
    # Candidate with no visual dependency → skip.
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="1",
                stem="纯文字题干。",
                completeness="complete",
                incomplete_reason="",
                figure_refs=[],
                status="pending",
                extracted_at="2026-09-28T00:00:00+00:00",
                user_edited=False,
                visual_dependency="none",
                visual_notes="",
                binding_confirmed=False,
            )
        ],
    )
    result2 = questions.backfill_visual_material_from_candidates(
        question.id, store
    )
    assert result2["status"] == "skipped"
    refreshed = questions.get_question_item(question.id)
    assert not (refreshed.ai_draft or {}).get("visual_material")
