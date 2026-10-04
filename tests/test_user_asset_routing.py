"""M2 focused regression — user-asset-first routing for trivial computations.

HBV2-MORNING-20260909 M2 (TRIVIAL_COMPUTATION_ROUTE_TO_ANSWER_DIRECTLY):
questions the model could compute itself were routed to ANSWER_DIRECTLY and
answered with the canned refusal even though the user's own corpus (我的物理
错题本 p1) held the original problem and its answer. The user-asset-first
principle: when the user points at their own materials ("我的资料/我上传的/
之前保存的/这道题…"), retrieval must come first — a simple computation is
never a reason to skip it.

Three layers are locked here:

- deterministic guard: ANSWER_DIRECTLY decisions on generic asset /
  quantity / verification shapes are reconsidered against one lexical
  search; a hit reroutes to ``page_search`` (honest "信息不足" stays possible
  when the hit is irrelevant), a miss keeps the honest boundary;
- the substantive-term bar: a reroute is never driven by single characters
  or bare digits ("3 加 4 等于几" stays a direct answer even when the corpus
  contains the word 等于);
- decision prompt: the user-asset-first and no-trivial-computation-skip
  disciplines are present as generic semantics with no fixture leakage.
"""

from __future__ import annotations

from pathlib import Path

from src.agent.decision.prompt import build_decision_prompt
from src.agent.execution.contracts import AgentDecision, AgentDecisionKind
from src.agent.execution.guard import (
    answer_directly_corpus_guard,
    asks_quantity_or_verification,
    concept_search_terms,
    looks_like_guard_trigger,
    references_user_asset,
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

def test_asset_reference_shapes_match() -> None:
    for question in (
        "我有一本错题本，里面就有这道题，你再看看",
        "我的资料里有没有这个参数",
        "我上传的文档里怎么说的",
        "这个文件里写的是多少",
        "这份资料里 45 钢屈服强度是多少",
        "我之前保存的那个结论是什么",
    ):
        assert references_user_asset(question), question


def test_quantity_and_verification_shapes_match() -> None:
    for question in (
        "一辆车每秒走20米，走了10秒，走了多远啊？",
        "把五千克的东西举高两米要做多少功",
        "老师说是100焦耳，对吗？为什么啊",
        "串联的两个灯泡，一个3欧一个6欧，电源9伏，电流是多少啊",
    ):
        assert asks_quantity_or_verification(question), question
    # "A 还是 B" selection shape is covered by the concept patterns
    assert looks_like_guard_trigger("含碳 2.5% 的是钢还是铸铁？")


def test_meta_and_command_shapes_do_not_match() -> None:
    for question in (
        "这个软件是干什么的？",
        "你能帮我写作业吗？",
        "把那个保存一下",
        "帮我总结这份 PDF",
    ):
        assert not references_user_asset(question), question
        assert not asks_quantity_or_verification(question), question


def test_search_terms_strip_quantity_shapes() -> None:
    cleaned = concept_search_terms("把五千克的东西举高两米要做多少功")
    assert "多少" not in cleaned
    assert "功" in cleaned
    cleaned = concept_search_terms("老师说是100焦耳，对吗？为什么啊")
    assert "对吗" not in cleaned and "为什么" not in cleaned
    assert "焦耳" in cleaned


# ------------------------------------------------------------- guard tests

def _database_with_homework_corpus(tmp_path: Path) -> Database:
    """Synthetic corpus holding recorded-style homework problems and answers."""

    database_dir = tmp_path / "db"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(database_dir / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    image_path = pages_dir / "page_0001.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")
    document = database.create_document(
        title="我的物理错题本",
        filename="physics-notebook.pdf",
        source_path="data/raw/physics-notebook.pdf",
        sha256="b" * 64,
        page_count=1,
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text=(
            "我的物理错题本\n"
            "1. 一辆车每秒走20米，走了10秒，走了多远？答：200米。\n"
            "2. 把五千克的东西举高两米要做多少功？答：100焦耳。\n"
            "3. 串联的两个灯泡，一个3欧一个6欧，电源9伏，电流是多少？\n"
            "答：电流等于电压除以电阻，9 伏除以 9 欧，等于 1 安培。\n"
            "4. 含碳 2.5% 的是钢还是铸铁？答：铸铁，分界是 2.11%。\n"
        ),
    )
    return database


def test_guard_reroutes_recorded_computation_questions(tmp_path: Path) -> None:
    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    for question in (
        "一辆车每秒走20米，走了10秒，走了多远啊？",
        "把五千克的东西举高两米要做多少功",
        "老师说是100焦耳，对吗？为什么啊",
        "串联的两个灯泡，一个3欧一个6欧，电源9伏，电流是多少啊",
    ):
        decision = answer_directly_corpus_guard(question, _answer_directly, service)
        assert decision.kind is AgentDecisionKind.CALL_TOOL, question
        assert decision.tool_name == "page_search", question


def test_guard_reroutes_asset_anchored_question(tmp_path: Path) -> None:
    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "你没找到吗？我有一本错题本，里面就有这道题，你再看看",
        _answer_directly,
        service,
    )
    assert decision.kind is AgentDecisionKind.CALL_TOOL
    assert decision.tool_name == "page_search"


def test_guard_keeps_honest_boundary_for_corpus_absent_topic(tmp_path: Path) -> None:
    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "量子纠缠实验里粒子的自旋是多少",
        _answer_directly,
        service,
    )
    assert decision.kind is AgentDecisionKind.ANSWER_DIRECTLY


def test_pure_arithmetic_without_content_words_stays_direct(tmp_path: Path) -> None:
    """The reroute must never be driven by single characters or bare digits.

    The corpus deliberately contains 等于 (in the recorded answer text), yet
    "3 加 4 等于几" keeps no substantive ranking term after cleaning, so the
    guard does not even search and the direct answer survives.
    """

    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "3 加 4 等于几？", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.ANSWER_DIRECTLY


def test_guard_reroutes_referential_digit_fragments_m1r(tmp_path: Path) -> None:
    """M1R-002 (HBV2 Phase 2 RUN2): 「那个20米每秒的车」 carries no question
    word at all — its digits/units are the corpus anchor. An ANSWER_DIRECTLY
    decision must be reconsidered and rerouted when the corpus holds the
    matching page; the glued variant 「20m每秒那个车」 reaches the same reroute
    through the zero-recall digit-fragment retry."""

    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    for question in ("那个20米每秒的车", "20m每秒那个车"):
        decision = answer_directly_corpus_guard(question, _answer_directly, service)
        assert decision.kind is AgentDecisionKind.CALL_TOOL, question
        assert decision.tool_name == "page_search", question


def test_guard_keeps_pure_anaphora_without_content_direct(tmp_path: Path) -> None:
    """M3 discipline: pure anaphora with no corpus-matchable content keeps
    the honest clarify boundary (the corpus in this fixture does not contain
    刚才/那个/答案, so the reconsidered search misses and nothing changes)."""

    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    decision = answer_directly_corpus_guard(
        "刚才那个答案是多少", _answer_directly, service
    )
    assert decision.kind is AgentDecisionKind.ANSWER_DIRECTLY


def test_guard_passes_call_tool_through_unchanged(tmp_path: Path) -> None:
    database = _database_with_homework_corpus(tmp_path)
    service = SearchService(database)
    original = _page_search("轴承温度趋势 2000 小时")
    decision = answer_directly_corpus_guard(
        "冷却水泵第 2 页趋势图 2000 小时是多少度？",
        lambda: original,
        service,
    )
    assert decision is original


# ------------------------------------------------------ decision prompt rule

def test_decision_prompt_carries_user_asset_first_discipline() -> None:
    prompt = build_decision_prompt("随便一个问题", phase1_tool_definitions())
    assert "用户资产优先纪律" in prompt
    assert "必须调用检索工具" in prompt
    assert "禁止 ANSWER_DIRECTLY" in prompt
    assert "简单计算判断纪律" in prompt
    assert "我自己会算" in prompt
    assert "先用 page_search 查用户自己的资料" in prompt
    # generic shapes only: no frozen-corpus asset nouns leak into the prompt
    assert "错题本" not in prompt


# --------------------------------------------------- composition integration

_CANNED = (
    "这一轮没有检索你的知识库，因此没有找到可依据的资料。"
    "我只能基于你这一轮的问题检索：请补充资料名称，或把问题和你所指的内容"
    "说得更完整（例如写明资料名、设备或上一轮提到的对象）。"
)


class _AlwaysSkipDecisionProvider:
    """Stub model that always decides ANSWER_DIRECTLY (the failure mode)."""

    def complete(self, prompt: str, **kwargs):
        from src.ai.provider import CompletionResult

        if "[USER_REQUEST]" in prompt:
            return CompletionResult(
                text='{"kind": "ANSWER_DIRECTLY", "tool_name": null, "arguments": {}}',
                model="stub",
            )
        return CompletionResult(
            text="根据提供的知识上下文，信息不足", model="stub"
        )


def test_local_agent_reroutes_trivial_computation_to_page_search(
    tmp_path: Path,
) -> None:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent.response.final_answer import _ANSWER_DIRECTLY_MESSAGE
    from src.agent_document_reader import AgentReadingStore

    database = _database_with_homework_corpus(tmp_path)
    agent = LocalDocumentAgent(
        database=database,
        provider=_AlwaysSkipDecisionProvider(),
        readings=AgentReadingStore(tmp_path / "readings"),
        model="stub",
    )
    response = agent.ask("把五千克的东西举高两米要做多少功")
    assert response.trace.selected_tool == "page_search"
    assert response.trace.decision_kind == "call_tool"
    assert response.answer != _ANSWER_DIRECTLY_MESSAGE


def test_local_agent_keeps_honest_boundary_for_absent_topic(
    tmp_path: Path,
) -> None:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent_document_reader import AgentReadingStore

    database = _database_with_homework_corpus(tmp_path)
    agent = LocalDocumentAgent(
        database=database,
        provider=_AlwaysSkipDecisionProvider(),
        readings=AgentReadingStore(tmp_path / "readings"),
        model="stub",
    )
    response = agent.ask("量子纠缠实验里粒子的自旋是多少")
    assert response.trace.decision_kind == "answer_directly"
    assert response.answer == _CANNED
