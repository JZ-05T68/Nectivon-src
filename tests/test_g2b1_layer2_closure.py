"""GEOGRAPHY G2-B1 tests: layer-2 closure (queue / cold start / lint).

Covers the task-book §43 suggestions:

- review queue: per-question grouping, defer (first-class, never deleted),
  priority order, batch defer
- cold start: shared base-family candidate from a same-batch LOW cluster,
  page-based titles rejected, thresholds untouched
- conclusion lint: definition conclusions no longer get the geography
  condition warning; material-derived conclusions keep a scope reminder
- legacy visual backfill execution stays draft-only
- the strengthened weak-OCR split prompt keeps its fidelity contract
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import (
    HIGH_CONFIDENCE_SCORE,
    QuestionOrganizationService,
    QuestionService,
    classify_conclusion,
    conclusion_lint,
)
from src.question_candidate_service import (
    QuestionCandidateStore,
    build_extraction_prompt,
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
        extracted_text="读图题若干。",
        status="ready",
    )
    questions = QuestionService(db)
    organization = QuestionOrganizationService(db)
    ids = []
    for number, stem in [
        ("1", "读图，判断江苏省人口分布的主要特点。"),
        ("2", "说明江苏省人口分布的主导因素。"),
        ("3", "分析江苏省人口分布的合理措施。"),
    ]:
        ids.append(
            questions.create_question_item(
                document_id=1,
                page_id=1,
                question_kind="error",
                question_number=number,
                stem_text=stem,
            ).id
        )
    return db, questions, organization, ids


# ------------------------------------------------------- queue (G2B-M1)
def test_review_queue_group_by_question(env) -> None:
    """§5: one group per question, not one card per suggestion."""

    _db, _questions, organization, ids = env
    organization.organize_with_confidence(
        ids[0],
        type_family={"title": "人口分布特征判读甲", "description": ""},
        method_families=[{"title": "从空间分布稳定性判断主导因素", "description": ""}],
    )
    organization.organize_with_confidence(
        ids[1],
        type_family={"title": "人口分布特征判读乙", "description": ""},
    )
    groups = organization.list_review_groups(status="pending")
    assert len(groups) >= 2, "each question becomes ONE group"
    for group in groups:
        assert "stem_text" in group and group["items"]
        # Same-question items must live in the SAME group.
        qids = {int(item["question_id"]) for item in group["items"]}
        assert len(qids) == 1


def test_review_queue_defer(env) -> None:
    """§8: defer is a first-class state — kept, no rejection side effect."""

    _db, _questions, organization, ids = env
    outcome = organization.organize_with_confidence(
        ids[0],
        method_families=[{"title": "从空间分布稳定性判断主导因素", "description": ""}],
    )
    review_id = outcome["new_drafts"][0]
    organization.defer_review_item(review_id)
    assert organization.list_review_items(status="pending") == []
    deferred = organization.list_review_items(status="deferred")
    assert len(deferred) == 1
    assert deferred[0]["status"] == "deferred"
    # No rejection was recorded by deferring (§8 hard rule).
    assert organization.rejected_family_ids(ids[0]) == set()
    organization.reopen_review_item(review_id)
    assert len(organization.list_review_items(status="pending")) == 1


def test_review_queue_deferred_not_deleted(env) -> None:
    """§8: deferring never deletes the row and never accepts it."""

    _db, _questions, organization, ids = env
    outcome = organization.organize_with_confidence(
        ids[0],
        type_family={"title": "人口分布特征判读丙", "description": ""},
    )
    review_id = outcome["new_drafts"][0]
    organization.defer_review_item(review_id)
    row = organization.list_review_items(status=None, limit=500)
    match = [item for item in row if int(item["id"]) == review_id]
    assert match, "deferred row must still exist in the database"
    assert match[0]["status"] == "deferred"
    # Low drafts never became formal families by being deferred.
    assert organization.list_families(family_kind="type") == []


def test_review_queue_priority(env) -> None:
    """§9: MEDIUM decisions first, newer before older — explainable only."""

    _db, questions, organization, ids = env
    # Deterministic MEDIUM: a proposal hitting a family the user removed.
    family_id = organization.create_family(
        family_kind="type", title="人口分布特征判读方法", description=""
    )
    organization.assign_to_family(ids[0], family_id)
    organization.remove_from_family(ids[0], family_id, note="priority test")
    organization.organize_with_confidence(
        ids[0], type_family={"title": "人口分布特征判读方法", "description": ""}
    )
    # Deterministic LOW: a proposal with no plausible existing family.
    organization.organize_with_confidence(
        ids[1],
        type_family={"title": "湖泊水位线淤积变化判读", "description": ""},
    )
    items = organization.list_review_items(status="pending")
    assert items, "a pending queue must exist for the priority check"
    confidences = [str(item["confidence"]) for item in items]
    mediums = [i for i, c in enumerate(confidences) if c == "medium"]
    lows = [i for i, c in enumerate(confidences) if c == "low"]
    if mediums and lows:
        assert max(mediums) < min(lows), "MEDIUM rows come before LOW rows"
    for same_tier in (mediums, lows):
        ids_in_tier = [int(items[i]["id"]) for i in same_tier]
        assert ids_in_tier == sorted(ids_in_tier, reverse=True), (
            "newer suggestions first inside a tier"
        )


def test_review_queue_batch_defer(env) -> None:
    """§7: one low-risk batch action — defer all, never batch-accept."""

    _db, _questions, organization, ids = env
    review_ids = []
    for qid in ids[:2]:
        outcome = organization.organize_with_confidence(
            qid,
            type_family={"title": f"人口分布判读变体{qid}", "description": ""},
        )
        review_ids.extend(outcome["new_drafts"] or [])
    deferred = organization.defer_review_items(review_ids)
    assert deferred == len(review_ids)
    assert organization.list_review_items(status="pending") == []
    assert len(organization.list_review_items(status="deferred")) == len(
        review_ids
    )


# -------------------------------------------------- cold start (G2B-M2)
def test_cold_start_batch_candidate(env) -> None:
    """§14-§16: a same-batch cluster yields ONE candidate; one confirm joins all."""

    _db, _questions, organization, ids = env
    organization.organize_with_confidence(
        ids[0],
        type_family={"title": "人口分布特征判读", "description": "读图判断人口分布"},
    )
    organization.organize_with_confidence(
        ids[1],
        type_family={"title": "人口分布特征判断", "description": "读图判断人口分布特点"},
    )
    proposal = organization.propose_batch_base_family(
        ids[:2], suggestion_kind="type"
    )
    assert proposal is not None, "shared core → one candidate base family"
    review_id, cluster = proposal
    assert set(cluster) == set(ids[:2])
    # Nothing formal until the user confirms (§16).
    assert organization.list_families(family_kind="type") == []
    family_id = organization.confirm_review_item(
        review_id, also_question_ids=cluster
    )
    members = {
        member.id
        for _rel, member, _prov in organization.list_family_members(family_id)
    }
    assert set(ids[:2]).issubset(members), "one confirmation joins the cluster"
    # The cluster's own LOW drafts are subsumed, not left pending (§15).
    low_pending = [
        item
        for item in organization.list_review_items(status="pending")
        if int(item["question_id"]) in cluster
        and str(item["confidence"]) == "low"
    ]
    assert low_pending == []
    # Thresholds were NOT touched (§13): this is a candidate, not a hack.
    assert QuestionOrganizationService.REUSE_SIMILARITY_THRESHOLD == 42.0
    assert HIGH_CONFIDENCE_SCORE == 70.0


def test_cold_start_no_page_based_family(env) -> None:
    """§17/§18: page/document identifiers never become family titles."""

    _db, _questions, organization, ids = env
    organization.organize_with_confidence(
        ids[0],
        type_family={"title": "如皋地理第3页题型", "description": "人口"},
    )
    organization.organize_with_confidence(
        ids[1],
        type_family={"title": "如皋地理第3页题目", "description": "人口"},
    )
    proposal = organization.propose_batch_base_family(
        ids[:2], suggestion_kind="type"
    )
    if proposal is not None:
        _review_id, _cluster = proposal
        pending = organization.list_review_items(status="pending")
        shared_row = next(
            (row for row in pending if int(row["id"]) == _review_id), None
        )
        assert shared_row is not None
        assert "第3页" not in str(shared_row["title"]), (
            "the shared candidate itself must not be page-based"
        )
        assert "如皋" not in str(shared_row["title"])
    else:
        assert proposal is None


# ----------------------------------------------- conclusion lint (G2B-L1)
def test_definition_conclusion_no_geo_condition_warning() -> None:
    """§20/§21: definitions/correspondences need no region/scale/season."""

    assert classify_conclusion("RS负责获取影像，GIS负责分析处理，GNSS负责定位") == "definition"
    assert conclusion_lint("RS负责获取影像，GIS负责分析处理，GNSS负责定位") == ""
    # Conditional regularities keep the strict lint (§22).
    assert conclusion_lint("某地一般更大") != ""
    assert conclusion_lint("夏季沿海地区人口密度通常更高（季风区、城市尺度）") == ""


def test_material_derived_conclusion_requires_material_scope() -> None:
    """§23: material-derived conclusions are reminded to stay in scope."""

    kind = classify_conclusion("根据本题材料，河流含沙量较高")
    assert kind == "material_derived"
    warning = conclusion_lint("根据本题材料，河流含沙量较高")
    assert "根据本题材料" in warning and "普遍" in warning
    # Method-style tips are not geography-condition flagged either.
    assert conclusion_lint("看到统计表先比较统计口径与单位") == ""


# --------------------------------------------- backfill + weak OCR (D/E)
def test_legacy_visual_backfill_execution(env, tmp_path: Path) -> None:
    """§24-§28: real execution on a Q1-like legacy row, draft-only."""

    _db, questions, _organization, ids = env
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(
        1,
        [
            __import__(
                "src.question_candidate_service", fromlist=["QuestionCandidate"]
            ).QuestionCandidate(
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
    result = questions.backfill_visual_material_from_candidates(ids[0], store)
    assert result["status"] == "backfilled"
    block = result["visual_material"]
    assert block["binding_confirmed"] is False
    assert block["binding_provenance"] == "AI BINDING DRAFT（存量回填，未经你确认）"


def test_weak_ocr_resplit_fidelity_prompt_contract() -> None:
    """§31/§32: the split prompt keeps the verbatim-transcription hard rule."""

    prompt = build_extraction_prompt("识别文字样例", "")
    assert "逐句照抄" in prompt
    assert "不要概括、改写、合并、简化" in prompt
    assert "宁可长一点也不要概述" in prompt
