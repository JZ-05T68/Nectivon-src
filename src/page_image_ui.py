"""Display the original-image content blocks for every imported source format."""

from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st

from src.agent_document_reader import AgentReadingStore, PageReading
from src.math_display import render_question_math_markdown
from src.models import Page
from src.question_content_ui import render_region_images


def current_image_reading(page: Page, root: Path) -> PageReading | None:
    """Keep stale page geometry and transcripts out of the reading surface."""

    reading = AgentReadingStore(root).page_reading(page.id)
    image = Path(page.image_path)
    if (
        reading is None
        or not reading.source_text_kind.startswith("page-image-")
        or not image.is_file()
        or reading.source_image_sha256 != hashlib.sha256(image.read_bytes()).hexdigest()
    ):
        return None
    return reading


def render_page_image_blocks(page: Page, root: Path) -> None:
    """Expose paragraph, table and figure blocks without inventing exam items."""

    reading = current_image_reading(page, root)
    if reading is None or not reading.blocks:
        return
    with st.expander("AI 读图内容块"):
        for index, block in enumerate(reading.blocks, 1):
            kind = str(block.get("kind", "text"))
            st.caption(f"内容块 {index}")
            text = str(block.get("text", "")).strip()
            if text:
                render_question_math_markdown(text)
            if kind.lower() in ("figure", "image", "diagram", "table", "chart", "图", "表格"):
                # The whole block is source material; retain its original layout.
                regions = [{**region, "role": "question"} for region in block.get("regions", [])]
                render_region_images(page.image_path, regions)
