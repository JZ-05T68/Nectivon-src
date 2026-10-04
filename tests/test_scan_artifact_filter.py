"""V086-309 regression: scanner branding out of knowledge text, footers in.

The paper's own printed page numbers (「高三数学试卷（九） 第1页（共6页）」)
are part of the original layout and must survive every filter; scanner
artifacts (「扫码使用」「夸克扫描王」/QR promo) must leave FTS search text
and Agent reading input while raw OCR and source files stay untouched.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.scan_artifact_filter import (
    extract_printed_page_info,
    filter_knowledge_text,
)
from src.text_utils import build_agent_page_text

WATERMARK_OCR = (
    "已知函数 f(x)=x²lnx，求极值。\n"
    "高三数学试卷(七) 第1页(共6页)\n"
    "扫码使用\n"
    "夸克扫描王"
)


# ------------------------------------------------------------- filter rules
def test_branding_lines_removed_and_printed_footer_kept() -> None:
    report = filter_knowledge_text(WATERMARK_OCR)
    assert "扫码使用" not in report.filtered_text
    assert "夸克扫描王" not in report.filtered_text
    assert "高三数学试卷(七) 第1页(共6页)" in report.filtered_text
    assert "已知函数" in report.filtered_text
    assert report.removed_lines == ("扫码使用", "夸克扫描王")
    assert report.kept_page_footer


def test_footer_with_brand_sharing_one_line_keeps_page_number() -> None:
    report = filter_knowledge_text("第3页（共6页） 夸克扫描王")
    assert "第3页（共6页）" in report.filtered_text
    assert "夸克扫描王" not in report.filtered_text


def test_full_width_parentheses_footer_recognised() -> None:
    report = filter_knowledge_text("扫码使用\n高三数学试卷（九） 第3页（共6页）\n夸克扫描王")
    assert "第3页（共6页）" in report.filtered_text
    assert report.kept_page_footer


def test_clean_text_is_returned_unchanged() -> None:
    clean = "第1题 已知函数 f(x)=x²lnx，求极值。\n第2题 求积分。"
    report = filter_knowledge_text(clean)
    assert report.filtered_text == clean
    assert report.removed_lines == ()


def test_extensible_brand_table_covers_common_scanner_apps() -> None:
    for token in ("扫描全能王", "CamScanner", "极速扫描，就是高效"):
        report = filter_knowledge_text(f"正文内容\n{token}")
        assert token not in report.filtered_text
        assert "正文内容" in report.filtered_text


def test_answer_sheet_boilerplate_never_becomes_knowledge_text() -> None:
    """V0.8.7 contract: sheet logistics + identity fields stay out."""

    sheet = (
        "请在各题的答题区域内作答，超出黑色矩形边框范围的答案无效\n"
        "正确填涂\n"
        "缺考标记\n"
        "贴条形码区\n"
        "准考证号\n"
        "3 1 0 5 2 2\n"
        "第1页（共6页）"
    )
    report = filter_knowledge_text(sheet)
    assert "答题区域" not in report.filtered_text
    assert "准考证号" not in report.filtered_text
    assert "第1页（共6页）" in report.filtered_text  # footer stays


def test_boilerplate_filter_is_conservative_not_line_shape_greedy() -> None:
    """Real content mentioning 答题 must not be dropped."""

    report = filter_knowledge_text("请在答题时注意单位换算，本题 6 分。")
    assert "请在答题时注意单位换算" in report.filtered_text


def test_printed_page_info_extraction() -> None:
    info = extract_printed_page_info(WATERMARK_OCR)
    assert info.printed_page_number == 1
    assert info.printed_total_pages == 6
    assert "第1页(共6页)" in info.footer_text


def test_printed_page_info_absent_is_none() -> None:
    info = extract_printed_page_info("只有正文内容，没有页脚。")
    assert info.printed_page_number is None
    assert info.printed_total_pages is None


# ----------------------------------------------------- database integration
@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "07常州数学.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="07常州数学",
        filename="07常州数学.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    return db


def test_create_page_filters_fts_but_keeps_raw_ocr(database: Database) -> None:
    page = database.create_page(
        document_id=1,
        page_number=1,
        image_path="page-1.png",
        ocr_text=WATERMARK_OCR,
        status="ready",
    )
    assert page.ocr_text == WATERMARK_OCR  # raw layer untouched
    with database._connection() as connection:
        row = connection.execute(
            "SELECT search_ocr_text, printed_page_number, printed_total_pages, "
            "document_footer FROM pages WHERE id = ?",
            (page.id,),
        ).fetchone()
        assert "夸克" not in str(row["search_ocr_text"])
        assert "扫描王" not in str(row["search_ocr_text"])
        assert row["printed_page_number"] == 1
        assert row["printed_total_pages"] == 6
        assert "第1页(共6页)" in str(row["document_footer"])
    # The real regression: FTS must not recall scanner branding (07常州 math).
    with database._connection() as connection:
        hits = connection.execute(
            "SELECT COUNT(*) FROM page_search WHERE page_search MATCH ?", ('"夸克"',)
        ).fetchone()[0]
    assert hits == 0
    # The exam's own page-number text stays searchable: every token of the
    # footer survives into the FTS mirror (token set check is tokenizer-
    # independent, unlike a fixed phrase query).
    from src.database import _tokenize_for_fts

    expected_tokens = _tokenize_for_fts("第1页(共6页)").split()
    assert expected_tokens
    with database._connection() as connection:
        search_text = connection.execute(
            "SELECT search_ocr_text FROM pages WHERE id = ?", (page.id,)
        ).fetchone()[0]
    indexed_tokens = set(str(search_text).split())
    assert set(expected_tokens) <= indexed_tokens


def test_update_page_refreshes_filtered_search_columns(database: Database) -> None:
    page = database.create_page(
        document_id=1,
        page_number=1,
        image_path="page-1.png",
        ocr_text="干净的正文",
        status="ready",
    )
    database.update_page(page.id, ocr_text=WATERMARK_OCR)
    with database._connection() as connection:
        row = connection.execute(
            "SELECT search_ocr_text, ocr_text FROM pages WHERE id = ?", (page.id,)
        ).fetchone()
    assert "夸克" not in str(row["search_ocr_text"])
    assert str(row["ocr_text"]) == WATERMARK_OCR


def test_user_correction_text_verbatim_but_search_mirror_clean(
    database: Database,
) -> None:
    """The real 07常州 user correction keeps its watermark lines verbatim in
    the stored text AND in the Agent's user-correction section, while the
    FTS retrieval mirror (a derived knowledge surface) stays clean."""

    page = database.create_page(
        document_id=1,
        page_number=1,
        image_path="page-1.png",
        ocr_text="正文",
        status="ready",
    )
    correction = "16√3+12√7\n扫码使用\n夸克扫描王"  # user data, watermark kept
    database.update_page(page.id, markdown_content=correction)
    with database._connection() as connection:
        row = connection.execute(
            "SELECT markdown_content, search_markdown_content FROM pages WHERE id = ?",
            (page.id,),
        ).fetchone()
    # User data verbatim — never modified on disk.
    assert str(row["markdown_content"]) == correction
    # Derived retrieval mirror is knowledge text: branding filtered, the
    # user's own content tokens still indexed (mirror is jieba-tokenized).
    assert "夸克" not in str(row["search_markdown_content"])
    mirror_tokens = set(str(row["search_markdown_content"]).split())
    assert "16" in mirror_tokens
    # Agent input keeps the user's correction verbatim (user intent wins).
    source_text, kind = build_agent_page_text(
        extracted_text="", ocr_text="正文", manual_text=correction
    )
    assert "夸克扫描王" in source_text


# ----------------------------------------------------------- agent input
def test_agent_page_text_excludes_scanner_branding() -> None:
    source_text, kind = build_agent_page_text(
        extracted_text="", ocr_text=WATERMARK_OCR, manual_text=""
    )
    assert kind == "ocr_text"
    assert "夸克扫描王" not in source_text
    assert "扫码使用" not in source_text
    assert "高三数学试卷(七) 第1页(共6页)" in source_text


def test_agent_page_text_never_filters_manual_correction() -> None:
    source_text, kind = build_agent_page_text(
        extracted_text="", ocr_text=WATERMARK_OCR, manual_text="用户校对：保留水印行\n夸克扫描王"
    )
    assert "【用户人工校对或补充】" in source_text
    assert "夸克扫描王" in source_text
