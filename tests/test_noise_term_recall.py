"""M1 focused regression — low-information term demotion in FTS5 ranking.

HBV2-MORNING-20260909 M1 (NOISE_TERM_OR_FLOOD_FALSE_NEGATIVE_RETRIEVAL):
questions carrying digits, units and interrogatives fused number fragments
(0/50/63/2/5) and question debris (多少/还是) into the FTS5 OR ranking;
number-dense decoy pages (price lists, boundary test sets, maintenance plans)
matched them wholesale and pushed the truly relevant page out of the window
(recorded: correct page at rank 16 of 20).

The fix is generic (no domain word lists): ``partition_query_terms`` splits
extracted terms into ranking terms and demoted terms — isolated pure digits
and interrogative fragments lose ranking weight but keep literal recall and
highlighting; digits inside letter-mixed identifiers (VFD-600, P0-07,
STM32H750) are anchored and keep full weight. ``Database.search`` restricts
the bm25 ranking CTE and the field relevance boosts to the ranking terms;
the exact-phrase bonus keeps its pre-M1 anchor (the leading CJK group).

Layer checks:
- unit: term classification rules (digits, identifiers, interrogatives,
  fallback for numbers-only queries);
- corpus: the six recorded failure questions must surface the correct page
  in the effective top-5, plus five new cross-domain probes
  (PLC / 电机 / 传感器 / PID / 材料参数);
- guardrails: previously-passing anchored and short queries stay green.
"""

from __future__ import annotations

from pathlib import Path

from src.database import Database, _relevance_expression
from src.search_service import SearchService
from src.text_utils import extract_search_terms, partition_query_terms

# ------------------------------------------------------------ term classes

def test_isolated_digits_and_interrogatives_are_demoted() -> None:
    terms = extract_search_terms("缸径 50 毫米在 0.63 兆帕下推力是多少")
    ranking, demoted = partition_query_terms(terms, "缸径 50 毫米在 0.63 兆帕下推力是多少")

    assert {"50", "0", "63"} <= set(demoted)
    assert "多少" in demoted
    for content in ("缸径", "毫米", "推力"):
        assert content in ranking, content


def test_identifier_digits_keep_full_weight() -> None:
    for query, anchored in (
        ("STM32H750 主频是多少", "stm32h750"),
        ("VFD-600 的启动转矩是多少", "600"),
        ("P0-07 参数是什么意思", "07"),
        ("S7-1200 的 AIW0 量程是多少", "1200"),
    ):
        terms = extract_search_terms(query)
        ranking, demoted = partition_query_terms(terms, query)
        assert anchored in ranking, (query, ranking, demoted)
        assert anchored not in demoted


def test_isolated_digit_cannot_reenter_exact_phrase_bonus() -> None:
    query = "24 时间继电器"
    terms = extract_search_terms(query)
    ranking, demoted = partition_query_terms(terms, query)
    _, parameters = _relevance_expression(terms, ranking)

    assert "24" in demoted
    assert any("时间继电器" in str(value) for value in parameters)
    assert not any("24" in str(value) for value in parameters)


def test_identifier_digits_remain_eligible_for_relevance_boosts() -> None:
    for query, fragment in (
        ("STM32H750", "stm32h750"),
        ("VFD-600", "600"),
    ):
        terms = extract_search_terms(query)
        ranking, _ = partition_query_terms(terms, query)
        _, parameters = _relevance_expression(terms, ranking)
        assert any(fragment in str(value).casefold() for value in parameters)


def test_decimal_value_with_solid_unit_keeps_adjacent_digit() -> None:
    # 0.63MPa written solid: jieba keeps the value+unit as one letter-mixed
    # token which stays in the ranking; a bare leading "0" across the decimal
    # point is demoted.
    query = "0.63MPa 下推力"
    terms = extract_search_terms(query)
    ranking, demoted = partition_query_terms(terms, query)
    assert "63mpa" in ranking
    assert "0" in demoted


def test_numbers_only_query_falls_back_to_full_terms() -> None:
    terms = extract_search_terms("50 还是 63")
    ranking, demoted = partition_query_terms(terms, "50 还是 63")
    assert demoted == ()
    assert set(ranking) == set(terms)


def test_date_like_digits_stay_demoted() -> None:
    terms = extract_search_terms("2025 修订版和 2024 版差在哪")
    ranking, demoted = partition_query_terms(terms, "2025 修订版和 2024 版差在哪")
    assert {"2025", "2024"} <= set(demoted)
    assert "修订" in ranking


# ------------------------------------------------------------- corpus probe

def _database_with_noise_corpus(tmp_path: Path) -> Database:
    """Synthetic noise environment mirroring the recorded failure corpus.

    Number-dense decoy documents (price list, boundary-label test set,
    maintenance plans, parameter tables) flood isolated-digit matches; the
    correct pages hold the recorded answers. Text is synthetic — no frozen
    corpus content is copied.
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
            sha256=(str(abs(hash(title)) % 10 ** 6)).ljust(64, "0"),
            page_count=len(page_texts),
        )
        for page_number, text in enumerate(page_texts, start=1):
            database.create_page(
                document_id=document.id,
                page_number=page_number,
                image_path=image_path,
                extracted_text=text,
            )

    # number-dense decoys (the flood source)
    add_document(
        "D2_备件价格清单2026跨页",
        [
            "备件价格清单 2026 跨页\n"
            "序号 0 名称 单价 数量 小计\n"
            "1 密封圈 5 100 500\n"
            "2 滤芯 63 2 126\n"
            "3 电磁阀 50 4 200\n"
            "4 压力表 0.5 10 5\n"
            "5 油管 2 63 126\n",
            "备件价格清单 2026 跨页（续）\n"
            "6 螺栓 0.2 500 100\n"
            "7 垫片 2.5 63 157.5\n"
            "8 卡箍 5 26 130\n"
            "9 铭牌 0.63 20 12.6\n",
        ],
    )
    add_document(
        "图表标签边界测试集",
        [
            "图表标签边界测试集 p1\n"
            "标签 0=2.6 1=26 2=0.63 3=63 4=50 5=2 6=5\n"
            "边界数值：0 2.6 26 0.63 63 50 2 5\n",
            "图表标签边界测试集 p2\n"
            "第二组标签：6=0 7=2.6 8=26 9=63 10=50 11=2 12=5\n"
            "校验位：2.6/26/0.63/63/50/2/5 全部在容差内\n",
        ],
    )
    add_document(
        "年度维护计划_2025",
        [
            "年度维护计划 2025\n"
            "1月 每日巡检 2 次，每周抽检 5 次\n"
            "2月 计划工单 63 张，完成 50 张\n"
            "3月 备件消耗 0.63 万元，同比 2% \n",
        ],
    )
    add_document(
        "年度维护计划_2026",
        [
            "年度维护计划 2026\n"
            "1月 每日巡检 3 次，每周抽检 5 次\n"
            "2月 计划工单 50 张，完成 63 张\n"
            "3月 备件消耗 5 万元，同比 2.6%\n",
        ],
    )
    add_document(
        "设备参数汇总表",
        [
            "设备参数汇总表\n"
            "设备 编号 压力 MPa 温度 转速\n"
            "A 0 0.63 50 2\n"
            "B 1 63 5 26\n"
            "C 2 2.5 0.5 100\n",
        ],
    )
    add_document(
        "气缸行程规格",
        [
            "气缸行程规格\n"
            "型号 行程 mm 缸径 mm\n"
            "A 50 0\n"
            "B 63 2\n"
            "C 26 5\n",
        ],
    )

    # correct pages (recorded answers)
    add_document(
        "液压与气动技术手册",
        [
            "液压与气动技术手册\n"
            "第 1 章 液压系统概述：液压站由泵、阀、缸、辅件组成，"
            "系统压力由溢流阀整定。\n",
            "液压与气动技术手册\n"
            "第 2 章 气缸理论推力：F = 压力 × 活塞面积。"
            "缸径 63 毫米在 0.63 MPa 下理论推力约 2.0 kN；"
            "缸径 50 毫米在 0.63 MPa 下理论推力约 1.2 kN。"
            "实际选型时负载率按 80% 折算。\n",
            "液压与气动技术手册\n"
            "第 3 章 气源三联件：过滤器、减压阀、油雾器组成气源三联件，"
            "每日巡检需打开过滤器排水阀排水，检查减压阀整定压力。\n",
        ],
    )
    add_document(
        "检测与机器人传感器讲义",
        [
            "检测与机器人传感器讲义\n"
            "第 1 章 传感器分类：位移、力、温度、视觉传感器。\n",
            "检测与机器人传感器讲义\n"
            "第 2 章 六维力传感器：标定周期为 12 个月，超差或撞击后须立即"
            "重新标定；零点漂移每月核查一次。\n",
        ],
    )
    add_document(
        "材料科学基础讲义",
        [
            "材料科学基础讲义\n"
            "铁碳相图：含碳量 2.11% 是钢与铸铁的分界，含碳 2.5% 的铁碳合金"
            "属于铸铁；共晶点含碳 4.3%。\n",
        ],
    )
    add_document(
        "齿轮箱检修手册_2024版",
        [
            "齿轮箱检修手册 2024版\n"
            "润滑：油温报警 75 摄氏度；轴承润滑周期 4000 小时。\n",
        ],
    )
    add_document(
        "齿轮箱检修手册_2025修订版",
        [
            "齿轮箱检修手册 2025修订版\n"
            "润滑：油温报警修订为 78 摄氏度，取代 2024版 数值；"
            "新增轴承振动限值 4.5 mm/s。\n",
        ],
    )

    # five new cross-domain probes (correct pages)
    add_document(
        "PLC应用手册",
        [
            "PLC应用手册\n"
            "D 寄存器：D100 掉电保持区，断电后数据不丢失；D0-D99 每次上电"
            "清零。掉电保持由参数设定。\n",
        ],
    )
    add_document(
        "电机选型手册",
        [
            "电机选型手册\n"
            "三相异步电机：额定功率 7.5 千瓦的电机额定电流约 15 安；"
            "4 极电机同步转速 1500 转每分。\n",
        ],
    )
    add_document(
        "传感器选型指南",
        [
            "传感器选型指南\n"
            "压力传感器精度等级：量程 0 到 10 千帕、精度 0.5 级表示最大"
            "允许误差为量程的 0.5%，即 0.05 千帕。\n",
        ],
    )
    add_document(
        "自动控制原理课程讲义",
        [
            "自动控制原理课程讲义\n"
            "PID 参数整定：比例增益 kp 增大会加快响应但超调量增大；"
            "出现大幅超调时先减小 kp，再按 Z-N 法重整定 ti。\n",
        ],
    )
    add_document(
        "材料性能手册",
        [
            "材料性能手册\n"
            "45 钢：屈服强度约 355 MPa，抗拉强度约 600 MPa，调质处理后"
            "硬度 220-250 HB。\n",
        ],
    )
    return database


_RECORDED_M1_CASES = [
    # (question, expected document title fragment)
    ("缸径 63 毫米在 0.63 MPa 下理论推力多少？实际选型按多少折算？", "液压与气动技术手册"),
    ("缸径 50 毫米在 0.63 兆帕下推力是多少", "液压与气动技术手册"),
    ("气动系统里气源三联件每日要做什么", "液压与气动技术手册"),
    ("你说反了吧，我记得六维力传感器是 6 个月", "检测与机器人传感器讲义"),
    ("含碳 2.5% 的是钢还是铸铁？", "材料科学基础讲义"),
]

_NEW_CROSS_DOMAIN_CASES = [
    ("PLC 的 D100 寄存器掉电后数据还能保持吗", "PLC应用手册"),
    ("额定功率 7.5 千瓦的电机额定电流是多少", "电机选型手册"),
    ("量程 0 到 10 千帕的传感器精度 0.5 级是什么意思", "传感器选型指南"),
    ("PID 控制 kp 取太大时超调量大怎么办", "自动控制原理课程讲义"),
    ("45 号钢的屈服强度是多少 MPa", "材料性能手册"),
]


def test_recorded_m1_failures_surface_correct_page(tmp_path: Path) -> None:
    database = _database_with_noise_corpus(tmp_path)
    service = SearchService(database)
    for question, expected_title in _RECORDED_M1_CASES:
        results = service.search(question, limit=20)
        ranks = [
            index
            for index, result in enumerate(results, start=1)
            if expected_title in result.document_title
        ]
        assert ranks, f"{question} -> {expected_title} 未召回"
        assert ranks[0] <= 5, (
            f"{question} -> {expected_title} 排名 {ranks[0]}，超出有效 top-5："
            + ", ".join(f"{r.document_title}({r.rank:.1f})" for r in results[:5])
        )


def test_new_cross_domain_probes_surface_correct_page(tmp_path: Path) -> None:
    database = _database_with_noise_corpus(tmp_path)
    service = SearchService(database)
    for question, expected_title in _NEW_CROSS_DOMAIN_CASES:
        results = service.search(question, limit=20)
        ranks = [
            index
            for index, result in enumerate(results, start=1)
            if expected_title in result.document_title
        ]
        assert ranks, f"{question} -> {expected_title} 未召回"
        assert ranks[0] <= 5, (
            f"{question} -> {expected_title} 排名 {ranks[0]}，超出有效 top-5："
            + ", ".join(f"{r.document_title}({r.rank:.1f})" for r in results[:5])
        )


def test_version_compare_surfaces_both_family_members(tmp_path: Path) -> None:
    """D2-T3 face: the 2025 revision leads and the 2024 edition stays in the
    lexical top-20 window (page_visual_search additionally re-pulls family
    members at tool level)."""

    database = _database_with_noise_corpus(tmp_path)
    service = SearchService(database)
    results = service.search("那 2025 修订版和 2024 版差在哪", limit=20)
    titles = [result.document_title for result in results]
    assert "齿轮箱检修手册_2025修订版" in titles
    assert "齿轮箱检修手册_2024版" in titles
    assert titles.index("齿轮箱检修手册_2025修订版") < 3
    assert titles.index("齿轮箱检修手册_2024版") < 20


def test_previously_passing_probes_do_not_regress(tmp_path: Path) -> None:
    database = _database_with_noise_corpus(tmp_path)
    service = SearchService(database)
    for question, expected_title in (
        ("缸径", "液压与气动技术手册"),
        ("推力", "液压与气动技术手册"),
        ("气缸理论推力怎么算", "液压与气动技术手册"),
        ("液压与气动技术手册里写的负载率按多少折算", "液压与气动技术手册"),
        ("气源三联件", "液压与气动技术手册"),
    ):
        results = service.search(question, limit=20)
        ranks = [
            index
            for index, result in enumerate(results, start=1)
            if expected_title in result.document_title
        ]
        assert ranks and ranks[0] <= 3, (
            f"{question} -> {expected_title} 期望进入 top-3，实际 top-3："
            + ", ".join(r.document_title for r in results[:3])
        )


def test_identifier_anchor_query_still_finds_model_page(tmp_path: Path) -> None:
    database = _database_with_noise_corpus(tmp_path)
    service = SearchService(database)
    # anchored digits stay in the ranking so the code+value stays findable
    results = service.search("PLC 里 D100 掉电保持吗", limit=20)
    assert results and "PLC应用手册" in results[0].document_title


def test_phrase_bonus_keeps_pre_m1_anchor(tmp_path: Path) -> None:
    """B1 regression (frozen Phase 1D baseline lost its rank-1 hit).

    The exact-phrase bonus must stay anchored on the *first literal* term
    (the leading CJK group), not on the first ranking term. Anchoring it on
    the ranking terms let the demoted fragment 「一个」 — the new first
    ranking term of 「怎么让一个引脚点亮发光二极管」 — hand decoy pages whose
    content merely contains 「一个」 a -10 bonus, and they outranked the page
    holding the actual content words (引脚/点亮/发光二极管).
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
            sha256=(str(abs(hash(title)) % 10 ** 6)).ljust(64, "0"),
            page_count=len(page_texts),
        )
        for page_number, text in enumerate(page_texts, start=1):
            database.create_page(
                document_id=document.id,
                page_number=page_number,
                image_path=image_path,
                extracted_text=text,
            )

    # relevant page: holds the content words, never the fragment 「一个」
    add_document(
        "LED驱动基础",
        [
            "LED驱动基础\n"
            "引脚配置为输出模式后，拉高电平即可点亮 LED；"
            "推挽输出可以直接驱动小功率发光二极管。\n",
        ],
    )
    # decoy page: matches only the low-information fragment 「一个」
    add_document(
        "设备巡检记录",
        [
            "设备巡检记录\n"
            "一个班次需要巡检两次，记录一个异常点；"
            "交接班时要核对一个台账。\n",
        ],
    )

    service = SearchService(database)
    results = service.search("怎么让一个引脚点亮发光二极管", limit=10)
    assert results, "相关页应当被召回"
    assert "LED驱动基础" in results[0].document_title, (
        "仅含「一个」的 decoy 页不应排在相关页之前，top-3："
        + ", ".join(r.document_title for r in results[:3])
    )


def test_question_number_does_not_change_target_relevance_score(tmp_path: Path) -> None:
    database = Database(tmp_path / "knowledge.db")
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")

    target = database.create_document(
        title="电气控制基础",
        filename="control.pdf",
        source_path="data/raw/control.pdf",
        sha256="a" * 64,
        page_count=1,
    )
    database.create_page(
        document_id=target.id,
        page_number=1,
        image_path=image_path,
        extracted_text="时间继电器分为通电延时型和断电延时型。",
    )
    decoy = database.create_document(
        title="24 号巡检记录",
        filename="record-24.pdf",
        source_path="data/raw/record-24.pdf",
        sha256="b" * 64,
        page_count=1,
    )
    database.create_page(
        document_id=decoy.id,
        page_number=1,
        image_path=image_path,
        extracted_text="24 号设备完成例行检查。",
    )

    service = SearchService(database)
    plain = service.search("时间继电器", limit=10)
    numbered = service.search("24 时间继电器", limit=10)
    assert plain[0].document_id == numbered[0].document_id == target.id
    assert numbered[0].rank == plain[0].rank
