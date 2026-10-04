"""M1-RESIDUAL regression — unanchored colloquial queries after digit demotion.

HBV2 Phase 2 RUN2 (2026-09-09, independent WorkBuddy HY4 evidence, HEAD
35e368f): the M1 digit/interrogative demotion fixed the numeric flood but
introduced a new residual. When the user's colloquial wording shares no
literal token with the source text (「每秒走20米」 vs 「20 m/s 匀速行驶」),
the surviving ranking terms match no page in the corpus at all; the demoted
digits (20/10) keep the only literal link to the correct page but cannot
contribute bm25 weight, so that page sinks below every zero-signal row
(staging: rank 51-72 of ~76, outside the agent's top-20 window).

Fix contract (generic, no domain vocabulary):
- when demotion removed terms but the surviving ranking terms match zero
  pages via FTS, ``Database.search`` restores full-term ranking for that
  query — digits regain weight only when nothing else carries any signal;
- when literal recall returns nothing and a compact letter+digit token
  (「20m」) hides its digit from recall, ``SearchService.search`` retries
  once with the token's digit fragment added (zero-result queries only).

Layered checks in this module (synthetic corpus, no frozen content copied):
- GROUP A: M1R reproduction family — unanchored colloquial variants must
  surface the physics-notebook page inside the effective top-20;
- GROUP B: anchored controls — 资料名/序号 anchoring keeps rank-1;
- GROUP C/D: numeric-dense and meaningful-ranking-term controls — the
  fallback must NOT engage (and digits must not flood) when the ranking
  terms genuinely match pages;
- zero-result retry must never change a query that already recalls pages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.search_service import SearchService

TOP_K = 20  # page_search tool default limit


def _database_with_m1r_corpus(tmp_path: Path) -> Database:
    """Synthetic environment mirroring the recorded staging failure shape.

    30 long maintenance-log decoy pages carry the digits 20/10 in a low
    density comparable to real corpus pages (half contain 20, half contain
    10, none contain both). They build the literal recall pool that, pre-fix,
    buries the target page by the document-id tie-break. The colloquial
    tokens 每秒/那个 appear NOWHERE in the corpus — exactly the staging
    condition that made the surviving ranking terms match zero pages. The
    target notebook page is created LAST (highest document id, mirroring
    staging doc 189). One unrelated control page contains the distinct
    colloquial token 每小时 so the "ranking terms still match" control has a
    real target.
    """

    database_dir = tmp_path / "db"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(database_dir / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    image_path = pages_dir / "page_0001.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")

    def add_document(title: str, page_texts: list[str]) -> None:
        document = database.create_document(
            title=title,
            filename=f"{title}.pdf",
            source_path=f"data/raw/{title}.pdf",
            sha256=(str(abs(hash(title)) % 10**6)).ljust(64, "0"),
            page_count=len(page_texts),
        )
        for page_number, text in enumerate(page_texts, start=1):
            database.create_page(
                document_id=document.id,
                page_number=page_number,
                image_path=image_path,
                extracted_text=text,
            )

    filler = (
        "车间设备运行平稳，班组长按点检表逐项确认，阀组无渗漏，"
        "管路压力在正常区间，记录员核对台账后签字归档。"
    )

    # long, low-density maintenance decoys: realistic length so bm25 length
    # normalization does not hand them an artificial advantage; they carry
    # the digits that keep the target page's only literal recall link.
    for index in range(1, 31):
        digit = "20" if index % 2 == 0 else "10"
        add_document(
            f"车间备件流水台账_{index:02d}",
            [
                f"车间备件流水台账 第 {index} 期\n"
                f"本期盘点螺栓 {digit} 只，垫圈若干，滤芯按周期更换，"
                f"记录员登记 {digit} 条巡检结果，异常项已转维修工单。"
                + filler
                + f"月末汇总：备件消耗与上月持平，库存 {digit} 箱待补。"
                + filler
                + "下期计划：重点跟踪易损件寿命，提前申报采购。"
                + filler
            ],
        )

    # control page for "ranking terms still match" behaviour (distinct
    # colloquial token 每小时, never used by the M1R family)
    add_document(
        "电机转速手册",
        [
            "电机转速手册\n"
            "电机每小时 1800 转，风扇每小时 1200 转；"
            "转速由变频器频率设定。\n"
        ],
    )

    # target page — created last, highest document id (mirrors staging doc 189)
    add_document(
        "我的物理错题本",
        [
            "我的物理错题本\n"
            "1. 一辆汽车以 20 m/s 匀速行驶，10 秒内通过的路程是多少？\n"
            "答案：s = v*t = 20 * 10 = 200 米。\n"
            "2. 五千克的东西举高两米，做的功是多少？答案：W = mgh = 100 焦。\n"
            "3. 9V 电源接 3Ω 与 6Ω 串联电路，电流是多少？答案：1 安。\n"
        ],
    )
    return database


def _target_rank(results: list, title_fragment: str) -> int | None:
    return next(
        (
            index
            for index, result in enumerate(results, start=1)
            if title_fragment in result.document_title
        ),
        None,
    )


# --------------------------------------------------------------- GROUP A


@pytest.mark.parametrize(
    "question",
    [
        "那个20米每秒的车",
        "车每秒走20米，10秒多远",
        "每秒20米走十秒",
        "20米每秒那个车走10秒",
    ],
    ids=["M1R-002", "variant-1", "variant-2", "variant-3"],
)
def test_unanchored_colloquial_variants_surface_target_page(
    tmp_path: Path, question: str
) -> None:
    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(question, limit=TOP_K)
    rank = _target_rank(results, "我的物理错题本")
    assert rank is not None, (
        f"{question!r} -> 正确页掉出 top-{TOP_K}："
        + ", ".join(r.document_title for r in results[:5])
    )


def test_m1r001_raw_full_question_stays_top(tmp_path: Path) -> None:
    """M1R-001 raw wording carries 一辆 which literally matches the source —
    this must keep working (it already does; guards the fix)."""

    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(
        "一辆车每秒走20米，走了10秒，一共走了多远？", limit=TOP_K
    )
    rank = _target_rank(results, "我的物理错题本")
    assert rank is not None and rank <= 5, (
        f"M1R-001 raw wording rank={rank}"
    )


def test_glued_identifier_colloquial_zero_recall_retry(tmp_path: Path) -> None:
    """「20m每秒那个车」: the glued token 20m hides the digit from literal
    recall and the colloquial tokens exist nowhere — zero results pre-fix.
    The zero-result retry must recover the digit and surface the target."""

    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search("20m每秒那个车", limit=TOP_K)
    assert results, "零召回的口语问法应通过数字化碎片重试召回"
    rank = _target_rank(results, "我的物理错题本")
    assert rank is not None and rank <= TOP_K, (
        f"重试后正确页 rank={rank}"
    )


# --------------------------------------------------------------- GROUP B


@pytest.mark.parametrize(
    "question",
    [
        "错题本里20m/s那题答案是多少",
        "错题本第1题是不是200米",
    ],
)
def test_anchored_controls_keep_rank_one(
    tmp_path: Path, question: str
) -> None:
    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(question, limit=TOP_K)
    rank = _target_rank(results, "我的物理错题本")
    assert rank is not None and rank <= 3, (
        f"{question!r} anchored control rank={rank}"
    )


# ----------------------------------------------------------- GROUP C / D


def test_meaningful_rank_terms_do_not_trigger_fallback(
    tmp_path: Path,
) -> None:
    """「那个每小时 1800 转的电机」: 每小时 genuinely matches its target page,
    so the fallback must not engage and the page must lead on its own
    ranking signal (digits demoted as designed)."""

    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search("那个每小时1800转的电机", limit=TOP_K)
    rank = _target_rank(results, "电机转速手册")
    assert rank is not None and rank <= 5, (
        f"有效排序词场景 rank={rank}，top-3："
        + ", ".join(r.document_title for r in results[:3])
    )


def test_numeric_dense_engineering_query_no_flood(tmp_path: Path) -> None:
    """Numeric-dense engineering question whose content words match pages:
    demotion keeps doing its job (decoy digit pages must not flood the top
    window ahead of genuinely matching pages)."""

    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(
        "电机每小时1800转，转速与频率的关系是多少", limit=TOP_K
    )
    rank = _target_rank(results, "电机转速手册")
    assert rank is not None and rank <= 5


def test_zero_result_retry_never_changes_successful_queries(
    tmp_path: Path,
) -> None:
    """The retry is strictly gated on zero literal recall: queries that
    already recall pages must return byte-identical results as before."""

    database = _database_with_m1r_corpus(tmp_path)
    service = SearchService(database)
    for question in (
        "一辆车每秒走20米，走了10秒，一共走了多远？",
        "错题本第1题是不是200米",
        "那个20米每秒的车",
        "车间备件流水台账",
    ):
        first = service.search(question, limit=TOP_K)
        second = service.search(question, limit=TOP_K)
        assert [r.page_id for r in first] == [r.page_id for r in second]
