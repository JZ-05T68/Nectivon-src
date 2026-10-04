"""V086-309-PRT1 regression: the DEFAULT keyword search surface is knowledge.

Round 2 built ``search_*`` FTS mirrors that exclude scanner artifacts, but the
product keyword search (「检索资料」→ 关键词搜索) still recalled and displayed
raw OCR — searching 「夸克」 returned 13 scanner pages whose snippets showed
「扫码使用 夸克扫描王」. The default knowledge search (recall, matched fields,
matched content, snippets, facet/document counts) must evaluate the SAME
sanitized knowledge surface as the FTS mirrors, while raw OCR stays verbatim.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.search_service import SearchService

#: A real scanned-exam page: printed footer + real math content + scanner
#: watermark lines. The footer and the math content MUST stay on the
#: knowledge surface; the watermark lines must not.
EXAM_PAGE_OCR = (
    "高三数学试卷（九）\n"
    "17. 已知函数 f(x)=x²lnx，求它的极值。\n"
    "第3页（共6页）\n"
    "扫码使用\n"
    "夸克扫描王"
)

#: A page whose only OCR content is scanner branding (cover/back page).
SCANNER_ONLY_OCR = "扫码使用\n夸克扫描王\n二维码"


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "2").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "07常州数学.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    db.create_document(
        title="07常州数学",
        filename="07常州数学.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=2,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path="pages/1/page-1.png",
        ocr_text=EXAM_PAGE_OCR,
        status="ready",
    )
    for index in range(2, 12):  # scanner-only pages: recall-pool flood guard
        (tmp_path / "data" / "pages" / "2" / f"page-{index}.png").write_bytes(b"png")
        db.create_page(
            document_id=1,
            page_number=index,
            image_path=f"pages/2/page-{index}.png",
            ocr_text=SCANNER_ONLY_OCR,
            status="ready",
        )
    return db


# ------------------------------------------------------------------ A/B raw
def test_raw_ocr_still_contains_watermark(database: Database) -> None:
    """RAW EVIDENCE layer is never modified by the search-surface fix."""
    page = database.get_page_by_number(1, 1)
    assert page is not None
    assert "扫码使用" in page.ocr_text
    assert "夸克扫描王" in page.ocr_text
    assert "第3页（共6页）" in page.ocr_text


def test_search_ocr_mirror_has_no_scanner_branding(database: Database) -> None:
    with database._connection() as connection:
        rows = connection.execute(
            "SELECT search_ocr_text FROM pages"
        ).fetchall()
    for row in rows:
        mirror = str(row["search_ocr_text"])
        assert "夸克" not in mirror
        assert "扫描王" not in mirror
        assert "扫码使用" not in mirror


# ----------------------------------------------------- C/D recall semantics
def test_default_keyword_search_excludes_scanner_only_pages(
    database: Database,
) -> None:
    service = SearchService(database)
    for query in ("夸克", "扫描王", "扫码使用"):
        results = service.search(query, limit=20)
        assert results == [], f"query {query!r} must not recall scanner pages"


def test_default_keyword_search_still_recalls_real_math_content(
    database: Database,
) -> None:
    service = SearchService(database)
    results = service.search("极值", limit=20)
    assert results, "real exam content must stay recallable"
    page_ids = {result.page_id for result in results}
    first_page = database.get_page_by_number(1, 1)
    assert first_page is not None
    assert first_page.id in page_ids


# --------------------------------------------------------------- E snippets
def test_snippet_and_matched_content_never_leak_branding(
    database: Database,
) -> None:
    service = SearchService(database)
    results = service.search("极值", limit=20)
    assert results
    forbidden = ("夸克", "扫描王", "扫码使用")
    for result in results:
        for text in (
            result.snippet,
            result.content,
            *(snippet.text for snippet in result.snippets),
        ):
            for token in forbidden:
                assert token not in text, (
                    f"knowledge snippet leaked {token!r}: {text!r}"
                )


def test_matched_fields_do_not_include_artifact_only_match(
    database: Database,
) -> None:
    """The real page matches through its real content, never through the
    watermark-only OCR lines."""
    service = SearchService(database)
    results = service.search("极值", limit=20)
    assert results
    for result in results:
        assert result.match_fields  # a real match field is reported
        assert result.match_type


# ------------------------------------------------- F printed page number
def test_printed_page_number_stays_on_knowledge_surface(
    database: Database,
) -> None:
    service = SearchService(database)
    results = service.search("极值", limit=20)
    assert results
    for result in results:
        if result.page_number == 1:
            assert "第3页（共6页）" in result.content
    page = database.get_page_by_number(1, 1)
    assert page is not None
    with database._connection() as connection:
        row = connection.execute(
            "SELECT printed_page_number, printed_total_pages FROM pages WHERE id = ?",
            (page.id,),
        ).fetchone()
    assert row["printed_page_number"] == 3
    assert row["printed_total_pages"] == 6


# ------------------------------------------------- G user correction kept
def test_user_correction_remains_verbatim_and_recallable(
    database: Database,
) -> None:
    page = database.get_page_by_number(1, 1)
    assert page is not None
    correction = "16√3+12√7"
    database.update_page(page.id, markdown_content=correction)
    # Raw user data verbatim.
    updated = database.get_page(page.id)
    assert updated is not None
    assert updated.markdown_content == correction
    # Direct knowledge-surface recall of the correction.
    results = database.search('"16√3"', terms=("16√3",), limit=10)
    assert any(result.page_id == page.id for result in results)
    for result in results:
        assert "16√3+12√7" in result.content
    # And through the product-facing SearchService path.
    service = SearchService(database)
    hits = service.search("16√3", limit=10)
    assert any(hit.page_id == page.id for hit in hits)


# --------------------------------------------- counts agree with the window
def test_facet_and_document_counts_exclude_scanner_only_rows(
    database: Database,
) -> None:
    from src.models import SearchFilters

    filters = SearchFilters()
    scanner_terms = ("夸克",)
    facets = database.search_facet_counts(terms=scanner_terms, filters=filters)
    assert facets.total == 0
    assert all(count == 0 for count in facets.documents.values())
    assert database.search_document_counts(terms=scanner_terms, filters=filters) == {}

    real_terms = ("极值",)
    real_facets = database.search_facet_counts(terms=real_terms, filters=filters)
    assert real_facets.total >= 1
    real_counts = database.search_document_counts(terms=real_terms, filters=filters)
    assert real_counts.get(1, 0) >= 1
    assert sum(real_counts.values()) == real_facets.total


def test_search_window_is_not_flooded_by_scanner_rows(
    database: Database,
) -> None:
    """Over-fetch + post-filter: scanner-only rows cannot displace the real
    page from the result window."""
    service = SearchService(database)
    results = service.search("极值 夸克", limit=5)
    page_ids = {result.page_id for result in results}
    first_page = database.get_page_by_number(1, 1)
    assert first_page is not None
    assert first_page.id in page_ids
