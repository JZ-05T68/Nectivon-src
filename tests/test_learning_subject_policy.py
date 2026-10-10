"""Education-specific subject lists and explicit manual confirmation on reruns."""

from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.learning_subject_policy import (
    MANUAL_SUBJECT,
    UNIVERSITY_SUBJECTS,
    normalize_subject_name,
    read_saved_subject,
    subject_selection_policy,
)
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
)

PRIMARY = ("语文", "数学", "英语")
FIRST = (*PRIMARY, "历史", "政治", "地理", "生物")
SECOND = (*FIRST, "物理")
THIRD = (*PRIMARY, "物理", "化学", "历史", "政治")
HIGH = (*THIRD, "地理", "生物")


@pytest.fixture(autouse=True)
def subject_metadata(monkeypatch, tmp_path):
    database = SimpleNamespace(database_path=tmp_path / "database" / "knowledge.db")
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    return database


def _basic(
    stage: str, grade: str, *, strong: bool = False, competition: bool = False,
) -> LearnerProfile:
    return LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="", province_code="", city="", city_code="", stage=stage, grade=grade,
            in_strong_base=strong, in_competition=competition,
        ),
    )


@pytest.fixture()
def profile_source(monkeypatch):
    state = {"profile": _basic("初中", "初一"), "upgrade_checks": []}

    def get_profile(*, check_grade_upgrade=True):
        state["upgrade_checks"].append(check_grade_upgrade)
        return state["profile"]

    monkeypatch.setattr(
        runtime, "application_training_profile_service",
        lambda: SimpleNamespace(get_profile=get_profile),
    )
    return state


def _app(existing_subject: str = "") -> AppTest:
    return AppTest.from_string(
        "import streamlit as st\n"
        "from types import SimpleNamespace\n"
        "from src.learning_entry_ui import _render_subject_picker\n"
        "view = st.radio('测试页面', ['审核', '学习'])\n"
        "document = st.radio('测试文档', [1, 2])\n"
        "if view == '审核':\n"
        f"    existing = [SimpleNamespace(subject={existing_subject!r})]\n"
        "    subject = _render_subject_picker(document, existing)\n"
        "    st.button('加入学习整理', disabled=not subject)\n"
    ).run()


def _join(app: AppTest):
    return next(button for button in app.button if button.label == "加入学习整理")


def _confirm(app: AppTest):
    return next(button for button in app.button if button.label == "确认学科")


@pytest.mark.parametrize(("stage", "grade", "expected"), [
    ("小学", "一年级", PRIMARY),
    ("小学", "六年级", PRIMARY),
    ("初中", "初一", FIRST),
    ("初中", "初二", SECOND),
    ("初中", "初三", THIRD),
    ("高中", "高一", HIGH),
    ("高中", "高二", HIGH),
    ("高中", "高三", HIGH),
])
def test_actual_picker_has_exact_grade_subjects(profile_source, stage, grade, expected):
    profile_source["profile"] = _basic(stage, grade)
    app = _app()
    assert not app.exception
    assert tuple(app.selectbox[0].options) == (*expected, MANUAL_SUBJECT)
    assert app.selectbox[0].value is None
    assert not app.text_input
    assert _join(app).disabled
    assert profile_source["upgrade_checks"] == [False]
    app.selectbox[0].select("数学").run()
    assert not _join(app).disabled


@pytest.mark.parametrize(("strong", "competition"), [(True, False), (False, True), (True, True)])
def test_high_school_advanced_subject_requires_confirmation(profile_source, strong, competition):
    profile_source["profile"] = _basic("高中", "高一", strong=strong, competition=competition)
    app = _app()
    assert tuple(app.selectbox[0].options) == (*HIGH, "强基/竞赛", MANUAL_SUBJECT)
    assert not app.text_input
    app.selectbox[0].select("强基/竞赛").run()
    assert app.text_input[0].label == "填写强基/竞赛学科或方向"
    assert _confirm(app).disabled
    assert _join(app).disabled
    app.text_input[0].set_value("数学竞赛").run()
    assert not _confirm(app).disabled
    assert _join(app).disabled
    _confirm(app).click().run()
    assert not _join(app).disabled
    assert app.session_state["learning_subject_choice_1"]["confirmed"] == "数学竞赛"
    app.text_input[0].set_value("物理强基").run()
    assert _join(app).disabled
    _confirm(app).click().run()
    assert not _join(app).disabled
    app.selectbox[0].select("化学").run()
    assert not app.text_input
    assert not _join(app).disabled


def test_junior_three_removes_geography_then_high_school_restores_it(profile_source):
    profile_source["profile"] = _basic("初中", "初二")
    app = _app()
    app.selectbox[0].select("地理").run()
    assert not _join(app).disabled
    profile_source["profile"] = _basic("初中", "初三")
    app.run()
    assert not app.exception
    assert tuple(app.selectbox[0].options) == (*THIRD, MANUAL_SUBJECT)
    assert app.selectbox[0].value is None
    assert _join(app).disabled
    profile_source["profile"] = _basic("高中", "高一")
    app.run()
    assert tuple(app.selectbox[0].options) == (*HIGH, MANUAL_SUBJECT)
    app.selectbox[0].select("生物").run()
    assert not _join(app).disabled


def test_valid_regular_subject_survives_saved_grade_change(profile_source):
    app = _app()
    app.selectbox[0].select("数学").run()
    profile_source["profile"] = _basic("初中", "初三")
    app.run()
    assert not app.exception
    assert app.selectbox[0].value == "数学"
    assert not _join(app).disabled


def test_turning_off_advanced_training_invalidates_manual_selection(profile_source):
    profile_source["profile"] = _basic("高中", "高二", strong=True)
    app = _app()
    app.selectbox[0].select("强基/竞赛").run()
    app.text_input[0].set_value("数学强基").run()
    _confirm(app).click().run()
    assert not _join(app).disabled
    profile_source["profile"] = _basic("高中", "高二")
    app.run()
    assert not app.exception
    assert tuple(app.selectbox[0].options) == (*HIGH, MANUAL_SUBJECT)
    assert app.selectbox[0].value is None
    assert not app.text_input
    assert _join(app).disabled


def test_higher_education_lists_official_discipline_classification(profile_source):
    profile_source["profile"] = LearnerProfile(education_type=EducationType.HIGHER)
    app = _app()
    assert not app.exception
    options = tuple(app.selectbox[0].options)
    assert "计算机科学与技术" in options
    assert "心理学" in options
    assert options[-1] == MANUAL_SUBJECT
    assert len(options) == len(set(options))
    assert not app.text_input
    assert _join(app).disabled
    app.selectbox[0].select("心理学").run()
    assert not app.text_input
    assert not _join(app).disabled


def test_higher_education_saved_major_or_discipline_ranks_first(profile_source):
    profile_source["profile"] = LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school="测试大学",
            education_level="研究生",
            discipline_category="04 教育学",
            discipline_first_level="0402 心理学",
        ),
    )
    assert subject_selection_policy(profile_source["profile"]).subjects[0] == "心理学"
    profile_source["profile"] = LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school="测试大学",
            education_level="本科",
            major="计算机科学与技术",
        ),
    )
    app = _app()
    assert app.selectbox[0].options[0] == "计算机科学与技术"


def test_higher_education_manual_fill_requires_confirmation_and_survives_navigation(
    profile_source,
):
    profile_source["profile"] = LearnerProfile(education_type=EducationType.HIGHER)
    app = _app()
    app.selectbox[0].select(MANUAL_SUBJECT).run()
    assert app.text_input[0].label == "填写自定义学科或方向"
    app.text_input[0].set_value("  高等数学  ").run()
    assert _join(app).disabled
    _confirm(app).click().run()
    assert not _join(app).disabled
    app.radio[0].set_value("学习").run()
    app.radio[0].set_value("审核").run()
    assert app.selectbox[0].value == MANUAL_SUBJECT
    assert app.text_input[0].value == "高等数学"
    assert not _join(app).disabled
    app.radio[1].set_value(2).run()
    assert app.selectbox[0].value is None
    assert not app.text_input
    assert _join(app).disabled
    app.radio[1].set_value(1).run()
    assert app.selectbox[0].value == MANUAL_SUBJECT
    assert app.text_input[0].value == "高等数学"
    assert not _join(app).disabled
    app.selectbox[0].select("心理学").run()
    assert not app.text_input
    assert not _join(app).disabled


def test_switching_to_higher_education_requires_fresh_selection(profile_source):
    profile_source["profile"] = _basic("高中", "高三", competition=True)
    app = _app()
    app.selectbox[0].select("强基/竞赛").run()
    app.text_input[0].set_value("数学竞赛").run()
    _confirm(app).click().run()
    assert not _join(app).disabled
    profile_source["profile"] = LearnerProfile(education_type=EducationType.HIGHER)
    app.run()
    assert not app.exception
    assert app.selectbox[0].value is None
    assert _join(app).disabled
    app.selectbox[0].select("计算机科学与技术").run()
    assert not _join(app).disabled


def test_saved_higher_subject_prefills_but_still_requires_confirmation(profile_source):
    profile_source["profile"] = LearnerProfile(education_type=EducationType.HIGHER)
    app = _app("材料力学")
    assert app.selectbox[0].value == MANUAL_SUBJECT
    assert app.text_input[0].value == "材料力学"
    assert _join(app).disabled
    _confirm(app).click().run()
    assert not _join(app).disabled


def test_university_subject_classification_matches_official_catalog():
    assert len(UNIVERSITY_SUBJECTS) == len(set(UNIVERSITY_SUBJECTS))
    assert "计算机科学与技术" in UNIVERSITY_SUBJECTS
    assert "心理学" in UNIVERSITY_SUBJECTS
    assert MANUAL_SUBJECT not in UNIVERSITY_SUBJECTS


def test_existing_political_subject_is_offered_without_rewriting_user_record(profile_source):
    app = _app("道德与法治")
    assert app.selectbox[0].value == "政治"
    assert not _join(app).disabled
    assert normalize_subject_name(" 思想政治 ") == "政治"


@pytest.mark.parametrize("profile", [None, LearnerProfile(), _basic("初中", "未知年级")])
def test_missing_or_unknown_training_configuration_remains_usable_offline(profile_source, profile):
    profile_source["profile"] = profile
    assert subject_selection_policy(profile).manual_only
    app = _app()
    assert not app.exception
    assert not app.selectbox
    app.text_input[0].set_value("数学").run()
    assert _join(app).disabled
    _confirm(app).click().run()
    assert not _join(app).disabled


def test_profile_read_failure_has_visible_manual_fallback(monkeypatch):
    def unavailable():
        raise OSError("test profile storage unavailable")

    monkeypatch.setattr(runtime, "application_training_profile_service", unavailable)
    app = _app()
    assert not app.exception
    assert any("训练配置暂时无法读取" in warning.value for warning in app.warning)
    assert not app.selectbox
    app.text_input[0].set_value("数学").run()
    _confirm(app).click().run()
    assert not _join(app).disabled


def test_language_subject_persists_across_new_sessions_and_imports(
    profile_source, subject_metadata,
):
    app = _app()
    app.selectbox[0].select(MANUAL_SUBJECT).run()
    app.text_input[0].set_value("日语阅读").run()
    assert _join(app).disabled
    _confirm(app).click().run()
    assert read_saved_subject(subject_metadata.database_path) == "日语阅读"
    new_session = _app()
    assert new_session.text_input[0].value == "日语阅读"
    assert not _join(new_session).disabled
    new_session.text_input[0].set_value("英语").run()
    assert _join(new_session).disabled
