"""The public distribution can omit restricted catalog resources honestly."""

from types import SimpleNamespace
from unittest.mock import Mock

from src import training_profile_data, training_profile_ui
from src.training_profile_models import LearnerProfile


def test_missing_catalog_files_return_no_invented_entries(tmp_path, monkeypatch):
    loaders = (
        training_profile_data.load_official_schools,
        training_profile_data.load_undergraduate_majors,
    )
    for loader in loaders:
        loader.cache_clear()
    try:
        with monkeypatch.context() as context:
            context.setattr(training_profile_data, "_DATA_DIR", tmp_path)
            assert training_profile_data.load_official_schools() == []
            assert training_profile_data.load_undergraduate_majors() == []
    finally:
        for loader in loaders:
            loader.cache_clear()


def test_missing_catalog_disables_higher_profile_edit_without_false_school_claim(monkeypatch):
    ui = SimpleNamespace(
        session_state={},
        markdown=Mock(),
        info=Mock(),
        caption=Mock(),
        button=Mock(return_value=False),
    )
    monkeypatch.setattr(training_profile_ui, "st", ui)
    monkeypatch.setattr(training_profile_ui, "load_official_schools", lambda: [])
    monkeypatch.setattr(training_profile_ui, "load_undergraduate_majors", lambda: [])
    service = Mock()
    training_profile_ui._render_higher_education_section(service, LearnerProfile(), True)
    message = ui.info.call_args.args[0]
    assert "当前公开发行版未包含" in message
    assert "不存在此校" not in message
    service.save_higher_profile.assert_not_called()


def test_missing_catalog_rejects_save_with_explicit_unavailable_message(monkeypatch):
    ui = SimpleNamespace(session_state={"profile_edu_type": "高等教育"}, error=Mock())
    monkeypatch.setattr(training_profile_ui, "st", ui)
    monkeypatch.setattr(training_profile_ui, "load_official_schools", lambda: [])
    monkeypatch.setattr(training_profile_ui, "load_undergraduate_majors", lambda: [])
    service = Mock()
    assert training_profile_ui._trigger_save_from_state(service) is False
    assert "未包含该目录数据" in ui.error.call_args.args[0]
    service.save_higher_profile.assert_not_called()
