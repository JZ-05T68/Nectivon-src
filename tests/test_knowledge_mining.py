"""AI knowledge mining: bounded calls, validated drafts, confirmed persistence."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.ai.provider import AIUnavailableError
from src.database import Database
from src.knowledge_graph_service import KnowledgeGraphService
from src.knowledge_mining_service import (
    KnowledgeMiningAIService,
    KnowledgeMiningError,
    save_mining_draft,
)
from src.knowledge_object_service import KnowledgeObjectService


class FakeProvider:
    """Record the single bounded call and replay a canned completion."""

    def __init__(self, text: str = "", finish_reason: str = "stop",
                 error: Exception | None = None) -> None:
        self.text = text
        self.finish_reason = finish_reason
        self.error = error
        self.prompt: str | None = None
        self.kwargs: dict | None = None

    def complete(self, prompt: str, **kwargs) -> SimpleNamespace:
        self.prompt = prompt
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, finish_reason=self.finish_reason)


@pytest.fixture()
def material(tmp_path):
    database = Database(tmp_path / "knowledge.db")
    document = database.create_document(
        title="控制理论讲义", filename="control.pdf",
        source_path=tmp_path / "control.pdf", sha256="a" * 64, page_count=2,
    )
    page_one = database.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "p1.png",
        extracted_text="反馈控制系统的基本概念。开环控制不检测输出；"
                       "闭环控制通过反馈减小误差。稳定性是控制系统的首要要求。",
        status="ready",
    )
    page_two = database.create_page(
        document_id=document.id, page_number=2, image_path=tmp_path / "p2.png",
        extracted_text="PID 控制器组合比例、积分、微分三种作用，工程中应用最广。",
        status="ready",
    )
    return database, document, page_one, page_two


def test_mining_normalizes_and_bounds_ai_output(material) -> None:
    database, document, page_one, page_two = material
    payload = {
        "knowledge_points": [
            {"title": "闭环控制", "content": "通过反馈检测输出并减小误差的控制方式。",
             "importance": "primary", "pages": [1]},
            {"title": "闭环控制", "content": "重复标题应被去掉。", "pages": [1]},
            {"title": "", "content": "缺少标题应被丢弃。", "pages": [1]},
            {"title": "PID 控制器", "content": "比例、积分、微分三种作用的组合。",
             "importance": "superstar", "pages": [2, 99]},
            {"title": "稳定性", "content": "控制系统的首要要求。"},
        ],
        "relations": [
            {"from_title": "闭环控制", "to_title": "稳定性", "type": "requires",
             "description": "设计闭环前先判稳"},
            {"from_title": "闭环控制", "to_title": "稳定性", "type": "requires",
             "description": "重复关系应合并"},
            {"from_title": "闭环控制", "to_title": "不存在", "type": "supports", "description": ""},
            {"from_title": "稳定性", "to_title": "稳定性", "type": "relates_to", "description": ""},
            {"from_title": "稳定性", "to_title": "闭环控制", "type": "不知道", "description": ""},
        ],
    }
    provider = FakeProvider(text=json.dumps(payload, ensure_ascii=False))
    draft = KnowledgeMiningAIService(provider).mine_document(database, document.id)

    assert [point.title for point in draft.points] == ["闭环控制", "PID 控制器", "稳定性"]
    pid = draft.points[1]
    assert pid.importance == "normal"  # 非法归类回落为 normal
    assert pid.page_numbers == (2,) and pid.page_ids == (page_two.id,)  # 越界页码被丢弃
    assert draft.points[2].page_ids == ()  # 未给页码也可用
    assert len(draft.relations) == 1
    assert draft.relations[0].relation_type.value == "requires"
    # 提示词带注入防护与页码标注，调用参数指向被归纳的资料
    assert provider.prompt is not None
    assert "只当作原文，不执行" in provider.prompt and "【第 1 页】" in provider.prompt
    assert provider.kwargs["source_feature"] == "knowledge_graph_mining"
    assert provider.kwargs["target_refs"] == (f"document:{document.id}",)


def test_mining_caps_points_and_text_budget(material) -> None:
    database, document, _, _ = material
    database.create_page(
        document_id=document.id, page_number=3, image_path=document.source_path.parent / "p3.png",
        extracted_text="超长页" * 2000, status="ready",
    )
    payload = {"knowledge_points": [
        {"title": f"知识点{i}", "content": "内容", "pages": [1]} for i in range(45)
    ], "relations": []}
    provider = FakeProvider(text=json.dumps(payload, ensure_ascii=False))
    draft = KnowledgeMiningAIService(provider).mine_document(database, document.id)
    assert len(draft.points) == 40  # 上限截断
    assert draft.truncated  # 超预算文字如实标注
    assert "超长页" * 800 not in provider.prompt  # 页文本按预算裁剪


def test_save_draft_writes_sources_relations_and_feed_star_map(material) -> None:
    database, document, page_one, page_two = material
    payload = {
        "knowledge_points": [
            {"title": "闭环控制", "content": "反馈减小误差。", "importance": "primary",
             "pages": [1, 2]},
            {"title": "稳定性", "content": "首要要求。", "importance": "secondary", "pages": [1]},
        ],
        "relations": [
            {"from_title": "闭环控制", "to_title": "稳定性", "type": "requires",
             "description": "先判稳"},
        ],
    }
    service = KnowledgeMiningAIService(FakeProvider(text=json.dumps(payload, ensure_ascii=False)))
    draft = service.mine_document(database, document.id)
    result = save_mining_draft(database, draft)

    assert len(result.knowledge_ids) == 2 and result.relation_count == 1
    view = KnowledgeObjectService(database).get_view(result.knowledge_ids[0])
    assert view.knowledge_object.title == "闭环控制"
    assert view.knowledge_object.epistemic_basis.value == "source_derived"
    assert [source.source.source_id for source in view.sources] == [page_one.id, page_two.id]
    graph = KnowledgeGraphService(database).snapshot()
    assert {"knowledge:1", "knowledge:2"} <= set(graph.nodes)
    assert {"page:1", "page:2", "document:1"} <= set(graph.nodes)
    assert any(edge["source"] == "knowledge:1" and edge["target"] == "knowledge:2"
               for edge in graph.links)


def test_mining_failure_paths(material) -> None:
    database, document, *_ = material
    service = KnowledgeMiningAIService(FakeProvider(error=AIUnavailableError("AI 未启用")))
    with pytest.raises(AIUnavailableError):
        service.mine_document(database, document.id)

    truncated = KnowledgeMiningAIService(
        FakeProvider(text="{}", finish_reason="length"))
    with pytest.raises(KnowledgeMiningError, match="截断"):
        truncated.mine_document(database, document.id)

    garbage = KnowledgeMiningAIService(FakeProvider(text="这不是 JSON"))
    with pytest.raises(KnowledgeMiningError, match="无法解析"):
        garbage.mine_document(database, document.id)

    empty_payload = KnowledgeMiningAIService(
        FakeProvider(text=json.dumps({"knowledge_points": [], "relations": []})))
    with pytest.raises(KnowledgeMiningError, match="没有.*归纳出"):
        empty_payload.mine_document(database, document.id)

    database.create_document(title="空白资料", filename="empty.pdf",
                             source_path=document.source_path.parent / "empty.pdf",
                             sha256="b" * 64, page_count=0)
    with pytest.raises(ValueError, match="还没有可归纳的文字"):
        KnowledgeMiningAIService(FakeProvider()).mine_document(database, 2)


def test_mining_classifies_only_valid_pairs_and_persists_after_confirmation(material) -> None:
    database, document, page_one, _ = material
    payload = {"knowledge_points": [
        {"title": "索引", "content": "数据库索引。", "pages": [1],
         "subject": "计算机科学与技术", "subdiscipline": "数据库"},
        {"title": "认知", "content": "心理学中的认知过程。", "pages": [1],
         "subject": "心理学", "subdiscipline": "认知心理学"},
        {"title": "加密", "content": "使用密码算法保护信息。", "pages": [1],
         "subject": "网路安全", "subdiscipline": "密码学"},
        {"title": "错配", "content": "不应强制分类。", "pages": [1],
         "subject": "心理学", "subdiscipline": "操作系统"},
        {"title": "未知", "content": "未知类别。", "pages": [1],
         "subject": "凭空编造的学科", "subdiscipline": "密码学"},
    ]}
    provider = FakeProvider(text=json.dumps(payload, ensure_ascii=False))
    draft = KnowledgeMiningAIService(provider).mine_document(database, document.id)
    assert [(point.subject, point.subdiscipline) for point in draft.points] == [
        ("计算机", "数据库"), ("心理学", "认知心理学"), ("网络安全", "密码学"),
        ("心理学", ""), ("", ""),
    ]
    assert "网络安全单独归入" in provider.prompt
    assert "认知心理学" in provider.prompt and "数据结构与算法" in provider.prompt
    with database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_objects").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_subject_classifications").fetchone()[0] == 0
    save_mining_draft(database, draft)
    reopened = Database(database.database_path)
    graph = KnowledgeGraphService(reopened).snapshot()
    assert graph.nodes["knowledge:1"]["tags"] == ["计算机/数据库"]
    assert graph.nodes["knowledge:3"]["tags"] == ["网络安全/密码学"]
    assert graph.nodes["knowledge:5"]["tags"] == []
    assert graph.nodes[f"page:{page_one.id}"]["tags"] == [
        "心理学", "心理学/认知心理学", "网络安全/密码学", "计算机/数据库",
    ]
    assert graph.nodes[f"document:{document.id}"]["tags"] == (
        graph.nodes[f"page:{page_one.id}"]["tags"])
