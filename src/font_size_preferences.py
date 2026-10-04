"""Local, per-instance reading-size preference (no profile data or network)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_LEVEL = 3
MIN_LEVEL = 1
MAX_LEVEL = 5


def preference_path(data_dir: Path) -> Path:
    return data_dir / "ui_preferences.json"


def read_font_level(data_dir: Path) -> int:
    path = preference_path(data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("font_level")
        return value if type(value) is int and MIN_LEVEL <= value <= MAX_LEVEL else DEFAULT_LEVEL
    except (OSError, ValueError, AttributeError):
        return DEFAULT_LEVEL


def write_font_level(data_dir: Path, level: int) -> None:
    if type(level) is not int or not MIN_LEVEL <= level <= MAX_LEVEL:
        raise ValueError("字体大小级别必须是 1 至 5 的整数。")
    path = preference_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=".ui_preferences.", suffix=".tmp", delete=False,
    ) as temporary:
        json.dump({"font_level": level}, temporary, ensure_ascii=False)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def font_size_css(level: int) -> str:
    """Scale readable content and KaTeX together; no literal user text is changed."""

    value = max(MIN_LEVEL, min(MAX_LEVEL, int(level)))
    body_px = (15, 17, 19, 21, 23)[value - 1]
    caption_px = (13, 14, 15, 17, 19)[value - 1]
    return (
        "<style>"
        f".stApp [data-testid='stMain'] [data-testid='stMarkdownContainer'] p,"
        f".stApp [data-testid='stMain'] [data-testid='stMarkdownContainer'] li"
        f"{{font-size:{body_px}px;line-height:1.7;overflow-wrap:anywhere;}}"
        f".stApp [data-testid='stMain'] [data-testid='stCaptionContainer'] p"
        f"{{font-size:{caption_px}px;line-height:1.6;}}"
        f".stApp [data-testid='stMain'] .katex{{font-size:1.06em;}}"
        ".stApp [data-testid='stMain'] [data-testid='stMarkdownContainer']"
        "{overflow-x:auto;}"
        "</style>"
    )
