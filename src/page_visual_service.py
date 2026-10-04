"""Dual-stage page visual parsing (v0.8.6 duty D).

Stage 1 runs locally and deterministically on every page: it inspects the
source PDF page with PyMuPDF for embedded raster images and vector drawings
and combines that with the existing text-layer thinness signal.  Detection
is deliberately conservative and honest - "this page contains visual
content" is recorded as a *presence* fact, never as understanding; the
handwriting signal can only ever mean "content is outside the PDF text
layer" (likely scanned or handwritten) and is marked ``uncertain`` because
a printed scan and handwriting look identical at this level.

Stage 2 exists only behind an explicit user action.  Its results are
stored by :meth:`PageVisualService.record_interpretation` with a provenance
label (TEXT_LAYER / HANDWRITING_VISION / IMAGE_REGION /
DIAGRAM_INTERPRETATION) so vision output can never pose as the PDF text
layer; low-confidence readings stay ``uncertain`` and AI output stays in
``draft`` until the user confirms it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from src.database import Database, DatabaseError
from src.pdf_service import PdfService, _load_pymupdf

PROVENANCE_TYPES: Final = (
    "TEXT_LAYER",
    "HANDWRITING_VISION",
    "IMAGE_REGION",
    "DIAGRAM_INTERPRETATION",
)
_DETECTION_STATUSES: Final = ("not_checked", "checked", "uncertain")
_CONFIDENCE_LEVELS: Final = ("confirmed", "probable", "uncertain")
_ORIGINS: Final = ("ai_vision", "user", "ocr")
#: Handwriting-presence declaration levels (human-review fix 2026-09-27 §18):
#: a HANDWRITING_VISION reading must first declare whether recognisable
#: student handwriting exists at all.  ``none`` readings must never be
#: consumed as a handwriting source downstream; ``possible`` stays
#: "待核对"; only ``confirmed`` may feed learning assets.
HANDWRITING_PRESENCE_LEVELS: Final = ("none", "possible", "confirmed")
#: region_json key under which the declared presence level is stored so the
#: declaration travels with the reading it belongs to (evidence pointer §19).
HANDWRITING_PRESENCE_KEY: Final = "handwriting_presence"
#: A PDF page with at least this many vector drawing paths is treated as
#: containing a diagram/figure (text glyphs are not drawings; a simple
#: underline or box is one or two paths, a chart is many).
_DIAGRAM_PATH_THRESHOLD: Final = 4


class PageVisualError(DatabaseError):
    """A page visual detection or interpretation operation was refused."""


@dataclass(frozen=True, slots=True)
class PageVisualDetection:
    """Stage-1 result for one page: presence facts, never understanding."""

    page_id: int
    has_handwriting: bool | None
    has_visual_content: bool | None
    detection_status: str
    raster_image_count: int
    vector_drawing_count: int
    detail: str
    #: Detection provenance (V086-302): which detector produced this row, so
    #: a stored "uncertain/1/1" can always be told apart from "never
    #: checked" and from a failed attempt. Values: ``pymupdf_structure`` or
    #: ``failed`` (recorded when the detector itself errored).
    method: str = "pymupdf_structure"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PageVisualService:
    """Detect stage-1 visual presence and record stage-2 user-triggered reads."""

    def __init__(self, database: Database, *, minimum_text_length: int = 20) -> None:
        self._database = database
        self._minimum_text_length = minimum_text_length

    # ------------------------------------------------------------- stage 1
    def detect_from_pdf_page(
        self,
        page_id: int,
        *,
        pdf_page: object,
        extracted_text: str,
        ocr_text: str = "",
    ) -> PageVisualDetection:
        """Run stage-1 detection from an already-open PyMuPDF page object."""

        raster_images = list(pdf_page.get_images(full=False))
        try:
            drawing_paths = len(list(pdf_page.get_drawings()))
        except Exception:  # noqa: BLE001 - get_drawings can fail on odd PDFs
            drawing_paths = 0
        effective_chars = PdfService.effective_text_length(extracted_text)
        effective_ocr_chars = PdfService.effective_text_length(ocr_text)

        has_visual_content = bool(raster_images) or drawing_paths >= _DIAGRAM_PATH_THRESHOLD
        text_layer_thin = effective_chars < self._minimum_text_length
        # Handwriting can only be claimed when the content lives outside the
        # text layer (scan-like page) - and even then it stays uncertain.
        if effective_ocr_chars > 0 and text_layer_thin:
            has_handwriting: bool | None = True
            detail = "检测到疑似手写/扫描内容（非文本层），可进一步读取。"
            status = "uncertain"
        elif raster_images or drawing_paths >= _DIAGRAM_PATH_THRESHOLD:
            has_handwriting = None
            detail = "检测到图片/图表类视觉内容，可进一步解析。"
            status = "checked"
        elif not raster_images and drawing_paths < _DIAGRAM_PATH_THRESHOLD and not text_layer_thin:
            has_handwriting = False
            has_visual_content = False
            detail = "未检测到手写或图片/图表类视觉内容。"
            status = "checked"
        else:
            has_handwriting = None
            detail = "文本层较薄但未发现视觉区域；如页面确有手写，请手动触发读取。"
            status = "uncertain"
        detection = PageVisualDetection(
            page_id=page_id,
            has_handwriting=has_handwriting,
            has_visual_content=has_visual_content,
            detection_status=status,
            raster_image_count=len(raster_images),
            vector_drawing_count=drawing_paths,
            detail=detail,
        )
        self.record_detection(detection)
        return detection

    def detect_from_source(
        self, page_id: int, *, source_path: Path, page_number: int
    ) -> PageVisualDetection:
        """Open the source PDF, locate the page, and run stage-1 detection."""

        extracted_text, ocr_text, page_handle = self._load_page_facts(
            page_id, source_path, page_number
        )
        return self.detect_from_pdf_page(
            page_id, pdf_page=page_handle, extracted_text=extracted_text, ocr_text=ocr_text
        )

    def record_detection(self, detection: PageVisualDetection) -> None:
        """Persist one stage-1 detection onto the page row."""

        with self._database._connection() as connection:
            page = connection.execute(
                "SELECT id FROM pages WHERE id = ?", (detection.page_id,)
            ).fetchone()
            if page is None:
                raise PageVisualError(f"页面不存在：{detection.page_id}")
            connection.execute(
                """
                UPDATE pages SET
                    has_handwriting = ?, has_visual_content = ?,
                    visual_detection_status = ?, visual_detected_at = ?,
                    visual_detection_method = ?
                WHERE id = ?
                """,
                (
                    detection.has_handwriting,
                    detection.has_visual_content,
                    detection.detection_status,
                    _utc_now(),
                    detection.method,
                    detection.page_id,
                ),
            )

    def record_detection_failure(self, page_id: int) -> None:
        """Mark stage-1 detection as attempted-but-failed (provenance honesty).

        A failed detector run must never look like ``not_checked``: the row
        keeps its previous status values but the method records the failure
        so the UI can show「检测失败」instead of silently pretending the
        page was never examined.
        """

        with self._database._connection() as connection:
            page = connection.execute(
                "SELECT id FROM pages WHERE id = ?", (page_id,)
            ).fetchone()
            if page is None:
                raise PageVisualError(f"页面不存在：{page_id}")
            connection.execute(
                """
                UPDATE pages SET
                    visual_detection_method = 'failed', visual_detected_at = ?
                WHERE id = ?
                """,
                (_utc_now(), page_id),
            )

    def get_page_visual_state(self, page_id: int) -> dict[str, object]:
        """Return the user-visible stage-1 state plus stage-2 readings."""

        with self._database._connection() as connection:
            page = connection.execute(
                """
                SELECT has_handwriting, has_visual_content,
                       visual_detection_status, visual_detected_at,
                       visual_detection_method
                FROM pages WHERE id = ?
                """,
                (page_id,),
            ).fetchone()
            if page is None:
                raise PageVisualError(f"页面不存在：{page_id}")
            interpretations = connection.execute(
                """
                SELECT id, provenance, content, region_json, confidence,
                       origin, status, user_edited, original_ai_content,
                       created_at, updated_at
                FROM page_visual_interpretations
                WHERE page_id = ? ORDER BY id
                """,
                (page_id,),
            ).fetchall()
        rows = []
        for row in interpretations:
            entry = dict(row)
            rows.append(entry)
        return {
            "has_handwriting": page["has_handwriting"],
            "has_visual_content": page["has_visual_content"],
            "visual_detection_status": page["visual_detection_status"],
            "visual_detected_at": page["visual_detected_at"],
            "visual_detection_method": (
                str(page["visual_detection_method"] or "")
                if "visual_detection_method" in page.keys()
                else ""
            ),
            "interpretations": rows,
        }

    # ------------------------------------------------------------- stage 2
    def record_interpretation(
        self,
        page_id: int,
        *,
        provenance: str,
        content: str,
        confidence: str = "uncertain",
        origin: str = "ai_vision",
        region_json: dict | None = None,
    ) -> int:
        """Store one stage-2 reading; provenance keeps sources distinguishable."""

        if provenance not in PROVENANCE_TYPES:
            raise PageVisualError(
            "解析来源必须是 TEXT_LAYER/HANDWRITING_VISION/IMAGE_REGION/"
            "DIAGRAM_INTERPRETATION。"
        )
        if confidence not in _CONFIDENCE_LEVELS:
            raise PageVisualError("置信度必须是 confirmed/probable/uncertain。")
        if origin not in _ORIGINS:
            raise PageVisualError("解析方必须是 ai_vision/user/ocr。")
        if provenance == "TEXT_LAYER" and origin != "ocr":
            raise PageVisualError("TEXT_LAYER 来源只能由文本层产生，不能写入视觉解析结果。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            page = connection.execute(
                "SELECT id FROM pages WHERE id = ?", (page_id,)
            ).fetchone()
            if page is None:
                raise PageVisualError(f"页面不存在：{page_id}")
            cursor = connection.execute(
                """
                INSERT INTO page_visual_interpretations(
                    page_id, provenance, content, region_json, confidence,
                    origin, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    page_id,
                    provenance,
                    content,
                    None if region_json is None else _dump_region(region_json),
                    confidence,
                    origin,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def confirm_interpretation(self, interpretation_id: int) -> None:
        """User confirms one stage-2 reading; AI drafts never self-confirm."""

        with self._database._connection() as connection:
            cursor = connection.execute(
                "UPDATE page_visual_interpretations SET status = 'confirmed', updated_at = ? "
                "WHERE id = ?",
                (_utc_now(), interpretation_id),
            )
            if cursor.rowcount == 0:
                raise PageVisualError(f"解析记录不存在：{interpretation_id}")

    def delete_interpretation(self, interpretation_id: int) -> dict[str, int]:
        """User rejects/removes one stage-2 reading (fix round §25).

        Deleting a reading is a user *negation* of that content.  The row is
        removed; the caller uses the returned page binding to invalidate
        downstream AI-derived (not user-confirmed) fields so stale
        hallucination cannot survive upstream deletion.  Returns the page
        binding of the deleted row.
        """

        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT page_id, provenance FROM page_visual_interpretations WHERE id = ?",
                (interpretation_id,),
            ).fetchone()
            if row is None:
                raise PageVisualError(f"解析记录不存在：{interpretation_id}")
            connection.execute(
                "DELETE FROM page_visual_interpretations WHERE id = ?",
                (interpretation_id,),
            )
        return {
            "page_id": int(row["page_id"]),
            "provenance": str(row["provenance"]),
        }

    def update_interpretation(
        self, interpretation_id: int, *, content: str
    ) -> dict[str, object]:
        """Apply a user edit to one stage-2 reading (overnight round §7).

        Priority contract: USER CONFIRMED > AI DRAFT.  The first user edit
        snapshots the original AI content into ``original_ai_content`` so a
        later re-read or undo can still find what the AI produced; the row
        becomes ``confirmed`` + ``user_edited`` and its content is exactly
        what the user saved.  A future AI re-run must never silently
        overwrite this user-confirmed content.
        """

        cleaned = str(content).strip()
        if not cleaned:
            raise PageVisualError("修改后的内容不能为空。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT id, origin, user_edited, original_ai_content, content "
                "FROM page_visual_interpretations WHERE id = ?",
                (interpretation_id,),
            ).fetchone()
            if row is None:
                raise PageVisualError(f"解析记录不存在：{interpretation_id}")
            first_edit = not bool(row["user_edited"])
            original = (
                str(row["content"])
                if first_edit and not str(row["original_ai_content"] or "")
                else str(row["original_ai_content"] or "")
            )
            connection.execute(
                """
                UPDATE page_visual_interpretations SET
                    content = ?, user_edited = 1, status = 'confirmed',
                    original_ai_content = ?, updated_at = ?
                WHERE id = ?
                """,
                (cleaned, original, timestamp, interpretation_id),
            )
        return {
            "id": interpretation_id,
            "status": "confirmed",
            "user_edited": True,
            "original_ai_content": original,
        }

    # ------------------------------------------------------------- internals
    def _latest_presence(
        self, connection: object, page_id: int, provenance: str
    ) -> str | None:
        """Return the newest declared presence level for one provenance."""

        rows = connection.execute(
            "SELECT region_json FROM page_visual_interpretations "
            "WHERE page_id = ? AND provenance = ? ORDER BY id DESC",
            (page_id, provenance),
        ).fetchall()
        for row in rows:
            raw = row["region_json"]
            if not raw:
                continue
            try:
                region = json.loads(raw)
            except (TypeError, ValueError):
                continue
            level = region.get(HANDWRITING_PRESENCE_KEY)
            if level in HANDWRITING_PRESENCE_LEVELS:
                return str(level)
        return None

    def check_handwriting_consistency(self, page_id: int) -> dict[str, object]:
        """Cross-channel handwriting consistency check (fix round §20).

        Compares the newest HANDWRITING_VISION presence declaration with the
        newest IMAGE_REGION presence observation on the same page.  When the
        handwriting channel claims ``confirmed`` while the region channel saw
        ``none``, the two readings contradict each other and the result is
        flagged: the contradiction must be resolved (by re-reading or by the
        user comparing against the original image) before any downstream
        asset may trust the handwriting reading.

        Missing declarations are reported as ``None`` (not checked), never
        invented.
        """

        with self._database._connection() as connection:
            page = connection.execute(
                "SELECT id FROM pages WHERE id = ?", (page_id,)
            ).fetchone()
            if page is None:
                raise PageVisualError(f"页面不存在：{page_id}")
            hw_level = self._latest_presence(connection, page_id, "HANDWRITING_VISION")
            ir_level = self._latest_presence(connection, page_id, "IMAGE_REGION")
        conflict = hw_level == "confirmed" and ir_level == "none"
        return {
            "handwriting_presence": hw_level,
            "image_region_presence": ir_level,
            "conflict": conflict,
        }

    def _load_page_facts(
        self, page_id: int, source_path: Path, page_number: int
    ) -> tuple[str, str, object]:
        with self._database._connection() as connection:
            page = connection.execute(
                "SELECT extracted_text, ocr_text, document_id FROM pages WHERE id = ?",
                (page_id,),
            ).fetchone()
        if page is None:
            raise PageVisualError(f"页面不存在：{page_id}")
        fitz = _load_pymupdf()
        document = fitz.open(source_path)
        try:
            if page_number < 1 or page_number > document.page_count:
                raise PageVisualError(f"页码超出源 PDF 范围：{page_number}")
            page_handle = document.load_page(page_number - 1)
            # The page object must outlive this method for the caller; hand
            # back the facts and the handle together.
            return (
                str(page["extracted_text"] or ""),
                str(page["ocr_text"] or ""),
                _PinnedPage(document, page_handle),
            )
        except Exception:
            document.close()
            raise

class _PinnedPage:
    """Keep the PyMuPDF document alive while exposing the page handle."""

    def __init__(self, document: object, page: object) -> None:
        self._document = document
        self._page = page

    def get_images(self, full: bool = False) -> list:
        return list(self._page.get_images(full=full))

    def get_drawings(self) -> list:
        return list(self._page.get_drawings())

def _dump_region(region: dict) -> str:
    import json

    return json.dumps(region, ensure_ascii=False)
