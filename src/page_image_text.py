"""Read locally stored image transcripts without invoking any AI provider."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from src.models import Page

LOGGER = logging.getLogger(__name__)


def image_transcript(root: Path, page_id: int, image_path: Path) -> str:
    """Return only a direct-image transcript bound to the current original."""

    path = root / "pages" / f"page_{page_id}.json"
    if not path.is_file() or not image_path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not str(payload.get("source_text_kind", "")).startswith("page-image-"):
            return ""
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if payload.get("source_image_sha256") != digest or payload.get("page_id") != page_id:
            return ""
        return str(payload.get("transcript", ""))
    except (OSError, ValueError, AttributeError):
        LOGGER.warning("读取本地图片转录失败：page_id=%s", page_id, exc_info=True)
        return ""


def agent_image_text(transcript: str, manual: str) -> str:
    """Keep the image transcription distinct from authoritative corrections."""

    return transcript + ("\n\n【人工修正】\n" + manual if manual.strip() else "")


def page_ai_text(page: Page, readings_root: Path | None = None) -> str:
    """Select AI context and embedding text without legacy OCR content."""

    transcript = (
        image_transcript(readings_root, page.id, page.image_path)
        if readings_root is not None else ""
    )
    if transcript:
        return agent_image_text(transcript, page.markdown_content)
    return page.markdown_content.strip() or page.extracted_text.strip()
