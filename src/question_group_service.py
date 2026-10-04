"""Question group service — G3-B P0 (comprehensive question as ONE unit).

Round 2 (G3-A) proved the splitter cut ``23(1)…23(4)`` into isolated
questions.  This service makes the QUESTION GROUP a first-class object the
user can draft, confirm, and bind evidence to — while keeping every
anti-over-binding guarantee:

* A group draft may only be *suggested*; confirmation is a user action.
* Subquestion evidence subsets are NEVER auto-filled with "every material
  of the group" — there is deliberately no such API.  Evidence rows are
  created per (subquestion, material) pair and stay ``ai_draft`` until the
  user confirms.
* Materials stay ZERO-COPY: a group material references ``page_id`` /
  ``source_id``; nothing duplicates page content.

Handwriting regions live here too (P1): page-level vision drafts are
upgraded to region-level structure with ``identity_draft`` defaulting to
UNKNOWN.  The service exposes NO path for AI to set student/teacher
identity — only :func:`set_region_identity` (a user action) can.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from src.database import Database


class QuestionGroupError(ValueError):
    """Raised for invalid group / evidence / region operations."""


@dataclass(frozen=True)
class GroupDraft:
    """AI/user draft of one comprehensive question group."""

    document_id: int
    group_number: str
    page_ids: list[int]
    subquestion_numbers: list[str]
    material_notes: str = ""


def draft_group(
    database: Database,
    *,
    document_id: int,
    group_number: str,
    page_ids: list[int],
    subquestion_numbers: list[str],
    material_notes: str = "",
    group_type: str = "comprehensive",
) -> int:
    """Create an ``ai_draft`` group (never auto-merged into questions).

    ``group_type`` unifies choice and comprehensive question groups in the
    ONE question_groups model (F3): 'choice' groups share material across
    printed sub-questions; 'comprehensive' keeps the G3-B semantics.
    """

    if not group_number.strip():
        raise QuestionGroupError("题组号不能为空（原文没有就写页内大题号）。")
    if not subquestion_numbers:
        raise QuestionGroupError("题组至少要有一个小问。")
    if group_type not in ("choice", "comprehensive", "other"):
        raise QuestionGroupError("题组类型必须是 choice/comprehensive/other。")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            INSERT INTO question_groups(
                document_id, group_number, group_summary, status, provenance,
                source_pages, group_type, created_at, updated_at
            ) VALUES (?, ?, ?, 'ai_draft', 'ai_draft', ?, ?, datetime('now'),
                      datetime('now'))
            """,
            (
                document_id,
                group_number.strip(),
                f"综合题草稿：小问 {'、'.join(subquestion_numbers)}"
                if group_type == "comprehensive"
                else f"选择题组草稿：小题 {'、'.join(subquestion_numbers)}",
                json.dumps(page_ids),
                group_type,
            ),
        )
        return int(cursor.lastrowid)


def confirm_group(database: Database, group_id: int) -> None:
    """User confirms the group; identity/provenance records WHO confirmed."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE question_groups
            SET status = 'user_confirmed', provenance = 'user_confirmed',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (group_id,),
        )
        if cursor.rowcount == 0:
            raise QuestionGroupError(f"题组不存在：{group_id}")


def dissolve_group(database: Database, group_id: int) -> None:
    """USER action: undo a confirmed group entirely (F6-05).

    A wrong confirmation must never be a dead end.  Dissolving removes
    the GROUP-level structure only:

    * subquestion rows keep existing — their ``group_id`` link is cleared;
    * the group's shared-material rows are removed, together with the
      per-subquestion evidence rows that referenced them (those bindings
      become meaningless without the group);
    * the group row itself is deleted.

    Everything lives in ONE transaction — a half-dissolved group would be
    worse than no dissolution at all.
    """

    with database._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT id FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            raise QuestionGroupError(f"题组不存在：{group_id}")
        connection.execute(
            """
            DELETE FROM question_item_evidence
            WHERE source_id IN (
                SELECT id FROM question_group_materials WHERE group_id = ?
            )
            """,
            (group_id,),
        )
        connection.execute(
            "DELETE FROM question_group_materials WHERE group_id = ?",
            (group_id,),
        )
        connection.execute(
            """
            UPDATE question_items SET group_id = NULL, updated_at =
                datetime('now') WHERE group_id = ?
            """,
            (group_id,),
        )
        connection.execute(
            "DELETE FROM question_groups WHERE id = ?", (group_id,)
        )


def add_group_material(
    database: Database,
    *,
    group_id: int,
    material_kind: str,
    material_label: str,
    page_id: int | None,
    content_text: str = "",
) -> int:
    """Add ONE shared material/figure to the group (zero-copy page ref)."""

    if material_kind not in ("text_material", "figure"):
        raise QuestionGroupError("材料类型必须是 text_material 或 figure。")
    with database._connection() as connection:  # noqa: SLF001
        group = connection.execute(
            "SELECT id FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None:
            raise QuestionGroupError(f"题组不存在：{group_id}")
        cursor = connection.execute(
            """
            INSERT INTO question_group_materials(
                group_id, material_kind, material_label, content_text,
                page_id, created_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (group_id, material_kind, material_label, content_text, page_id),
        )
        return int(cursor.lastrowid)


def attach_question_to_group(
    database: Database, question_item_id: int, group_id: int
) -> None:
    """Link one subquestion row to its parent group (parent link P0)."""

    with database._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT document_id FROM question_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            raise QuestionGroupError(f"题组不存在：{group_id}")
        connection.execute(
            "UPDATE question_items SET group_id = ?, updated_at ="
            " datetime('now') WHERE id = ?",
            (group_id, question_item_id),
        )


def latest_group_for_page(database: Database, page_id: int) -> dict | None:
    """Most recent user-confirmed group whose source_pages include the page."""

    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM question_groups
            WHERE status = 'user_confirmed'
            ORDER BY id DESC
            """
        ).fetchall()
    for row in rows:
        try:
            pages = json.loads(row["source_pages"] or "[]")
        except (TypeError, ValueError):
            pages = []
        if page_id in pages:
            return dict(row)
    return None


def set_question_evidence(
    database: Database,
    *,
    question_item_id: int,
    evidence_type: str,
    source_id: int | None,
    page_id: int | None,
    status: str = "ai_draft",
    confidence: str = "uncertain",
) -> int:
    """Bind ONE evidence reference to ONE subquestion (never bulk)."""

    if evidence_type not in ("text_material", "figure", "page_region"):
        raise QuestionGroupError(
            "证据类型必须是 text_material / figure / page_region。"
        )
    if status not in ("ai_draft", "user_confirmed", "rejected"):
        raise QuestionGroupError("证据状态必须是 ai_draft/user_confirmed/rejected。")
    if source_id is None and page_id is None:
        raise QuestionGroupError("证据必须引用材料或页面（禁止空绑定）。")
    with database._connection() as connection:  # noqa: SLF001
        existing = connection.execute(
            """
            SELECT id FROM question_item_evidence
            WHERE question_item_id = ? AND evidence_type = ? AND
                  (source_id IS ? OR (source_id IS NULL AND ? IS NULL))
            """,
            (question_item_id, evidence_type, source_id, source_id),
        ).fetchone()
        if existing is not None:
            connection.execute(
                """
                UPDATE question_item_evidence
                SET status = ?, confidence = ?, page_id = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (status, confidence, page_id, existing["id"]),
            )
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO question_item_evidence(
                question_item_id, evidence_type, source_id, page_id,
                confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (question_item_id, evidence_type, source_id, page_id, confidence, status),
        )
        return int(cursor.lastrowid)


def confirm_question_evidence(
    database: Database, question_item_id: int, evidence_id: int
) -> None:
    """User confirms ONE evidence row belongs to ONE subquestion."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE question_item_evidence
            SET status = 'user_confirmed', confidence = 'confirmed',
                updated_at = datetime('now')
            WHERE id = ? AND question_item_id = ?
            """,
            (evidence_id, question_item_id),
        )
        if cursor.rowcount == 0:
            raise QuestionGroupError(
                f"证据行不存在或不属于该小问：{evidence_id}"
            )


def question_evidence(database: Database, question_item_id: int) -> list[dict]:
    """Evidence subset of ONE subquestion (the answer to 「为什么第(2)问需要看这张图？」)."""

    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT qie.id, qie.evidence_type, qie.source_id, qie.page_id,
                   qie.status, qie.confidence,
                   qgm.material_label, qgm.material_kind
            FROM question_item_evidence qie
            LEFT JOIN question_group_materials qgm
                   ON qgm.id = qie.source_id
            WHERE qie.question_item_id = ?
            ORDER BY qie.id
            """,
            (question_item_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def group_children(database: Database, group_id: int) -> dict:
    """Group card payload: identity + materials + subquestions."""

    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        group = connection.execute(
            "SELECT * FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None:
            raise QuestionGroupError(f"题组不存在：{group_id}")
        materials = connection.execute(
            "SELECT * FROM question_group_materials WHERE group_id = ?"
            " ORDER BY id",
            (group_id,),
        ).fetchall()
        subquestions = connection.execute(
            """
            SELECT id, question_number, question_kind, stem_text, status,
                   stem_confidence
            FROM question_items WHERE group_id = ? ORDER BY id
            """,
            (group_id,),
        ).fetchall()
    return {
        "group": dict(group),
        "materials": [dict(m) for m in materials],
        "subquestions": [dict(s) for s in subquestions],
    }


def groups_for_document(database: Database, document_id: int) -> list[dict]:
    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM question_groups WHERE document_id = ? ORDER BY id",
            (document_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_group_source_pages(database: Database, group: dict) -> dict:
    """Resolve ``source_pages`` page_ids into document-local page numbers.

    F6 P0 contract fix: ``question_groups.source_pages`` stores GLOBAL
    ``pages.id`` values (that is what :func:`draft_group` writes), but the
    UI must show and jump by DOCUMENT-LOCAL ``pages.page_number`` — showing
    "第 784 页" leaks an internal id and jumping with it lands on the wrong
    (or first) page.

    Returns ``{"resolved": [{"page_id", "page_number"}, ...],
    "missing": [page_id, ...]}`` where ``resolved`` is deduplicated and
    ordered by ``page_number``. Orphan ids (page deleted / foreign document)
    go to ``missing`` and stay visible to the user — silently dropping them
    would make a dead button the user cannot explain.
    """

    try:
        page_ids = json.loads(group.get("source_pages") or "[]")
    except (TypeError, ValueError):
        page_ids = []
    if not isinstance(page_ids, list):
        page_ids = []
    page_ids = [int(value) for value in page_ids if isinstance(value, int)]
    if not page_ids:
        return {"resolved": [], "missing": []}
    document_id = int(group.get("document_id") or 0)
    placeholders = ",".join("?" for _ in page_ids)
    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, page_number FROM pages WHERE id IN "
            f"({placeholders}) AND document_id = ?",
            (*page_ids, document_id),
        ).fetchall()
    number_by_id = {int(row["id"]): int(row["page_number"]) for row in rows}
    seen: set[int] = set()
    resolved: list[dict] = []
    for page_id in page_ids:
        page_number = number_by_id.get(page_id)
        if page_number is None or page_id in seen:
            continue
        seen.add(page_id)
        resolved.append({"page_id": page_id, "page_number": page_number})
    resolved.sort(key=lambda item: item["page_number"])
    missing = [pid for pid in page_ids if pid not in number_by_id]
    return {"resolved": resolved, "missing": missing}


def group_member_numbers(group: dict) -> set[str] | None:
    """Parse the printed member numbers of a choice group (F-BOSS-02).

    Choice groups carry ``group_number`` in the detector's verbatim shape
    ``"{first}~{last}题组"``.  Returns the member number set, or ``None``
    when the shape cannot be parsed — callers must treat ``None`` as
    "cannot prove membership" and NOT auto-attach (宁可漏归，不可错归:
    the previous unconditional attach pulled same-page strays — e.g. the
    leftover previous question #12 — into a 3~5 group).
    """

    import re as _re

    match = _re.match(
        r"^(\d{1,3})\s*[～~]\s*(\d{1,3})题组$",
        str(group.get("group_number") or "").strip(),
    )
    if not match:
        return None
    first, last = int(match.group(1)), int(match.group(2))
    if last < first or last - first > 30:
        return None
    return {str(n) for n in range(first, last + 1)}


def normalize_candidate_number(number: str) -> str | None:
    """'3' / '3.' / '3、' / ' 3 ' -> '3'; non-numeric -> None."""

    import re as _re

    match = _re.match(r"^\s*(\d{1,3})", str(number or ""))
    return match.group(1) if match else None


# --- handwriting regions -----------------------------------------------------


def add_handwriting_region(
    database: Database,
    *,
    page_id: int,
    text: str,
    interpretation_id: int | None = None,
    bbox: list[float] | None = None,
    ink_color: str = "unknown",
    confidence: str = "uncertain",
) -> int:
    """Create a region row; identity stays UNKNOWN until the user says."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            INSERT INTO handwriting_regions(
                page_id, interpretation_id, bbox, text, ink_color,
                identity_draft, identity_status, confidence,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'unknown', 'ai_draft', ?,
                      datetime('now'), datetime('now'))
            """,
            (
                page_id,
                interpretation_id,
                json.dumps(bbox) if bbox else None,
                text,
                ink_color if ink_color in ("red", "blue", "black", "unknown") else "unknown",
                confidence,
            ),
        )
        return int(cursor.lastrowid)


def set_region_identity(
    database: Database,
    region_id: int,
    identity: str,
) -> None:
    """USER action: set a region's identity (student/teacher). AI never may."""

    if identity not in ("student", "teacher"):
        raise QuestionGroupError("身份只能由用户设为 student 或 teacher。")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE handwriting_regions
            SET identity_draft = ?, identity_status = 'user_confirmed',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (identity, region_id),
        )
        if cursor.rowcount == 0:
            raise QuestionGroupError(f"手写区域不存在：{region_id}")


def set_region_target(
    database: Database, region_id: int, question_item_id: int | None
) -> None:
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            UPDATE handwriting_regions
            SET target_subquestion = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (question_item_id, region_id),
        )
        if cursor.rowcount == 0:
            raise QuestionGroupError(f"手写区域不存在：{region_id}")


def page_handwriting_regions(database: Database, page_id: int) -> list[dict]:
    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT hr.id, hr.page_id, hr.bbox, hr.text, hr.ink_color,
                   hr.identity_draft, hr.identity_status,
                   hr.target_subquestion, hr.confidence,
                   qi.question_number AS target_number
            FROM handwriting_regions hr
            LEFT JOIN question_items qi ON qi.id = hr.target_subquestion
            WHERE hr.page_id = ?
            ORDER BY hr.id
            """,
            (page_id,),
        ).fetchall()
        return [dict(row) for row in rows]
