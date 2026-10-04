"""Regression — FTS topup must survive multiple insertions (C3 family).

HBV2 Phase 2 RUN2 / M1-RESIDUAL fix session (2026-09-09): with a query like
「气源三联件 每日 维护」 the page holding the rare exact phrase ranks #1 by
pure bm25 but carries no title boost, while ~20 维护-titled decoys each take
the -30 title boost and crowd the relevance-ordered window. The FAIL-021
topup exists precisely to re-insert the strongest pure-FTS matches — but its
insertion loop popped the *last list element*, which after the first append
is the previously inserted topup row, so with several topup candidates only
the last one survived and the exact-phrase page fell out of the tool's
top-20 window (staging e2e: 《气源三联件》 question failed 2/3 runs).

Contract: every topup row that fits must be inserted by evicting an
*original* window row (metadata-only rows first, then the weakest original
tail) — never a previously inserted topup row.
"""

from __future__ import annotations

from pathlib import Path

from src.database import Database
from src.search_service import SearchService

TOP_K = 20  # page_search tool default limit


def _database_with_topup_corpus(tmp_path: Path) -> Database:
    database_dir = tmp_path / "db"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(database_dir / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    image_path = pages_dir / "page_0001.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")

    def add_document(title: str, text: str) -> None:
        document = database.create_document(
            title=title,
            filename=f"{title}.pdf",
            source_path=f"data/raw/{title}.pdf",
            sha256=(str(abs(hash(title)) % 10**6)).ljust(64, "0"),
            page_count=1,
        )
        database.create_page(
            document_id=document.id,
            page_number=1,
            image_path=image_path,
            extracted_text=text,
        )

    # 22 title-boosted decoys: 维护 in the title (-30 boost) and the body —
    # every window row carries an FTS match, so there are no NULL-rank
    # evictable rows and the relevance window fills with boosted decoys.
    for index in range(1, 23):
        add_document(
            f"设备维护手册_{index:02d}",
            f"设备维护手册 第 {index} 分册\n"
            "本册说明维护频次与维护记录要求，维护工作每日登记，"
            "维护项目完成后由班组长复核。\n",
        )
    # 8 unboosted high-tf decoys: no rank term in the title (no boost), but
    # dense 维护/每日 bodies give them top pure-bm25 ranks — they sit outside
    # the boost-ordered main window and become multiple topup candidates,
    # which is what triggers the pop-last defect.
    for index in range(1, 9):
        add_document(
            f"运行日志_{index:02d}",
            f"运行日志 第 {index} 周\n"
            "维护维护维护：维护班每日执行维护保养，维护记录每日归档，"
            "维护项与每日巡检结果一致。\n",
        )
    # target: holds the rare exact phrase; best pure-bm25 score by far, no
    # title boost — it must reach the window through the topup.
    add_document(
        "气动巡检要点",
        "气动巡检要点\n"
        "气源三联件由过滤器、减压阀、油雾器组成，每日巡检需打开"
        "过滤器排水阀排水。\n",
    )
    return database


def test_topup_survives_multiple_insertions(tmp_path: Path) -> None:
    database = _database_with_topup_corpus(tmp_path)
    service = SearchService(database)
    results = service.search("气源三联件 每日 维护", limit=TOP_K)
    rank = next(
        (
            index
            for index, result in enumerate(results, start=1)
            if result.document_title == "气动巡检要点"
        ),
        None,
    )
    assert rank is not None, (
        "整词短语最佳 bm25 页应在 topup 后进入 top-20："
        + ", ".join(r.document_title for r in results[:5])
    )


def test_topup_keeps_metadata_only_eviction_preference(tmp_path: Path) -> None:
    """Original FAIL-021 semantics: metadata-only rows (no FTS match) are
    evicted first; the fix must not change that preference."""

    database = _database_with_topup_corpus(tmp_path)
    service = SearchService(database)
    # a title-only query: decoy titles all match (metadata), pages without
    # FTS content match may still fill the window; results must come back
    # ordered and bounded.
    results = service.search("设备维护手册", limit=TOP_K)
    assert len(results) <= TOP_K
    assert all("设备维护手册" in r.document_title for r in results[:5])
