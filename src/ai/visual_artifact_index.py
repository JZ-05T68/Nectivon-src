"""Visual-artifact retrieval index (FAIL-016 ADR Option E-1).

The document reader already stores per-page visual reading artifacts
(``summary`` / ``keywords`` / ``key_facts``) on disk, but the lexical
``page_search`` FTS only indexes the three text layers. Chart-only pages
therefore could never be *found* by their in-chart entity words, which is
the root cause of the FAIL-016 blind spot and of the FAIL-018 silent
stale-value face (the superseding page's data-rich text was unreachable
for queries phrased with a different token form, e.g. ``2000小时`` vs
``2000h``).

This module closes exactly that gap, and nothing more:

- a separate, purely additive FTS5 table ``page_visual_index`` holds the
  normalized artifact text of every *fresh* page reading;
- :meth:`VisualArtifactIndex.rebuild` re-derives the whole table from the
  existing artifacts on disk (no model calls, idempotent, touches no user
  data and no other index);
- authority boundary: artifacts are a **retrieval aid**, never a source of
  truth. The index only helps *locate* a page; answers must still be read
  from the real page (text layer or vision) and citations keep pointing at
  the original page.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from src.agent_document_reader import AgentReadingStore
from src.database import Database, _tokenize_for_fts
from src.models import PageStatus, SearchResult
from src.page_image_text import agent_image_text, image_transcript
from src.text_utils import build_agent_page_text

LOGGER = logging.getLogger(__name__)

#: Maximum pages returned per visual-index lookup (bounded, additive merge).
VISUAL_INDEX_HIT_LIMIT = 4

_TOKENIZE_H_UNIT_RE = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*h(?![a-z0-9])")
_TEMP_RE = re.compile(r"℃")


def normalize_visual_text(text: str) -> str:
    """Normalize artifact text so token forms meet lexical queries halfway.

    ``2000h`` → ``2000 小时`` (the canonical Chinese query form), ``℃`` →
    ``°c``, casefolded. Deterministic and offline; applied identically at
    index time and query time.
    """

    text = _TEMP_RE.sub("°c", text)
    text = _TOKENIZE_H_UNIT_RE.sub(r"\1 小时", text)
    return " ".join(text.casefold().split())


def _artifact_text(reading) -> str:  # noqa: ANN001 - PageReading dataclass
    parts = [reading.summary or ""]
    parts.extend(reading.keywords or ())
    parts.extend(reading.key_facts or ())
    return " ".join(parts)


class VisualArtifactIndex:
    """FTS5 index over the per-page visual reading artifacts."""

    table_name = "page_visual_index"

    def __init__(self, database: Database, readings: AgentReadingStore) -> None:
        self._database = database
        self._readings = readings

    def _table_exists(self, connection) -> bool:  # noqa: ANN001
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (self.table_name,),
        ).fetchone()
        return row is not None

    def rebuild(self) -> dict[str, int]:
        """Rebuild the whole index from existing artifacts (idempotent).

        Only *fresh* readings (source-text hash still matches the page's
        current text layer) are indexed. Returns simple stats for the
        before/after record; never mutates anything besides its own table.
        """

        with self._database._connection() as connection:  # noqa: SLF001 - same package family
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS page_visual_index USING fts5("
                "visual_text, page_id UNINDEXED)"
            )
            connection.execute(f"DELETE FROM {self.table_name}")
            pages = connection.execute(
                """
                SELECT p.id, p.image_path, p.extracted_text, p.markdown_content
                FROM pages p JOIN documents d ON d.id = p.document_id
                """
            ).fetchall()
            indexed = 0
            skipped_stale = 0
            for row in pages:
                page_id = int(row["id"])
                reading = self._readings.page_reading(page_id)
                if reading is None:
                    continue
                source_text = build_agent_page_text(
                    extracted_text=str(row["extracted_text"] or ""),
                    ocr_text="",
                    manual_text=str(row["markdown_content"] or ""),
                )[0]
                if reading.transcript:
                    transcript = image_transcript(
                        self._readings.root, page_id, Path(str(row["image_path"])),
                    )
                    if not transcript:
                        skipped_stale += 1
                        continue
                    source_text = agent_image_text(transcript, str(row["markdown_content"] or ""))
                if not source_text:
                    skipped_stale += 1
                    continue
                current_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                if reading.source_text_sha256 != current_hash:
                    skipped_stale += 1
                    continue
                text = normalize_visual_text(_artifact_text(reading))
                # Same pre-segmentation contract as page_search: jieba tokens
                # joined by spaces, so CJK queries meet tokens halfway.
                text = _tokenize_for_fts(text)
                if not text:
                    continue
                connection.execute(
                    f"INSERT INTO {self.table_name}(visual_text, page_id) VALUES (?, ?)",
                    (text, page_id),
                )
                indexed += 1
            connection.commit()
        return {
            "pages_total": len(pages),
            "pages_indexed": indexed,
            "pages_skipped_stale_or_empty": skipped_stale,
        }

    def search(self, query: str, limit: int = VISUAL_INDEX_HIT_LIMIT) -> list[int]:
        """Return page_ids whose visual artifacts match the query tokens."""

        normalized = normalize_visual_text(query)
        terms = _tokenize_for_fts(normalized).split() if normalized else []
        if not terms or limit <= 0:
            return []
        # Same OR-of-terms contract as the lexical page_search surface.
        match_query = " OR ".join(f'"{token}"*' for token in terms)
        try:
            with self._database._connection() as connection:  # noqa: SLF001
                if not self._table_exists(connection):
                    return []
                rows = connection.execute(
                    f"SELECT page_id FROM {self.table_name} "
                    f"WHERE page_visual_index MATCH ? ORDER BY rank LIMIT ?",
                    (match_query, max(1, min(limit, 10))),
                ).fetchall()
        except Exception:  # noqa: BLE001 - retrieval aid must never break the tool
            LOGGER.warning("视觉阅读索引查询失败，按无命中继续", exc_info=True)
            return []
        return [int(row["page_id"]) for row in rows]

    def search_hits(self, query: str, limit: int = VISUAL_INDEX_HIT_LIMIT) -> list[SearchResult]:
        """Return citation-ready search results for artifact-matching pages."""

        page_ids = self.search(query, limit)
        if not page_ids:
            return []
        placeholders = ", ".join("?" for _ in page_ids)
        try:
            with self._database._connection() as connection:  # noqa: SLF001
                rows = connection.execute(
                    f"""
                    SELECT p.id AS page_id, p.document_id, p.page_number,
                        p.image_path, p.review_status AS page_status,
                        p.extracted_text, p.ocr_text, p.markdown_content,
                        p.updated_at, d.title AS document_title, d.filename,
                        d.source_path AS document_source_path, d.sha256 AS document_sha256
                    FROM pages p JOIN documents d ON d.id = p.document_id
                    WHERE p.id IN ({placeholders})
                    """,
                    page_ids,
                ).fetchall()
        except Exception:  # noqa: BLE001
            LOGGER.warning("视觉阅读索引命中页取数失败，按无命中继续", exc_info=True)
            return []
        by_id = {int(row["page_id"]): row for row in rows}
        hits: list[SearchResult] = []
        for page_id in page_ids:
            row = by_id.get(page_id)
            if row is None:
                continue
            hits.append(
                SearchResult(
                    page_id=page_id,
                    document_id=int(row["document_id"]),
                    document_title=str(row["document_title"]),
                    filename=str(row["filename"]),
                    page_number=int(row["page_number"]),
                    image_path=Path(str(row["image_path"])),
                    content="视觉阅读索引命中",
                    snippet="",
                    rank=0.0,
                    status=PageStatus(str(row["page_status"])),
                    match_type="视觉阅读索引",
                    document_source_path=Path(str(row["document_source_path"])),
                    document_sha256=str(row["document_sha256"]),
                    extracted_text=str(row["extracted_text"] or ""),
                    ocr_text="",
                    markdown_content=str(row["markdown_content"] or ""),
                )
            )
        return hits


def build_visual_artifact_index(
    database: Database, readings_root: Path | str
) -> VisualArtifactIndex:
    """Convenience factory used by the tool bootstrap."""

    return VisualArtifactIndex(database, AgentReadingStore(readings_root))
