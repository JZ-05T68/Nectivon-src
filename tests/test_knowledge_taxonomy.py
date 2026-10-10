"""Offline regression coverage for two-level nebulae and durable classifications."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src.knowledge_starmap_component as component
import src.runtime as runtime
from src.database import Database
from src.knowledge_graph_service import KnowledgeGraph, KnowledgeGraphService
from src.knowledge_object_service import KnowledgeObjectService, KnowledgeObjectValidationError
from src.knowledge_taxonomy import normalize_classification, subject_tag
from src.migrations import SCHEMA_VERSION, migrate_database


@pytest.mark.parametrize(("subject", "child", "expected"), [
    ("网路安全", "密码学", ("网络安全", "密码学")),
    ("网络空间安全", "Web安全", ("网络安全", "Web 安全")),
    ("计算机科学与技术/数据库", "", ("计算机", "数据库")),
    ("心理学", "数据库", ("心理学", "")),
    (None, "数据库", ("", "")),
    ("虚构学科", "密码学", ("", "")),
])
def test_ai_classification_validates_parent_child_pairs(subject, child, expected) -> None:
    assert normalize_classification(subject, child) == expected


def test_existing_custom_subjects_and_directions_remain_available() -> None:
    assert subject_tag("自定义学科/自定义方向") == "自定义学科/自定义方向"
    assert subject_tag("心理学/自定义方向") == "心理学/自定义方向"
    assert subject_tag("数据结构") == "计算机/数据结构与算法"
    assert subject_tag("网络空间安全") == "网络安全"


def test_nebulae_include_all_subjects_and_drill_down_without_changing_nodes() -> None:
    tags = ["计算机/数据库", "计算机", "心理学/认知心理学", "网络安全/密码学",
            "数学", "物理", "化学", "生物", "地理", "历史"]
    nodes = {f"knowledge:{i}": {"id": f"knowledge:{i}", "tags": [tag]}
             for i, tag in enumerate(tags)}
    nodes["document:1"] = {"id": "document:1", "tags": ["计算机/数据库", "心理学/认知心理学"]}
    links = [{"source": "knowledge:0", "target": "document:1"},
             {"source": "knowledge:2", "target": "document:1"}]
    graph = KnowledgeGraph(nodes, links, len(nodes), len(nodes))
    overview = graph.star_payload()
    assert len(overview["config"]["constellations"]["groups"]) == 9
    assert overview["config"]["constellations"]["nested"] == "top"
    detail = graph.star_payload(subject="计算机")
    assert detail["config"]["constellations"]["nested"] == "full"
    assert {note["id"] for note in detail["vault"]["notes"]} == {
        "knowledge:0", "knowledge:1", "document:1"}
    assert detail["vault"]["links"] == [links[0]]
    assert detail["vault"]["notes"][-1]["tags"] == ["计算机/数据库"]
    groups = detail["config"]["constellations"]["groups"]
    assert {group["name"] for group in groups} >= {"数据库", "人工智能", "待细分"}
    assert nodes["document:1"]["tags"] == ["计算机/数据库", "心理学/认知心理学"]
    with pytest.raises(ValueError, match="不存在"):
        graph.star_payload(subject="不存在")


def test_v35_upgrade_keeps_existing_knowledge_and_creates_verified_backup(tmp_path) -> None:
    database = Database(tmp_path / "knowledge.db")
    knowledge = KnowledgeObjectService(database).create(
        kind="concept", title="已有知识", content="已有原文", epistemic_basis="personal_judgment"
    ).knowledge_object
    with database._connection() as connection:
        connection.execute("DROP TABLE knowledge_subject_classifications")
        connection.execute("DELETE FROM schema_migrations WHERE version=36")
    backup = migrate_database(database.database_path)
    assert backup is not None
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 35
        assert connection.execute("SELECT content FROM knowledge_objects").fetchone()[0] == (
            "已有原文")
    assert KnowledgeObjectService(database).get(knowledge.id) == knowledge
    with database._connection() as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == (
            SCHEMA_VERSION)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrate_database(database.database_path) is None


def test_classification_and_knowledge_creation_roll_back_together(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "knowledge.db")
    service = KnowledgeObjectService(database)
    with pytest.raises(KnowledgeObjectValidationError, match="二级方向"):
        service.create(kind="concept", title="错配", content="原文",
                       epistemic_basis="personal_judgment",
                       subject="心理学", subdiscipline="数据库")

    def fail_revision(*args, **kwargs):
        raise RuntimeError("模拟写入失败")

    monkeypatch.setattr(service, "_insert_revision", fail_revision)
    with pytest.raises(RuntimeError, match="模拟写入失败"):
        service.create(kind="concept", title="索引", content="原文",
                       epistemic_basis="personal_judgment",
                       subject="计算机", subdiscipline="数据库")
    with database._connection() as connection:
        for table in ("knowledge_objects", "knowledge_subject_classifications"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_offline_ui_expands_second_level_nebulae(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "knowledge.db")
    KnowledgeObjectService(database).create(
        kind="concept", title="索引", content="数据库索引", epistemic_basis="personal_judgment",
        subject="计算机", subdiscipline="数据库")
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    # Keep this classification test independent of optional math/AI rendering.
    monkeypatch.setattr("src.knowledge_graph_ui._render_knowledge_math", lambda *args: None)
    captured = []
    monkeypatch.setattr(component, "render_starmap", lambda payload: captured.append(payload))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="graph_nebula_subject").options == [
        "一级星云全景", "计算机", "心理学", "网络安全"]
    app.selectbox(key="graph_nebula_subject").select("计算机").run(timeout=30)
    assert not app.exception
    assert captured[-1]["vaultName"] == "计算机 · 二级星云"
    assert captured[-1]["config"]["constellations"]["groups"][6]["name"] == "数据库"
    assert KnowledgeGraphService(database).snapshot().nodes["knowledge:1"]["tags"] == [
        "计算机/数据库"]
