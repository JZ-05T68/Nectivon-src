"""Multi-subject metadata keeps one knowledge object, sources and upgrade safety."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from streamlit.testing.v1 import AppTest

import src.migrations as migrations
from src.database import Database
from src.knowledge_graph_service import KnowledgeGraphService
from src.knowledge_object_service import KnowledgeObjectService, KnowledgeObjectValidationError
from src.knowledge_subject_service import KnowledgeSubjectService


def classifications(database, identifier):
    with database._connection() as connection:
        return {tuple(row) for row in connection.execute(
            "SELECT subject, subdiscipline FROM knowledge_subject_classifications "
            "WHERE knowledge_object_id = ?", (identifier,),
        )}


def make_v36(database):
    """Recreate the actual v36 primary key in a synthetic latest-schema fixture."""

    with database._connection() as connection:
        rows = connection.execute("SELECT * FROM knowledge_subject_classifications").fetchall()
        connection.execute("DROP TABLE knowledge_subject_classifications")
        connection.execute("DELETE FROM schema_migrations WHERE version>=36")
    with closing(sqlite3.connect(database.database_path)) as connection:
        migrations._apply_version_thirty_six(connection)
        connection.executemany(
            "INSERT INTO knowledge_subject_classifications VALUES (?, ?, ?)", rows,
        )
        connection.commit()


@pytest.fixture()
def existing(tmp_path):
    database = Database(tmp_path / "knowledge.db")
    knowledge = KnowledgeObjectService(database).create(
        kind="concept", title="反馈控制", content="闭环控制通过反馈减小误差。",
        epistemic_basis="personal_judgment", subject="控制科学与工程", subdiscipline="自动控制原理",
    ).knowledge_object
    return database, knowledge


def test_append_keeps_existing_direction_content_revisions_and_one_object(existing):
    database, knowledge = existing
    with database._connection() as connection:
        before = [tuple(row) for row in connection.execute(
            "SELECT * FROM knowledge_object_revisions")]
    service = KnowledgeSubjectService(database)
    assert service.add_subjects(knowledge.id, ["数学", "电气工程", "数学"]) == 2
    assert service.add_subjects(knowledge.id, ["控制科学与工程", "数学"]) == 0
    assert classifications(database, knowledge.id) == {
        ("控制科学与工程", "自动控制原理"), ("数学", ""), ("电气工程", ""),
    }
    assert KnowledgeObjectService(database).get(knowledge.id) == knowledge
    with database._connection() as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM knowledge_object_revisions")] == before
        assert connection.execute("SELECT count(*) FROM knowledge_objects").fetchone()[0] == 1
    graph = KnowledgeGraphService(Database(database.database_path)).snapshot()
    assert len(graph.nodes) == 1
    assert set(graph.nodes[f"knowledge:{knowledge.id}"]["tags"]) == {
        "控制科学与工程/自动控制原理", "数学", "电气工程",
    }
    assert set(graph.primary_subjects()) == {"控制科学与工程", "数学", "电气工程"}


def test_bad_batch_and_missing_object_do_not_write(existing):
    database, knowledge = existing
    service = KnowledgeSubjectService(database)
    with pytest.raises(ValueError, match="学科"):
        service.add_subjects(knowledge.id, ["数学", "未知学科"])
    with pytest.raises(ValueError, match="不存在"):
        service.add_subjects(999, ["数学"])
    assert classifications(database, knowledge.id) == {("控制科学与工程", "自动控制原理")}
    with pytest.raises(KnowledgeObjectValidationError):
        KnowledgeObjectService(database).create(
            kind="concept", title="错误分类", content="内容", epistemic_basis="personal_judgment",
            classifications=(("数学", "微积分"), ("英语", "微积分")),
        )
    assert KnowledgeObjectService(database).count() == 1


def test_v36_upgrade_keeps_rows_and_verified_backup_then_allows_multiple_subjects(existing):
    database, knowledge = existing
    make_v36(database)
    backup = migrations.migrate_database(database.database_path)
    assert backup is not None
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 36
        assert connection.execute("SELECT * FROM knowledge_subject_classifications").fetchall() == [
            (knowledge.id, "控制科学与工程", "自动控制原理")]
    assert classifications(database, knowledge.id) == {("控制科学与工程", "自动控制原理")}
    assert KnowledgeObjectService(database).get(knowledge.id) == knowledge
    assert KnowledgeSubjectService(database).add_subjects(knowledge.id, ["数学"]) == 1
    assert migrations.migrate_database(database.database_path) is None
    with database._connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v37_failure_rolls_back_table_rows_and_ledger(existing, monkeypatch):
    database, knowledge = existing
    make_v36(database)

    def fail(connection):
        raise migrations.MigrationError("synthetic validation failure")

    monkeypatch.setattr(migrations, "_validate_version_thirty_seven_schema", fail)
    with pytest.raises(migrations.MigrationError, match="synthetic"):
        migrations.migrate_database(database.database_path)
    assert classifications(database, knowledge.id) == {("控制科学与工程", "自动控制原理")}
    with database._connection() as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 36
        assert [row[5] for row in connection.execute(
            "PRAGMA table_info(knowledge_subject_classifications)")] == [1, 0, 0]
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='knowledge_subject_classifications_v37'",
        ).fetchall()
    assert list(database.database_path.parent.glob("backups/*.v36.*.db"))


def test_append_ui_writes_only_after_confirmation(existing):
    database, knowledge = existing
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from src.database import Database\n"
        "from src.knowledge_graph_ui import _render_subject_links\n"
        f"_render_subject_links(Database(Path({str(database.database_path)!r})), {knowledge.id})\n"
    ).run(timeout=30)
    app.multiselect(key=f"graph_subjects_{knowledge.id}").set_value(["数学", "电气工程"]).run()
    assert not app.exception
    assert classifications(database, knowledge.id) == {("控制科学与工程", "自动控制原理")}
    next(button for button in app.button if button.label == "保存学科关联").click().run(timeout=30)
    assert not app.exception
    assert classifications(database, knowledge.id) == {
        ("控制科学与工程", "自动控制原理"), ("数学", ""), ("电气工程", ""),
    }
