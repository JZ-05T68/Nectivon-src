# ruff: noqa: E501, E402
"""HBENCH_V2 多领域语料扩容 builder（staging only）。

按用户指示为 Human Benchmark V2 Night 1 扩充多学科模拟材料：

- 自动控制原理 / 电气控制与PLC / 电力电子技术 / 检测与机器人传感器 /
  液压与气动 / 人工智能 / 材料科学基础 / 材料物理性能 / 电路（邱关源教材参考提纲）
- 内容为自写的教科书常识要点摘要（非教材原文），含参数表、状态灯表、
  故障代码表、版本取代族（液压油 2024 试行 → 2026 修订）与 3 个图表页。

Stages:
  --generate  生成 PDF 到 staging-data/inbox-hbv2-domains
  --import    经 DocumentService 导入 staging
  --read      Agent 页面阅读（付费，写入 agent_readings）
  --rebuild-visual  幂等重建 E-1 视觉索引（与 agent bootstrap 同机制）
  --verify    只读核验导入结果

隔离纪律（PROCESS_INTEGRITY-003 教训）：本文件在导入任何 src 模块之前设置
EKB_STAGING_INSTANCE=1，全程仅使用 staging_settings()。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ["EKB_STAGING_INSTANCE"] = "1"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from src.config import staging_settings  # noqa: E402

INBOX = PROJECT_ROOT / "staging-data" / "inbox-hbv2-domains"

PAGE_W, PAGE_H = 595, 842


def _page(doc: fitz.Document) -> fitz.Page:
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _text(page: fitz.Page, x: float, y: float, size: int, value: str) -> None:
    page.insert_text((x, y), value, fontsize=size, fontname="china-s")


def _title(page: fitz.Page, value: str) -> None:
    _text(page, 72, 72, 14, value)


def text_doc(title: str, blocks: list[str]) -> fitz.Document:
    doc = fitz.open()
    for block in blocks:
        page = _page(doc)
        _title(page, title)
        y = 120
        for line in block.splitlines():
            _text(page, 72, y, 11, line)
            y += 22
    return doc


def bars_doc(title: str, caption: str, labels: list[str], values: list[int]) -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, title)
    _text(page, 200, 160, 12, caption)
    vmax = max(values)
    y0, height, width = 620, 360, 380
    x = 90
    step = width / len(values)
    for label, value in zip(labels, values, strict=False):
        h = value / vmax * height
        page.draw_rect(fitz.Rect(x, y0 - h, x + 46, y0), fill=(0.30, 0.56, 0.78))
        _text(page, x + 4, y0 - h - 10, 11, str(value))
        _text(page, x + 4, y0 + 16, 11, label)
        x += step
    page.draw_line((80, y0), (480, y0))
    return doc


def line_doc(title: str, caption: str, points: list[tuple[str, int]]) -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, title)
    _text(page, 200, 160, 12, caption)
    vmax = max(v for _, v in points)
    x0, y0, width, height = 100, 620, 380, 330
    step = width / (len(points) - 1)
    prev = None
    for i, (label, value) in enumerate(points):
        x = x0 + i * step
        y = y0 - value / vmax * height
        if prev is not None:
            page.draw_line(prev, (x, y), color=(0.78, 0.27, 0.35), width=1.6)
        page.draw_circle((x, y), 3, color=(0.78, 0.27, 0.35), fill=(0.78, 0.27, 0.35))
        _text(page, x - 10, y0 + 16, 10, label)
        _text(page, x - 16, y - 14, 10, str(value))
        prev = (x, y)
    page.draw_line((80, y0), (500, y0))
    return doc


DOCS: list[tuple[str, fitz.Document]] = [
    ("自动控制原理课程讲义", text_doc("自动控制原理课程讲义", [
        "第一讲 反馈控制基本概念\n"
        "1. 开环控制：输出不影响输入，结构简单但抗扰差。\n"
        "2. 闭环控制（反馈控制）：检测输出并与给定比较，按偏差调节。\n"
        "3. 典型环节：比例 P、积分 I、微分 D、惯性环节、振荡环节。\n"
        "4. 负反馈是控制系统最常见的连接方式。",
        "第二讲 一阶与二阶系统\n"
        "1. 一阶系统 G(s)=1/(Ts+1)：时间常数 T 越小响应越快。\n"
        "2. 一阶系统单位阶跃响应的调节时间（2% 误差带）约为 4T。\n"
        "3. 二阶系统 G(s)=wn^2/(s^2+2*zeta*wn*s+wn^2)。\n"
        "4. 超调量只与阻尼比 zeta 有关：\n"
        "   zeta=0.5 时超调量约 16.3%；zeta=0.707 时约 4.3%；zeta>=1 时无超调。\n"
        "5. zeta=0.707 称为工程最佳阻尼比。",
        "第三讲 PID 控制器与工程整定\n"
        "1. PID：u(t)=Kp*e + Ki*∫e dt + Kd*de/dt。\n"
        "2. Ziegler-Nichols 临界比例度法整定参考表：\n"
        "   纯 P 控制：Kp = 0.5*Ku（Ku 为临界增益）。\n"
        "   PI 控制：Kp = 0.45*Ku，Ti = 0.83*Tu（Tu 为临界振荡周期）。\n"
        "   PID 控制：Kp = 0.6*Ku，Ti = 0.5*Tu，Td = 0.125*Tu。\n"
        "3. 增大 Kp 加快响应但增大超调；增大 Ti 消除稳态误差但变慢；\n"
        "   增大 Td 抑制超调但对噪声敏感。",
    ])),
    ("电气控制与PLC应用手册", text_doc("电气控制与PLC应用手册", [
        "第一章 常用低压电器参数\n"
        "1. CJX2 系列交流接触器：线圈电压 AC220V，AC3 额定电流 9A/12A/18A/25A。\n"
        "2. 热继电器整定电流：按电动机额定电流的 0.95～1.05 倍整定。\n"
        "3. 断路器长延时脱扣：按 1.05 倍额定电流校验。\n"
        "4. 控制回路熔断器：按回路额定电流的 1.5～2 倍选择。",
        "第二章 PLC 状态指示灯含义（模块面板）\n"
        "1. RUN 灯：绿色常亮 = 运行正常；绿色闪烁 = 启动自检中。\n"
        "2. ERR 灯：红色闪烁 = 用户程序错误；红色常亮 = 硬件故障。\n"
        "3. COMM 灯：橙色闪烁 = 通讯正常；橙色常亮 = 通讯保持无数据。\n"
        "4. SF 灯：红色常亮 = 系统故障（SYSTEM FAULT），此时必须停机检查。\n"
        "5. BF 灯：红色闪烁 = 总线故障（BUS FAULT），从站掉线常见。\n"
        "注：以上为该系列模块的通用指示；具体含义以随机手册为准。",
        "第三章 PLC 故障代码表\n"
        "1. E01：通讯超时——检查网线与从站地址。\n"
        "2. E02：I/O 模块丢失——检查模块插槽与背板连接。\n"
        "3. E03：看门狗复位——扫描周期超限，检查程序死循环。\n"
        "4. E10：模拟量通道溢出——量程卡与传感器不匹配。\n"
        "5. 处理顺序：先看 SF/BF 灯，再查故障代码，最后核对程序。",
    ])),
    ("电力电子技术基础讲义", None),  # 占位：文本页在下方 text_blocks 组装
    ("检测与机器人传感器讲义", None),
    ("液压与气动技术手册", text_doc("液压与气动技术手册", [
        "第一章 液压系统压力等级\n"
        "1. 低压系统：<= 2.5 MPa。\n"
        "2. 中压系统：2.5 ～ 8 MPa。\n"
        "3. 中高压系统：8 ～ 16 MPa。\n"
        "4. 高压系统：16 ～ 32 MPa。\n"
        "5. 液压泵额定压力应比系统最高工作压力高 20% 以上裕量。",
        "第二章 气缸选型与推力\n"
        "1. 气缸理论推力 F = 压力 * 活塞面积。\n"
        "2. 缸径 32mm 在 0.63 MPa 下理论推力约 0.5 kN。\n"
        "3. 缸径 50mm 在 0.63 MPa 下理论推力约 1.2 kN。\n"
        "4. 缸径 63mm 在 0.63 MPa 下理论推力约 2.0 kN。\n"
        "5. 实际选型按理论推力的 80% 折算负载率。",
        "第三章 气动系统维护要点\n"
        "1. 气源三联件：过滤器 + 减压阀 + 油雾器，每日巡检排水。\n"
        "2. 电磁换向阀线圈电压 DC24V，绝缘电阻应 >= 1 MΩ。\n"
        "3. 管路泄漏率：保压 5 分钟压降不得超过 0.05 MPa。\n"
        "4. 气缸缓冲：负载速度超过 0.5 m/s 时必须带缓冲。",
    ])),
    ("液压油更换周期标准_2024试行", text_doc("液压油更换周期标准（2024 试行）", [
        "发布说明：本标准为 2024 年试行版本。\n"
        "1. 液压油 L-HM46：更换周期 2000 小时（试行值）。\n"
        "2. 液压油 L-HL46：更换周期 1500 小时（试行值）。\n"
        "3. 回油滤芯：每 1000 小时检查。\n"
        "4. 本试行值基于 2023 年设备台账统计制定。",
    ])),
    ("液压油更换周期标准_2026修订", text_doc("液压油更换周期标准（2026 修订）", [
        "发布说明：本标准为 2026 年修订版本，自发布之日起取代 2024 试行版。\n"
        "1. 液压油 L-HM46：更换周期 3000 小时（依据 2025 年油液检测数据修订）。\n"
        "2. 液压油 L-HL46：更换周期 2000 小时。\n"
        "3. 回油滤芯：每 1500 小时更换。\n"
        "4. 油液污染度 NAS 8 级及以上时提前换油。",
    ])),
    ("人工智能基础课程讲义", None),
    ("材料科学基础讲义_相图与热处理", None),
    ("材料物理性能讲义", text_doc("材料物理性能讲义", [
        "第一章 电学性能\n"
        "1. 20°C 电阻率（Ω·m）：铜 1.7e-8；铝 2.8e-8；铁 9.7e-8。\n"
        "2. 电阻温度系数（1/°C）：铜 0.0039；铝 0.0040。\n"
        "3. 掺杂与冷加工都会增大金属电阻率；退火恢复。\n"
        "4. 超导体：温度低于临界温度时电阻为零。",
        "第二章 热学与磁学性能\n"
        "1. 室温导热系数 W/(m·K)：铜 401；铝 237；碳钢约 50。\n"
        "2. 软磁材料（硅钢）磁滞回线窄，适合交变磁场；硬磁材料回线宽，适合永磁体。\n"
        "3. 铁的居里温度约 770°C，超过后失去铁磁性。\n"
        "4. 磁导率越大，同样磁场强度下磁感应强度越高。",
    ])),
    ("电路原理复习提纲_邱关源教材参考", text_doc("电路原理复习提纲（邱关源教材参考）", [
        "第一讲 基本定律\n"
        "1. KCL：任一节点电流代数和为零。\n"
        "2. KVL：任一回路电压代数和为零。\n"
        "3. 关联参考方向下 p = ui 吸收为正；非关联方向取负。\n"
        "4. 串联分压、并联分流是等效变换的基础。",
        "第二讲 等效与定理\n"
        "1. 戴维南定理：线性含源二端网络可等效为 Uoc 串联 Rth。\n"
        "2. 诺顿定理：等效为 Isc 并联 Rth；Rth = Uoc / Isc。\n"
        "3. 最大功率传输：当 R_L = Rth 时负载获得最大功率，Pmax = Uoc^2/(4*Rth)。\n"
        "4. 叠加定理只适用于线性电路的电压电流，不适用于功率。",
        "第三讲 一阶电路三要素法与例题\n"
        "1. 三要素公式：f(t) = f(∞) + [f(0+) - f(∞)] * e^(-t/τ)。\n"
        "2. RC 电路时间常数 τ = Req * C；RL 电路 τ = L / Req。\n"
        "3. 例题：U0 = 10 V，R = 2 kΩ，C = 100 μF，放电电路 τ = 0.2 s。\n"
        "   t = 0.2 s 时 uC = 10 * e^(-1) ≈ 3.68 V。\n"
        "4. 正弦稳态功率：P = U * I * cosφ；cosφ 为功率因数。",
    ])),
]


def _compose_mixed_docs() -> None:
    """把图表页与文本页组合成混合文档（占位为 None 的条目在此组装）。"""
    composed: dict[str, fitz.Document] = {}
    for title, doc in DOCS:
        if doc is not None:
            composed[title] = doc

    pe = fitz.open()  # 电力电子
    p1 = _page(pe)
    _title(p1, "电力电子技术基础讲义")
    y = 120
    for line in (
        "第一章 电力半导体器件参数\n"
        "1. IGBT 模块：额定电压 1200 V，额定电流 75 A，开关频率 <= 20 kHz。\n"
        "2. 功率 MOSFET：600 V / 40 A，开关频率可达 100 kHz 以上。\n"
        "3. 快恢复二极管：1200 V / 30 A，反向恢复时间约 100 ns。\n"
        "4. 器件选型电压按母线电压 2 倍裕量选取。"
    ).splitlines():
        _text(p1, 72, y, 11, line)
        y += 22
    p2 = _page(pe)
    _title(p2, "电力电子技术基础讲义")
    y = 120
    for line in (
        "第二章 变换器与开关频率\n"
        "1. Buck 降压：Uo = D * Ui；Boost 升压：Uo = Ui / (1 - D)。\n"
        "2. 开关频率升高：滤波器体积减小，但开关损耗增大。\n"
        "3. 中小功率电机驱动常用 8 ～ 16 kHz；伺服高频场合可用 20 kHz 以上。\n"
        "4. 不同开关频率下整机总损耗见附图（单位 W）。"
    ).splitlines():
        _text(p2, 72, y, 11, line)
        y += 22
    page3 = bars_doc(
        "电力电子技术基础讲义",
        "图2-1 变频器总损耗随开关频率变化（W）",
        ["10kHz", "20kHz", "50kHz", "100kHz"],
        [18, 12, 21, 35],
    )
    pe.insert_page(page3[0], 2) if False else None  # 直接拷贝图表页内容
    composed["电力电子技术基础讲义"] = pe

    se = fitz.open()  # 传感器
    p1 = _page(se)
    _title(p1, "检测与机器人传感器讲义")
    y = 120
    for line in (
        "第一章 常用传感器量程与精度\n"
        "1. K 型热电偶：量程 0 ～ 1200 °C，精度 ±1.5 °C。\n"
        "2. PT100 热电阻：量程 -50 ～ 400 °C，精度 ±0.3 °C。\n"
        "3. 激光位移传感器：量程 ±5 mm，精度 ±0.02 mm。\n"
        "4. 压力变送器：量程 0 ～ 10 MPa，精度 0.5 级。"
    ).splitlines():
        _text(p1, 72, y, 11, line)
        y += 22
    p2 = _page(se)
    _title(p2, "检测与机器人传感器讲义")
    y = 120
    for line in (
        "第二章 机器人关节编码器与标定\n"
        "1. 17 位绝对值编码器：每转 131072 个脉冲位置。\n"
        "2. 视觉传感器标定周期：6 个月。\n"
        "3. 六维力传感器标定周期：12 个月。\n"
        "4. 机器人重复定位精度随负载率变化见附图（mm）。"
    ).splitlines():
        _text(p2, 72, y, 11, line)
        y += 22
    composed["检测与机器人传感器讲义"] = se

    ai = fitz.open()  # 人工智能
    p1 = _page(ai)
    _title(p1, "人工智能基础课程讲义")
    y = 120
    for line in (
        "第一章 机器学习基本范式\n"
        "1. 监督学习：训练数据带标签，如分类与回归。\n"
        "2. 无监督学习：无标签，如聚类降维。\n"
        "3. 强化学习：智能体通过与环境交互的奖励学习策略。\n"
        "4. RAG（检索增强生成）：回答前先从资料库检索相关内容，再组织答案，\n"
        "   可以减少凭空编造；OCR 是把图片里的文字识别成文本的技术。"
    ).splitlines():
        _text(p1, 72, y, 11, line)
        y += 22
    p2 = _page(ai)
    _title(p2, "人工智能基础课程讲义")
    y = 120
    for line in (
        "第二章 常见算法对比与训练过程\n"
        "1. 线性回归：可解释性强，适合线性关系。\n"
        "2. 决策树：规则直观，易过拟合，常与随机森林结合。\n"
        "3. KNN：无需训练，但推理慢，对维度敏感。\n"
        "4. 过拟合：训练误差很小但测试误差大；缓解手段包括正则化与早停。\n"
        "5. 某分类任务的训练损失随训练轮次变化见附图。"
    ).splitlines():
        _text(p2, 72, y, 11, line)
        y += 22
    composed["人工智能基础课程讲义"] = ai

    ms = fitz.open()  # 材料科学基础
    p1 = _page(ms)
    _title(p1, "材料科学基础讲义（相图与热处理）")
    y = 120
    for line in (
        "第一章 铁碳相图要点\n"
        "1. 共析点：含碳 0.77%，温度 727 °C。\n"
        "2. 亚共析钢：含碳 0.0218% ～ 0.77%，室温组织为铁素体 + 珠光体。\n"
        "3. 过共析钢：含碳 0.77% ～ 2.11%，室温组织为珠光体 + 渗碳体。\n"
        "4. 钢与铸铁的分界：含碳 2.11%。"
    ).splitlines():
        _text(p1, 72, y, 11, line)
        y += 22
    p2 = _page(ms)
    _title(p2, "材料科学基础讲义（相图与热处理）")
    y = 120
    for line in (
        "第二章 常用钢的热处理参数\n"
        "1. 45 钢：淬火温度 840 °C 水冷，回火 560 °C。\n"
        "2. T8 钢：淬火温度 780 °C 水冷。\n"
        "3. 退火目的：降低硬度、改善切削性。\n"
        "4. 45 钢淬火后经不同温度回火的硬度见附图（HRC）。"
    ).splitlines():
        _text(p2, 72, y, 11, line)
        y += 22
    composed["材料科学基础讲义_相图与热处理"] = ms

    # 追加图表页
    chart_pages = [
        ("电力电子技术基础讲义", bars_doc(
            "电力电子技术基础讲义",
            "图2-1 变频器总损耗随开关频率变化（W）",
            ["10kHz", "20kHz", "50kHz", "100kHz"],
            [18, 12, 21, 35],
        )),
        ("检测与机器人传感器讲义", line_doc(
            "检测与机器人传感器讲义",
            "图2-1 重复定位精度随负载率变化（0.01mm）",
            [("20%", 2), ("50%", 3), ("80%", 5), ("100%", 8)],
        )),
        ("人工智能基础课程讲义", line_doc(
            "人工智能基础课程讲义",
            "图2-1 训练损失随训练轮次变化",
            [("1", 90), ("2", 55), ("3", 38), ("4", 30), ("5", 27), ("6", 26)],
        )),
        ("材料科学基础讲义_相图与热处理", bars_doc(
            "材料科学基础讲义（相图与热处理）",
            "图2-1 45钢回火温度与硬度（HRC）",
            ["200°C", "400°C", "600°C"],
            [52, 38, 25],
        )),
    ]
    for title, chart_doc in chart_pages:
        page = composed[title].new_page(width=PAGE_W, height=PAGE_H)
        page.show_pdf_page(page.rect, chart_doc, 0)

    # 写回 DOCS 顺序列表
    for i, (title, _doc) in enumerate(DOCS):
        if title in composed:
            DOCS[i] = (title, composed[title])


def stage_generate() -> int:
    _compose_mixed_docs()
    INBOX.mkdir(parents=True, exist_ok=True)
    for title, doc in DOCS:
        path = INBOX / f"{title}.pdf"
        path.write_bytes(doc.tobytes())
        print(f"generated {path.name}")
    return 0


def _document_service():
    from src.database import Database
    from src.document_service import DocumentService

    settings = staging_settings()
    return DocumentService(
        Database(settings.database_path),
        settings.raw_dir,
        settings.pages_dir,
        settings.markdown_dir,
    )


def _database():
    from src.database import Database

    return Database(staging_settings().database_path)


def stage_import() -> int:
    _compose_mixed_docs()
    service = _document_service()
    existing = {d.title for d in service.database.list_documents()}
    for title, _doc in DOCS:
        if title in existing:
            print(f"skip existing doc: {title}")
            continue
        path = INBOX / f"{title}.pdf"
        if not path.exists():
            print(f"missing {path.name}；先运行 --generate")
            return 2
        result = service.import_document(path.read_bytes(), path.name)
        print(f"imported id={result.document.id} {result.document.title}")
    return 0


def stage_read() -> int:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent_document_reader import AgentReadingStore
    from src.runtime import application_ai_provider

    _compose_mixed_docs()
    settings = staging_settings()
    database = _database()
    store = AgentReadingStore(settings.agent_readings_dir)
    agent = LocalDocumentAgent(
        database=database,
        provider=application_ai_provider(),
        readings=store,
        model=settings.ai_llm_model_hard,
        vision_provider=application_ai_provider(),
        vision_model=settings.ai_vision_model,
        pages_dir=settings.pages_dir,
    )
    wanted = {title for title, _doc in DOCS}
    for document in database.list_documents():
        if document.title not in wanted:
            continue
        state = store.document_state(document.id)
        if state and state.completed:
            continue
        print(f"reading {document.id} {document.title} ...", flush=True)
        agent.read_document(document.id)
    print("new documents read")
    return 0


def stage_rebuild_visual() -> int:
    from src.agent_document_reader import AgentReadingStore
    from src.ai.visual_artifact_index import VisualArtifactIndex

    settings = staging_settings()
    index = VisualArtifactIndex(_database(), AgentReadingStore(settings.agent_readings_dir))
    stats = index.rebuild()
    print(f"visual index rebuilt: {stats}")
    return 0


def stage_verify() -> int:
    import sqlite3

    conn = sqlite3.connect(f"file:{staging_settings().database_path}?mode=ro", uri=True)
    cur = conn.cursor()
    print("documents =", cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    print("pages =", cur.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
    print("visual_index =", cur.execute("SELECT COUNT(*) FROM page_visual_index").fetchone()[0])
    print("integrity =", cur.execute("PRAGMA integrity_check").fetchone()[0])
    print("fk =", cur.execute("PRAGMA foreign_key_check").fetchall())
    wanted = {t for t, _ in DOCS}
    for title, in cur.execute(
        "SELECT title FROM documents WHERE title IN ({})".format(
            ",".join("?" * len(wanted))
        ),
        sorted(wanted),
    ):
        print("present:", title)
    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HBENCH_V2 domain corpus builder (staging only)")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--read", action="store_true")
    parser.add_argument("--rebuild-visual", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not any((args.generate, args.do_import, args.read, args.rebuild_visual, args.verify)):
        parser.print_help()
        return 2
    if args.generate:
        rc = stage_generate()
        if rc:
            return rc
    if args.do_import:
        rc = stage_import()
        if rc:
            return rc
    if args.read:
        rc = stage_read()
        if rc:
            return rc
    if args.rebuild_visual:
        rc = stage_rebuild_visual()
        if rc:
            return rc
    if args.verify:
        rc = stage_verify()
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
