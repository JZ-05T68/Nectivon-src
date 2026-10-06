"""Real Streamlit rerun regressions for the five October review-page issues."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src.learning_entry_ui as entry_ui
import src.runtime as runtime
from src.database import Database
from src.learning_workflow_service import QuestionService
from src.math_formatting_service import display_field, format_candidate_math
from src.question_candidate_service import QuestionCandidate, QuestionCandidateStore


def _subject_app(existing_subject: str = "", *, higher: bool = False) -> AppTest:
    profile_source = (
        "profile = LearnerProfile(education_type=EducationType.HIGHER)\n"
        if higher else
        "profile = LearnerProfile(education_type=EducationType.BASIC, basic=BasicEducationProfile("
        "province='', province_code='', city='', city_code='', stage='初中', grade='初一'))\n"
    )
    return AppTest.from_string(
        "import streamlit as st\n"
        "from types import SimpleNamespace\n"
        "from src.learning_entry_ui import _render_subject_picker\n"
        "from src.training_profile_models import (\n"
        "    BasicEducationProfile, EducationType, LearnerProfile,\n"
        ")\n"
        + profile_source
        + "view = st.radio('测试界面', ['审核', '学习'])\n"
        "document = st.radio('测试资料', [1, 2])\n"
        "if view == '审核':\n"
        f"    questions = [SimpleNamespace(subject={existing_subject!r})]\n"
        "    subject = _render_subject_picker(document, questions, profile=profile)\n"
        "    st.button('加入学习整理', disabled=not subject)\n"
    ).run()


def test_subject_survives_widget_cleanup_after_navigation() -> None:
    app = _subject_app()
    app.selectbox[0].select("数学").run()
    assert not app.button[0].disabled
    app.radio[0].set_value("学习").run()
    # Streamlit really removed the widget; the separate durable state survives.
    assert "join_learning_subject_1" not in app.session_state.filtered_state
    app.radio[0].set_value("审核").run()
    assert not app.exception
    assert app.selectbox[0].value == "数学"
    assert not app.button[0].disabled
    app.radio[1].set_value(2).run()
    assert app.selectbox[0].value is None
    assert app.button[0].disabled
    app.radio[1].set_value(1).run()
    assert app.selectbox[0].value == "数学"


def test_custom_subject_survives_navigation() -> None:
    app = _subject_app(higher=True)
    app.text_input[0].set_value("工程力学").run()
    next(b for b in app.button if b.label == "确认学科").click().run()
    app.radio[0].set_value("学习").run()
    app.radio[0].set_value("审核").run()
    assert app.text_input[0].value == "工程力学"
    assert not next(b for b in app.button if b.label == "加入学习整理").disabled


def test_previous_human_subject_restores_buttons_in_new_session() -> None:
    app = _subject_app("数学")
    assert app.selectbox[0].value == "数学"
    assert not app.button[0].disabled


@pytest.mark.parametrize("state", ["none", "empty", "pending", "added"])
def test_split_controls_never_offer_whole_page_join(state: str) -> None:
    app = AppTest.from_string(
        "from types import SimpleNamespace\n"
        "from src.learning_entry_ui import _render_candidate_split_section\n"
        f"state = {state!r}\n"
        "candidates = None if state == 'none' else ([] if state == 'empty' else "
        "[SimpleNamespace(status=state)])\n"
        "_render_candidate_split_section(SimpleNamespace(id=7), candidates)\n"
    ).run()
    assert not app.exception
    labels = [button.label for button in app.button]
    assert not any("整页加入" in label for label in labels)
    assert any("拆分" in label or "切分" in label for label in labels)
    assert not any("整页作为" in expander.label for expander in app.expander)


@pytest.fixture()
def review_store(tmp_path: Path, monkeypatch):
    database = Database(tmp_path / "knowledge.db")
    document = database.create_document(
        title="审核测试卷", filename="test.pdf", source_path=tmp_path / "test.pdf",
        sha256="a" * 64,
    )
    page = database.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "page.png",
    )
    store = QuestionCandidateStore(tmp_path / "question-candidates")
    monkeypatch.setattr(entry_ui, "_database", lambda: database)
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: None)
    return database, page, store


def _card_app(monkeypatch, page, store) -> AppTest:
    monkeypatch.setattr(entry_ui, "_candidate_store", lambda: store)
    monkeypatch.setattr(runtime, "application_database", entry_ui._database)
    return AppTest.from_string(
        "from src.learning_entry_ui import _render_candidate_cards, _candidate_store\n"
        "from src.learning_workflow_service import QuestionService\n"
        "from src.question_candidate_service import iter_atomic_leaves\n"
        "from src.runtime import application_database\n"
        "database = application_database()\n"
        f"page = database.get_page({page.id})\n"
        "store = _candidate_store()\n"
        "entries = [e for e in iter_atomic_leaves(store.page_candidates(page.id)) "
        "if e[1].status == 'pending']\n"
        "_render_candidate_cards(page, entries, 'error', '数学', "
        "QuestionService(database), store)\n"
    ).run()


def test_full_candidate_math_and_long_options_are_visible(review_store, monkeypatch) -> None:
    _, page, store = review_store
    options = [f"{label}. " + ("一至两句话的完整选项内容。" * 15) for label in "ABCD"]
    stem = "已知(a+1)²=4，|b|/b=-1，选择正确说法。\n" + " ".join(options)
    store.save_page_candidates(page.id, [QuestionCandidate(
        number="9", stem=stem, completeness="complete", visual_dependency="none",
    )])
    app = _card_app(monkeypatch, page, store)
    assert not app.exception
    rendered = "\n".join(element.value for element in app.markdown)
    assert r"$(a+1)^{2}=4$" in rendered
    assert r"$\dfrac{|b|}{b}=-1$" in rendered
    assert "\n\nD. " + "一至两句话的完整选项内容。" * 15 in rendered
    assert "题干完整" not in rendered
    assert store.page_candidates(page.id)[0].stem == stem


def test_only_genuine_parent_conditions_appear_in_candidate_cards(review_store, monkeypatch):
    _, page, store = review_store
    children = [QuestionCandidate("7(1)", "求 x^2。", "complete")]
    parent = QuestionCandidate("7", "已知 x=2。", "complete", children=children,
                               question_kind="composite", has_shared_stem=True)
    store.save_page_candidates(page.id, [parent])
    app = _card_app(monkeypatch, page, store)
    assert not app.exception
    assert any(e.label == "本小题需要的公共题干" for e in app.expander)
    assert "已知" in "\n".join(m.value for m in app.markdown)
    parent.has_shared_stem = False
    parent.stem = ""
    store.save_page_candidates(page.id, [parent])
    app.run()
    assert not app.exception
    assert not any(e.label == "本小题需要的公共题干" for e in app.expander)


def test_saved_candidate_edit_triggers_ai_typesetting_and_keeps_content(
    review_store, monkeypatch
) -> None:
    _, page, store = review_store
    store.save_page_candidates(page.id, [QuestionCandidate(
        number="23(3)", stem="原识别文字", completeness="complete", visual_dependency="none",
    )])
    calls = []
    monkeypatch.setattr(
        entry_ui, "_format_candidate_after_save",
        lambda page, store, source: calls.append(source),
    )
    app = _card_app(monkeypatch, page, store)
    source = "人工校正：(a+1)²+|b+5|=b+5。"
    app.text_area[0].set_value(source).run()
    next(b for b in app.button if b.label == "保存修改").click().run()
    assert not app.exception
    assert calls == [source]
    saved = store.page_candidates(page.id)[0]
    assert saved.stem == source
    assert saved.user_edited
    assert saved.status == "pending"
    assert r"$(a+1)^{2}+|b+5|=b+5$" in "\n".join(m.value for m in app.markdown)


def test_join_button_saves_one_question_and_preserves_candidate_typesetting(
    review_store, monkeypatch
) -> None:
    database, page, store = review_store
    source = "已知|b|/b=-1。"
    store.save_page_candidates(page.id, [
        QuestionCandidate(number="1", stem=source, completeness="complete"),
        QuestionCandidate(number="2", stem="求a+b。", completeness="complete"),
    ])

    class AI:
        def _complete(self, prompt: str, *, target_refs: tuple[str, ...]) -> str:
            return '{"stem": []}'

    assert format_candidate_math(store.root, page.id, source, AI())
    app = _card_app(monkeypatch, page, store)
    next(b for b in app.button if b.label == "加入学习整理").click().run()
    assert not app.exception
    questions = QuestionService(database).list_questions_for_document(page.document_id)
    assert len(questions) == 1
    assert questions[0].stem_text == source
    assert questions[0].subject == "数学"
    assert display_field(questions[0], "stem_text") == r"已知$\dfrac{|b|}{b}=-1$。"
    assert [c.status for c in store.page_candidates(page.id)] == ["added", "pending"]
    assert not next(b for b in app.button if b.label == "加入学习整理").disabled


def test_join_preserves_original_option_images(review_store, monkeypatch) -> None:
    from PIL import Image

    from src.question_visual_regions import bind_regions

    database, page, store = review_store
    Image.new("RGB", (800, 1000), "white").save(page.image_path)
    regions = bind_regions([
        {"role": "option", "option_label": label, "bbox": [100, 100 + i * 100, 500, 180 + i * 100]}
        for i, label in enumerate("ABCD")
    ], page_id=page.id, image_bytes=Path(page.image_path).read_bytes())
    candidate = QuestionCandidate(
        number="3", stem="选择对应数轴。\n\n" + "\n\n".join(f"{x}. （见原图）" for x in "ABCD"),
        completeness="complete", visual_dependency="required", visual_regions=regions,
    )
    store.save_page_candidates(page.id, [candidate])
    app = _card_app(monkeypatch, page, store)
    assert len(app.get("image")) == 4
    next(b for b in app.button if b.label == "加入学习整理").click().run()
    assert not app.exception
    question = QuestionService(database).list_questions_for_document(page.document_id)[0]
    assert question.ai_draft["visual_material"]["regions"] == regions
    assert question.ai_draft["candidate"]["visual_regions"] == regions
    assert question.stem_text == candidate.stem
