"""scripts/zero_batch/summarize_zero_batch.py 的离线汇总测试（合成匿名数据）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.zero_batch.summarize_zero_batch import RecordError, main, summarize

SESSION_HEADER = [
    "session_id",
    "user_pseudonym",
    "user_track",
    "wave",
    "session_date",
    "start_time",
    "end_time",
    "environment",
    "app_version",
    "session_type",
    "first_task_id",
    "first_action_success",
    "user_blocked",
    "trust_failure",
    "session_completion",
    "notes",
]
OBSERVATION_HEADER = [
    "session_id",
    "task_id",
    "task_kind",
    "user_track",
    "task_success",
    "first_action",
    "first_action_success",
    "user_blocked",
    "query",
    "expected",
    "actual",
    "source_opened",
    "source_correct",
    "user_understood_source",
    "error_type",
    "recovery_event",
    "recovery_success",
    "user_judgment",
    "user_comment",
    "observer_note",
    "severity",
]
TURN_HEADER = [
    "session_id",
    "task_id",
    "turn_index",
    "query",
    "answer_summary",
    "citations_given",
    "classification",
    "commission",
    "fabrication",
    "silent_stale",
    "version_condition_object_error",
    "evidence_error",
    "experience_authority_error",
    "honest_omission",
    "out_of_scope",
    "trust_failure",
    "user_blocked",
    "recovery_event",
    "recovery_success",
    "invalid",
    "notes",
]
INCIDENT_HEADER = [
    "incident_id",
    "session_id",
    "task_id",
    "severity",
    "error_type",
    "summary",
    "evidence_path",
    "reported_to_owner",
    "follow_up",
]
FEEDBACK_HEADER = [
    "user_pseudonym",
    "user_track",
    "session_id",
    "q1_want_to_solve",
    "q2_stuck_step",
    "q3_untrusted_answer",
    "q4_miss_but_exists",
    "q5_most_valuable",
    "q6_least_useful",
    "q7_return_intent",
    "q7_reason",
    "q8_next_request",
    "free_text",
    "value_signals",
    "top_value_feature",
    "mental_model",
    "teachback_done",
    "teachback_q1",
    "teachback_q2",
    "teachback_q3",
    "teachback_q4",
    "teachback_q5",
    "feature_requests",
]


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _base_data_dir(tmp_path: Path) -> Path:
    """两 formal session + 一 rehearsal session；3 任务 4 turn（1 commission + 1 omission）。"""
    data_dir = tmp_path / "zb"
    data_dir.mkdir()
    _write(
        data_dir / "sessions.csv",
        SESSION_HEADER,
        [
            ["ZB-A-20260915-001", "U-01", "A", "1", "2026-09-15", "10:00", "11:00",
             "8501_formal", "v0.8.4", "NEW", "G01", "true", "false", "false", "completed", ""],
            ["ZB-D-20260916-001", "U-02", "D", "1", "2026-09-16", "14:00", "15:00",
             "8501_formal", "v0.8.4", "NEW", "G05", "false", "true", "true", "completed", ""],
            ["ZB-B-20260917-001", "U-03", "B", "1", "2026-09-17", "09:00", "09:30",
             "8511_rehearsal", "v0.8.4", "NEW", "G01", "true", "false", "false", "completed", ""],
        ],
    )
    _write(
        data_dir / "observations.csv",
        OBSERVATION_HEADER,
        [
            ["ZB-A-20260915-001", "G05", "GUIDED", "A", "success", "打开Agent页", "true", "false",
             "钢的含碳量界限", "2.11%", "2.11%", "true", "true", "yes", "NONE",
             "false", "false", "", "", "", "NONE"],
            ["ZB-A-20260915-001", "U01", "USER_OWNED", "A", "partial", "先去搜索", "true", "false",
             "我的泵扬程", "扬程值", "证据不足", "false", "na", "na", "HONEST_OMISSION",
             "true", "true", "一般", "", "", "NONE"],
            ["ZB-D-20260916-001", "G06", "GUIDED", "D", "failed", "点回答里的来源", "true", "true",
             "打开引用", "原文页", "引用打不开", "true", "false", "no", "EVIDENCE",
             "false", "false", "", "", "", "P1"],
            ["ZB-B-20260917-001", "G01", "GUIDED", "B", "success", "看首页", "true", "false",
             "看懂首页", "能复述", "能复述", "false", "na", "na", "NONE",
             "false", "false", "", "", "", "NONE"],
        ],
    )
    _write(
        data_dir / "turns.csv",
        TURN_HEADER,
        [
            ["ZB-A-20260915-001", "G05", "1", "钢的含碳量界限", "2.11%", "true", "pass",
             "false", "false", "false", "false", "false", "false", "false", "false",
             "false", "false", "false", "false", "false", ""],
            ["ZB-A-20260915-001", "U01", "1", "我的泵扬程", "证据不足", "false",
             "honest_omission_recall_gap", "false", "false", "false", "false", "false", "false",
             "true", "false", "false", "false", "true", "true", "false", ""],
            ["ZB-A-20260915-001", "U01", "2", "重问扬程", "仍不足", "false", "pass",
             "false", "false", "false", "false", "false", "false", "false", "false",
             "false", "false", "false", "false", "false", "快照失败重记为invalid样例不适用"],
            ["ZB-D-20260916-001", "G06", "1", "打开引用", "引用指向错误页", "true",
             "commission_wrong_evidence", "true", "false", "false", "false", "true", "false",
             "false", "false", "true", "false", "false", "false", "false", ""],
        ],
    )
    _write(
        data_dir / "incidents.csv",
        INCIDENT_HEADER,
        [
            ["ZBINC-20260916-001", "ZB-D-20260916-001", "G06", "P1", "EVIDENCE",
             "引用指向错误页", "incidents/ZBINC-20260916-001.md", "yes", "停止扩大评估"],
        ],
    )
    _write(
        data_dir / "feedback.csv",
        FEEDBACK_HEADER,
        [
            ["U-01", "A", "ZB-A-20260915-001", "查错题", "", "", "扬程那次", "Agent回答",
             "", "yes", "能查到出处", "更快导入", "", "VS_PAST_PAIN", "Agent回答",
             "PARTIAL", "true", "correct", "correct", "partial", "correct", "correct", ""],
            ["U-02", "D", "ZB-D-20260916-001", "查参数", "引用打不开", "有一次", "",
             "引用出处", "引用稳定性", "unsure", "引用错了", "修引用", "", "", "",
             "INCORRECT", "false", "na", "na", "na", "na", "na", "移动端"],
        ],
    )
    return data_dir


def test_summarize_computes_rates_and_excludes_rehearsal(tmp_path: Path) -> None:
    data_dir = _base_data_dir(tmp_path)
    out_dir = tmp_path / "out"
    metrics = summarize(data_dir, out_dir)

    assert metrics["data_layer"] == "REAL_USER"
    assert metrics["scale"]["sessions"] == 2
    assert metrics["scale"]["rehearsal_sessions_excluded"] == 1
    assert metrics["scale"]["valid_turns"] == 4
    # turn 级：1 commission / 4 valid；1 honest omission / 4 valid
    assert metrics["turn_level"]["commission"]["numerator"] == 1
    assert metrics["turn_level"]["commission"]["denominator"] == 4
    assert metrics["turn_level"]["honest_omission"]["numerator"] == 1
    # task 级：primary tasks 排除 incomplete；success = G05 only
    assert metrics["task_level"]["task_success_rate"]["numerator"] == 1
    assert metrics["task_level"]["task_success_rate"]["denominator"] == 3
    # session 级：trust failure = ZB-D 会话
    assert metrics["session_level"]["trust_failure"]["numerator"] == 1
    # incident P1 已计数并附证据路径
    assert metrics["incidents"]["counts_by_severity"] == {"P1": 1}
    # user 级反馈
    assert metrics["user_level"]["return_intent"] == {"unsure": 1, "yes": 1}
    assert metrics["user_level"]["mental_model"] == {"INCORRECT": 1, "PARTIAL": 1}

    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "REAL_USER" in summary
    assert "1/4" in summary
    payload = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["metrics_version"] == "ZERO_BATCH_METRICS_V1"


def test_summarize_empty_dir_yields_no_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    metrics = summarize(data_dir, tmp_path / "out-empty")
    assert metrics["no_data"] is True
    assert metrics["scale"]["sessions"] == 0
    assert metrics["turn_level"]["commission"]["rate"] is None


def test_placeholder_row_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "placeholder"
    data_dir.mkdir()
    _write(data_dir / "sessions.csv", SESSION_HEADER, [])
    _write(data_dir / "observations.csv", OBSERVATION_HEADER, [])
    _write(data_dir / "turns.csv", TURN_HEADER, [])
    _write(data_dir / "incidents.csv", INCIDENT_HEADER, [])
    _write(
        data_dir / "feedback.csv",
        FEEDBACK_HEADER,
        [["U-xx", "A|B|C|D|E", "ZB-_-________-___"] + [""] * 20],
    )
    with pytest.raises(RecordError, match="占位符"):
        summarize(data_dir, tmp_path / "out-placeholder")


def test_turn_index_gap_is_rejected(tmp_path: Path) -> None:
    data_dir = _base_data_dir(tmp_path)
    rows = (data_dir / "turns.csv").read_text(encoding="utf-8").splitlines()
    rows.append(
        "ZB-A-20260915-001,G05,3,补录,值,true,pass,false,false,false,false,false,false,"
        "false,false,false,false,false,false,false,跳号"
    )
    (data_dir / "turns.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="turn_index 不连续"):
        summarize(data_dir, tmp_path / "out-gap")


def test_p1_incident_requires_evidence_path(tmp_path: Path) -> None:
    data_dir = _base_data_dir(tmp_path)
    rows = (data_dir / "incidents.csv").read_text(encoding="utf-8").splitlines()
    rows.append("ZBINC-20260918-001,ZB-A-20260915-001,G05,P1,COMMISSION,x,,no,")
    (data_dir / "incidents.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="evidence_path"):
        summarize(data_dir, tmp_path / "out-incident")


def test_main_cli_reports_failure_and_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _base_data_dir(tmp_path)
    assert main(["--data-dir", str(data_dir), "--out", str(tmp_path / "o1")]) == 0
    assert "OK" in capsys.readouterr().out

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "sessions.csv").write_text("session_id\n", encoding="utf-8")
    assert main(["--data-dir", str(bad), "--out", str(tmp_path / "o2")]) == 1
    assert "缺少必需列" in capsys.readouterr().err
