"""User-attached answer images: safe uploads plus committed crops of original pages.

The「我的作答」block owns two kinds of visual attachments, both user-provided:

- ``uploads`` — photos of the user's own answer sheet, stored as
  metadata-free PNG next to the page images (``pages/answer-uploads``) so
  they ride along with the normal data backup and never disappear when a
  source document is deleted;
- ``regions`` — committed crop references (选择文件 → 选择页码 → 框选) into
  ANY imported page, kept in the per-question ``QuestionRegionStore`` under
  a dedicated ``answer`` scope so they can never collide with the question
  stem's own screenshot boxes.

References live in ``question_items.ai_draft.answer_material``; the files on
disk are content-addressed and are never deleted automatically (removing an
attachment only drops its reference).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from src.question_image_store import MAX_IMAGE_BYTES, QuestionImageError
from src.question_region_store import QuestionRegionStore
from src.question_visual_regions import normalize_regions

LOGGER = logging.getLogger(__name__)

ANSWER_UPLOAD_LIMIT = 12
ANSWER_MAX_PIXELS = 32_000_000
ANSWER_UPLOAD_DIR_NAME = "answer-uploads"
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class AnswerImageError(ValueError):
    """An answer image or one of its references could not be safely handled."""


def answer_crop_scope(question_id: int) -> str:
    """Dedicated on-disk scope for one question's answer crop boxes."""

    return hashlib.sha256(f"answer:{int(question_id)}".encode()).hexdigest()[:32]


def answer_upload_dir(pages_dir: Path | str, question_id: int) -> Path:
    """Uploads live beside page images, inside the backed-up pages tree."""

    return Path(pages_dir) / ANSWER_UPLOAD_DIR_NAME / str(int(question_id))


class AnswerUploadStore:
    """Content-addressed answer photos; original uploads are re-encoded PNG."""

    def __init__(self, pages_dir: Path | str, question_id: int):
        self.question_id = int(question_id)
        self.root = answer_upload_dir(pages_dir, question_id)

    def save(self, data: bytes) -> dict:
        """Validate one uploaded file and store it without any metadata.

        The pixels are re-encoded as plain RGB PNG, which strips EXIF/GPS
        metadata a phone photo may carry.  Files are named by content digest,
        so re-uploading the same picture never duplicates bytes on disk.
        """

        if not data or len(data) > MAX_IMAGE_BYTES:
            raise AnswerImageError("图片数据无效或超过 24MB，未保存。")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > ANSWER_MAX_PIXELS:
                    raise AnswerImageError("图片像素过大，未保存。")
                width, height = image.size
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
        except AnswerImageError:
            raise
        except (OSError, Image.DecompressionBombError) as exc:
            raise AnswerImageError("无法读取这张图片，请换一张再试。") from exc
        canonical = output.getvalue()
        digest = hashlib.sha256(canonical).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{digest}.png").write_bytes(canonical)
        return {
            "id": uuid4().hex,
            "sha256": digest,
            "width": int(width),
            "height": int(height),
            "saved_at": datetime.now(UTC).isoformat(),
            "origin": "upload",
        }

    def read(self, entry: dict) -> bytes:
        """Return the stored bytes, verifying they still hash to the digest."""

        path = self.root / f"{entry['sha256']}.png"
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AnswerImageError("这张作答图片的文件缺失，请重新上传。") from exc
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise AnswerImageError("这张作答图片校验失败，请重新上传。")
        return data


def normalize_answer_material(raw: object) -> dict:
    """Keep only provably valid upload entries and committed crop references."""

    if not isinstance(raw, dict):
        return {"regions": [], "uploads": []}
    regions = [
        region for region in normalize_regions(raw.get("regions"))
        if region.get("role") == "answer" and region.get("crop_scope")
        and region.get("region_id") and region.get("page_id") is not None
    ]
    uploads: list[dict] = []
    seen_ids: set[str] = set()
    seen_digests: set[str] = set()
    entries = raw.get("uploads")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        digest, identifier = entry.get("sha256"), entry.get("id")
        if (not isinstance(digest, str) or not _HEX64.fullmatch(digest)
                or not isinstance(identifier, str) or not _HEX32.fullmatch(identifier)
                or identifier in seen_ids or digest in seen_digests):
            continue
        width, height = entry.get("width"), entry.get("height")
        if (type(width) is not int or type(height) is not int
                or width < 1 or height < 1):
            continue
        seen_ids.add(identifier)
        seen_digests.add(digest)
        uploads.append({
            "id": identifier, "sha256": digest, "width": width, "height": height,
            "saved_at": str(entry.get("saved_at", "")),
            "origin": "upload",
        })
    return {"regions": regions, "uploads": uploads}


def sync_crop_references(material: dict, page_id: int, page_regions: list[dict]) -> dict:
    """Replace one page's committed answer references, keeping other pages."""

    merged = [
        region for region in material.get("regions", [])
        if region.get("page_id") != page_id
    ]
    merged.extend(
        region for region in normalize_regions(page_regions)
        if region.get("role") == "answer"
    )
    return {"regions": merged, "uploads": material.get("uploads", [])}


def remove_crop_reference(
    material: dict, *, page_id: int, region_id: str,
    page_image: Path | str, question_id: int,
) -> dict:
    """Drop one committed answer box from the reference list and the box store.

    Removing the reference alone would leave the box on disk, and the next
    crop-editor save would resurrect it (the editor syncs the whole store).
    The box list is therefore updated in the same breath; a store failure
    still drops the reference and is only logged — display never depends on
    the on-disk box list.
    """

    remaining = [
        region for region in material.get("regions", [])
        if not (region.get("page_id") == page_id and region.get("region_id") == region_id)
    ]
    try:
        store = QuestionRegionStore(page_image, scope=answer_crop_scope(question_id))
        state = store.state()
        store.save_regions(
            [box for box in state["regions"] if box["id"] != region_id],
            expected_revision=state["revision"],
        )
    except (OSError, QuestionImageError):
        LOGGER.warning("作答截图方框同步移除失败，仅移除引用：%s", region_id, exc_info=True)
    return {"regions": remaining, "uploads": material.get("uploads", [])}


def remove_upload_reference(material: dict, upload_id: str) -> dict:
    """Drop one upload reference; the stored file itself is kept."""

    return {
        "regions": material.get("regions", []),
        "uploads": [entry for entry in material.get("uploads", [])
                    if entry["id"] != upload_id],
    }
