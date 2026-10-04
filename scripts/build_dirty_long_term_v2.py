# ruff: noqa: E501, E402
"""DIRTY_LONG_TERM V2 builder (staging only, v0.8.3 Personal Memory Reliability).

Expands the staging corpus from dirty-long-term-v1 (12 docs / 24 memories) to
``dirty-long-term-v2`` with 40 additional documents and 76 additional memory
entries — deliberate variety, not mechanical copies:

- two 3-generation version families (数值随版本演进);
- two same-name different-content pairs; two error-code collision docs
  (变频器 E17 vs PLC E17); two contradictory pairs (润滑周期/绝缘标准);
- six visual-only pages (charts with image-internal values);
- three long manuals (6-10 text pages); two low-quality scans;
- three deprecated/superseded docs; one deleted-source document;
- 15 single-topic reference sheets.

Memory set: 4 longitudinal chains (refine/contradict/supersede), stale
experience, wrong raw_qa, only-worked-once, similar-but-different pollution
pairs, condition/version-limited experiences, plus assorted facts. Session
history is embedded ("第 N 次会话") for longitudinal realism.

Stages:
  --generate   write the 40 new PDFs into staging-data/inbox-dirty-v2
  --import     import them through DocumentService
  --read       Agent page reading for the new docs (paid, slow)
  --memories   seed the 76 memory entries (chains via source_entry_id)
  --delete-source  delete 热处理炉温记录 (deleted-source state for E1)
  --manifest   write corpus_manifest_dirty_v3.json (id dirty-long-term-v2)
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

import math

import fitz  # noqa: E402

from src.config import staging_settings  # noqa: E402

INBOX = PROJECT_ROOT / "staging-data" / "inbox-dirty-v2"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "reliability_eval"
MANIFEST_V3 = FIXTURE_DIR / "corpus_manifest_dirty_v3.json"

PAGE_W, PAGE_H = 595, 842

DELETED_SOURCE_TITLE = "热处理炉温记录_2026Q2"


def _page(doc: fitz.Document) -> fitz.Page:
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _text(page: fitz.Page, x: float, y: float, size: int, value: str) -> None:
    page.insert_text((x, y), value, fontsize=size, fontname="china-s")


def _title(page: fitz.Page, value: str) -> None:
    _text(page, 72, 72, 14, value)


def text_doc(title: str, blocks: list[str]) -> fitz.Document:
    """One page per block; each block is a short multi-line section."""
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
        _text(page, x - 30, y - 12, 10, f"{label}: {value}")
        prev = (x, y)
    page.draw_line((90, 640), (90, 260))
    page.draw_line((90, 640), (500, 640))
    return doc


def pie_doc(title: str, caption: str, parts: list[tuple[str, int]]) -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, title)
    _text(page, 210, 160, 12, caption)
    total = sum(v for _, v in parts)
    colors = ((0.78, 0.27, 0.35), (0.30, 0.56, 0.78), (0.44, 0.66, 0.53), (0.80, 0.68, 0.35))
    cx, cy, r = 300, 420, 130
    start = -90.0
    for i, (label, value) in enumerate(parts):
        sweep = value / total * 360.0
        rad = start * 3.14159265 / 180.0
        edge = fitz.Point(cx + r * math.cos(rad), cy + r * math.sin(rad))
        page.draw_sector(fitz.Point(cx, cy), edge, sweep, fill=colors[i % 4])
        _text(page, cx + 150, cy - 60 + i * 26, 11, f"{label} {value}%")
        start += sweep
    return doc


def lowres_doc(title: str, lines: list[str]) -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, title)
    y = 130
    for line in lines:
        _text(page, 72, y, 8, line)
        y += 16
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
    new = fitz.open()
    page2 = new.new_page(width=PAGE_W, height=PAGE_H)
    page2.insert_image(fitz.Rect(40, 40, 555, 802), pixmap=pix)
    return new


DOCS: list[tuple[str, fitz.Document]] = [
    ("齿轮箱检修手册_2024版", text_doc("齿轮箱检修手册（2024 版）", [
        "1. 轴承温度报警阈值：75°C，超过即停机检查。\n2. 齿面检查每 2000 小时一次。\n3. 润滑脂每 2000 小时补充。",
    ])),
    ("齿轮箱检修手册_2025修订版", text_doc("齿轮箱检修手册（2025 修订版）", [
        "1. 轴承温度报警阈值修订为 78°C。\n2. 齿面检查每 2000 小时一次。\n3. 振动限值 2.8 mm/s。",
    ])),
    ("齿轮箱检修手册_2026试行版", text_doc("齿轮箱检修手册（2026 试行版）", [
        "1. 轴承温度报警阈值试行 80°C（试行版，未最终定稿）。\n2. 振动限值 2.8 mm/s 维持。",
    ])),
    ("液压站巡检要点_v1", text_doc("液压站巡检要点（v1）", [
        "1. 额定油压 12 MPa，偏差 ±0.5 MPa 报警。\n2. 油温不得超过 55°C。",
    ])),
    ("液压站巡检要点_v2", text_doc("液压站巡检要点（v2）", [
        "1. 额定油压修订为 12.5 MPa。\n2. 油温限值维持 55°C。",
    ])),
    ("液压站巡检要点_v3草案", text_doc("液压站巡检要点（v3 草案）", [
        "1. 额定油压拟调整为 13 MPa（草案，未发布）。",
    ])),
    ("设备点检表_冲压车间", text_doc("设备点检表（冲压车间）", [
        "1. 冲压机 A 线：油位、滑块行程 1.2 m、气压 0.6 MPa。\n2. 点检频次：每日班前。\n3. 异常即报修，禁止带病运行。",
    ])),
    ("设备点检表_装配车间", text_doc("设备点检表（装配车间）", [
        "1. 装配线 3 工位：扭矩枪 8 Nm、气动平衡器、输送带张紧。\n2. 点检频次：每周一次。",
    ])),
    ("年度维护计划_2025", text_doc("年度维护计划（2025）", [
        "1. 全厂大修 2 次/年：8 月与 12 月。\n2. 中修随大修安排。",
    ])),
    ("年度维护计划_2026", text_doc("年度维护计划（2026）", [
        "1. 全厂大修 3 次/年：4 月、8 月、12 月。",
    ])),
    ("变频器维护指南", text_doc("变频器维护指南", [
        "1. 错误码 E17 = 散热过温：检查散热风道与风扇。\n2. 风扇每月清洁一次。\n3. OC 报警先查制动回路。",
    ])),
    ("PLC 维护手册", text_doc("PLC 维护手册", [
        "1. 错误码 E17 = 通讯超时：检查终端电阻与总线。\n2. 模块更换必须断电。",
    ])),
    ("电机负载电流曲线", line_doc("电机负载电流曲线", "电机负载电流", [
        ("50%", 8), ("75%", 11), ("100%", 15),
    ])),
    ("车间噪音分布", bars_doc("车间噪音分布", "各工位噪音 dB", ["工位A", "工位B", "工位C", "工位D"], [78, 82, 75, 85])),
    ("液压油温趋势", line_doc("液压油温趋势", "油温随运行小时", [
        ("0h", 32), ("20h", 41), ("40h", 47), ("60h", 51), ("80h", 54), ("100h", 56),
    ])),
    ("摄像头帧率对比", bars_doc("摄像头帧率对比", "各摄像头帧率 fps", ["cam1", "cam2", "cam3", "cam4"], [30, 25, 45, 60])),
    ("焊接电流对照", bars_doc("焊接电流对照", "板厚与电流 A", ["2mm", "3mm", "4mm"], [90, 120, 150])),
    ("注塑周期饼图", pie_doc("注塑周期饼图", "注塑成型周期构成", [("锁模", 20), ("射胶", 15), ("冷却", 50), ("顶出", 15)])),
    ("空压机二号机完整手册", text_doc("空压机二号机完整手册", [
        "1. 油品更换：首次 500 小时，之后每 2000 小时。",
        "2. 高粉尘环境维护周期缩短至 300 小时。",
        "3. 排气温度报警 105°C，联锁停机。",
        "4. 储气罐排污：每 500 小时一次。",
        "5. 安全阀每 2000 小时校验。",
        "6. 地脚螺栓参照表 1：M6 10/12 Nm，M8 25/30 Nm。",
        "7. 长期停机每两周盘车。",
        "8. 本手册仅适用二号机，一号机另有规程。",
        "9. 冷却水进口温度不超过 35°C。",
        "10. 皮带走廊每季度测一次张力。",
    ])),
    ("伺服驱动器调试手册", text_doc("伺服驱动器调试手册", [
        "1. 方向参数：DIR_INV=1 时反向；改向后必须复核上位轴标定。",
        "2. 速度环增益出厂默认 25 Hz。",
        "3. U/V 相序对调仅用于应急，禁止作为根因处理。",
        "4. 过载能力 120%/60 秒。",
        "5. 编码器线缆远离动力线。",
        "6. 增益调整先降后升，每次 10%。",
        "7. 位置环增益为速度环的 1/2 起步。",
        "8. 报警清除后必须复测三个循环。",
    ])),
    ("输送线电气维护手册", text_doc("输送线电气维护手册", [
        "1. 急停回路测试：每月一次。",
        "2. 光电传感器对准公差 5 mm。",
        "3. 链速 0.8 m/s。",
        "4. 端子紧固每半年。",
        "5. 变频器参数备份每季度。",
        "6. 线体接地电阻 ≤ 4 Ω。",
        "7. 急停后复位须两人确认。",
    ])),
    ("空压机维护规程_2019废止版", text_doc("空压机维护规程（2019，已废止）", [
        "1. 维护周期 1000 小时。\n2. 本规程已废止，现行要求以 2026 版为准。",
    ])),
    ("旧版点检作业指导书", text_doc("旧版点检作业指导书（已被取代）", [
        "1. 点检频次：每周一次。\n2. 本指导书已被 2026 版取代（现为每日班前）。",
    ])),
    ("涂装线参数卡_作废", text_doc("涂装线参数卡（作废）", [
        "1. 烘干温度 140°C。\n2. 本卡作废，现行参数 160°C 另行发布。",
    ])),
    ("手写点检记录_低清", text_doc("手写点检记录（低清手写体）", [
        "点检日期：？？年？月？日",
        "油位：偏？？",
        "温度：？？°C",
        "备注：字迹不清，无法辨认（FAIL-017 工作区：小字号文本风格）",
    ])),
    ("传真采购单", text_doc("传真采购单（低清传真件）", [
        "品名：？？轴承 数量：？？",
        "单价：？？元 金额：？？元",
        "（传真件，字迹模糊难辨，FAIL-017 工作区：改用小字号文本传真风格）",
    ])),
    ("轴承润滑周期卡", text_doc("轴承润滑周期卡", [
        "1. 电机轴承加脂周期：每 2000 小时。",
    ])),
    ("润滑作业指导书", text_doc("润滑作业指导书", [
        "1. 电机轴承加脂周期：每 1500 小时。\n2. 与旧周期卡冲突时以本书面指导为准。",
    ])),
    ("电机绝缘阻值标准A", text_doc("电机绝缘阻值标准（A 分册）", [
        "1. 绝缘阻值 ≥ 1 MΩ 判合格（低压电机）。",
    ])),
    ("电机绝缘阻值标准B", text_doc("电机绝缘阻值标准（B 分册）", [
        "1. 绝缘阻值 ≥ 10 MΩ 判合格（新绕组交付标准）。",
    ])),
    ("热处理炉温记录_2026Q2", text_doc("热处理炉温记录（2026Q2）", [
        "1. Q2 校准偏差 +3°C，已修正。\n2. 校准周期 6 个月。\n3. 记录人：热处理班组。",
    ])),
    ("皮带张力速查", text_doc("皮带张力速查", [
        "1. 新皮带中部挠度 4.5 mm（跨距 1000 mm）。",
    ])),
    ("法兰螺栓力矩卡", text_doc("法兰螺栓力矩卡", [
        "1. M12 65 Nm；M16 100 Nm。\n2. 对角分三次拧紧。",
    ])),
    ("接地电阻要求", text_doc("接地电阻要求", [
        "1. 设备接地电阻 ≤ 4 Ω，每年雨季前复测。",
    ])),
    ("振动标准速查", text_doc("振动标准速查", [
        "1. ISO 10816 A 级 ≤ 0.71 mm/s。\n2. C 级以上安排检修。",
    ])),
    ("轴承型号对照", text_doc("轴承型号对照", [
        "1. 6204 内径 20 mm；6205 内径 25 mm；6206 内径 30 mm。",
    ])),
    ("电机功率速查", text_doc("电机功率速查", [
        "1. 2.2 kW 四极额定电流约 4.7 A（380 V）。",
    ])),
    ("变频器参数备份说明", text_doc("变频器参数备份说明", [
        "1. 参数备份每季度一次，改名含日期。",
    ])),
    ("气缸行程规格", text_doc("气缸行程规格", [
        "1. CQ2 系列常用行程 50/100/200 mm。",
    ])),
    ("备件清单_2026", text_doc("备件清单（2026）", [
        "1. 滤芯 P-191116 库存 2 件，最低 1 件。",
    ])),
    ("培训记录_2026Q1", text_doc("培训记录（2026Q1）", [
        "1. 新员工安全培训 8 人，考核全部通过。\n2. EOD 培训覆盖 3 个班组。",
    ])),
]


def stage_generate() -> int:
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


def stage_import() -> int:
    service = _document_service()
    existing = {d.title for d in service.database.list_documents()}
    for title, _ in DOCS:
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
    wanted = {title for title, _ in DOCS}
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


def _database():
    from src.database import Database

    return Database(staging_settings().database_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="DIRTY_LONG_TERM V2 builder (staging only)")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--read", action="store_true")
    parser.add_argument("--memories", action="store_true")
    parser.add_argument("--delete-source", action="store_true")
    parser.add_argument("--manifest", action="store_true")
    args = parser.parse_args()
    if not any(
        (args.generate, args.do_import, args.read, args.memories, args.delete_source, args.manifest)
    ):
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
    if args.memories:
        from build_dirty_long_term_v2_memories import (
            stage_delete_source,
            stage_manifest,
            stage_memories,
        )

        rc = stage_memories()
        if rc:
            return rc
    if args.delete_source:
        from build_dirty_long_term_v2_memories import stage_delete_source

        rc = stage_delete_source()
        if rc:
            return rc
    if args.manifest:
        from build_dirty_long_term_v2_memories import stage_manifest

        rc = stage_manifest()
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
