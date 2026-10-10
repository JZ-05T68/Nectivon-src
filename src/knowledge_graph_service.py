"""Shared SQLite knowledge graph projection for learning and the star-map view.

Questions, knowledge objects, families, source anchors and mastery remain in
their original Nectivon tables. Explicit question-to-knowledge references and
confirmed subject metadata stay in SQLite; no browser vault is persisted.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.database import Database, DatabaseError
from src.knowledge_taxonomy import FEATURED_SUBJECTS, SECONDARY_SUBJECTS, subject_tag
from src.learning_workflow_service import MasteryService
from src.models import KnowledgeRelationType

NODE_KINDS = {"question": "题目", "knowledge": "知识点", "family": "归纳族",
              "page": "来源页", "document": "来源文档"}


@dataclass(frozen=True)
class KnowledgeGraph:
    """Bounded view of live entities, with explicit truncation information."""

    nodes: dict[str, dict[str, Any]]
    links: list[dict[str, str]]
    total: int
    shown: int

    def primary_subjects(self) -> tuple[str, ...]:
        """Return featured nebulae and every represented subject without a top-seven cap."""

        roots = {tag.split("/", 1)[0] for node in self.nodes.values() for tag in node["tags"]}
        return tuple(dict.fromkeys((*FEATURED_SUBJECTS, *sorted(roots))))

    def star_payload(self, *, subject: str | None = None) -> dict[str, Any]:
        """Adapt to star-vault's in-memory note contract; never write a vault."""

        notes = list(self.nodes.values())
        if subject is not None:
            if subject not in self.primary_subjects():
                raise ValueError("一级星云不存在，请重新选择。")
            notes = [dict(node, tags=[tag for tag in node["tags"]
                                     if tag.split("/", 1)[0] == subject])
                     for node in notes
                     if any(tag.split("/", 1)[0] == subject for tag in node["tags"])]
            children = tuple(dict.fromkeys((
                *(f"{subject}/{child}" for child in SECONDARY_SUBJECTS.get(subject, ())),
                *sorted({tag for node in notes for tag in node["tags"] if "/" in tag}),
            )))
            groups = [self._star_group(tag, tag.split("/", 1)[1]) for tag in children]
            groups.append(self._star_group(subject, "待细分"))
        else:
            groups = [self._star_group(root, root) for root in self.primary_subjects()]
        identifiers = {node["id"] for node in notes}
        links = [link for link in self.links
                 if link["source"] in identifiers and link["target"] in identifiers]
        return {
            "mode": "nectivon", "vaultName": (f"{subject} · 二级星云" if subject else
                                              "Nectivon 知识星图"),
            "config": {"constellations": {"groups": groups,
                                          "nested": "full" if subject else "top"}},
            "vault": {"vault": "Nectivon", "notes": notes,
                      "links": links,
                      "stats": {"notes": len(notes), "links": len(links),
                                "unresolved": 0}},
        }

    @staticmethod
    def _star_group(tag: str, name: str) -> dict[str, Any]:
        """Keep each group's colour stable across filtering and application reruns."""

        palette = ("#6FA2FF", "#FF9A62", "#B48CFF", "#58DCCB", "#FF7FA6", "#8FD17A",
                   "#F2D16B", "#7FD3FF", "#FF8A7A", "#C5A3FF", "#9BE0A8", "#E8B07E")
        index = int.from_bytes(hashlib.sha256(tag.encode("utf-8")).digest()[:2], "big")
        return {"id": f"g:{tag}", "name": name, "latin": "", "tags": [tag],
                "tint": palette[index % len(palette)]}


class KnowledgeGraphService:
    """Read current shared assets and persist only user-submitted cross references."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def link_question(self, question_id: int, knowledge_object_id: int, *, note: str = "") -> None:
        """Add an idempotent reference; never overwrite an existing annotation."""

        if len(note) > 1000:
            raise ValueError("关联说明不能超过 1000 个字符。")
        try:
            with self._database._connection() as connection:
                for table, identifier in (("question_items", question_id),
                                          ("knowledge_objects", knowledge_object_id)):
                    if connection.execute(
                        f"SELECT id FROM {table} WHERE id = ?", (identifier,)
                    ).fetchone() is None:
                        raise ValueError("关联的题目或知识点已不存在，请刷新后重选。")
                connection.execute(
                    "INSERT INTO question_knowledge_links "
                    "(question_id, knowledge_object_id, note, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(question_id, knowledge_object_id) DO NOTHING",
                    (question_id, knowledge_object_id, note.strip(), datetime.now(UTC).isoformat()),
                )
        except sqlite3.Error as exc:
            raise DatabaseError("保存题目与知识点的关联失败，请稍后重试。") from exc

    def snapshot(self, *, query: str = "", limit: int = 300) -> KnowledgeGraph:
        """Project live assets and their actual edges; do not infer semantic relations.

        The cap applies to primary learning/knowledge nodes. Their source nodes
        are included afterwards so a bounded view still preserves traceability.
        Filtering uses SQLite bound parameters; mastery is recomputed by the
        existing learning service only for the displayed questions.
        """

        if not 1 <= limit <= 1000:
            raise ValueError("星图节点上限必须在 1 到 1000 之间。")
        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, str]] = []
        query = query.strip()
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        tables = (
            ("question", "question_items", "stem_text", "stem_text", "1=1"),
            ("knowledge", "knowledge_objects", "title", "content", "lifecycle = 'active'"),
            ("family", "question_families", "title", "description", "status != 'retired'"),
        )
        with self._database._connection() as connection:
            total = 0
            candidates: list[tuple[str, sqlite3.Row]] = []
            # Equal per-kind allocation prevents a large question library hiding knowledge points.
            per_kind = max(1, limit // 3)
            for kind, table, title, content, condition in tables:
                where = condition + (f" AND ({title} LIKE ? ESCAPE '\\' OR "
                                     f"{content} LIKE ? ESCAPE '\\')" if query else "")
                params = (pattern, pattern) if query else ()
                total += connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}", params
                ).fetchone()[0]
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE {where} ORDER BY id DESC LIMIT ?",
                    (*params, per_kind),
                ).fetchall()
                candidates.extend((kind, row) for row in rows)
            for kind, row in candidates[:limit]:
                identifier = f"{kind}:{row['id']}"
                title = row["stem_text"] if kind == "question" else row["title"]
                if kind == "question":
                    title = f"{row['question_number'] or '题目'} · {title[:70] or '待补题干'}"
                summary = row["stem_text"] if kind == "question" else (
                    row["content"] if kind == "knowledge" else row["description"]
                )
                nodes[identifier] = self._node(identifier, kind, title, summary)
            shown = len(nodes)
            for kind, row in candidates[:limit]:
                identifier = f"{kind}:{row['id']}"
                if kind == "question":
                    self._source(connection, nodes, links, identifier, row["document_id"],
                                 row["page_id"])
                    if row["document_id"] is None:
                        nodes[identifier]["summary"] += (
                            "\n原始来源已删除；保留来源记录："
                            + (row["source_document_title_snapshot"] or "来源未记录")
                            + " " + (row["source_page_label_snapshot"] or "")
                        )
                elif kind == "knowledge":
                    sources = connection.execute(
                        "SELECT source_type, source_id FROM knowledge_object_sources "
                        "WHERE knowledge_object_id = ?", (row["id"],)
                    ).fetchall()
                    for source in sources:
                        source_type, source_id = source
                        if source_type == "document":
                            self._source(connection, nodes, links, identifier, source_id, None)
                        elif source_type == "page":
                            self._source(connection, nodes, links, identifier, None, source_id)
                        elif source_type in ("note", "evidence"):
                            table = "notes" if source_type == "note" else "evidence_items"
                            anchor = connection.execute(
                                f"SELECT document_id, page_id FROM {table} WHERE id = ?",
                                (source_id,),
                            ).fetchone()
                            if anchor:
                                self._source(connection, nodes, links, identifier, *anchor)
            for row in connection.execute("SELECT * FROM knowledge_relations"):
                self._edge(nodes, links, f"knowledge:{row['source_ko_id']}",
                           f"knowledge:{row['target_ko_id']}",
                           KnowledgeRelationType(row["relation_type"]).label, row["description"])
            for row in connection.execute("SELECT * FROM question_family_members"):
                self._edge(nodes, links, f"question:{row['question_id']}",
                           f"family:{row['family_id']}", "归纳成员",
                           {"member": "成员题", "variant": "变式题",
                            "counterexample": "反例题"}.get(row["relation"], ""))
            for row in connection.execute("SELECT * FROM question_knowledge_links"):
                self._edge(nodes, links, f"question:{row['question_id']}",
                           f"knowledge:{row['knowledge_object_id']}", "涉及知识点", row["note"])
            self._apply_subject_tags(connection, nodes)
        mastery = MasteryService(self._database)
        for identifier, node in nodes.items():
            if node["entity_kind"] == "question":
                node["mastery"] = mastery.mastery_states(int(identifier.split(":")[1]))
        return KnowledgeGraph(nodes, links, int(total), shown)

    @staticmethod
    def _node(identifier: str, kind: str, title: str, summary: str) -> dict[str, Any]:
        return {"id": identifier, "entity_kind": kind, "title": title, "summary": summary,
                "tags": [NODE_KINDS[kind]], "path": "", "meta": {}}

    @staticmethod
    def _apply_subject_tags(connection: sqlite3.Connection, nodes: dict[str, Any]) -> None:
        """按学科重排星云：tags 决定星图分组，未确定学科的节点进入「未归类」。

        题目直接取自身学科；知识点读取已保存的两级分类并继承关联题目的学科；
        归纳族取关联题目的学科并集；
        文档和页面承接引用它们的题目、知识点的学科并集。
        """

        question_subjects: dict[int, str] = {
            row["id"]: subject_tag(row["subject"])
            for row in connection.execute(
                "SELECT id, subject FROM question_items "
                "WHERE subject IS NOT NULL AND TRIM(subject) <> ''")
        }
        owner_subjects: dict[str, set[str]] = {
            f"question:{question_id}": {subject}
            for question_id, subject in question_subjects.items()
        }
        for row in connection.execute("SELECT * FROM knowledge_subject_classifications"):
            tag = row["subject"] + (f"/{row['subdiscipline']}" if row["subdiscipline"] else "")
            owner_subjects[f"knowledge:{row['knowledge_object_id']}"] = {tag}
        for row in connection.execute(
                "SELECT knowledge_object_id, question_id FROM question_knowledge_links"):
            subject = question_subjects.get(row["question_id"])
            if subject:
                owner_subjects.setdefault(
                    f"knowledge:{row['knowledge_object_id']}", set()).add(subject)
        for row in connection.execute(
                "SELECT family_id, question_id FROM question_family_members"):
            subject = question_subjects.get(row["question_id"])
            if subject:
                owner_subjects.setdefault(f"family:{row['family_id']}", set()).add(subject)
        for row in connection.execute(
                "SELECT id, document_id, page_id FROM question_items "
                "WHERE document_id IS NOT NULL OR page_id IS NOT NULL"):
            subject = question_subjects.get(row["id"])
            if not subject:
                continue
            if row["document_id"] is not None:
                owner_subjects.setdefault(
                    f"document:{row['document_id']}", set()).add(subject)
            if row["page_id"] is not None:
                owner_subjects.setdefault(f"page:{row['page_id']}", set()).add(subject)
        knowledge_subject_sets = {
            int(identifier.split(":")[1]): subjects
            for identifier, subjects in owner_subjects.items()
            if identifier.startswith("knowledge:")
        }
        for row in connection.execute(
                "SELECT s.knowledge_object_id, s.source_type, s.source_id, p.document_id "
                "FROM knowledge_object_sources s LEFT JOIN pages p "
                "ON s.source_type = 'page' AND p.id = s.source_id"):
            subjects = knowledge_subject_sets.get(row["knowledge_object_id"])
            if not subjects or row["source_type"] not in ("document", "page"):
                continue
            owner_subjects.setdefault(
                f"{row['source_type']}:{row['source_id']}", set()).update(subjects)
            if row["source_type"] == "page" and row["document_id"] is not None:
                owner_subjects.setdefault(f"document:{row['document_id']}", set()).update(subjects)
        for identifier, node in nodes.items():
            node["tags"] = sorted(owner_subjects.get(identifier, ()))

    @staticmethod
    def _edge(nodes: dict, links: list, source: str, target: str,
              label: str, note: str = "") -> None:
        if source in nodes and target in nodes:
            edge = {"source": source, "target": target, "label": label, "note": note}
            if edge not in links:
                links.append(edge)

    def _source(self, connection: sqlite3.Connection, nodes: dict, links: list,
                owner: str, document_id: int | None, page_id: int | None) -> None:
        page = connection.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if page:
            document_id = page["document_id"]
        document = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if document:
            doc_key = f"document:{document_id}"
            nodes[doc_key] = self._node(
                doc_key, "document", document["title"], document["filename"]
            )
            self._edge(nodes, links, owner, doc_key, "来自文档")
        if page:
            page_key = f"page:{page_id}"
            title = (document["title"] if document else "来源") + f" · 第 {page['page_number']} 页"
            nodes[page_key] = self._node(page_key, "page", title, page["extracted_text"] or "")
            self._edge(nodes, links, owner, page_key, "来自页面")
            if document:
                self._edge(nodes, links, page_key, doc_key, "所属文档")
