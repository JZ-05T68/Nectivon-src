"""M1R-A second residual regression — unanchored colloquial zero-recall.

HY4 independent retest (2026-09-09, %TEMP%/ekb_hy4_m1r, HEAD 2818737): nine
unanchored colloquial questions produced zero tool recall. Instrumented
staging reproduction (tmp/zh_repro_prefix.json) showed the model's rewritten
queries were reasonable (「铜 铝 导电」「闸 保险丝 选大小」「钢 碳含量 界限 铁」)
but :func:`extract_search_terms` had dropped every single-character CJK term
(铜/铝/钢/铁/闸/缸) because multi-character companions existed, Chinese
numerals (二十米/五公斤/五厘米) never meet the source's Arabic digits, and the
existing zero-result retry only widened glued letter+digit tokens. Literal
OR-recall therefore died before ranking could matter.

Fix contract (generic, no domain vocabulary) — the zero-result retry widens
once with:
- relaxed term extraction that keeps single-character CJK terms;
- Arabic digit fragments converted from Chinese numeral runs (二十→20);
- character fragments of multi-character CJK terms (coverage boosts);
- the existing glued identifier digit fragments.
Queries that already recall pages never enter the retry, so behaviour for
every previously-recalling query is unchanged by construction.

Synthetic corpus mirrors the recorded staging shapes without copying frozen
content: each target page carries the professional wording, decoys carry the
generic maintenance vocabulary, and the colloquial query words exist nowhere
in the corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.search_service import SearchService

TOP_K = 20  # page_search tool default limit


def _database_with_hy4_corpus(tmp_path: Path) -> Database:
    """Synthetic staging-shaped corpus for the second-residual family.

    Decoy pages use the same low-density maintenance filler as the M1R
    first-residual fixture (车间/管路/台账 vocabulary, sparse digits). Target
    pages are created last (highest document ids) mirroring staging, and none
    of them contains the colloquial query wording.
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
    for index in range(1, 25):
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
            ],
        )

    # generic-vocabulary neighbours that keep ranking honest without carrying
    # the target facts. Deliberately free of the S3-T7 hooks (选/大小): a pool
    # recalled by such generic words would suppress the zero-result retry —
    # that "junk recall" shape is a separate known boundary (CLASS C), not
    # this ladder's contract.
    add_document(
        "车间通用安全守则",
        ["车间通用安全守则\n1. 工具用后归位。2. 防护用品穿戴整齐。3. 下班断电。" + filler],
    )
    add_document(
        "设备管理参考",
        ["设备管理参考\n1. 机型按负载与转速确定。2. 备件齐套率每月核对。" + filler],
    )

    # ---- target pages (created last, mirroring staging id ordering) ----
    add_document(
        "材料物理性能讲义",
        [
            "材料物理性能讲义\n第一章 电学性能\n"
            "1. 20°C 电阻率（Ω·m）：铜 1.7e-8；铝 2.8e-8；铁 9.7e-8。\n"
            "2. 掺杂与冷加工都会增大金属电阻率；退火恢复。\n"
        ],
    )
    add_document(
        "材料科学基础讲义",
        [
            "材料科学基础讲义\n"
            "1. 钢与铸铁的分界：含碳 2.11%。\n"
            "2. 45 钢淬火温度 840 °C，水冷。\n"
        ],
    )
    add_document(
        "电气控制与PLC应用手册",
        [
            "电气控制与PLC应用手册\n第一章 常用低压电器参数\n"
            "1. 断路器长延时脱扣：按 1.05 倍额定电流校验。\n"
            "2. 控制回路熔断器：按回路额定电流的 1.5～2 倍选择。\n"
        ],
    )
    add_document(
        "液压与气动技术手册",
        [
            "液压与气动技术手册\n第二章 气缸选型与推力\n"
            "1. 气缸理论推力 F = 压力 * 活塞面积。\n"
            "2. 缸径 50mm 在 0.63 MPa 下理论推力约 1.2 kN。\n"
            "3. 缸径 63mm 在 0.63 MPa 下理论推力约 2.0 kN。\n"
        ],
    )
    add_document(
        "我的物理错题本",
        [
            "我的物理错题本\n"
            "1. 一辆汽车以 20 m/s 匀速行驶，10 秒内通过的路程是多少？\n"
            "答案：s = v*t = 20 * 10 = 200 米。\n"
            "2. 把 5 千克的物体举高 2 米，克服重力做功多少？答案：100 焦。\n"
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


def _rank_or_fail(question: str, results: list, title_fragment: str, bound: int) -> int:
    rank = _target_rank(results, title_fragment)
    top_titles = ", ".join(r.document_title for r in results[:5])
    assert rank is not None, (
        f"{question!r} 零召回未恢复（正确页不在池中）；top：{top_titles}"
    )
    assert rank <= bound, f"{question!r} 正确页 rank={rank} > {bound}；top：{top_titles}"
    return rank


# ---------------------------------------------- HY4 second-residual shapes


@pytest.mark.parametrize(
    ("question", "target", "bound"),
    [
        # S5-T3 shape: rewrite keeps 铜/铝, extraction dropped them (1-char)
        ("铜 铝 导电", "材料物理性能讲义", 3),
        ("铜和铝哪个导电好一点", "材料物理性能讲义", 5),
        # S5-T1 shape: 钢/铁 dropped, 碳含量 absent from source wording
        ("钢 碳含量 界限 铁", "材料科学基础讲义", 5),
        # S3-T7 shape: 闸 dropped, 保险丝 vs 熔断器 synonym gap
        ("闸 保险丝 选大小", "电气控制与PLC应用手册", 5),
        # S1-T3 shape: 缸 dropped, 五厘米 vs 50mm unit gap
        ("圆桶 缸 直径五厘米 顶多重", "液压与气动技术手册", TOP_K),
        # S2-T2 shape: 五公斤 vs 5 千克, 两米 vs 2 米
        ("五公斤 抬 两米", "我的物理错题本", 5),
    ],
    ids=["S5-T3-agent", "S5-T3-raw", "S5-T1", "S3-T7", "S1-T3", "S2-T2"],
)
def test_zero_recall_colloquial_shapes_recall_target(
    tmp_path: Path, question: str, target: str, bound: int
) -> None:
    database = _database_with_hy4_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(question, limit=TOP_K)
    _rank_or_fail(question, results, target, bound)


def test_chinese_numeral_speed_question_recalls_pool(
    tmp_path: Path,
) -> None:
    """S2-T1 shape (二十米 vs 20 m/s): the widened retry must at least pull
    the target page into the recall pool; staging measured rank 23/100 after
    the fix, so this asserts the pool-level contract rather than the window."""

    database = _database_with_hy4_corpus(tmp_path)
    service = SearchService(database)
    results = service.search("车一秒钟跑二十米，跑十秒能跑多远啊？", limit=100)
    _rank_or_fail("车一秒钟跑二十米", results, "我的物理错题本", 30)


# ------------------------------------------------------------ protections


@pytest.mark.parametrize(
    ("question", "target", "bound"),
    [
        ("错题本第2题答案是多少", "我的物理错题本", 3),
        ("材料物理性能讲义里 PT100 的量程是多少", "材料物理性能讲义", 3),
        ("缸径63mm在0.63MPa下理论推力是多少", "液压与气动技术手册", 3),
    ],
    ids=["anchored-notebook", "anchored-sensor", "numeric-engineering"],
)
def test_anchored_and_numeric_controls_keep_leading(
    tmp_path: Path, question: str, target: str, bound: int
) -> None:
    """Queries that already recall pages must be untouched by the widened
    retry (they never enter it) and keep their existing leading ranks."""

    database = _database_with_hy4_corpus(tmp_path)
    service = SearchService(database)
    results = service.search(question, limit=TOP_K)
    _rank_or_fail(question, results, target, bound)


def test_retry_is_idempotent_and_stable(tmp_path: Path) -> None:
    database = _database_with_hy4_corpus(tmp_path)
    service = SearchService(database)
    for question in (
        "铜 铝 导电",
        "五公斤 抬 两米",
        "错题本第2题答案是多少",
        "车间备件流水台账",
    ):
        first = service.search(question, limit=TOP_K)
        second = service.search(question, limit=TOP_K)
        assert [r.page_id for r in first] == [r.page_id for r in second]
