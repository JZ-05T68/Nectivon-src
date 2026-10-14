"""User-confirmed subject links for existing local knowledge, without copying content."""

from __future__ import annotations

from collections.abc import Sequence

from src.database import Database
from src.knowledge_taxonomy import normalize_classification


def validate_classifications(
    classifications: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Validate every parent/child pair before any durable write, then deduplicate."""

    for subject, child in classifications:
        if normalize_classification(subject, child) != (subject, child) or not subject:
            raise ValueError("学科或二级方向不在知识星图分类中，请重新选择。")
    return tuple(dict.fromkeys(classifications))


class KnowledgeSubjectService:
    """Append explicit subject metadata; existing classifications and content stay intact."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add_subjects(self, knowledge_object_id: int, subjects: Sequence[str]) -> int:
        """Save additional roots atomically, skipping roots already explicitly classified."""

        classifications = validate_classifications(tuple((subject, "") for subject in subjects))
        with self._database.knowledge_transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM knowledge_objects WHERE id = ?", (knowledge_object_id,),
            ).fetchone():
                raise ValueError("知识点已不存在，请刷新后重新选择。")
            existing = {row[0] for row in connection.execute(
                "SELECT subject FROM knowledge_subject_classifications "
                "WHERE knowledge_object_id = ?",
                (knowledge_object_id,),
            )}
            additions = [pair for pair in classifications if pair[0] not in existing]
            connection.executemany(
                "INSERT INTO knowledge_subject_classifications "
                "(knowledge_object_id, subject, subdiscipline) VALUES (?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                [(knowledge_object_id, subject, child) for subject, child in additions],
            )
        return len(additions)
