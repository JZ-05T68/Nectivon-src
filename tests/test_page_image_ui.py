"""Image blocks remain usable for non-exam pages and after file changes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest

from src.agent_document_reader import AgentReadingStore, PageReading
from src.database import Database
from src.page_image_ui import current_image_reading


def test_non_exam_content_blocks_show_math_and_source_image(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    db = Database(tmp_path / "database" / "knowledge.db")
    document = db.create_document(
        title="Lecture", filename="lecture.pptx", source_path=tmp_path / "lecture.pptx",
        sha256="a" * 64, page_count=1,
    )
    page = db.create_page(document_id=document.id, page_number=1, image_path=image)
    root = tmp_path / "readings"
    reading = PageReading(
        format_version=1, document_id=document.id, page_id=page.id, page_number=1,
        document_sha256=document.sha256, source_text_sha256="b" * 64,
        source_text_kind="page-image-v2-pixel-rulers", model="qwen3.8-max",
        summary="Lecture", keywords=(), key_facts=(), read_at="2026-10-05T00:00:00Z",
        source_image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        blocks=[{"kind": "text", "text": r"Formula $\sin\alpha+x^{2}$"},
                {"kind": "figure", "text": "Source figure", "regions": [
                    {"role": "stem", "bbox": [100, 100, 800, 800]},
                ]}],
    )
    AgentReadingStore(root).save_page_reading(reading)
    assert current_image_reading(page, root) is not None
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from src.database import Database\n"
        "from src.page_image_ui import render_page_image_blocks\n"
        f"page=Database(Path({str(db.database_path)!r})).get_page({page.id})\n"
        f"render_page_image_blocks(page, Path({str(root)!r}))\n"
    ).run()
    assert not app.exception
    assert app.expander[0].label == "AI 读图内容块"
    assert any(r"$\sin\alpha+x^{2}$" in item.value for item in app.markdown)
    assert app.get("image")
    # A stale crop cannot be attached to a replacement page image.
    Image.new("RGB", (300, 300), "black").save(image)
    assert current_image_reading(page, root) is None
