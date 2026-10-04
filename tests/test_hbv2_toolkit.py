"""HBENCH_V2 toolkit 守卫测试（spec §17 数据完整性机制）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import scripts.hbv2_toolkit as hbv2
from scripts.hbv2_toolkit import (
    BENCHMARK_VERSION,
    DriftError,
    ValidationError,
    _build_sandbox_db,
    _smoke_interaction,
    _smoke_session,
    check_corpus_drift,
    check_leakage,
    check_raw_manifest,
    compute_metrics,
    freeze_corpus,
    record_interaction,
    register_session,
    update_campaign_state,
    validate_all,
    write_raw_manifest,
)


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    _build_sandbox_db(db_path)
    record = freeze_corpus(tmp_path, db_path, notes="pytest sandbox")
    monkeypatch.setattr(hbv2, "_SMOKE_CORPUS_REF", record["corpus_ref_id"])
    return tmp_path


def _register_pair(art_dir: Path) -> tuple[str, str]:
    s1, s2 = "SIMV2-A-20260101-001", "SIMV2-B-20260101-001"
    register_session(art_dir, _smoke_session(s1, "A", "CONTINUOUS", "success"))
    register_session(art_dir, _smoke_session(s2, "B", "NEW", "success"))
    return s1, s2


def test_session_id_format_enforced(sandbox: Path) -> None:
    with pytest.raises(ValidationError, match="SIMV2"):
        register_session(sandbox, _smoke_session("SIMV2-E-20260101-001", "A", "NEW", "success"))
    with pytest.raises(ValidationError, match="SIMV2"):
        register_session(sandbox, _smoke_session("SIMV2-A-2026010-01", "A", "NEW", "success"))


def test_duplicate_session_rejected(sandbox: Path) -> None:
    _register_pair(sandbox)
    with pytest.raises(ValidationError, match="重复"):
        register_session(sandbox, _smoke_session("SIMV2-A-20260101-001", "A", "NEW", "success"))


def test_real_user_id_rejected_in_simulated_layer(sandbox: Path) -> None:
    with pytest.raises(ValidationError, match="SESSION_ID"):
        register_session(sandbox, _smoke_session("ZB-A-20260101-001", "A", "NEW", "success"))


def test_unknown_corpus_ref_rejected(sandbox: Path) -> None:
    row = _smoke_session("SIMV2-A-20260101-001", "A", "NEW", "success")
    row["CORPUS_REF"] = "NOT-FROZEN"
    with pytest.raises(ValidationError, match="CORPUS_REF"):
        register_session(sandbox, row)


def test_interaction_uniqueness_and_sequence(sandbox: Path) -> None:
    s1, _ = _register_pair(sandbox)
    record_interaction(sandbox, _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass"))
    record_interaction(sandbox, _smoke_interaction(s1, 2, "A", "CONTINUOUS", "pass"))
    with pytest.raises(ValidationError, match="重复"):
        record_interaction(sandbox, _smoke_interaction(s1, 2, "A", "CONTINUOUS", "pass"))
    with pytest.raises(ValidationError, match="逐条递增"):
        record_interaction(sandbox, _smoke_interaction(s1, 5, "A", "CONTINUOUS", "pass"))


def test_orphan_session_rejected(sandbox: Path) -> None:
    _register_pair(sandbox)
    with pytest.raises(ValidationError, match="未注册"):
        orphan = _smoke_interaction("SIMV2-C-20260101-009", 1, "C", "NEW", "pass")
        record_interaction(sandbox, orphan)


def test_commission_requires_trust_failure(sandbox: Path) -> None:
    s1, _ = _register_pair(sandbox)
    bad = _smoke_interaction(s1, 1, "A", "CONTINUOUS", "commission_wrong_fact", COMMISSION=True)
    with pytest.raises(ValidationError, match="TRUST_FAILURE"):
        record_interaction(sandbox, bad)


def test_omission_flag_mapping(sandbox: Path) -> None:
    s1, _ = _register_pair(sandbox)
    bad = _smoke_interaction(s1, 1, "A", "CONTINUOUS", "honest_omission_recall_gap")
    with pytest.raises(ValidationError, match="HONEST_OMISSION"):
        record_interaction(sandbox, bad)
    good = _smoke_interaction(
        s1, 1, "A", "CONTINUOUS", "honest_omission_recall_gap", HONEST_OMISSION=True
    )
    record_interaction(sandbox, good)


def test_recovery_denominator_requires_predefined_event(sandbox: Path) -> None:
    s1, _ = _register_pair(sandbox)
    bad = _smoke_interaction(
        s1, 1, "A", "CONTINUOUS", "recovery_success", RECOVERY_EVENT=False, RECOVERY_SUCCESS=True
    )
    with pytest.raises(ValidationError, match="RECOVERY"):
        record_interaction(sandbox, bad)


def test_invalid_row_excluded_from_metrics(sandbox: Path) -> None:
    s1, s2 = _register_pair(sandbox)
    record_interaction(sandbox, _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass"))
    record_interaction(
        sandbox,
        _smoke_interaction(
            s1, 2, "A", "CONTINUOUS", "invalid", INVALID=True, NOTES="snapshot timeout"
        ),
    )
    record_interaction(sandbox, _smoke_interaction(s2, 1, "B", "NEW", "pass"))
    metrics = compute_metrics(sandbox)
    assert metrics["valid_interactions"] == 2
    assert metrics["invalid_interactions"] == 1
    assert metrics["commission_count"] == 0


def test_append_only_tamper_detection(sandbox: Path) -> None:
    s1, _ = _register_pair(sandbox)
    write_raw_manifest(sandbox)
    assert not check_raw_manifest(sandbox)["violations"]
    sessions_path = sandbox / "sessions.jsonl"
    original = sessions_path.read_bytes()
    sessions_path.write_bytes(original.replace(b"CONTINUOUS", b"NEW       ", 1))
    violations = check_raw_manifest(sandbox)["violations"]
    assert violations and "sessions.jsonl" in violations[0]
    sessions_path.write_bytes(original)
    assert not check_raw_manifest(sandbox)["violations"]


def test_corpus_drift_fail_fast(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    _build_sandbox_db(db_path)
    frozen = freeze_corpus(tmp_path, db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO documents VALUES (2, 'new_doc', 'b')")
    conn.commit()
    conn.close()
    report = check_corpus_drift(frozen, db_path)
    assert any("新增文档" in d for d in report["drift"])
    row = _smoke_session("SIMV2-A-20260101-001", "A", "NEW", "success")
    row["CORPUS_REF"] = frozen["corpus_ref_id"]
    with pytest.raises(DriftError):
        register_session(tmp_path, row, db_path)


def test_memory_changes_are_observations_not_drift(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    _build_sandbox_db(db_path)
    freeze_corpus(tmp_path, db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO knowledge_memory_entries VALUES (2, 'active', 'fp-new')")
    conn.commit()
    conn.close()
    frozen = json.loads((tmp_path / "corpus_refs.json").read_text(encoding="utf-8"))["refs"][-1]
    report = check_corpus_drift(frozen, db_path)
    assert report["drift"] == []
    assert report["memory_observations"]["new_memory_ids"] == [2]


def test_leakage_checker_planted_and_clean(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "fake_prompt.py").write_text(
        'PROMPT = "示例：设备 SMOKE-999 力矩 25 Nm。"', encoding="utf-8"
    )
    leak = tmp_path / "leak.json"
    leak.write_text(json.dumps({"device": "SMOKE-999", "answer": "25 Nm"}), encoding="utf-8")
    report = check_leakage([leak], prompt_dir=prompt_dir)
    assert not report["pass"]
    assert any(f["type"] == "device_token" for f in report["findings"])
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"device": "OTHER-111", "answer": "73 °C"}), encoding="utf-8")
    assert check_leakage([clean], prompt_dir=prompt_dir)["pass"]


def test_campaign_state_status_enum(sandbox: Path) -> None:
    state = update_campaign_state(sandbox, {"campaign_status": "RUNNING", "current_phase": 1})
    assert state["campaign_status"] == "RUNNING"
    with pytest.raises(ValidationError, match="campaign_status"):
        update_campaign_state(sandbox, {"campaign_status": "WHATEVER"})


def test_validate_all_ledger_consistency(sandbox: Path) -> None:
    s1, s2 = _register_pair(sandbox)
    record_interaction(sandbox, _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass"))
    report = validate_all(sandbox, None, allow_open_sessions=True)
    assert report["pass"]
    closed = validate_all(sandbox, None, allow_open_sessions=False)
    assert not closed["pass"]
    record_interaction(sandbox, _smoke_interaction(s2, 1, "B", "NEW", "pass"))
    assert validate_all(sandbox, None, allow_open_sessions=False)["pass"]


def test_benchmark_version_pinned() -> None:
    assert BENCHMARK_VERSION == "HBENCH_V2"
