"""GMT+8 conversion, UTC preservation, and real homepage/import UI regressions."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import src.config as config
import src.runtime as runtime
import src.time_display as time_display
import src.workspace_ui as workspace_ui
from src.database import Database
from src.models import ImportStatus
from src.time_display import BEIJING_TIMEZONE, format_beijing_time, to_beijing_time

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [
    datetime(2026, 10, 5, 4, 36, tzinfo=UTC),
    datetime(2026, 10, 5, 4, 36),
    "2026-10-05T04:36:00+00:00",
    "2026-10-05T04:36:00Z",
    "2026-10-05 04:36:00",
    "2026-10-05T12:36:00+08:00",
    datetime(2026, 10, 4, 23, 36, tzinfo=timezone(timedelta(hours=-5))),
])
def test_utc_and_existing_offsets_display_same_instant_once(value):
    assert format_beijing_time(value) == "2026-10-05 12:36"
    assert format_beijing_time(value, fmt="%Y-%m-%d %H:%M:%S") == "2026-10-05 12:36:00"


def test_conversion_handles_date_rollover_without_mutating_input() -> None:
    original = datetime(2026, 10, 5, 23, 59, tzinfo=UTC)
    assert format_beijing_time(original) == "2026-10-06 07:59"
    assert original.hour == 23 and original.tzinfo is UTC
    assert to_beijing_time(original).utcoffset() == timedelta(hours=8)


def test_clock_always_requests_explicit_gmt8(monkeypatch) -> None:
    class ControlledClock:
        @staticmethod
        def now(tz):
            assert tz is BEIJING_TIMEZONE
            return datetime(2026, 10, 5, 4, 36, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(time_display, "datetime", ControlledClock)
    assert time_display.beijing_now().hour == 12


@pytest.mark.parametrize("value", [None, ""])
def test_missing_time_has_explicit_placeholder(value):
    assert format_beijing_time(value) == "—"
    assert format_beijing_time(value, empty="未排期") == "未排期"


def test_invalid_legacy_time_remains_visible() -> None:
    assert format_beijing_time("旧记录未保存时间") == "旧记录未保存时间"


def _isolated_shell(tmp_path: Path, monkeypatch) -> None:
    settings = SimpleNamespace(data_dir=tmp_path, host="127.0.0.1", port=8501)
    monkeypatch.setattr(config, "runtime_settings", lambda: settings)
    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    monkeypatch.setattr(runtime, "application_startup_reconciliation", lambda: None)


@pytest.mark.parametrize("empty", [True, False])
def test_homepage_clock_present_on_first_use_and_normal_home(tmp_path, monkeypatch, empty):
    _isolated_shell(tmp_path, monkeypatch)
    database = Database(tmp_path / "knowledge.db")
    if not empty:
        database.create_document(
            title="时间测试", filename="time.pdf", source_path=tmp_path / "time.pdf",
            sha256="4" * 64,
        )
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    now = [datetime(2026, 10, 6, 7, 36, tzinfo=BEIJING_TIMEZONE)]
    monkeypatch.setattr(workspace_ui, "beijing_now", lambda: now[0])
    monkeypatch.setattr(time_display, "beijing_now", lambda: now[0])
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    clock_markup = next(
        element.value for element in app.markdown if 'class="ekb-clock"' in element.value
    )
    assert 'aria-label="北京时间 GMT+8"' in clock_markup
    assert "2026-10-06 07:36" in clock_markup
    assert "+08:00" in clock_markup
    assert "GMT+8" in clock_markup
    if not empty:
        assert any("2026年10月06日" in element.value for element in app.markdown)
    now[0] += timedelta(minutes=1)
    app.run(timeout=30)
    assert not app.exception
    assert any("2026-10-06 07:37" in element.value for element in app.markdown)


def test_import_history_displays_1236_for_stored_0436_utc(tmp_path, monkeypatch):
    _isolated_shell(tmp_path, monkeypatch)
    stored = datetime(2026, 10, 5, 4, 36, tzinfo=UTC)
    record = SimpleNamespace(
        title="上传测试", filename="upload.pdf", status=ImportStatus.COMPLETED,
        started_at=stored, total_pages=1, processed_pages=1, text_pages=1,
        review_pages=0, failed_pages=0, error_message="",
    )
    monkeypatch.setattr(
        runtime, "application_database",
        lambda: SimpleNamespace(list_import_records=lambda: [record]),
    )
    app = AppTest.from_file(str(ROOT / "pages" / "2_导入记录.py")).run(timeout=30)
    assert not app.exception
    assert any("开始：2026-10-05 12:36:00" in caption.value for caption in app.caption)
    assert not any('class="ekb-clock"' in element.value for element in app.markdown)
    assert record.started_at is stored and stored.hour == 4


def test_notes_and_saved_memory_dates_share_fixed_timezone() -> None:
    from src.knowledge_memory_ui import _friendly_date
    from src.note_list_ui import _format_time as list_time
    from src.note_ui import _format_time as detail_time

    utc = datetime(2026, 10, 5, 23, 36, tzinfo=UTC)
    assert list_time(utc) == detail_time(utc) == "2026-10-06 07:36"
    assert _friendly_date(utc) == "10 月 6 日"


def test_human_report_export_uses_gmt8_but_record_keeps_utc() -> None:
    from src.learning_report_models import LearningReport

    original = "2026-10-05T04:36:00+00:00"
    report = LearningReport(
        report_id="test-time", user_id="local", subject="数学", generated_at=original,
    )
    assert "2026-10-05 12:36 (GMT+8)" in report.to_markdown()
    assert report.generated_at == report.to_dict()["generated_at"] == original
