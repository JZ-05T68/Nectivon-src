"""Offline shared-data, migration and UI contracts for the v0.8.7 star map."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import src.knowledge_starmap_component as component
import src.runtime as runtime
from scripts.build_starmap_component import build_component
from src.database import Database
from src.knowledge_graph_service import KnowledgeGraphService
from src.knowledge_math_service import knowledge_math_display
from src.knowledge_mining_service import (
    KnowledgeMiningAIService,
    KnowledgeMiningDraft,
    MinedKnowledgePoint,
)
from src.knowledge_object_service import KnowledgeObjectService
from src.learning_workflow_service import (
    MasteryService,
    QuestionOrganizationService,
    QuestionService,
)
from src.migrations import migrate_database
from src.workspace_ui import _NAVIGATION


@pytest.fixture(autouse=True)
def isolated_math_runtime(tmp_path, monkeypatch) -> None:
    """Never load real credentials or write application caches in UI tests."""

    monkeypatch.setattr(runtime, "application_ai_provider", lambda: None)
    monkeypatch.setattr(runtime, "application_settings",
                        lambda: SimpleNamespace(cache_dir=tmp_path / "cache"))


@pytest.fixture()
def assets(tmp_path: Path) -> tuple[Database, int, int]:
    """Build synthetic learning and knowledge assets in one temporary database."""

    database = Database(tmp_path / "knowledge.db")
    document = database.create_document(
        title="星图验收资料", filename="fixture.pdf", source_path=tmp_path / "fixture.pdf",
        sha256="b" * 64, page_count=1,
    )
    page = database.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "page.png",
        extracted_text="函数定义域与导数", status="ready",
    )
    question = QuestionService(database).create_question_item(
        document_id=document.id, page_id=page.id, question_kind="typical",
        question_number="1", stem_text="求函数的定义域", subject="数学",
    )
    knowledge = KnowledgeObjectService(database).create(
        kind="concept", title="定义域", content="函数自变量的取值范围",
        epistemic_basis="source_derived", source_links=[("page", page.id, "人工核对")],
    ).knowledge_object
    return database, question.id, knowledge.id


def test_live_graph_reuses_questions_relations_sources_and_mastery(assets) -> None:
    database, question_id, knowledge_id = assets
    service = KnowledgeGraphService(database)
    service.link_question(question_id, knowledge_id, note="先检查定义域")
    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(family_kind="method", title="先检查定义域")
    organization.assign_to_family(question_id, family_id, relation="member")
    graph = service.snapshot()
    assert {"question:1", "knowledge:1", "family:1", "page:1", "document:1"} == set(graph.nodes)
    assert graph.nodes["question:1"]["mastery"] == (
        MasteryService(database).mastery_states(question_id)
    )
    assert any(edge["label"] == "涉及知识点" for edge in graph.links)
    assert any(edge["label"] == "归纳成员" for edge in graph.links)
    assert any(edge["target"] == "page:1" for edge in graph.links)
    MasteryService(database).record_evidence(
        question_id, event_type="practice", result="correct", independence="independent",
        source="system_training", idempotency_key="graph-synthetic-practice",
    )
    assert service.snapshot().nodes["question:1"]["mastery"]["do_state"] == "independent"
    KnowledgeObjectService(database).update_content(knowledge_id, title="定义域核对")
    assert service.snapshot().nodes["knowledge:1"]["title"] == "定义域核对"
    QuestionService(database).update_question_item(question_id, stem_text="判断新的定义域")
    assert "新的定义域" in service.snapshot().nodes["question:1"]["title"]
    with database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM question_items").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_objects").fetchone()[0] == 1


def test_link_is_idempotent_and_preserves_user_note(assets) -> None:
    database, question_id, knowledge_id = assets
    service = KnowledgeGraphService(database)
    service.link_question(question_id, knowledge_id, note="保留说明")
    service.link_question(question_id, knowledge_id, note="不得覆盖")
    with database._connection() as connection:
        rows = connection.execute("SELECT note FROM question_knowledge_links").fetchall()
    assert [row[0] for row in rows] == ["保留说明"]
    with pytest.raises(ValueError, match="不存在"):
        service.link_question(999, knowledge_id)
    with pytest.raises(ValueError, match="1000"):
        service.link_question(question_id, knowledge_id, note="x" * 1001)


def test_shared_knowledge_relations_keep_direction_and_type(assets) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    second = service.create(kind="concept", title="求导", content="求导前核对定义域",
                            epistemic_basis="personal_judgment").knowledge_object
    service.add_relation(second.id, knowledge_id, relation_type="requires", description="前置条件")
    edges = KnowledgeGraphService(database).snapshot().links
    assert {"source": "knowledge:2", "target": "knowledge:1", "label": "依赖",
            "note": "前置条件"} in edges


@pytest.mark.parametrize("shared_subjects", [False, True])
def test_cross_subject_relation_survives_reopening_without_duplicating_knowledge(
    tmp_path, shared_subjects,
) -> None:
    database = Database(tmp_path / "knowledge.db")
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="微积分", content="变化率与积分", epistemic_basis="personal_judgment",
        classifications=(("数学", "微积分"),),
    ).knowledge_object
    target_subjects = (("物理", "力学"),)
    if shared_subjects:
        # Both primary locations can be mathematics while the relation spans physics too.
        target_subjects += (("数学", "微积分"),)
    target = service.create(
        kind="concept", title="运动分析", content="用微积分分析速度",
        epistemic_basis="personal_judgment",
        classifications=target_subjects,
    ).knowledge_object
    unrelated = service.create(
        kind="concept", title="化学", content="未确认与上述知识点的关系",
        epistemic_basis="personal_judgment", subject="化学",
    ).knowledge_object
    relation = service.add_relation(
        target.id, source.id, relation_type="requires", description="运动分析需要微积分",
    )
    reopened = Database(database.database_path)
    graph = KnowledgeGraphService(reopened).snapshot()
    payload = graph.star_payload()
    assert payload["vault"]["links"] == [{
        "source": f"knowledge:{target.id}", "target": f"knowledge:{source.id}",
        "label": "依赖", "note": "运动分析需要微积分", "cross_subject": True,
    }]
    assert "cross_subject" not in graph.links[0]  # Projection never mutates stored relations.
    assert len(payload["vault"]["notes"]) == 3
    assert all(f"knowledge:{unrelated.id}" not in (link["source"], link["target"])
               for link in payload["vault"]["links"])
    assert service.get(source.id) == source and service.get(target.id) == target
    assert service.relations(source.id) == [relation]
    # A search that hides one endpoint cannot leave a dangling visual line.
    assert KnowledgeGraphService(reopened).snapshot(query="运动分析").star_payload()[
        "vault"]["links"] == []


def test_same_subject_relation_and_source_links_keep_their_existing_projection(assets) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    first = service.create(
        kind="concept", title="导数", content="同学科知识", epistemic_basis="personal_judgment",
        subject="数学", subdiscipline="微积分",
    ).knowledge_object
    second = service.create(
        kind="concept", title="矩阵", content="同学科另一方向", epistemic_basis="personal_judgment",
        subject="数学", subdiscipline="线性代数",
    ).knowledge_object
    service.add_relation(first.id, second.id, relation_type="relates_to", description="已核对关系")
    service.add_relation(second.id, knowledge_id, relation_type="relates_to")
    graph = KnowledgeGraphService(database).snapshot()
    assert graph.star_payload()["vault"]["links"] == graph.links


def test_cross_subject_relation_editor_saves_explicit_endpoints(assets, monkeypatch) -> None:
    database, _, _ = assets
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="微积分", content="数学知识", epistemic_basis="personal_judgment",
        subject="数学", subdiscipline="微积分",
    ).knowledge_object
    target = service.create(
        kind="concept", title="运动分析", content="物理知识", epistemic_basis="personal_judgment",
        subject="物理", subdiscipline="力学",
    ).knowledge_object
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    source_picker = next(value for value in app.selectbox if value.label == "起点知识点")
    target_picker = next(value for value in app.selectbox if value.label == "终点知识点")
    assert any("数学" in option for option in source_picker.options)
    assert any("物理" in option for option in target_picker.options)
    source_picker.set_value(f"knowledge:{target.id}")
    target_picker.set_value(f"knowledge:{source.id}")
    next(value for value in app.text_input if value.label == "关系说明").set_value(
        "运动分析需要微积分")
    assert service.relations(target.id) == []
    next(button for button in app.button if button.label == "保存知识关系").click().run(timeout=30)
    assert not app.exception
    relation = service.relations(target.id)[0]
    assert relation.source_ko_id == target.id and relation.target_ko_id == source.id
    assert relation.description == "运动分析需要微积分"
    payload = KnowledgeGraphService(database).snapshot().star_payload()
    assert any(link.get("cross_subject") for link in payload["vault"]["links"])


def test_search_and_cap_preserve_sources_and_never_seed_demo(assets, tmp_path) -> None:
    database, _, _ = assets
    graph = KnowledgeGraphService(database).snapshot(query="取值范围", limit=3)
    assert graph.total == 1 and graph.shown == 1
    assert set(graph.nodes) == {"knowledge:1", "page:1", "document:1"}
    missing = KnowledgeGraphService(database).snapshot(query="不存在%_关键词")
    assert not missing.nodes and missing.total == 0
    empty = KnowledgeGraphService(Database(tmp_path / "empty.db")).snapshot()
    assert empty.star_payload()["vault"]["notes"] == []
    with pytest.raises(ValueError):
        KnowledgeGraphService(database).snapshot(limit=1001)


def test_deleted_source_keeps_question_snapshot_without_false_source(assets) -> None:
    database, _, _ = assets
    with database._connection() as connection:
        connection.execute("UPDATE question_items SET document_id=NULL, page_id=NULL")
    node = KnowledgeGraphService(database).snapshot().nodes["question:1"]
    assert "原始来源已删除" in node["summary"]
    assert "星图验收资料" in node["summary"]


def test_v34_upgrade_is_backed_up_and_repeatable(assets, tmp_path) -> None:
    database, _, _ = assets
    path = tmp_path / "knowledge.db"
    with database._connection() as connection:
        connection.execute("DROP TABLE question_knowledge_links")
        connection.execute("DROP TABLE knowledge_subject_classifications")
        connection.execute("DELETE FROM schema_migrations WHERE version>=35")
    backup = migrate_database(path)
    assert backup is not None and backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 34
        assert connection.execute("SELECT count(*) FROM question_items").fetchone()[0] == 1
    assert migrate_database(path) is None
    assert KnowledgeGraphService(database).snapshot().nodes["question:1"]


def test_navigation_has_two_peer_learning_entries() -> None:
    learning = next(entries for group, entries in _NAVIGATION if group == "学习")
    assert [entry[1] for entry in learning] == ["学习整理", "知识串联"]


def test_component_selection_and_explicit_save_use_same_database(assets, monkeypatch) -> None:
    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap",
                        lambda payload: {"node_id": "knowledge:1", "event_id": "fixture:1"})
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="graph_node_picker").value == "knowledge:1"
    assert any("来源提炼" in value.value for value in app.caption)
    next(value for value in app.text_input if value.label == "知识点名称").set_value("连续性")
    next(value for value in app.text_area if value.label == "知识点内容").set_value("连续性的定义")
    next(button for button in app.button if button.label == "保存修改").click().run(timeout=30)
    assert not app.exception
    assert KnowledgeObjectService(database).count() == 2
    assert any(obj.title == "连续性" for obj in KnowledgeObjectService(database).list())
    assert app.text_input(key="graph_new_knowledge_title").value == ""
    assert app.text_area(key="graph_new_knowledge_content").value == ""


def test_discard_knowledge_draft_clears_fields_without_writing(assets, monkeypatch) -> None:
    database, _, knowledge_id = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    service = KnowledgeObjectService(database)
    before = service.get(knowledge_id)
    revisions_before = len(service.revisions(knowledge_id))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    app.text_input(key="graph_new_knowledge_title").set_value("不保存的草稿")
    app.text_area(key="graph_new_knowledge_content").set_value("不写入知识库")
    app.selectbox(key="graph_new_knowledge_basis").select_index(1)
    app.selectbox(key="graph_new_knowledge_source").select_index(1)
    discard = next(button for button in app.button if button.label == "放弃修改")
    save = next(button for button in app.button if button.label == "保存修改")
    assert discard.proto.type == "secondary"
    assert save.proto.type == "primary"
    discard.click().run(timeout=30)
    assert not app.exception
    assert app.text_input(key="graph_new_knowledge_title").value == ""
    assert app.text_area(key="graph_new_knowledge_content").value == ""
    assert app.selectbox(key="graph_new_knowledge_basis").value.value == "personal_judgment"
    assert app.selectbox(key="graph_new_knowledge_source").value is None
    assert service.count() == 1
    assert service.get(knowledge_id) == before
    assert len(service.revisions(knowledge_id)) == revisions_before


def test_failed_knowledge_save_retains_draft_and_latex(assets, monkeypatch) -> None:
    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    raw_content = r"质量与能量：$E=mc^2$；$$\frac{a}{b}$$。"
    app.text_input(key="graph_new_knowledge_title").set_value("公式原文")
    app.text_area(key="graph_new_knowledge_content").set_value(raw_content)
    app.selectbox(key="graph_new_knowledge_basis").select_index(1)
    next(button for button in app.button if button.label == "保存修改").click().run(timeout=30)
    assert not app.exception
    assert any("请关联一个来源页" in error.value for error in app.error)
    assert app.text_input(key="graph_new_knowledge_title").value == "公式原文"
    assert app.text_area(key="graph_new_knowledge_content").value == raw_content
    assert KnowledgeObjectService(database).count() == 1
    app.selectbox(key="graph_new_knowledge_source").select_index(1)
    next(button for button in app.button if button.label == "保存修改").click().run(timeout=30)
    assert not app.exception
    saved = next(obj for obj in KnowledgeObjectService(database).list() if obj.title == "公式原文")
    assert saved.content == raw_content
    assert len(KnowledgeObjectService(database).get_view(saved.id).sources) == 1


def test_knowledge_save_automatically_typesets_and_selects_new_node(assets, monkeypatch) -> None:
    import src.knowledge_graph_ui as graph_ui

    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    entered, release = Event(), Event()
    tasks = []

    class Provider:
        calls = 0

        def complete(self, prompt, **kwargs):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return SimpleNamespace(text=json.dumps({"title": [], "content": [
                {"text": "x的n次方根", "latex": r"\sqrt[n]{x}"},
            ]}), finish_reason="stop")

    provider = Provider()
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: provider)
    schedule = graph_ui.schedule_knowledge_math

    def capture_task(*args, **kwargs):
        task = schedule(*args, **kwargs)
        tasks.append(task)
        return task

    monkeypatch.setattr(graph_ui, "schedule_knowledge_math", capture_task)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    app.text_input(key="graph_new_knowledge_title").set_value("根式排版")
    app.text_area(key="graph_new_knowledge_content").set_value("说明：x的n次方根。")
    try:
        next(button for button in app.button if button.label == "保存修改").click().run(timeout=30)
        assert not app.exception and entered.wait(timeout=3)
        assert KnowledgeObjectService(database).count() == 2
        saved = next(obj for obj in KnowledgeObjectService(database).list()
                     if obj.title == "根式排版")
        assert app.selectbox(key="graph_node_picker").value == f"knowledge:{saved.id}"
        assert any("AI 正在自动排版" in item.value for item in app.info)
        assert tasks[0] is not None and not tasks[0].done()
    finally:
        release.set()
    assert tasks[0].result(timeout=5)
    # AppTest cannot drive the browser's timed fragments; a fresh reading session
    # checks that the completed cache survives reload without another AI call.
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py"))
    app.session_state["graph_node_picker"] = f"knowledge:{saved.id}"
    app.run(timeout=30)
    assert not app.exception
    assert any(r"$\sqrt[n]{x}$" in item.value for item in app.markdown), (
        app.selectbox(key="graph_node_picker").value, [item.value for item in app.markdown],
        [item.value for item in app.text],
    )
    assert knowledge_math_display(runtime.application_settings().cache_dir,
                                  database.database_path, saved).status == "ready"
    app.run(timeout=30)
    assert provider.calls == 1
    assert KnowledgeObjectService(database).get(saved.id).content == "说明：x的n次方根。"


def test_saved_knowledge_is_visible_when_previous_search_excluded_it(assets, monkeypatch) -> None:
    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    app.text_input(key="graph_query").set_value("定义域").run(timeout=30)
    app.text_input(key="graph_new_knowledge_title").set_value("与查找词无关的根式")
    app.text_area(key="graph_new_knowledge_content").set_value("x的n次方根")
    next(button for button in app.button if button.label == "保存修改").click().run(timeout=30)
    assert not app.exception
    assert app.text_input(key="graph_query").value == ""
    assert app.selectbox(key="graph_node_picker").value == "knowledge:2"
    assert KnowledgeObjectService(database).count() == 2


def test_bundled_view_omits_standalone_ai_and_import_paths() -> None:
    root = Path(__file__).parents[1]
    html = (root / "src/components/knowledge_starmap/index.html").read_text(encoding="utf-8")
    assert "async function wG(){return await window.nectivonInitialPayload;" in html
    assert "setPayload:D9" in html
    assert "local:{canWatch:false," in html
    assert "var KEY = 'starmap.llm'" not in html
    # 语音与手势控制按用户要求启用：识别与指令解析内联，模型资源本地打包。
    assert "webkitSpeechRecognition" in html
    assert "okGesture" in html and "nectivonVoiceBar" in html
    assert "async function z9(){return;" not in html
    # 说话前显式申请麦克风权限；标题+内容片段+识别候选词匹配任意层级节点。
    assert "async function startListen" in html
    assert "getUserMedia({ audio: true })" in html
    assert "function contentScore" in html and "find: function (target, alts)" in html
    assert "function cjkGrams" in html  # 中文词组滑窗兜底，词序不必一致
    assert "connect-src 'self' blob:" in html and "script-src 'self' blob:" in html
    policy_start = html.index("Content-Security-Policy")
    policy = html[policy_start:html.index(" />", policy_start)]
    assert "https:" not in policy and "http://" not in policy
    manifest = json.loads((root / "third_party/star_vault/source_manifest.json").read_text("utf-8"))
    assert len(manifest["files"]) == 12
    for item in manifest["files"]:
        if item["path"].startswith("assets/"):
            bundled = root / "src/components/knowledge_starmap" / item["path"]
            original = root / "third_party/star_vault" / item["path"]
            assert bundled.read_bytes() == original.read_bytes()


def test_component_can_be_rebuilt_without_desktop_or_network(tmp_path: Path) -> None:
    built = build_component(tmp_path / "component")
    shipped = Path(__file__).parents[1] / "src/components/knowledge_starmap/index.html"
    assert built.read_bytes() == shipped.read_bytes()
    for name in ("hand-model.js", "hand-wasm.js", "ok.js"):
        assert (tmp_path / "component" / "assets" / name).exists()


def test_material_mining_panel_degrades_gracefully_without_ai(assets, monkeypatch) -> None:
    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="graph_mining_document") is not None
    assert any("AI 未启用" in value.value for value in app.info)
    assert KnowledgeObjectService(database).count() == 1


def test_material_mining_saves_only_after_explicit_confirmation(assets, monkeypatch) -> None:
    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(component, "render_starmap", lambda payload: None)
    monkeypatch.setattr(runtime, "application_ai_provider",
                        lambda: SimpleNamespace(complete=lambda *args, **kwargs: None))
    page = database.list_pages(1)[0]

    def fake_mine(self, db, document_id):
        return KnowledgeMiningDraft(
            document_id=document_id, document_title="星图验收资料",
            points=(MinedKnowledgePoint(title="闭环控制", content="反馈减小误差。",
                                        importance="primary", page_ids=(page.id,),
                                        page_numbers=(1,)),),
        )

    monkeypatch.setattr(KnowledgeMiningAIService, "mine_document", fake_mine)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/20_知识串联.py")).run(timeout=30)
    next(button for button in app.button if button.label == "生成归纳草稿").click().run(timeout=30)
    assert not app.exception
    assert any("尚未保存" in value.value for value in app.caption)
    assert KnowledgeObjectService(database).count() == 1  # 草稿未入库
    save = next(button for button in app.button if button.label == "确认保存到知识库")
    save.click().run(timeout=30)
    assert not app.exception
    assert any("已保存 1 个知识点" in value.value for value in app.success)
    assert "knowledge:2" in KnowledgeGraphService(database).snapshot().nodes


def test_star_nebulae_group_by_subject(tmp_path) -> None:
    """不同学科各自成星云：题目带学科，知识点/来源随关联题目继承。"""

    database = Database(tmp_path / "knowledge.db")
    document = database.create_document(
        title="双学科资料", filename="dual.pdf", source_path=tmp_path / "dual.pdf",
        sha256="c" * 64, page_count=2,
    )
    math_page = database.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "p1.png",
        extracted_text="二次根式化简", status="ready")
    english_page = database.create_page(
        document_id=document.id, page_number=2, image_path=tmp_path / "p2.png",
        extracted_text="past continuous tense", status="ready")
    questions = QuestionService(database)
    math_id = questions.create_question_item(
        document_id=document.id, page_id=math_page.id, question_kind="typical",
        question_number="1", stem_text="化简二次根式", subject="数学").id
    english_id = questions.create_question_item(
        document_id=document.id, page_id=english_page.id, question_kind="typical",
        question_number="2", stem_text="Choose the correct tense", subject="英语").id
    knowledge = KnowledgeObjectService(database).create(
        kind="concept", title="最简二次根式", content="被开方数不含分母和开得尽方的因数",
        epistemic_basis="personal_judgment").knowledge_object
    KnowledgeGraphService(database).link_question(math_id, knowledge.id)

    graph = KnowledgeGraphService(database).snapshot()
    payload = graph.star_payload()
    tags = {note["id"]: note["tags"] for note in payload["vault"]["notes"]}
    assert tags[f"question:{math_id}"] == ["数学"]
    assert tags[f"question:{english_id}"] == ["英语"]
    assert tags[f"knowledge:{knowledge.id}"] == ["数学"]  # 经题目关联继承学科
    assert tags[f"page:{math_page.id}"] == ["数学"]
    assert tags[f"page:{english_page.id}"] == ["英语"]
    assert tags[f"document:{document.id}"] == ["数学", "英语"]  # 共享文档同时属于两个星云
    unlinked = KnowledgeObjectService(database).create(
        kind="concept", title="未关联知识点", content="没有学科归属",
        epistemic_basis="personal_judgment").knowledge_object
    tags2 = {note["id"]: note["tags"]
             for note in KnowledgeGraphService(database).snapshot()
             .star_payload()["vault"]["notes"]}
    assert tags2[f"knowledge:{unlinked.id}"] == []  # 渲染端归入「未归类」星云


def _run_page_with_star_event(assets, monkeypatch, request: dict) -> AppTest:
    """Run the real page once with the starmap returning one relation event."""

    database, _, _ = assets
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    seen = {"calls": 0}

    def fake_render(payload: dict) -> dict | None:
        seen["calls"] += 1
        return {"relation": request, "event_id": "test:1"} if seen["calls"] == 1 else None

    monkeypatch.setattr(component, "render_starmap", fake_render)
    return AppTest.from_file(
        str(Path(__file__).parents[1] / "pages/20_知识串联.py")
    ).run(timeout=30)


def test_star_relation_command_links_knowledge_stars(assets, monkeypatch) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="反馈控制", content="按偏差调节系统",
        epistemic_basis="personal_judgment").knowledge_object
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "link", "source": f"knowledge:{source.id}",
        "target": f"knowledge:{knowledge_id}", "relation_type": "derived_from",
        "source_title": "反馈控制", "target_title": "定义域",
    })
    assert not app.exception
    relations = [relation for relation in service.relations(knowledge_id)
                 if {relation.source_ko_id, relation.target_ko_id}
                 == {source.id, knowledge_id}]
    assert len(relations) == 1
    assert relations[0].source_ko_id == source.id
    assert relations[0].relation_type.value == "derived_from"
    assert relations[0].description == "星图动作"


def test_star_relation_command_repeats_are_idempotent(assets, monkeypatch) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="反馈控制", content="按偏差调节系统",
        epistemic_basis="personal_judgment").knowledge_object
    request = {
        "action": "link", "source": f"knowledge:{source.id}",
        "target": f"knowledge:{knowledge_id}", "relation_type": "relates_to",
        "source_title": "反馈控制", "target_title": "定义域",
    }
    _run_page_with_star_event(assets, monkeypatch, request)
    app = _run_page_with_star_event(assets, monkeypatch, request)
    assert not app.exception
    assert len(service.relations(knowledge_id)) == 1  # 同一条命令重复下达不产生重复关系


@pytest.mark.parametrize("confirm", [True, False])
def test_sequential_gesture_link_waits_for_confirmation(assets, monkeypatch, confirm) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="反馈控制", content="按偏差调节系统",
        epistemic_basis="personal_judgment").knowledge_object
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "link", "source": f"knowledge:{source.id}",
        "target": f"knowledge:{knowledge_id}", "relation_type": "relates_to",
        "source_title": "反馈控制", "target_title": "定义域", "require_confirmation": True,
    })
    assert not app.exception
    assert not service.relations(knowledge_id)
    assert app.session_state["graph_pending_link"]["require_confirmation"] is False
    assert any("待确认连接" in item.value for item in app.info)
    label = "确认连接" if confirm else "取消"
    next(button for button in app.button if button.label == label).click().run(timeout=30)
    assert not app.exception
    assert "graph_pending_link" not in app.session_state
    assert len(service.relations(knowledge_id)) == int(confirm)


@pytest.mark.parametrize("target", ["knowledge:999999", "same"])
def test_sequential_gesture_rejects_missing_or_same_star(assets, monkeypatch, target) -> None:
    database, _, knowledge_id = assets
    source = f"knowledge:{knowledge_id}"
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "link", "source": source, "target": source if target == "same" else target,
        "require_confirmation": True,
    })
    assert not app.exception
    assert "graph_pending_link" not in app.session_state
    assert not KnowledgeObjectService(database).relations(knowledge_id)
    assert any("不同且仍存在" in toast.value for toast in app.toast)


def test_star_relation_unlink_waits_for_explicit_confirmation(assets, monkeypatch) -> None:
    database, _, knowledge_id = assets
    service = KnowledgeObjectService(database)
    source = service.create(
        kind="concept", title="反馈控制", content="按偏差调节系统",
        epistemic_basis="personal_judgment").knowledge_object
    relation = service.add_relation(source.id, knowledge_id,
                                    relation_type="relates_to", description="星图动作")
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "unlink", "source": f"knowledge:{source.id}",
        "target": f"knowledge:{knowledge_id}", "relation_type": "relates_to",
        "source_title": "反馈控制", "target_title": "定义域",
    })
    assert not app.exception
    assert app.session_state["graph_pending_unlink"]["relation_ids"] == [relation.id]
    assert database.get_knowledge_relation(relation.id) is not None  # 未确认前不删除
    next(button for button in app.button if button.label == "取消").click().run()
    assert database.get_knowledge_relation(relation.id) is not None
    assert "graph_pending_unlink" not in app.session_state
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "unlink", "source": f"knowledge:{source.id}",
        "target": f"knowledge:{knowledge_id}", "relation_type": "relates_to",
        "source_title": "反馈控制", "target_title": "定义域",
    })
    next(button for button in app.button if button.label == "确认断开").click().run(timeout=30)
    assert not app.exception
    assert database.get_knowledge_relation(relation.id) is None
    assert "graph_pending_unlink" not in app.session_state


def test_star_relation_command_rejects_non_knowledge_stars(assets, monkeypatch) -> None:
    database, question_id, _ = assets
    app = _run_page_with_star_event(assets, monkeypatch, {
        "action": "link", "source": f"question:{question_id}",
        "target": "knowledge:999999", "relation_type": "relates_to",
        "source_title": "题目", "target_title": "知识点",
    })
    assert not app.exception
    assert "graph_pending_unlink" not in app.session_state
    with database._connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_relations").fetchone()[0] == 0
    assert any("知识点" in toast.value for toast in app.toast)


@pytest.mark.parametrize("filename", [
    "starmap_relation_commands.test.cjs", "knowledge_starmap.test.cjs",
    "starmap_selection.test.cjs",
])
def test_star_relation_command_layer_passes_node_checks(filename: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for starmap relation command checks")
    script = Path(__file__).parent / "js" / filename
    result = subprocess.run(
        [node, "--test", str(script)], capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
