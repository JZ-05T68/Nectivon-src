"""Document relation service — G3-A P0 (question paper ↔ answers ↔ rubrics).

Comprehensive-question workflows need explicit relations between a question
paper and its reference-answer / scoring-standard documents.  Relations are
NEVER auto-permanently confirmed from similar filenames (G3-A §23): the AI
may only *suggest* (``ai_suggested``), and a user (or high-confidence
metadata flow) confirms (``confirmed``) or rejects (``rejected``).

A reference answer is not ground truth by default (G3-A §25): the relation
only records the pairing, never overwrites student answers or teacher
annotations (§72/§73).
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from src.database import Database

RelationKind = Literal["reference_answer", "scoring_standard", "answer", "other"]
RelationStatus = Literal["ai_suggested", "confirmed", "rejected"]

_VALID_KINDS = {"reference_answer", "scoring_standard", "answer", "other"}
_VALID_STATUSES = {"ai_suggested", "confirmed", "rejected"}


class DocumentRelationError(ValueError):
    """Raised for invalid relation input or unknown relation ids."""


def suggest_document_relation(
    database: Database,
    *,
    primary_document_id: int,
    related_document_id: int,
    relation_kind: RelationKind,
    provenance: str = "ai_suggested",
) -> int:
    """Create (or reuse) an ``ai_suggested`` relation between two documents."""

    if primary_document_id == related_document_id:
        raise DocumentRelationError("文档不能与自身建立关系。")
    if relation_kind not in _VALID_KINDS:
        raise DocumentRelationError(f"未知的关系类型：{relation_kind}")
    with database._connection() as connection:  # noqa: SLF001 - service layer
        existing = connection.execute(
            """
            SELECT id FROM document_relations
            WHERE primary_document_id = ? AND related_document_id = ?
              AND relation_kind = ?
            """,
            (primary_document_id, related_document_id, relation_kind),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO document_relations(
                primary_document_id, related_document_id, relation_kind,
                status, provenance, created_at, updated_at
            ) VALUES (?, ?, ?, 'ai_suggested', ?, datetime('now'), datetime('now'))
            """,
            (primary_document_id, related_document_id, relation_kind, provenance),
        )
        return int(cursor.lastrowid)


def confirm_document_relation(
    database: Database, relation_id: int, *, confirmer: str = "user"
) -> None:
    """Mark a suggested relation as user-confirmed (persists across restarts)."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE document_relations
            SET status = 'confirmed', provenance = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (f"confirmed_by_{confirmer}", relation_id),
        )
        if cursor.rowcount == 0:
            raise DocumentRelationError(f"关系不存在：{relation_id}")


def reject_document_relation(database: Database, relation_id: int) -> None:
    """Reject an AI-suggested relation; it stays visible as rejected history."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE document_relations
            SET status = 'rejected', updated_at = datetime('now')
            WHERE id = ?
            """
            ,
            (relation_id,),
        )
        if cursor.rowcount == 0:
            raise DocumentRelationError(f"关系不存在：{relation_id}")


def list_document_relations(
    database: Database,
    document_id: int,
    *,
    include_rejected: bool = False,
) -> list[dict[str, object]]:
    """List relations where the document is the primary or the related side."""

    status_filter = "" if include_rejected else "AND status != 'rejected'"
    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT dr.id,
                   dr.primary_document_id,
                   dr.related_document_id,
                   dr.relation_kind,
                   dr.status,
                   dr.provenance,
                   pd.title AS primary_title,
                   rd.title AS related_title
            FROM document_relations dr
            LEFT JOIN documents pd ON pd.id = dr.primary_document_id
            LEFT JOIN documents rd ON rd.id = dr.related_document_id
            WHERE (dr.primary_document_id = ? OR dr.related_document_id = ?)
              {status_filter}
            ORDER BY dr.id
            """,
            (document_id, document_id),
        ).fetchall()
        return [dict(row) for row in rows]
