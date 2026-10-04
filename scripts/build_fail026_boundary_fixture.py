# ruff: noqa: E501
"""FAIL-026 图表标签边界定界 fixture（staging only）。

单文档 7 页，每页一种图表标签形态（文本层即真值）：
  P1 折线图 无间隙标签      26 / 55 / 68
  P2 折线图 小间隙(4px)     102 / 120
  P3 折线图 中间隙(16px)    26 / 55      （Night 1 触发形态直接复现控制）
  P4 折线图 大间隙(30px)    68 / 102
  P5 柱状图 间隙(16px)      26 / 55 / 68
  P6 散点图 间隙(16px)      120 / 26
  P7 折线图 真小数无间隙    2.6 / 5.5 / 10.2（小数读取对照）

用法：
  python scripts/build_fail026_boundary_fixture.py --generate
  python scripts/build_fail026_boundary_fixture.py --import
  python scripts/build_fail026_boundary_fixture.py --read
隔离：EKB_STAGING_INSTANCE=1 先于 src 导入。
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

INBOX = PROJECT_ROOT / "staging-data" / "inbox-fail026"
TITLE = "FAIL026图表标签边界测试集"
PAGE_W, PAGE_H = 595, 842


def _label(page: fitz.Page, x: float, y: float, text: str, gap: float) -> None:
    """按间隙渲染标签：gap>0 时数字间插入间隙。"""
    if gap <= 0:
        page.insert_text((x, y), text, fontsize=11, fontname="helv")
        return
    chars = list(text)
    cx = x
    for ch in chars:
        page.insert_text((cx, y), ch, fontsize=11, fontname="helv")
        cx += 7 + gap


def _frame(page: fitz.Page, caption: str, note: str) -> None:
    page.insert_text((72, 72), TITLE, fontsize=14, fontname="china-s")
    page.insert_text((160, 160), caption, fontsize=12, fontname="china-s")
    page.insert_text((72, 800), note, fontsize=9, fontname="china-s")


def _line_page(
    labels: list[tuple[str, int]],
    gap: float,
    caption: str,
    note: str,
) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _frame(page, caption, note)
    vmax = max(float(t) for t, _ in labels)
    x0, y0, width, height = 100, 620, 380, 330
    step = width / (len(labels) - 1)
    prev = None
    for i, (text, value) in enumerate(labels):
        x = x0 + i * step
        y = y0 - value / vmax * height
        if prev is not None:
            page.draw_line(prev, (x, y), color=(0.78, 0.27, 0.35), width=1.6)
        page.draw_circle((x, y), 3, color=(0.78, 0.27, 0.35), fill=(0.78, 0.27, 0.35))
        _label(page, x - 14, y - 12, text, gap)
        page.insert_text((x - 10, y0 + 16), str(i + 1), fontsize=10, fontname="helv")
        prev = (x, y)
    page.draw_line((80, y0), (500, y0))
    return doc


def _bar_page(labels: list[tuple[str, int]], gap: float, caption: str, note: str) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _frame(page, caption, note)
    vmax = max(float(t) for t, _ in labels)
    y0, height, width = 620, 360, 380
    x = 90
    step = width / len(labels)
    for i, (text, value) in enumerate(labels):
        h = value / vmax * height
        page.draw_rect(fitz.Rect(x, y0 - h, x + 46, y0), fill=(0.30, 0.56, 0.78))
        _label(page, x + 2, y0 - h - 10, text, gap)
        page.insert_text((x + 4, y0 + 16), str(i + 1), fontsize=11, fontname="helv")
        x += step
    page.draw_line((80, y0), (480, y0))
    return doc


def _scatter_page(labels: list[tuple[str, int]], gap: float, caption: str, note: str) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _frame(page, caption, note)
    points = [(130, 560), (300, 430), (430, 300)]
    for i, ((px, py), (text, _value)) in enumerate(zip(points, labels, strict=False)):
        page.draw_circle((px, py), 4, color=(0.2, 0.45, 0.75), fill=(0.2, 0.45, 0.75))
        _label(page, px + 10, py - 10, text, gap)
        page.insert_text((px - 5, 620), str(i + 1), fontsize=10, fontname="helv")
    page.draw_line((90, 580), (500, 580))
    return doc


PAGES = [
    ("P1-line-nogap", lambda: _line_page(
        [("26", 26), ("55", 55), ("68", 68)], 0,
        "图1-1 折线图 无间隙标签", "数据点标签：26、55、68；横轴 1-3。")),
    ("P2-line-smallgap", lambda: _line_page(
        [("102", 102), ("120", 120)], 4,
        "图2-1 折线图 小间隙标签", "数据点标签：102、120；横轴 1-2。")),
    ("P3-line-mediumgap", lambda: _line_page(
        [("26", 26), ("55", 55)], 16,
        "图3-1 折线图 中间隙标签", "数据点标签：26、55；横轴 1-2。")),
    ("P4-line-largegap", lambda: _line_page(
        [("68", 68), ("102", 102)], 30,
        "图4-1 折线图 大间隙标签", "数据点标签：68、102；横轴 1-2。")),
    ("P5-bar-gap", lambda: _bar_page(
        [("26", 26), ("55", 55), ("68", 68)], 16,
        "图5-1 柱状图 间隙标签", "柱顶标签：26、55、68；横轴 1-3。")),
    ("P6-scatter-gap", lambda: _scatter_page(
        [("120", 120), ("26", 26), ("68", 68)], 16,
        "图6-1 散点图 间隙标签", "散点标签：120、26、68；横轴 1-3。")),
    ("P7-line-decimal", lambda: _line_page(
        [("2.6", 26), ("5.5", 55), ("10.2", 102)], 0,
        "图7-1 折线图 真小数无间隙标签", "数据点标签：2.6、5.5、10.2；横轴 1-3。")),
]


def stage_generate() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _name, builder in PAGES:
        src = builder()
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.show_pdf_page(page.rect, src, 0)
    path = INBOX / f"{TITLE}.pdf"
    path.write_bytes(doc.tobytes())
    print(f"generated {path.name} pages={len(doc)}")
    return 0


def _database():
    from src.database import Database

    return Database(staging_settings().database_path)


def stage_import() -> int:
    from src.database import Database
    from src.document_service import DocumentService

    settings = staging_settings()
    service = DocumentService(
        Database(settings.database_path),
        settings.raw_dir,
        settings.pages_dir,
        settings.markdown_dir,
    )
    existing = {d.title for d in service.database.list_documents()}
    if TITLE in existing:
        print(f"skip existing doc: {TITLE}")
        return 0
    path = INBOX / f"{TITLE}.pdf"
    if not path.exists():
        print("missing pdf；先 --generate")
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
    for document in database.list_documents():
        if document.title != TITLE:
            continue
        state = store.document_state(document.id)
        if state and state.completed:
            print(f"already read: {document.id}")
            continue
        print(f"reading {document.id} {document.title} ...", flush=True)
        agent.read_document(document.id)
    print("done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FAIL-026 boundary fixture builder (staging only)")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--read", action="store_true")
    args = parser.parse_args()
    if not any((args.generate, args.do_import, args.read)):
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
