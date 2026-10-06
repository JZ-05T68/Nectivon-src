"""FAIL-014 version-family retrieval coverage engineering tests.

Covers the minimal general fix: when the lexical pool itself spans several
documents of one normalized-title family, ``page_visual_search`` reads the
missing family members (bounded), and everything else keeps the historical
top-``limit`` behaviour (precision guard).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from pathlib import Path

from src.agent.tools.adapters._version_family import (
    MAX_FAMILY_PAGES,
    normalize_family_title,
    select_family_expanded,
)
from src.agent.tools.adapters.page_visual import PageVisualAdapter
from src.agent.tools.contracts import ToolContext, ToolInput
from src.models import PAGE_STABLE_TYPE, PageStatus, SearchResult, build_stable_id


def _hit(
    *,
    page_id: int,
    document_id: int,
    title: str,
    page_number: int = 1,
    extracted_text: str = "",
) -> SearchResult:
    return SearchResult(
        page_id=page_id,
        document_id=document_id,
        document_title=title,
        filename=f"{title}.pdf",
        page_number=page_number,
        image_path=Path(f"pages/{document_id}/page_0001.png"),
        content="表 1：地脚螺栓紧固力矩对照表",
        snippet="地脚螺栓紧固力矩",
        rank=1.0,
        status=PageStatus.REVIEWED,
        extracted_text=extracted_text,
    )


# --- normalize_family_title -------------------------------------------------


def test_normalize_collapses_natural_naming_variants() -> None:
    keys = {
        normalize_family_title("泵维护视觉手册"),
        normalize_family_title("泵维护视觉手册_v1.0"),
        normalize_family_title("泵维护视觉手册_v1.1"),
        normalize_family_title("2026 修订版泵维护视觉手册"),
        normalize_family_title("泵维护视觉手册（版本 2.0，2027 草案）"),
    }
    assert len(keys) == 1


def test_normalize_keeps_distinct_documents_apart() -> None:
    keys = {
        normalize_family_title("泵维护视觉手册"),
        normalize_family_title("空压机完整维护手册"),
        normalize_family_title("冷却水泵性能图表"),
        normalize_family_title("传动轴振动排查手册"),
    }
    assert len(keys) == 4


def test_normalize_same_name_docs_share_key() -> None:
    # Same title imported twice with different content: one family by name.
    assert normalize_family_title("冷却水泵性能图表") == normalize_family_title(
        "冷却水泵性能图表"
    )


# --- select_family_expanded -------------------------------------------------


def test_selection_unchanged_without_family() -> None:
    """Precision guard: all-distinct titles → exact historical behaviour."""
    pool = [
        _hit(page_id=1, document_id=1, title="传动轴振动排查手册"),
        _hit(page_id=2, document_id=2, title="空压机完整维护手册"),
        _hit(page_id=3, document_id=3, title="泵维护视觉手册_v1.1"),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    assert [hit.page_id for hit in selected] == [1]
    assert info["triggered"] is False


def test_selection_appends_missing_family_members() -> None:
    """FAIL-014 core: pool ranks only one family member first, but the pool
    itself proves the family exists → read the missing members too."""
    pool = [
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=30, document_id=30, title="空压机完整维护手册"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    # Original top hit stays first, decoy is never pulled in, family covered.
    assert [hit.page_id for hit in selected] == [10, 11, 16]
    assert info["triggered"] is True
    assert info["family_key"] == "泵维护视觉手册"
    assert 30 not in {hit.document_id for hit in selected}


def test_selection_cap_is_bounded() -> None:
    pool = [
        _hit(page_id=i, document_id=i, title=f"泵维护视觉手册_v1.{i}")
        for i in range(1, 8)
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    assert len(selected) == MAX_FAMILY_PAGES
    assert info["triggered"] is True


def test_selection_single_family_member_does_not_expand() -> None:
    """One member only → nothing to cover; historical behaviour stands."""
    pool = [
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
        _hit(page_id=30, document_id=30, title="空压机完整维护手册"),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    assert [hit.page_id for hit in selected] == [11]
    assert info["triggered"] is False


def test_selection_same_name_pair_expands() -> None:
    pool = [
        _hit(page_id=8, document_id=8, title="冷却水泵性能图表"),
        _hit(page_id=80, document_id=80, title="传动轴振动排查手册"),
        _hit(page_id=17, document_id=17, title="冷却水泵性能图表", page_number=2),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    assert [hit.page_id for hit in selected] == [8, 17]
    assert info["triggered"] is True


def test_selection_keeps_second_page_of_same_document() -> None:
    """FAIL-018/E-1: a visual-index hit for a *different page* of an already
    selected document must survive selection (the trend-chart page of the
    superseding document enters the read set alongside its lexical page)."""

    pool = [
        _hit(page_id=13, document_id=8, title="冷却水泵性能图表", page_number=2),
        _hit(page_id=123, document_id=66, title="冷却水泵性能图表"),
        # Visual-index hit: the superseding document's chart page.
        _hit(page_id=124, document_id=66, title="冷却水泵性能图表", page_number=2),
    ]
    selected, info = select_family_expanded(pool, base_limit=2)
    assert [hit.page_id for hit in selected] == [13, 123, 124]
    assert info["triggered"] is True


def test_selection_dedupes_repeated_same_page_and_binds_per_doc_cap() -> None:
    pool = [
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=12, document_id=12, title="泵维护视觉手册_v2.0"),
        _hit(page_id=13, document_id=13, title="泵维护视觉手册_v3.0"),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    page_ids = [hit.page_id for hit in selected]
    assert page_ids[0] == 10
    assert len(page_ids) == len(set(page_ids))
    assert len(page_ids) <= MAX_FAMILY_PAGES
    assert info["triggered"] is True


def test_selection_family_completeness_beats_lower_original_slots() -> None:
    """Top-1 original stays first; a non-family slot-2 hit yields its cap
    space to the family (partially-read family = the FAIL-014 defect)."""
    pool = [
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=30, document_id=30, title="空压机完整维护手册"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
    ]
    selected, info = select_family_expanded(pool, base_limit=2)
    assert [hit.page_id for hit in selected] == [10, 11, 30]
    assert info["triggered"] is True


# --- adapter integration ----------------------------------------------------

# 1x1 transparent PNG so the adapter finds real image bytes on disk.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@dataclass
class _VisionResult:
    text: str
    model: str = "vision-test"
    usage: object = None
    finish_reason: str = "stop"
    retry_count: int = 0


class _StubVision:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def complete_vision(self, prompt: str, image_png_base64: str, **kwargs):
        self.prompts.append(prompt)
        self.calls.append(image_png_base64)
        return _VisionResult(text="表格读数：M8 28 Nm。")


class _StubSearch:
    """Deterministic stand-in for SearchService.search.

    ``pools`` maps an exact query string to its result list; any other query
    (or the no-mapping case) falls back to the single default ``pool``.
    """

    def __init__(
        self,
        pool: list[SearchResult] | None = None,
        *,
        pools: dict[str, list[SearchResult]] | None = None,
    ) -> None:
        self.pool = pool or []
        self.pools = pools or {}
        self.queries: list[str] = []
        self.limits: list[int] = []

    def search(self, query: str, limit: int = 20):
        self.queries.append(query)
        self.limits.append(limit)
        hits = self.pools.get(query, self.pool)
        return hits[:limit]


def _materialize_images(
    tmp_path: Path, hits: list[SearchResult]
) -> tuple[dict[tuple[int, int], SearchResult], Path]:
    """Write one real image file per document and key hits by (doc, page)."""
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    materialized: dict[tuple[int, int], SearchResult] = {}
    for hit in hits:
        key = (hit.document_id, hit.page_id)
        if key in materialized:
            continue
        doc_dir = pages_dir / str(hit.document_id)
        doc_dir.mkdir(exist_ok=True)
        image_path = doc_dir / "page_0001.png"
        image_path.write_bytes(_PNG_BYTES)
        materialized[key] = replace(hit, image_path=image_path)
    return materialized, pages_dir


def _adapter_result(
    tmp_path: Path,
    pool: list[SearchResult],
    arguments: dict[str, object],
    *,
    pools: dict[str, list[SearchResult]] | None = None,
):
    every_hit = list(pool) + [
        hit for hit_list in (pools or {}).values() for hit in hit_list
    ]
    materialized, pages_dir = _materialize_images(tmp_path, every_hit)

    def mat(hits: list[SearchResult]) -> list[SearchResult]:
        return [materialized[(h.document_id, h.page_id)] for h in hits]

    stub = _StubVision()
    search_stub = (
        _StubSearch(mat(pool), pools={q: mat(hits) for q, hits in pools.items()})
        if pools
        else _StubSearch(mat(pool))
    )
    adapter = PageVisualAdapter(
        search_stub,  # type: ignore[arg-type]
        kb_uuid="kb-test",
        vision_provider=stub,
        pages_dir=pages_dir,
    )
    result = adapter(
        ToolInput(tool_name="page_visual_search", arguments=arguments),
        ToolContext(run_id="t"),
    )
    return result, stub


def test_selection_anchors_to_densest_family_not_top_hit(
    tmp_path: Path,
) -> None:
    """K01-UI edge: top-1 is cross-family lexical noise (single-member
    family). The dense family in the pool must be anchored and read in
    full — the noise top-1 keeps its slot and the family budget grows by
    one so no thin-text member (v1.1, the 28 page) is cut by the cap."""
    pool = [
        _hit(page_id=70, document_id=30, title="空压机完整维护手册", page_number=7),
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
    ]
    selected, info = select_family_expanded(pool, base_limit=1)
    assert [hit.page_id for hit in selected] == [70, 16, 10, 11]
    assert info["triggered"] is True
    assert info["family_key"] == "泵维护视觉手册"


def test_selection_non_family_top1_gets_extra_budget(tmp_path: Path) -> None:
    pool = [
        _hit(page_id=70, document_id=30, title="空压机完整维护手册", page_number=7),
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
        _hit(page_id=52, document_id=5, title="泵维护视觉手册", page_number=2),
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
    ]
    selected, _ = select_family_expanded(pool, base_limit=1)
    assert [hit.page_id for hit in selected] == [70, 16, 52, 10, 11]


def test_adapter_reads_whole_family_and_reports_expansion(tmp_path: Path) -> None:
    pool = [
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=30, document_id=30, title="空压机完整维护手册"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
    ]
    result, stub = _adapter_result(
        tmp_path, pool, {"query": "地脚螺栓 力矩", "limit": 1}
    )
    assert result.status.value == "success"
    assert result.data["total"] == 3
    assert len(stub.calls) == 3
    expansion = result.data["family_expansion"]
    assert expansion["triggered"] is True
    titles = {item["document_title"] for item in result.data["results"]}
    assert titles == {"泵维护视觉手册_v1.0", "泵维护视觉手册_v1.1", "泵维护视觉手册_v2.0"}
    # Every read stays traceable to its own page.
    assert len(result.references) == 3
    expected = {
        build_stable_id("kb-test", PAGE_STABLE_TYPE, 10),
        build_stable_id("kb-test", PAGE_STABLE_TYPE, 11),
        build_stable_id("kb-test", PAGE_STABLE_TYPE, 16),
    }
    assert {ref.stable_id for ref in result.references} == expected


def test_adapter_non_family_query_keeps_historical_selection(tmp_path: Path) -> None:
    pool = [
        _hit(page_id=1, document_id=1, title="传动轴振动排查手册"),
        _hit(page_id=2, document_id=2, title="空压机完整维护手册"),
    ]
    result, stub = _adapter_result(tmp_path, pool, {"query": "振动 排查"})
    assert result.status.value == "success"
    assert result.data["total"] == 1
    assert len(stub.calls) == 1
    assert "family_expansion" not in result.data


def test_adapter_gives_vision_only_human_corrections_for_cross_check(
    tmp_path: Path,
) -> None:
    pool = [
        replace(
            _hit(
                page_id=1,
                document_id=1,
                title="备件价格清单",
                extracted_text="自动文字层误读型号",
            ),
            markdown_content="接近开关 E2E-X5ME1 78",
            ocr_text="OCR 错误型号",
        )
    ]
    result, stub = _adapter_result(tmp_path, pool, {"query": "接近开关价格"})

    assert result.status.value == "success"
    assert len(stub.prompts) == 1
    assert "用户已经保存的人工修正" in stub.prompts[0]
    assert "自动文字层误读型号" not in stub.prompts[0]
    assert "OCR 错误型号" not in stub.prompts[0]
    assert "E2E-X5ME1 78" in stub.prompts[0]
    assert "视觉索引摘要" not in stub.prompts[0]
    assert "E2E-X5ME1 78" in result.data["results"][0]["content"]
    assert "接近开关 E2E-X5ME1 78" in result.data["results"][0]["source_page_text"]


def test_adapter_co_query_rescues_thin_member_missed_by_question_pool(
    tmp_path: Path,
) -> None:
    """K05 root cause: v1.1's text layer is too thin to rank in the question
    pool at all; the family-title co-query must give it a second path in."""
    question_pool = [
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
        _hit(page_id=70, document_id=70, title="空压机完整维护手册", page_number=7),
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=52, document_id=5, title="泵维护视觉手册", page_number=2),
    ]
    title_pool = [
        _hit(page_id=52, document_id=5, title="泵维护视觉手册", page_number=2),
        _hit(page_id=16, document_id=16, title="泵维护视觉手册_v2.0"),
        _hit(page_id=10, document_id=10, title="泵维护视觉手册_v1.0"),
        _hit(page_id=11, document_id=11, title="泵维护视觉手册_v1.1"),
    ]
    result, stub = _adapter_result(
        tmp_path,
        question_pool,
        {"query": "最新正式版 M8 标准力矩", "limit": 2},
        pools={"泵维护视觉手册_v2.0": title_pool},
    )
    assert result.status.value == "success"
    titles = {item["document_title"] for item in result.data["results"]}
    assert "泵维护视觉手册_v1.1" in titles
    assert result.data["total"] == MAX_FAMILY_PAGES
    assert len(stub.calls) == MAX_FAMILY_PAGES
    # Co-query used the top hit's own document title, not the raw question.
    assert result.data["family_expansion"]["triggered"] is True
