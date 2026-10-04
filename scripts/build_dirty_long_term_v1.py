"""Build the DIRTY_LONG_TERM V1 corpus on the 8511 staging instance.

Deterministic, repeatable seed for long-term reliability history:

- 4 PDFs (3-generation version family, same-name different-content, a
  correcting troubleshooting record, a long manual with a condition conflict)
- 5 authored memory entries + 2 raw_qa entries in deliberately dirty states

Stages are explicit and idempotent-ish:
  --generate   write PDFs into staging-data/inbox-dirty-v1/
  --import     import generated PDFs through DocumentService (dedup by sha)
  --read       run the explicit agent page-by-page reading on unread imports
  --memories   create the dirty memory entries (skip titles that exist)

Everything writes only to staging. Never point this at the formal instance.
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

INBOX = PROJECT_ROOT / "staging-data" / "inbox-dirty-v1"

PAGE_W, PAGE_H = 595, 842


def _page(doc: fitz.Document) -> fitz.Page:
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _text(page: fitz.Page, x: float, y: float, size: float, s: str) -> None:
    page.insert_text((x, y), s, fontsize=size, fontname="china-s")


def _title(page: fitz.Page, s: str) -> None:
    _text(page, 72, 80, 14, s)


def _bars(
    page: fitz.Page,
    labels: list[str],
    values: list[float],
    *,
    x0: float = 150,
    y0: float = 560,
    height: float = 260,
    bar_w: float = 55,
    gap: float = 55,
) -> None:
    vmax = max(values) * 1.15
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        h = value / vmax * height
        x = x0 + index * (bar_w + gap)
        page.draw_rect(fitz.Rect(x, y0 - h, x + bar_w, y0), fill=(0.30, 0.56, 0.78))
        _text(page, x + 6, y0 - h - 8, 11, str(value))
        _text(page, x + 4, y0 + 18, 11, label)


def _pie(
    page: fitz.Page,
    items: list[tuple[str, float]],
    *,
    cx: float = 300,
    cy: float = 420,
    r: float = 130,
) -> None:
    colors = ((0.78, 0.27, 0.35), (0.30, 0.56, 0.78), (0.44, 0.66, 0.53), (0.80, 0.68, 0.35))
    total = sum(v for _, v in items)
    start = -90.0
    for index, (label, value) in enumerate(items):
        sweep = value / total * 360.0
        rect = fitz.Rect(cx - r, cy - r, cx + r, cy + r)
        page.draw_pie(rect, start, start + sweep, fill=colors[index % 4])
        _text(page, cx + r + 10, cy - 40 + index * 22, 11, f"{label} {value:g}%")
        start += sweep


def build_pump_manual_v20() -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, "泵维护视觉手册（版本 2.0，2027 草案）")
    _text(page, 72, 110, 11, "表 1：地脚螺栓紧固力矩对照表")
    _text(page, 72, 130, 11, "本表取代 1.1 及更早版本的力矩数值。")
    rows = [("M6", "10 Nm", "12 Nm"), ("M8", "30 Nm", "35 Nm"), ("M10", "46 Nm", "52 Nm")]
    y = 180
    _text(page, 150, y, 11, "螺栓规格")
    _text(page, 280, y, 11, "标准力矩")
    _text(page, 400, y, 11, "最大力矩")
    for spec, std, mx in rows:
        y += 34
        _text(page, 150, y, 11, spec)
        _text(page, 280, y, 11, std)
        _text(page, 400, y, 11, mx)
    page.draw_line((140, 168), (500, 168))
    page.draw_line((140, 194), (500, 194))
    page.draw_line((140, y + 14), (500, y + 14))
    return doc


def build_pump_flow_same_name() -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, "图 1：各冷却水泵额定流量对比")
    _text(page, 250, 150, 12, "冷却水泵额定流量")
    _bars(page, ["P-301", "P-302", "P-303", "P-304"], [40, 33, 46, 31])
    page2 = _page(doc)
    _title(page2, "图 2：轴承温度随运行小时的趋势")
    _text(page2, 72, 130, 11, "0h: 50°C    1000h: 57°C    2000h: 63°C")
    _text(page2, 72, 150, 11, "3000h: 69°C    4000h: 75°C    5000h: 80°C")
    _text(page2, 72, 180, 11, "（数据来自二车间 2026 年复测，取代一车间旧记录。）")
    return doc


def build_oc_alarm_record() -> fitz.Document:
    doc = fitz.open()
    page = _page(doc)
    _title(page, "变频器 OC 报警排查记录（2026 复测）")
    body = (
        "现象：变频器运行中偶发 OC 报警。\n"
        "\n"
        "排查过程：\n"
        "1. 直流母线电容老化确实会触发 OC，但本台电容容值正常。\n"
        "2. 复测定位主因：再生制动回路 IGBT 过流，制动电阻阻值漂移。\n"
        "3. 结论：OC 报警≠电容老化，必须先测制动回路，再查母线电容。\n"
        "\n"
        "处理：更换制动电阻并重新整定制动单元后，报警消失。\n"
        "唯一验证标记：EKB-OC-VERIFY-2026"
    )
    for index, line in enumerate(body.splitlines()):
        _text(page, 72, 130 + index * 24, 11, line)
    return doc


def build_long_air_manual() -> fitz.Document:
    doc = fitz.open()
    sections = [
        ("空压机完整维护手册（2026 修订）", "本章说明日常巡检与定期保养的总体安排。"),
        ("1. 日常巡检", "每日检查油位、排气温度与冷凝水排放；异常时停机登记。"),
        ("2. 定期保养（一般环境）", "一般环境下，整机维护周期为 500 小时，与环境无关。"),
        (
            "3. 高粉尘环境补充规定",
            "在高粉尘车间（粉尘浓度 > 10 mg/m³）运行时，维护周期缩短至"
            " 300 小时，且每 100 小时清洁进气滤芯。",
        ),
        ("4. 油品更换", "首次运行 500 小时换油，之后每 2000 小时更换；高温季节缩短至 1500 小时。"),
        ("5. 联轴器对中", "检修后联轴器对中偏差不得超过 0.05 mm；超差必须重新对中。"),
        ("6. 地脚螺栓", "地脚螺栓紧固参照表 1：M6 10/12 Nm，M8 25/30 Nm，M10 46/52 Nm。"),
        ("7. 安全阀校验", "安全阀每 2000 小时校验一次，铅封损坏立即停用。"),
        ("8. 长期停机", "停机超过 30 天时，每两周盘车一次并记录。"),
        ("9. 附录：故障代码", "E01 排气高温；E02 油压低；E03 主电机过载。"),
    ]
    for index, (heading, body) in enumerate(sections):
        page = _page(doc)
        _title(page, heading)
        for offset, line in enumerate(body.split("；")):
            _text(page, 72, 130 + offset * 26, 11, line)
        _text(page, 72, PAGE_H - 60, 9, f"第 {index + 1} / {len(sections)} 页")
    return doc


GENERATORS = {
    "泵维护视觉手册_v2.0.pdf": build_pump_manual_v20,
    "冷却水泵性能图表.pdf": build_pump_flow_same_name,
    "变频器OC报警排查记录.pdf": build_oc_alarm_record,
    "空压机完整维护手册.pdf": build_long_air_manual,
}


def stage_generate() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    for filename, builder in GENERATORS.items():
        path = INBOX / filename
        path.write_bytes(builder().tobytes())
        print(f"generated {path}")
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
    for filename in GENERATORS:
        path = INBOX / filename
        if not path.exists():
            print(f"missing {path}；先运行 --generate")
            return 2
        result = service.import_document(path.read_bytes(), filename)
        print(f"imported {filename} -> document_id={result.document.id} pages={len(result.pages)}")
    return 0


def stage_read() -> int:
    from src.agent.local_document import LocalDocumentAgent
    from src.agent_document_reader import AgentReadingStore
    from src.database import Database
    from src.runtime import application_ai_provider

    settings = staging_settings()
    database = Database(settings.database_path)
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
    for document in database.list_documents():
        state = store.document_state(document.id)
        if state and state.completed:
            continue
        print(f"reading {document.id} {document.title} ...", flush=True)
        agent.read_document(document.id)
    print("all documents read")
    return 0


MEMORIES = (
    {
        "kind": "experience",
        "title": "冷却水泵流量排序速查（二车间 2026 复测）",
        "content": (
            "遇到的问题：需要快速判断哪台冷却水泵流量最大。"
            " 处理方式：按《冷却水泵性能图表》（二车间 2026 复测版）图 1 读数。"
            " 结果：P-303 最大（46），P-302 最小（33）。"
            " 适用范围：仅限二车间复测数据对应的那份同名资料。"
        ),
        "root_cause": "二车间复测数据与一车间旧记录并存",
        "lesson": "同名资料先分车间版本，再读数。",
        "root_cause_confirmed": True,
        "context_conditions": "仅适用《冷却水泵性能图表》二车间复测版",
    },
    {
        "kind": "experience",
        "title": "OC 报警优先查制动回路（复测中）",
        "content": (
            "遇到的问题：变频器 OC 报警。"
            " 处理方式：先测制动电阻与 IGBT，再查母线电容。"
            " 结果：本台定位为制动回路过流，处理后报警消失；"
            "但只复测了一台，尚未全面确认。"
        ),
        "root_cause": "再生制动回路 IGBT 过流（推测为主因，未完全确认）",
        "lesson": "OC 报警先查制动回路，别急着换电容。",
        "root_cause_confirmed": False,
        "context_conditions": "基于单台复测，样本量 1",
    },
    {
        "kind": "experience",
        "title": "高粉尘车间空压机维护周期缩短规定",
        "content": (
            "遇到的问题：高粉尘车间空压机保养排期。"
            " 处理方式：按《空压机完整维护手册（2026 修订）》第 3 章。"
            " 结果：粉尘浓度 > 10 mg/m³ 时周期缩短至 300 小时。"
            " 适用范围：仅限高粉尘车间；一般环境仍按 500 小时。"
        ),
        "lesson": "先看粉尘浓度，再定周期。",
        "root_cause_confirmed": False,
        "context_conditions": "仅高粉尘车间（>10 mg/m³）",
    },
    {
        "kind": "experience",
        "title": "泵手册版本基线（已被 2.0 草案跟进）",
        "content": (
            "遇到的问题：泵手册 M8 力矩按哪版执行。"
            " 处理方式：比对 1.0/1.1 的版本标注与修订年份。"
            " 结果：2026-09 阶段以 1.1（2026 修订）为现行版本，M8 28 Nm。"
            " 备注：后续以正式发布的更新版本为准。"
        ),
        "lesson": "同名手册先比版本年份。",
        "root_cause_confirmed": True,
        "context_conditions": "在 v2.0 草案正式发布前适用",
    },
    {
        "kind": "experience",
        "title": "空压机联轴器对中标准（长手册摘要）",
        "content": (
            "遇到的问题：空压机检修后联轴器对中验收。"
            " 处理方式：按《空压机完整维护手册（2026 修订）》第 5 章。"
            " 结果：偏差不得超过 0.05 mm。"
            " 适用范围：仅空压机组，不适用于传动轴试验台案例（0.35 mm 为另一次排查记录）。"
        ),
        "lesson": "0.05 mm 是空压机标准，别和传动轴案例混用。",
        "root_cause_confirmed": False,
        "context_conditions": "仅空压机组检修",
    },
)

RAW_QA_SEEDS = (
    {
        "question": "M8 地脚螺栓最大力矩是多少？",
        "answer": "按《泵维护视觉手册》第 2 页表 1：M8 最大力矩 30 Nm。",
    },
    {
        "question": "M8 螺栓最多能拧到多少牛·米？",
        "answer": "表 1 里 M8 最大 30 Nm（标准 25 Nm）。",
    },
)


def stage_memories() -> int:
    from src.database import Database
    from src.knowledge_memory_service import KnowledgeMemoryService

    settings = staging_settings()
    database = Database(settings.database_path)
    service = KnowledgeMemoryService(database)
    existing = {
        entry.title
        for entry in database.list_knowledge_memory_entries(limit=500)
    }
    for spec in MEMORIES:
        if spec["title"] in existing:
            print(f"skip existing memory: {spec['title']}")
            continue
        entry = service.create_entry(
            kind=spec["kind"],
            title=spec["title"],
            content=spec["content"],
            root_cause=spec.get("root_cause", ""),
            lesson=spec.get("lesson", ""),
            status="active",
            context_conditions=spec.get("context_conditions", ""),
            creation_origin="human_saved",
            root_cause_confirmed=spec.get("root_cause_confirmed", False),
        )
        print(f"created memory id={entry.id}: {entry.title}")
    for seed in RAW_QA_SEEDS:
        if any(seed["question"] in str(t) for t in existing):
            print(f"skip existing raw_qa: {seed['question']}")
            continue
        result = service.create_raw_qa_entry(
            question=seed["question"],
            answer=seed["answer"],
        )
        print(f"created raw_qa id={result.entry.id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DIRTY_LONG_TERM V1 builder (staging only)")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--read", action="store_true")
    parser.add_argument("--memories", action="store_true")
    args = parser.parse_args()
    if not any((args.generate, args.do_import, args.read, args.memories)):
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
        rc = stage_memories()
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
