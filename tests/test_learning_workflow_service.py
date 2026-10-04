"""Learning workflow vertical slice tests (v0.8.6 duty C).

One fixture document flows through all layers: organized question → family
assignment → mastery records → output collection render.  These are the
contracts the real-corpus long-chain tests will rely on; no AI calls and no
network anywhere.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import (
    DocxCollectionRenderer,
    LearningWorkflowError,
    MarkdownCollectionRenderer,
    MasteryService,
    OutputCollectionService,
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
    render_collection_to_file,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = db.create_document(
        title="高三数学周测",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="第1题 已知函数 f(x)=x²lnx，求极值。",
        status="ready",
    )
    return db


def test_layer1_separates_stem_answer_verdict_and_ai_draft(database: Database) -> None:
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        stem_confidence="probable",
        student_answer="f'(x)=2xlnx，所以极大值在 x=1。",
        teacher_verdict="incorrect",
        teacher_comment="漏了定义域，lnx 要求 x>0。",
        correction_note="先写定义域再求导。",
        reason_tags=["定义域遗漏"],
        method_tags=["函数与导数"],
        ai_draft={"summary": "AI：极值点判断错误"},
    )

    assert question.stem_confidence == "probable"
    assert question.teacher_verdict == "incorrect"
    assert question.reason_tags == ["定义域遗漏"]
    assert question.ai_draft == {"summary": "AI：极值点判断错误"}
    assert question.user_edited is False
    assert question.status == "draft"

    updated = service.update_question_item(
        question.id,
        correction_note="定义域 x>0；f'(x)=x(2lnx+x)，唯一极小值点 x=e^(-1/2)。",
        status="organized",
    )
    assert updated.user_edited is True
    assert updated.status == "organized"
    assert updated.ai_draft == {"summary": "AI：极值点判断错误"}  # AI 草稿不被覆盖

    uncertain = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="typical",
    )
    assert uncertain.stem_confidence == "uncertain"
    assert uncertain.teacher_verdict is None


def test_layer1_low_confidence_cannot_be_confirmed_without_text(database: Database) -> None:
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        stem_text="",
        stem_confidence="confirmed",
    )
    assert question.stem_confidence == "uncertain"


def test_layer1_search_finds_organized_stems(database: Database) -> None:
    service = QuestionService(database)
    service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        stem_confidence="probable",
    )
    hits = service.search_questions("极值")
    assert len(hits) == 1
    assert hits[0].question_number == "1"


def test_layer1_evidence_link_uses_v29_page_reference(database: Database) -> None:
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1, page_id=1, question_kind="error"
    )
    link_id = service.link_evidence(
        question.id,
        evidence_item_id=None,
        region_json={"page": 1, "bbox": [10, 20, 300, 200]},
    )
    with database._connection() as connection:
        row = connection.execute(
            """SELECT status, confidence, evidence_type, page_id
            FROM question_item_evidence WHERE id = ?""",
            (link_id,),
        ).fetchone()
    assert row["status"] == "user_confirmed"
    assert row["confidence"] == "confirmed"
    assert row["evidence_type"] == "page_region"
    assert row["page_id"] == 1
    with pytest.raises(LearningWorkflowError):
        service.link_evidence(question.id, evidence_item_id=99999)


def test_layer2_family_matching_assignment_and_revision(database: Database) -> None:
    questions = QuestionService(database)
    organization = QuestionOrganizationService(database)

    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        stem_text="已知函数 f(x)=x²lnx 求极值，注意定义域。",
        method_tags=["函数与导数"],
    )
    family_id = organization.create_family(
        family_kind="type",
        title="函数与导数：含 ln 的极值讨论",
        description="先写定义域，再对 f(x) 求导并讨论符号。",
    )

    candidates = organization.find_candidate_families(question.id)
    assert candidates and candidates[0][0] == family_id

    organization.assign_to_family(question.id, family_id)
    organization.assign_to_family(question.id, family_id)  # 幂等 upsert
    assert organization.get_family(family_id).member_count == 1

    organization.revise_family(
        family_id,
        revision_kind="narrowed",
        note="新增反例表明结论仅在 x>0 时成立。",
        trigger_question_id=question.id,
    )
    family = organization.get_family(family_id)
    assert family.status == "revised"
    with database._connection() as connection:
        revisions = connection.execute(
            "SELECT revision_kind, note, trigger_question_id FROM conclusion_revisions"
        ).fetchall()
    assert len(revisions) == 1
    assert revisions[0]["revision_kind"] == "narrowed"

    retired_id = organization.create_family(family_kind="method", title="已停用旧方法")
    with database._connection() as connection:
        connection.execute(
            "UPDATE question_families SET status = 'retired' WHERE id = ?",
            (retired_id,),
        )
    assert retired_id not in {item.id for item in organization.list_families()}


def test_layer3_mastery_tracks_can_do_and_can_explain_separately(database: Database) -> None:
    questions = QuestionService(database)
    mastery = MasteryService(database)
    question = questions.create_question_item(
        document_id=1, page_id=1, question_kind="error"
    )

    mastery.record_practice(question.id, outcome="correct", can_explain=False)
    mastery.record_explanation(question.id, can_explain=True)

    summary = mastery.question_mastery_summary(question.id)
    assert summary["total"] == 2
    assert summary["can_do"] is True
    assert summary["can_explain"] is True
    assert summary["needs_review"] is False


def test_layer3_review_interval_resets_on_failure(database: Database) -> None:
    organization = QuestionOrganizationService(database)
    mastery = MasteryService(database)
    family_id = organization.create_family(family_kind="conclusion", title="对数不等式结论")

    mastery.bump_family_profile(family_id, outcome="correct")
    mastery.bump_family_profile(family_id, outcome="correct")
    with database._connection() as connection:
        interval = int(
            connection.execute(
                "SELECT review_interval_days FROM mastery_profiles WHERE family_id = ?",
                (family_id,),
            ).fetchone()[0]
        )
    assert interval == 4

    mastery.bump_family_profile(family_id, outcome="incorrect", weak_points=["真数大于零"])
    with database._connection() as connection:
        row = connection.execute(
            "SELECT review_interval_days, weak_points FROM mastery_profiles WHERE family_id = ?",
            (family_id,),
        ).fetchone()
    assert int(row[0]) == 1
    assert "真数大于零" in row[1]


def test_output_renders_real_markdown_from_structured_assets(database: Database) -> None:
    questions = QuestionService(database)
    organization = QuestionOrganizationService(database)
    mastery = MasteryService(database)
    output = OutputCollectionService(database)

    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        stem_confidence="probable",
        student_answer="f'(x)=2xlnx",
        teacher_verdict="incorrect",
        teacher_comment="漏了定义域。",
    )
    family_id = organization.create_family(
        family_kind="type",
        title="函数与导数：含 ln 的极值讨论",
        description="先写定义域，再求导讨论。",
    )
    organization.assign_to_family(question.id, family_id)
    mastery.record_practice(question.id, outcome="incorrect")

    collection_id = output.create_collection(
        kind="error_book",
        title="错题本 · 函数与导数（第 1 周）",
        description="来自周测的结构化整理，非聊天记录拼接。",
    )
    output.add_collection_item(collection_id, item_layer="question", item_id=question.id)
    output.add_collection_item(collection_id, item_layer="family", item_id=family_id)
    output.add_collection_item(collection_id, item_layer="mastery", item_id=question.id)

    rendered = MarkdownCollectionRenderer(database).render(collection_id).decode("utf-8")
    assert "# 错题本 · 函数与导数（第 1 周）" in rendered
    assert "已知函数 f(x)=x²lnx，求极值。" in rendered
    assert "× 错误" in rendered
    assert "漏了定义域。" in rendered
    assert "函数与导数：含 ln 的极值讨论" in rendered
    assert "会做：否" in rendered


def test_output_docx_renderer_is_an_honest_contract_stub(database: Database) -> None:
    output = OutputCollectionService(database)
    collection_id = output.create_collection(kind="error_book", title="错题本")
    with pytest.raises(LearningWorkflowError, match="尚未实现"):
        DocxCollectionRenderer().render(collection_id)


def test_output_file_render_uses_renderer_contract(database: Database, tmp_path: Path) -> None:
    output = OutputCollectionService(database)
    collection_id = output.create_collection(kind="review_pack", title="阶段复习册")
    destination = tmp_path / "输出" / "review.md"

    written = render_collection_to_file(database, collection_id, destination)

    assert written == destination
    assert "# 阶段复习册" in destination.read_text(encoding="utf-8")


def test_two_wings_method_trigger_wing_full_flow(database: Database) -> None:
    """Left wing: conditions -> method -> why -> how it lands on the question."""

    wings = TwoWingsService(database)
    question = QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="error"
    )

    entry_id = wings.create_entry(
        wing_kind="method_trigger",
        target_layer="question",
        question_id=question.id,
        origin="ai_draft",
        confidence="uncertain",
        trigger_conditions=["题干出现 f(x)=f(a+x) 且含 f(a-x)"],
        recognition_signals=["对称结构", "和为定值"],
        candidate_method="对称中心代换",
        selection_reason="自变量成对出现且和为常数，优先构造对称代换。",
        applicability_prerequisites=["函数在闭区间有定义"],
        application_to_current_question="令 x = a/2 + t 化简 f(x)+f(a-x)。",
        similar_method_distinction="与周期代换区分：对称不保证周期。",
    )

    entry = wings.get_entry(entry_id)
    assert entry.wing_kind == "method_trigger"
    assert entry.origin == "ai_draft"
    assert entry.confidence == "uncertain"
    assert entry.status == "draft"
    assert entry.fields["candidate_method"] == "对称中心代换"
    assert entry.fields["trigger_conditions"] == ["题干出现 f(x)=f(a+x) 且含 f(a-x)"]

    revised = wings.update_entry(
        entry_id,
        note="补充：先验证定义域。",
        application_to_current_question="先写定义域，再令 x = a/2 + t。",
    )
    assert revised.origin == "user"
    assert revised.status == "confirmed"
    history = wings.entry_history(entry_id)
    kinds = [item["revision_kind"] for item in history]
    assert kinds == ["ai_draft", "user_revision"]


def test_two_wings_boundary_counterexample_wing(database: Database) -> None:
    """Right wing: when it does NOT apply, misuses, counterexamples."""

    wings = TwoWingsService(database)
    question = QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="typical"
    )

    entry_id = wings.create_entry(
        wing_kind="boundary_counterexample",
        target_layer="question",
        question_id=question.id,
        validity_conditions=["仅对连续函数成立"],
        invalidation_conditions=["在间断点处不成立"],
        boundary_cases=["端点处需单独验证"],
        counterexamples=["f(x) = 1/x 在 x=0 附近"],
        common_misuses=["忘记检查端点"],
        confusing_conclusions=["与最值定理混淆"],
        condition_change_effect="增加闭区间条件后端点结论恢复。",
    )

    entry = wings.get_entry(entry_id)
    assert entry.wing_kind == "boundary_counterexample"
    assert entry.fields["counterexamples"] == ["f(x) = 1/x 在 x=0 附近"]

    wings.confirm_entry(entry_id, note="已对照教材核对。")
    assert wings.get_entry(entry_id).status == "confirmed"


def test_two_wings_family_attachment_and_filtering(database: Database) -> None:
    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(
        family_kind="conclusion", title="对数不等式结论"
    )
    wings = TwoWingsService(database)

    trigger_id = wings.create_entry(
        wing_kind="method_trigger",
        target_layer="family",
        family_id=family_id,
        candidate_method="同底对数比较",
        trigger_conditions=["不等式两边同为对数"],
    )
    boundary_id = wings.create_entry(
        wing_kind="boundary_counterexample",
        target_layer="family",
        family_id=family_id,
        validity_conditions=["真数大于零"],
    )

    triggers = wings.list_entries_for_family(family_id, wing_kind="method_trigger")
    boundaries = wings.list_entries_for_family(family_id, wing_kind="boundary_counterexample")
    assert [entry.id for entry in triggers] == [trigger_id]
    assert [entry.id for entry in boundaries] == [boundary_id]


def test_two_wings_evidence_binding_and_target_validation(database: Database) -> None:
    wings = TwoWingsService(database)
    question = QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="error"
    )
    entry_id = wings.create_entry(
        wing_kind="method_trigger",
        target_layer="question",
        question_id=question.id,
        candidate_method="换元法",
        trigger_conditions=["复合结构出现两次"],
    )

    wings.attach_evidence(
        entry_id, evidence_item_id=None, region_json={"page": 1, "bbox": [1, 2, 3, 4]}
    )
    assert wings.get_entry(entry_id).region_json == {"page": 1, "bbox": [1, 2, 3, 4]}

    with pytest.raises(LearningWorkflowError, match="翼类型"):
        wings.create_entry(
            wing_kind="decorative_ribbon",
            target_layer="question",
            question_id=question.id,
            candidate_method="x",
        )
    with pytest.raises(LearningWorkflowError, match="candidate_method"):
        wings.create_entry(
            wing_kind="method_trigger",
            target_layer="question",
            question_id=question.id,
            trigger_conditions=["条件不足也必须给方法"],
        )
    with pytest.raises(LearningWorkflowError, match="不存在"):
        wings.create_entry(
            wing_kind="method_trigger",
            target_layer="question",
            question_id=99999,
            candidate_method="x",
        )


# ------------------------------------------------- V086-R1 redteam fixes

def test_content_change_helper_distinguishes_blank_save(database: Database) -> None:
    """V086-R1 FIX-1: user_edited means "user really changed content"."""

    from src.learning_workflow_service import question_form_content_changed

    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        student_answer="答错了。",
        teacher_verdict="incorrect",
        correction_note="先写定义域。",
        reason_tags=["定义域遗漏"],
        method_tags=["函数与导数"],
    )
    blank_submit = question_form_content_changed(
        question,
        stem_text=question.stem_text,
        student_answer=question.student_answer,
        teacher_verdict=question.teacher_verdict,
        correction_note=question.correction_note,
        reason_tags=list(question.reason_tags),
        method_tags=list(question.method_tags),
        status=question.status,
    )
    assert blank_submit is False
    real_change = question_form_content_changed(
        question,
        stem_text="改过的题干。",
        student_answer=question.student_answer,
        teacher_verdict=question.teacher_verdict,
        correction_note=question.correction_note,
        reason_tags=list(question.reason_tags),
        method_tags=list(question.method_tags),
        status=question.status,
    )
    assert real_change is True


def test_first_layer_analysis_and_method_are_separate_fields(database: Database) -> None:
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="9",
        stem_text="0，12，42，……",
        analysis_note="先求相邻差，再求第二层差。",
        method_tags=["数列找规律"],
        solution_method="先列第一层差，再列第二层差。",
    )
    assert question.analysis_note == "先求相邻差，再求第二层差。"
    assert question.method_tags == ["数列找规律"]
    assert question.solution_method == "先列第一层差，再列第二层差。"
    updated = service.update_question_item(
        question.id,
        analysis_note="相邻差是 12、30、48。",
        solution_method="第一层差增加 18，推下一项。",
    )
    assert updated.analysis_note == "相邻差是 12、30、48。"
    assert updated.solution_method == "第一层差增加 18，推下一项。"
    assert updated.method_tags == ["数列找规律"]


def test_delete_question_item_removes_row_and_keeps_page(database: Database) -> None:
    """V086-R1 FIX-3: a mis-joined question can be removed from the library."""

    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="good",
        question_number="4",
        stem_text="y=cos³x，求 dy。",
    )
    service.delete_question_item(question.id)
    with pytest.raises(LearningWorkflowError):
        service.get_question_item(question.id)
    # The source page text survives a question delete.
    page = database.get_page(1)
    assert page is not None
    assert page.extracted_text.startswith("第1题")


def test_delete_question_item_missing_raises(database: Database) -> None:
    service = QuestionService(database)
    with pytest.raises(LearningWorkflowError):
        service.delete_question_item(999)


def test_question_subject_is_owned_by_question_and_scoped_to_document(
    database: Database,
) -> None:
    """Different documents can retain different human-reviewed subjects."""

    service = QuestionService(database)
    math_question = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        subject="数学",
        stem_text="计算有理数加法。",
    )
    second_raw = database.database_path.parent.parent / "raw" / "历史练习.pdf"
    second_raw.write_bytes(b"%PDF-1.7 history fixture")
    second_document = database.create_document(
        title="历史练习",
        filename="历史练习.pdf",
        source_path=second_raw,
        sha256=hashlib.sha256(second_raw.read_bytes()).hexdigest(),
        page_count=0,
        import_status="completed",
    )
    second_image = (
        database.database_path.parent.parent
        / "pages"
        / str(second_document.id)
        / "page-1.png"
    )
    second_image.parent.mkdir(parents=True)
    second_image.write_bytes(b"png")
    second_page = database.create_page(
        document_id=second_document.id,
        page_number=1,
        image_path=second_image,
        extracted_text="历史材料题。",
        status="ready",
    )
    history_question = service.create_question_item(
        document_id=second_document.id,
        page_id=second_page.id,
        question_kind="error",
        subject="历史",
        stem_text="判断史实。",
    )

    assert math_question.subject == "数学"
    assert history_question.subject == "历史"

    updated_count = service.update_subject_for_document(1, "物理")

    assert updated_count == 1
    assert service.get_question_item(math_question.id).subject == "物理"
    assert service.get_question_item(history_question.id).subject == "历史"
