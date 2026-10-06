"""Recursive question structure and shared-context resolution (v0.8.6).

The learning workflow owns atomic leaves only.  This module owns the source
question tree around those leaves so a comprehensive parent can preserve its
materials, page/image references and display structure without receiving an
independent mastery state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.database import Database


class QuestionStructureError(ValueError):
    """Raised when a question tree would violate the granularity contract."""


@dataclass(frozen=True, slots=True)
class QuestionContext:
    """Inherited context resolved for one atomic question item."""

    node_id: int
    root_question_id: int
    context_text: str
    page_refs: tuple[int, ...]
    image_refs: tuple[str, ...]
    answer_refs: tuple[str, ...]


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    return []


def _load_json_list(value: object) -> list:
    """Decode one persisted JSON list without breaking legacy/corrupt rows."""

    try:
        return _json_list(json.loads(str(value or "[]")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


_FIRST_SUBQUESTION_MARKER = re.compile(
    r"(?m)(?:^|\n|(?<=[：:]))\s*"
    r"(?:\d{1,3}\s*[.．、:：]?\s*)?"
    r"(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)?"
    r"[（(]\s*[0-9一二三四五六七八九十]+\s*[）)]"
)
_QUESTION_METADATA_ONLY = re.compile(
    r"(?:\d{1,3}\s*[.．、:：]?\s*)?"
    r"(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)?"
)


def _shared_parent_prompt(prompt: str) -> str:
    """Return only a composite question's common instructions/material.

    Some extraction responses repeat every child stem in the parent prompt.
    Passing that block to each leaf makes a large question look unsplit and
    can contaminate per-leaf method analysis.  Text before the first numbered
    sub-question is the common context; every expression stays on its own
    atomic leaf.
    """

    clean_prompt = str(prompt or "").strip()
    marker = _FIRST_SUBQUESTION_MARKER.search(clean_prompt)
    shared = clean_prompt if marker is None else clean_prompt[: marker.start()].strip()
    # A question number/score is metadata, never a shared condition.  The
    # first child may follow it on the same line (e.g. 23.(8分)(1)若...).
    return "" if _QUESTION_METADATA_ONLY.fullmatch(shared) else shared


def _labelled_parent_prompt(label: str, prompt: str) -> str:
    """Prefix a parent label exactly once for student-facing context.

    OCR candidates are inconsistent: some prompts already begin with ``24.``
    while others start directly with the score/instruction.  The context
    resolver used to prepend the stored label unconditionally, producing
    confusing text such as ``24 24.（10分）``.
    """

    clean_label = str(label or "").strip()
    clean_prompt = str(prompt or "").strip()
    if clean_label and clean_prompt:
        duplicate_prefix = re.compile(
            rf"^{re.escape(clean_label)}(?:\s*[.．、:：]\s*|\s+(?=[（(]))"
        )
        clean_prompt = duplicate_prefix.sub("", clean_prompt, count=1).strip()
    return f"{clean_label}\n{clean_prompt}".strip()


def _candidate_payload(candidate: object) -> dict[str, object]:
    """Return stable, source-derived fields used by persistence/fingerprint."""

    payload = {
        "label": str(getattr(candidate, "number", "") or "").strip(),
        "prompt": str(getattr(candidate, "stem", "") or "").strip(),
        "kind": str(getattr(candidate, "question_kind", "atomic") or "atomic"),
        "shared_context_refs": list(
            getattr(candidate, "shared_context_refs", []) or []
        ),
        "page_refs": list(getattr(candidate, "page_refs", []) or []),
        "image_refs": list(getattr(candidate, "image_refs", []) or []),
        "answer_refs": list(getattr(candidate, "answer_refs", []) or []),
        "split_source": str(
            getattr(candidate, "split_source", "ai_inference") or "ai_inference"
        ),
        "split_confidence": str(
            getattr(candidate, "split_confidence", "low") or "low"
        ),
        "children": [
            _candidate_payload(child)
            for child in (getattr(candidate, "children", []) or [])
        ],
    }
    decision = getattr(candidate, "has_shared_stem", None)
    if type(decision) is bool:
        payload["has_shared_stem"] = decision
    return payload


def tree_fingerprint(candidate: object) -> str:
    """Stable identity for one extracted root tree, independent of DB ids."""

    canonical = json.dumps(
        _candidate_payload(candidate), ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def materialize_candidate_tree(
    database: Database,
    *,
    document_id: int,
    source_page_id: int,
    root_candidate: object,
) -> dict[str, int]:
    """Persist one candidate tree idempotently and return ``path -> node id``.

    The root is inserted first and then points to itself through
    ``root_question_id``.  Descendants reference their immediate parent and
    the same root.  Composite nodes can never link to ``question_items``.
    """

    fingerprint = tree_fingerprint(root_candidate)
    with database._connection() as connection:  # noqa: SLF001
        existing = connection.execute(
            """
            SELECT node_path, id FROM question_nodes
            WHERE document_id = ? AND source_page_id = ?
              AND tree_fingerprint = ?
            """,
            (document_id, source_page_id, fingerprint),
        ).fetchall()
        if existing:
            return {str(row["node_path"]): int(row["id"]) for row in existing}

        mapping: dict[str, int] = {}

        def insert_node(
            candidate: object,
            *,
            path: str,
            parent_id: int | None,
            root_id: int | None,
            level: int,
            order: int,
        ) -> int:
            children = list(getattr(candidate, "children", []) or [])
            kind = str(getattr(candidate, "question_kind", "atomic") or "atomic")
            is_leaf = not children
            if kind not in ("atomic", "composite"):
                raise QuestionStructureError(f"题目粒度无效：{kind}")
            if children and kind != "composite":
                raise QuestionStructureError("含子题的节点必须是 composite。")
            if not children and kind != "atomic":
                raise QuestionStructureError("无子题的叶子节点必须是 atomic。")
            split_source = str(
                getattr(candidate, "split_source", "ai_inference")
                or "ai_inference"
            )
            split_confidence = str(
                getattr(candidate, "split_confidence", "low") or "low"
            )
            if split_source not in (
                "explicit_numbering", "layout", "ai_inference", "manual"
            ):
                split_source = "ai_inference"
            if split_confidence not in ("high", "medium", "low"):
                split_confidence = "low"
            cursor = connection.execute(
                """
                INSERT INTO question_nodes(
                    document_id, source_page_id, parent_question_id,
                    root_question_id, tree_fingerprint, node_path,
                    question_label, question_level, question_kind,
                    local_prompt, shared_context_refs, page_refs, image_refs,
                    answer_refs, display_order, is_leaf, split_source,
                    split_confidence, has_shared_stem, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, 'ai_draft', datetime('now'), datetime('now'))
                """,
                (
                    document_id,
                    source_page_id,
                    parent_id,
                    root_id,
                    fingerprint,
                    path,
                    str(getattr(candidate, "number", "") or "").strip(),
                    level,
                    kind,
                    str(getattr(candidate, "stem", "") or "").strip(),
                    json.dumps(
                        list(getattr(candidate, "shared_context_refs", []) or []),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        list(getattr(candidate, "page_refs", []) or [])
                        or [source_page_id]
                    ),
                    json.dumps(
                        list(getattr(candidate, "image_refs", []) or []),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        list(getattr(candidate, "answer_refs", []) or []),
                        ensure_ascii=False,
                    ),
                    order,
                    1 if is_leaf else 0,
                    split_source,
                    split_confidence,
                    getattr(candidate, "has_shared_stem", None),
                ),
            )
            node_id = int(cursor.lastrowid)
            effective_root = root_id or node_id
            if root_id is None:
                connection.execute(
                    "UPDATE question_nodes SET root_question_id = ? WHERE id = ?",
                    (node_id, node_id),
                )
            mapping[path] = node_id
            for child_order, child in enumerate(children):
                insert_node(
                    child,
                    path=f"{path}.{child_order}",
                    parent_id=node_id,
                    root_id=effective_root,
                    level=level + 1,
                    order=child_order,
                )
            return node_id

        insert_node(
            root_candidate,
            path="0",
            parent_id=None,
            root_id=None,
            level=0,
            order=0,
        )
        return mapping


def link_atomic_question(
    database: Database,
    *,
    node_id: int,
    question_item_id: int,
) -> None:
    """Link a learning item to an atomic leaf; composite linkage is refused."""

    with database._connection() as connection:  # noqa: SLF001
        node = connection.execute(
            "SELECT question_kind, is_leaf FROM question_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if node is None:
            raise QuestionStructureError(f"题目节点不存在：{node_id}")
        if str(node["question_kind"]) != "atomic" or int(node["is_leaf"]) != 1:
            raise QuestionStructureError("只有 atomic 叶子可以进入学习整理。")
        connection.execute(
            """
            UPDATE question_nodes
            SET question_item_id = ?, status = 'user_confirmed',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (question_item_id, node_id),
        )


def context_for_question_item(
    database: Database, question_item_id: int
) -> QuestionContext | None:
    """Resolve composite ancestor context and inherited source references."""

    with database._connection() as connection:  # noqa: SLF001
        leaf = connection.execute(
            "SELECT * FROM question_nodes WHERE question_item_id = ?",
            (question_item_id,),
        ).fetchone()
        if leaf is None:
            return None
        ancestors = connection.execute(
            """
            WITH RECURSIVE ancestry(id, parent_question_id, question_level,
                                    question_kind, question_label, local_prompt,
                                    page_refs, image_refs, answer_refs, has_shared_stem) AS (
                SELECT id, parent_question_id, question_level, question_kind,
                       question_label, local_prompt, page_refs, image_refs,
                       answer_refs, has_shared_stem
                FROM question_nodes WHERE id = ?
                UNION ALL
                SELECT n.id, n.parent_question_id, n.question_level,
                       n.question_kind, n.question_label, n.local_prompt,
                       n.page_refs, n.image_refs, n.answer_refs, n.has_shared_stem
                FROM question_nodes n JOIN ancestry a
                  ON n.id = a.parent_question_id
            )
            SELECT * FROM ancestry ORDER BY question_level
            """,
            (int(leaf["id"]),),
        ).fetchall()
    parent_rows = [row for row in ancestors if int(row["id"]) != int(leaf["id"])]
    text_parts = []
    for row in parent_rows:
        prompt = str(row["local_prompt"] or "").strip()
        if str(row["question_kind"]) == "composite":
            decision = row["has_shared_stem"]
            if decision == 0:
                prompt = ""
            elif decision is None:
                prompt = _shared_parent_prompt(prompt)
        if prompt:
            label = str(row["question_label"] or "").strip()
            text_parts.append(_labelled_parent_prompt(label, prompt))
    page_refs: list[int] = []
    image_refs: list[str] = []
    answer_refs: list[str] = []
    for row in ancestors:
        page_refs.extend(
            int(value) for value in _load_json_list(row["page_refs"])
            if isinstance(value, int)
        )
        image_refs.extend(
            str(value) for value in _load_json_list(row["image_refs"])
            if str(value).strip()
        )
        answer_refs.extend(
            str(value) for value in _load_json_list(row["answer_refs"])
            if str(value).strip()
        )
    return QuestionContext(
        node_id=int(leaf["id"]),
        root_question_id=int(leaf["root_question_id"]),
        context_text="\n\n".join(text_parts),
        page_refs=tuple(_unique(page_refs)),
        image_refs=tuple(_unique(image_refs)),
        answer_refs=tuple(_unique(answer_refs)),
    )


def tree_overview(database: Database, root_question_id: int) -> dict[str, object]:
    """Return a parent overview aggregated strictly from leaf evidence.

    There is intentionally no parent mastery row.  Counts are computed from
    linked atomic leaves and their mastery evidence only.
    """

    with database._connection() as connection:  # noqa: SLF001
        nodes = connection.execute(
            """
            SELECT n.*, q.status AS learning_status
            FROM question_nodes n
            LEFT JOIN question_items q ON q.id = n.question_item_id
            WHERE n.root_question_id = ?
            ORDER BY n.question_level, n.display_order, n.id
            """,
            (root_question_id,),
        ).fetchall()
        leaves = [row for row in nodes if int(row["is_leaf"]) == 1]
        mastered = 0
        for row in leaves:
            item_id = row["question_item_id"]
            if item_id is None:
                continue
            evidence = connection.execute(
                """
                SELECT 1 FROM mastery_evidence
                WHERE question_id = ? AND result = 'correct'
                LIMIT 1
                """,
                (int(item_id),),
            ).fetchone()
            if evidence is not None:
                mastered += 1
    return {
        "root_question_id": root_question_id,
        "nodes": [dict(row) for row in nodes],
        "leaf_count": len(leaves),
        "joined_count": sum(row["question_item_id"] is not None for row in leaves),
        "mastered_count": mastered,
    }
