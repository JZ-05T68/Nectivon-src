"""FAIL-017 layer probe: one isolated layer per invocation.

Used by ``tests/test_fail017_pathological_pdf.py`` to keep every
native-touching layer in a subprocess of its own, so a native-level silent
exit (the original FAIL-017 incident signature) surfaces as a non-zero exit
code instead of killing the test process.

Layers:
  render : rebuild the pathological 0.5x-pixmap PDF, render at 3 zooms, save PNG
  pil    : decode + resize the rendered PNG
  read   : scratch-DB import + ``read_document`` with a fake provider;
           the rasterized page has no text layer, so the reader must fail
           closed with an explicit error (printed as FAIL_CLOSED)

Writes only into ``--workdir``. Never touches staging or user data.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import shutil
import sys
from pathlib import Path

faulthandler.enable()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PAGE_W, PAGE_H = 595, 842


def _build_pathological_pdf(workdir: Path) -> Path:
    import fitz

    pdf_path = workdir / "pathological_lowres.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text((72, 80), "传真采购单（病态复现件）", fontsize=14, fontname="china-s")
    y = 130
    for line in ("品名：轴承 6205", "数量：4", "单价：28.5 元", "金额：114 元"):
        page.insert_text((72, y), line, fontsize=8, fontname="china-s")
        y += 16
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
    new = fitz.open()
    page2 = new.new_page(width=PAGE_W, height=PAGE_H)
    page2.insert_image(fitz.Rect(40, 40, 555, 802), pixmap=pix)
    new.save(pdf_path)
    return pdf_path


def layer_render(workdir: Path) -> None:
    import fitz

    pdf_path = _build_pathological_pdf(workdir)
    doc = fitz.open(pdf_path)
    page = doc[0]
    for zoom in (1.0, 2.0, 0.5):
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        print(f"zoom={zoom} pix={pix.width}x{pix.height}", flush=True)
    png = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    (workdir / "render.png").write_bytes(png)
    print("pdf:", pdf_path)


def layer_pil(workdir: Path) -> None:
    from PIL import Image

    layer_render(workdir)
    with Image.open(workdir / "render.png") as img:
        small = img.resize((img.width // 2, img.height // 2))
        small.save(workdir / "resized.png")
    print("pil_ok")


def layer_read(workdir: Path) -> None:
    from src.agent_document_reader import (
        AgentDocumentReader,
        AgentDocumentReadingError,
        AgentReadingStore,
    )
    from src.ai.provider import CompletionResult
    from src.database import Database
    from src.document_service import DocumentService

    pdf_path = _build_pathological_pdf(workdir)
    for stale in ("raw", "pages", "markdown", "readings"):
        shutil.rmtree(workdir / stale, ignore_errors=True)
    db = Database(workdir / "scratch.db")
    service = DocumentService(
        db,
        raw_dir=workdir / "raw",
        pages_dir=workdir / "pages",
        markdown_dir=workdir / "markdown",
    )
    record = service.import_document(pdf_path.read_bytes(), pdf_path.name)
    print("imported:", record.document.id, flush=True)

    class FakeProvider:
        def complete(self, prompt, **kwargs):  # noqa: ANN001, ANN003
            return CompletionResult(
                text=json.dumps(
                    {"summary": "s", "keywords": [], "key_facts": []},
                    ensure_ascii=False,
                ),
                model="fake-model",
            )

    reader = AgentDocumentReader(
        database=db,
        provider=FakeProvider(),
        store=AgentReadingStore(workdir / "readings"),
        model="fake-model",
    )
    try:
        reader.read_document(record.document.id)
    except AgentDocumentReadingError as error:
        print("FAIL_CLOSED", error)
        return
    raise AssertionError("read_document unexpectedly succeeded on a no-text page")


LAYERS = {
    "render": layer_render,
    "pil": layer_pil,
    "read": layer_read,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("layer", choices=sorted(LAYERS))
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    LAYERS[args.layer](workdir)
    print("LAYER_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
