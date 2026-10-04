"""Fail-closed provenance boundary for targeted training questions.

Only questions traceable to an imported document, page, and individual item
may enter the local training UI.  A URL that merely looks like the local reader
is not enough: it must be relative and agree with the stored identifiers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.question_source_models import VerifiedQuestion


def is_locatable_local_training_question(
    question: VerifiedQuestion | Mapping[str, Any],
) -> bool:
    """Return whether a training question points to its imported original item.

    This is a policy check, not a replacement for the database/file integrity
    verification performed when local candidates are retrieved.
    """

    def field(name: str) -> Any:
        if isinstance(question, Mapping):
            return question.get(name)
        return getattr(question, name, None)

    if field("verification_status") != "verified":
        return False
    if not all(
        isinstance(field(name), str) and field(name).strip()
        for name in ("question_text", "source_name", "subject")
    ):
        return False

    identifiers = ("document_id", "page_id", "page_number", "question_item_id")
    if any(type(field(name)) is not int or field(name) <= 0 for name in identifiers):
        return False

    source_url = field("source_url")
    if not isinstance(source_url, str):
        return False
    try:
        parsed = urlsplit(source_url)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False

    return (
        not parsed.scheme
        and not parsed.netloc
        and parsed.path == "pages/3_浏览资料.py"
        and query == {
            "document": [str(field("document_id"))],
            "page": [str(field("page_number"))],
        }
        and parsed.fragment == f"q_{field('question_item_id')}"
    )


def is_local_training_result_payload(payload: object) -> bool:
    """Reject stale or malformed browser-cached retrieval results before display."""
    if not isinstance(payload, Mapping):
        return False
    required = ("training_type", "target", "total_candidates", "rejected_count")
    if any(name not in payload for name in required):
        return False
    questions = payload.get("questions")
    verified_count = payload.get("verified_count")
    if not isinstance(questions, list) or type(verified_count) is not int:
        return False
    if verified_count != len(questions):
        return False
    question_fields = (
        "id", "question_text", "source_name", "source_url",
        "exam_or_contest_name", "subject", "applicable_scope",
    )
    return all(
        isinstance(question, Mapping)
        and all(name in question for name in question_fields)
        and is_locatable_local_training_question(question)
        for question in questions
    )
