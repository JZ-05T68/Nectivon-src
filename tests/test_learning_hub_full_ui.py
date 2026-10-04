"""Full learning-workflow UI path (V086-109) plus new list-service contracts.

The hub page must expose real user paths for every layer: browsing/editing
questions, both wings, families, mastery, and output collections - not just a
statistics dashboard.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.database import Database
from src.learning_workflow_service import (
    MasteryService,
    OutputCollectionService,
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
)
from src.training_profile_service import TrainingProfileService


def _database(tmp_path: Path) -> Database:
    data_dir = tmp_path / "data"
    for name in ("raw", "pages/1", "markdown"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    database = Database(data_dir / "database" / "knowledge.db")
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    database.create_document(
        title="学习夹具",
        filename="试卷.pdf",
        source_path=raw,
        sha256="9" * 64,
        import_status="completed",
    )
    database.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="已知函数 f(x) 求极值。",
        status="ready",
    )
    return database


def _seed(database: Database) -> tuple[int, int]:
    question = QuestionService(database).create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        stem_text="已知函数 f(x) 求极值，求最小值。",
        ai_draft={"summary": "AI 草稿内容"},
    )
    question_id = question.id
    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(
        family_kind="type",
        title="极值求解族",
        description="利用导数求极值的一类题",
    )
    organization.assign_to_family(question_id, family_id)
    TwoWingsService(database).create_entry(
        wing_kind="method_trigger",
        target_layer="question",
        question_id=question_id,
        origin="ai_draft",
        confidence="probable",
        candidate_method="求导找驻点",
    )
    MasteryService(database).record_practice(
        question_id, outcome="correct", can_explain=True
    )
    collection_id = OutputCollectionService(database).create_collection(
        kind="error_book", title="第一周错题本"
    )
    OutputCollectionService(database).add_collection_item(
        collection_id, item_layer="question", item_id=question_id
    )
    return question_id, family_id


# ------------------------------------------------- service list contracts
def test_list_question_items_filters_by_kind(tmp_path: Path) -> None:
    database = _database(tmp_path)
    service = QuestionService(database)
    for kind in ("error", "good", "typical"):
        service.create_question_item(document_id=1, page_id=1, question_kind=kind)
    all_items = service.list_question_items()
    assert {item.question_kind for item in all_items} == {"error", "good", "typical"}
    errors = service.list_question_items(question_kind="error")
    assert all(item.question_kind == "error" for item in errors)
    assert len(errors) >= 1


def test_family_listing_and_membership(tmp_path: Path) -> None:
    database = _database(tmp_path)
    question_id, family_id = _seed(database)
    organization = QuestionOrganizationService(database)
    families = organization.list_families(family_kind="type")
    assert any(family.id == family_id for family in families)
    members = organization.list_family_members(family_id)
    # §47: members are (relation, question, provenance) triples now.
    assert any(member.id == question_id for _, member, _ in members)
    containing = organization.list_families_for_question(question_id)
    assert any(family.id == family_id for family in containing)
    organization.revise_family(
        family_id, revision_kind="narrowed", note="收窄：仅讨论可导情形"
    )
    history = organization.family_revision_history(family_id)
    assert any(record["revision_kind"] == "narrowed" for record in history)


def test_mastery_review_queue_and_collection_listing(tmp_path: Path) -> None:
    database = _database(tmp_path)
    question_id, family_id = _seed(database)
    mastery = MasteryService(database)
    mastery.bump_family_profile(family_id, outcome="correct")
    queue = mastery.list_review_queue()
    assert any(row["family_id"] == family_id for row in queue)
    collections = OutputCollectionService(database).list_collections()
    assert any(collection["title"] == "第一周错题本" for collection in collections)
    assert any(collection["item_count"] >= 1 for collection in collections)


# ------------------------------------------------------------- full UI path
def test_learning_hub_page_exposes_all_layers(tmp_path: Path, monkeypatch) -> None:
    database = _database(tmp_path)
    _seed(database)
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(
        runtime,
        "application_training_profile_service",
        lambda: TrainingProfileService(
            tmp_path / "data" / "training_profile.db"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "application_page_visual_service",
        lambda: (_ for _ in ()).throw(RuntimeError("AppTest 无视觉服务")),
    )

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "18_学习整理.py")
    ).run(timeout=60)

    assert not app.exception
    markdown_text = "\n".join(
        element.value for element in app.markdown if isinstance(element.value, str)
    )
    captions = [element.value for element in app.caption]
    caption_text = "\n".join(str(value) for value in captions)
    subheaders = [str(element.value) for element in app.subheader]
    # Every layer must have a real surface on the page (V086-109).
    # R5.1 copy pass renamed the export tab 输出册子 → 导出; the learning
    # layers themselves are unchanged.
    for expected in ("归纳族", "掌握训练", "导出", "两翼", "题目库"):
        assert expected in subheaders or expected in markdown_text
    assert "被归入" in caption_text or "还没有被归入任何族" in caption_text
    joined = markdown_text + caption_text
    # R5.1 copy pass: the review surface is now the 导出 tab (错题本/复习册/
    # 专项册 booklets); assert its stable wording instead of 复习清单.
    assert "导出文档" in joined or "复习清单" in joined
