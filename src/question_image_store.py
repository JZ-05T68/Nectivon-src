"""Versioned question display images; original scans always remain immutable."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from PIL import Image

MAX_IMAGE_BYTES = 24_000_000


class QuestionImageError(ValueError):
    """A question display image could not be safely read or saved."""


@lru_cache(maxsize=64)
def _source_identity(path: str, modified_ns: int, size: int) -> tuple[str, tuple[int, int]]:
    data = Path(path).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(data).hexdigest(), image.size


@lru_cache(maxsize=64)
def _verified_image(path: str, modified_ns: int, size: int, digest: str) -> None:
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
        raise QuestionImageError("题目显示图校验失败，请恢复原图或重新保存。")


class QuestionImageStore:
    """Keep display revisions next to local pages, inside normal data backups."""

    def __init__(self, source: Path | str):
        self.source = Path(source).resolve()
        stat = self.source.stat()
        self.source_sha256, self.size = _source_identity(
            str(self.source), stat.st_mtime_ns, stat.st_size,
        )
        self.root = (
            self.source.parent / "question-display" / self.source.stem / self.source_sha256
        )

    def state(self) -> dict:
        """Resolve a checked revision, never accepting paths from saved metadata."""

        active = self.root / "active.json"
        if not active.is_file():
            return {"revision": "original", "origin": "original"}
        try:
            value = json.loads(active.read_text(encoding="utf-8"))
            revision = value["revision"]
            if (not isinstance(revision, str) or len(revision) != 32
                    or any(c not in "0123456789abcdef" for c in revision)
                    or value["source_sha256"] != self.source_sha256):
                raise ValueError("Invalid revision")
            image_path = self.root / f"{revision}.png"
            stat = image_path.stat()
            _verified_image(
                str(image_path), stat.st_mtime_ns, stat.st_size, value["image_sha256"],
            )
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuestionImageError("题目显示图记录损坏，请核对原图并重新保存。") from exc

    def display_path(self) -> Path:
        """Return the current display image or the untouched original scan."""

        revision = self.state()["revision"]
        return self.source if revision == "original" else self.root / f"{revision}.png"

    def save(
        self, data: bytes, *, expected_revision: str,
        details: dict | None = None,
    ) -> dict:
        """Validate and append a revision, then atomically select it for all views."""

        if self.state()["revision"] != expected_revision:
            raise QuestionImageError("显示图已在其它位置修改，请重新打开绘图工具后再保存。")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise QuestionImageError("题目显示图数据无效或过大，未保存。")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.size != self.size or image.width * image.height > 32_000_000:
                    raise QuestionImageError("修改图的尺寸必须与原始扫描图一致。")
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                canonical = output.getvalue()
        except (OSError, Image.DecompressionBombError) as exc:
            raise QuestionImageError("无法读取修改图，请重新打开绘图工具。") from exc
        revision = uuid4().hex
        value = {
            "version": 1, "revision": revision, "origin": "manual",
            "source_sha256": self.source_sha256,
            "image_sha256": hashlib.sha256(canonical).hexdigest(),
            "saved_at": datetime.now(UTC).isoformat(), "details": details or {},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{revision}.png").write_bytes(canonical)
        serialized = json.dumps(value, ensure_ascii=False, indent=2)
        (self.root / f"{revision}.json").write_text(serialized, encoding="utf-8")
        temporary = self.root / f"active-{revision}.tmp"
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(self.root / "active.json")
        return value

    def restore_original(self, *, expected_revision: str) -> dict:
        """Append an explicit manual restoration without deleting any revisions."""

        return self.save(
            self.source.read_bytes(), expected_revision=expected_revision,
            details={"restored_original": True},
        )


def question_display_path(source: Path | str) -> Path:
    """Central resolver for whole-page question fallbacks and image downloads."""

    try:
        return QuestionImageStore(source).display_path()
    except QuestionImageError:
        logging.getLogger(__name__).warning("题目显示图不可用，改用原始扫描图：%s", source)
        return Path(source)
