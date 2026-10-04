"""Unit tests for local search orchestration and manual prompt generation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.models import PageStatus, SearchResult
from src.prompt_builder import PromptBuilder, build_prompt
from src.search_service import SearchService


class FakeDatabase:
    """Small in-memory stand-in for the Database search contract."""

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int = 20, **options) -> list[SearchResult]:
        del options
        self.calls.append((query, limit))
        return self.results[:limit]


def make_result(
    *,
    content: str = "液压泵出现异常噪声时，应检查吸油管路和油液状态。",
    snippet: str = "",
) -> SearchResult:
    return SearchResult(
        page_id=7,
        document_id=3,
        document_title="液压设备维护手册",
        filename="hydraulics.pdf",
        page_number=12,
        image_path=Path("data/pages/3/page_0012.png"),
        content=content,
        snippet=snippet,
        rank=-1.5,
        status=PageStatus.READY,
    )


def test_normalize_query_is_segmented_deduplicated_and_fts_safe() -> None:
    service = SearchService(FakeDatabase())

    normalized = service.normalize_query('  pump，pump AND "noise"  ')

    assert normalized == '"pump" OR "noise"'
    assert service.normalize_query("液压系统故障").startswith('"')


def test_explicit_question_number_adds_precise_list_markers() -> None:
    service = SearchService(FakeDatabase())

    assert service.query_terms("答案里的第16题到底是什么？")[:3] == (
        "16．",
        "16.",
        "16、",
    )
    assert service.query_terms("Q8 的解释")[:3] == ("8．", "8.", "8、")
    assert service.query_terms("14（2）①")[:3] == ("14．", "14.", "14、")
    assert service.query_terms("17(2)④")[:3] == ("17．", "17.", "17、")
    assert "2024．" not in service.query_terms("2024年江苏卷")


def test_explicit_question_prioritizes_numbered_page_and_continuation() -> None:
    base = make_result(content="同一份试卷的公共标题")
    noise = replace(base, page_id=1, document_id=1, page_number=1)
    numbered = replace(
        base,
        page_id=2,
        document_id=2,
        page_number=6,
        ocr_text="16.(15分) 多阶段物理过程",
    )
    continuation = replace(
        base,
        page_id=3,
        document_id=2,
        page_number=7,
        ocr_text="（3）继续推导整体关系",
    )
    database = FakeDatabase([noise, numbered, continuation])
    service = SearchService(database)

    results = service.search("这份试卷第16题", limit=2)

    assert len(database.calls) == 1
    normalized_query, database_limit = database.calls[0]
    assert database_limit == 100
    assert all(f'"{marker}"' in normalized_query for marker in ("16．", "16.", "16、"))
    assert [result.page_id for result in results] == [2, 3]


def test_matching_exam_continuation_beats_unrelated_numbered_page() -> None:
    base = make_result(content="公共标题")
    target = replace(
        base,
        page_id=1,
        document_id=10,
        page_number=3,
        document_title="南京盐城二模物理答案",
        filename="南京盐城二模物理答案.pdf",
        ocr_text="16. 第一页解析",
    )
    unrelated = replace(
        base,
        page_id=2,
        document_id=20,
        page_number=6,
        document_title="南京盐城一模物理A",
        filename="南京盐城一模物理A.pdf",
        ocr_text="16. 另一份试卷",
    )
    continuation = replace(
        base,
        page_id=3,
        document_id=10,
        page_number=4,
        document_title="南京盐城二模物理答案",
        filename="南京盐城二模物理答案.pdf",
        ocr_text="（3）继续解析",
    )
    service = SearchService(FakeDatabase([target, unrelated, continuation]))

    results = service.search("南京盐城二模物理 第16题", limit=3)

    assert [result.page_id for result in results] == [1, 3, 2]


def test_matching_answer_table_is_kept_for_numbered_choice_question() -> None:
    base = make_result(content="公共标题")
    question = replace(
        base,
        page_id=1,
        document_id=10,
        page_number=3,
        document_title="南京都市圈物理AKL1",
        filename="南京都市圈物理AKL1.pdf",
        ocr_text="9. 飞机多普勒效应",
    )
    answer_table = replace(
        base,
        page_id=2,
        document_id=11,
        page_number=1,
        document_title="南京都市圈物理参考答案和评分标准",
        filename="南京都市圈物理参考答案和评分标准.pdf",
        ocr_text="题号 1 2 3 4 5 6 7 8 9 10 11 答案 A B A C A C D D B C B",
    )
    unrelated = replace(
        base,
        page_id=3,
        document_id=20,
        page_number=6,
        document_title="其他物理试卷",
        filename="其他物理试卷.pdf",
        ocr_text="9. 其他题目",
    )
    service = SearchService(FakeDatabase([question, unrelated, answer_table]))

    results = service.search("南京都市圈物理 第9题", limit=3)

    assert [result.page_id for result in results] == [1, 2, 3]


def test_matching_exam_content_recovers_page_when_ocr_loses_number() -> None:
    base = make_result(content="公共标题")
    target_without_number = replace(
        base,
        page_id=1,
        document_id=10,
        page_number=3,
        document_title="高三物理期中21校联考",
        filename="高三物理期中21校联考.pdf",
        ocr_text="蹦床运动的速度与机械能图像",
    )
    unrelated_numbered = replace(
        base,
        page_id=2,
        document_id=20,
        page_number=3,
        document_title="其他物理试卷",
        filename="其他物理试卷.pdf",
        ocr_text="9. 其他图像题",
    )
    service = SearchService(FakeDatabase([unrelated_numbered, target_without_number]))

    results = service.search("高三物理期中21校联考 第9题 蹦床", limit=2)

    assert [result.page_id for result in results] == [1, 2]


def test_empty_or_punctuation_only_query_does_not_call_database() -> None:
    database = FakeDatabase()
    service = SearchService(database)

    assert service.search(" \n ，！？ ") == []
    assert database.calls == []


def test_search_passes_normalized_query_and_builds_natural_snippet() -> None:
    content = "前置说明。" * 35 + "pump：液压泵异常噪声需要检查吸油管路。" + "后续说明。" * 35
    database = FakeDatabase([make_result(content=content)])
    service = SearchService(database, snippet_length=80)

    results = service.search("pump noise", limit=5)

    assert database.calls == [('"pump" OR "noise"', 5)]
    assert len(results) == 1
    assert "液压泵异常噪声" in results[0].snippet
    assert results[0].snippet.startswith("…")
    assert results[0].snippet.endswith("…")
    assert "<mark>" not in results[0].snippet


def test_prompt_contains_numbered_sources_and_strict_citation_rules() -> None:
    prompt = PromptBuilder().build("异常噪声应检查什么？", [make_result()])

    assert "只能根据“知识片段”" in prompt
    assert "信息不足" in prompt
    assert "每个事实性结论后都必须引用来源" in prompt
    assert "【文档名，第N页】" in prompt
    assert "[来源 1] 【液压设备维护手册，第12页】" in prompt
    assert "异常噪声应检查什么？" in prompt


def test_prompt_without_results_is_still_safe_and_copyable() -> None:
    prompt = build_prompt("未知问题", [])

    assert "（未提供知识片段）" in prompt
    assert "只能根据" in prompt
    assert "根据提供的知识片段，信息不足" in prompt
