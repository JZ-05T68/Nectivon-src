"""Short-concept-query routing regression (FAIL-020 face, Night 2).

Locked evidence from Night 1/1B: bare ML-domain concept questions
("RAG 是什么？" "KNN 有什么缺点？" "什么是过拟合？" "监督学习和无监督学习差在哪？")
fell into ANSWER_DIRECTLY canned refusals 13/13 while the corpus (人工智能基础
课程讲义 p1/p2) contains every concept; document-name anchors flipped routing
3/3. The fix is generic: a decision-prompt concept-question rule plus a
deterministic ANSWER_DIRECTLY corpus-match guard. No domain keyword lists.

Positive probes mirror the recorded probes; controls cover non-ML concepts,
corpus-absent topics (honest boundary), meta questions and CALL_TOOL
pass-through.
"""

from __future__ import annotations

from pathlib import Path

from src.agent.decision.prompt import build_decision_prompt
from src.agent.execution.contracts import (
    AgentDecision,
    AgentDecisionKind,
)
from src.agent.execution.guard import (
    answer_directly_corpus_guard,
    concept_search_terms,
    looks_like_concept_query,
)
from src.agent.tools.bootstrap import phase1_tool_definitions
from src.database import Database
from src.search_service import SearchService


def _answer_directly() -> AgentDecision:
    return AgentDecision(kind=AgentDecisionKind.ANSWER_DIRECTLY)


def _page_search(query: str) -> AgentDecision:
    return AgentDecision(
        kind=AgentDecisionKind.CALL_TOOL,
        tool_name="page_search",
        arguments={"query": query},
    )


# ------------------------------------------------------------ pattern tests

def test_pattern_matches_recorded_probe_shapes() -> None:
    for question in (
        "RAG 是什么？",
        "那 OCR 呢？",  # bare follow-up shape (recorded A2-T2 probe)
        "什么是过拟合？",
        "监督学习和无监督学习差在哪？",
        "KNN 有什么缺点？",
        "KNN 训练快还是推理快？",
        "随机森林有什么缺点？",
        "Transformer 是怎么工作的？",
        "模糊控制有什么作用？",
    ):
        assert looks_like_concept_query(question), question


def test_pattern_rejects_non_concept_shapes() -> None:
    for question in (
        "这个软件是干什么的？",  # product meta
        "3 加 4 等于几？",
        "帮我总结这份 PDF",
        "M8 用多大力矩",
        "冷却水泵第 2 页趋势图 2000 小时是多少度",
        "把那个保存一下",
    ):
        assert not looks_like_concept_query(question), question


def test_search_terms_strip_question_shape() -> None:
    assert concept_search_terms("RAG 是什么？") == "RAG"
    assert "过拟合" in concept_search_terms("什么是过拟合？")
    assert "KNN" in concept_search_terms("KNN 有什么缺点？")
    assert concept_search_terms("那 OCR 呢？") == "OCR"


# ------------------------------------------------------------- guard tests

def _database_with_concept_corpus(tmp_path: Path) -> Database:
    database_dir = tmp_path / "db"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(database_dir / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    image_path = pages_dir / "page_0001.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")
    document = database.create_document(
        title="人工智能基础课程讲义",
        filename="ai101.pdf",
        source_path="data/raw/ai101.pdf",
        sha256="c" * 64,
        page_count=2,
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text=(
            "人工智能基础课程讲义\n"
            "1. 监督学习：训练数据带标签，如分类与回归。\n"
            "2. 无监督学习：无标签，如聚类降维。\n"
            "4. RAG（检索增强生成）：回答前先从资料库检索相关内容，"
            "再组织答案；OCR 是把图片里的文字识别成文本的技术。"
        ),
    )
    database.create_page(
        document_id=document.id,
        page_number=2,
        image_path=image_path,
        extracted_text=(
            "人工智能基础课程讲义\n"
            "2. 决策树：规则直观，易过拟合，常与随机森林结合。\n"
            "3. KNN：无需训练，但推理慢，对维度敏感。\n"
            "4. 过拟合：训练误差很小但测试误差大。"
        ),
    )
    return database


def test_guard_reroutes_concept_query_when_corpus_matches(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "RAG 是什么？", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.CALL_TOOL
    assert decision.tool_name == "page_search"
    assert decision.arguments["query"] == "RAG"


def test_guard_covers_all_recorded_omission_probes(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    for question in (
        "RAG 是什么？",
        "监督学习和无监督学习差在哪？",
        "KNN 有什么缺点？",
        "KNN 训练快还是推理快？",
        "什么是过拟合？",
    ):
        decision = answer_directly_corpus_guard(question, _answer_directly, service)
        assert decision.kind is AgentDecisionKind.CALL_TOOL, question
        assert decision.tool_name == "page_search", question


def test_guard_non_ml_concept_also_reroutes(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "监督学习是什么意思？", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.CALL_TOOL


def test_guard_keeps_answer_directly_for_corpus_absent_topic(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "量子纠缠是什么？", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.ANSWER_DIRECTLY


def test_guard_ignores_non_concept_answer_directly(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "这个软件是干什么的？", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.ANSWER_DIRECTLY


def test_guard_passes_call_tool_through_unchanged(tmp_path: Path) -> None:
    database = _database_with_concept_corpus(tmp_path)
    service = SearchService(database)
    original = _page_search("训练损失曲线")
    decision = answer_directly_corpus_guard(
        "训练损失曲线第 6 轮降到多少？", lambda: original, service
    )
    assert decision is original


# ------------------------------------------------------ decision prompt rule

def test_decision_prompt_bans_internal_knowledge_as_skip_reason() -> None:
    prompt = build_decision_prompt(
        "随便一个问题", phase1_tool_definitions()
    )
    assert "概念问题判断纪律" in prompt
    assert "不得以“这是通用知识/模型自己知道该概念”为理由" in prompt
    assert "这类问题必须选择" in prompt
    assert "page_search" in prompt
    # leakage guard: none of the probe concepts may appear in the prompt
    for term in ("RAG", "过拟合", "监督学习", "随机森林", "KNN", "OCR"):
        assert term not in prompt, term


# --------------------------------------------------- composition integration

_CANNED_ANSWER_DIRECTLY = '{"kind": "ANSWER_DIRECTLY", "tool_name": null, "arguments": {}}'


class _AlwaysSkipDecisionProvider:
    """Stub model that always decides ANSWER_DIRECTLY (the failure mode)."""

    def complete(self, prompt: str, **kwargs):
        from src.ai.provider import CompletionResult

        if "[USER_REQUEST]" in prompt:
            return CompletionResult(text=_CANNED_ANSWER_DIRECTLY, model="stub")
        return CompletionResult(
            text="根据提供的知识上下文，信息不足", model="stub"
        )


def test_local_agent_guard_reroutes_concept_question(tmp_path: Path) -> None:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent.response.final_answer import _ANSWER_DIRECTLY_MESSAGE
    from src.agent_document_reader import AgentReadingStore

    database = _database_with_concept_corpus(tmp_path)
    agent = LocalDocumentAgent(
        database=database,
        provider=_AlwaysSkipDecisionProvider(),
        readings=AgentReadingStore(tmp_path / "readings"),
        model="stub",
    )
    response = agent.ask("RAG 是什么？")
    assert response.trace.selected_tool == "page_search"
    assert response.trace.decision_kind == "call_tool"
    assert response.answer != _ANSWER_DIRECTLY_MESSAGE


def test_local_agent_guard_keeps_non_concept_and_absent_topics(tmp_path: Path) -> None:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent.response.final_answer import _ANSWER_DIRECTLY_MESSAGE
    from src.agent_document_reader import AgentReadingStore

    database = _database_with_concept_corpus(tmp_path)
    agent = LocalDocumentAgent(
        database=database,
        provider=_AlwaysSkipDecisionProvider(),
        readings=AgentReadingStore(tmp_path / "readings"),
        model="stub",
    )
    for question in ("这个软件是干什么的？", "量子纠缠是什么？"):
        response = agent.ask(question)
        assert response.trace.decision_kind == "answer_directly", question
        assert response.answer == _ANSWER_DIRECTLY_MESSAGE, question
