"""Vendor-neutral completion-stage call context.

Product layers identify why a completion is being requested without knowing
which provider will execute it.  Adapters may then apply documented,
provider-specific wire policies while the public CompletionProvider protocol
and existing injected test providers remain backward compatible.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

__all__ = [
    "CompletionStage",
    "completion_stage_scope",
    "current_completion_stage",
    "is_length_truncated",
]


class CompletionStage(StrEnum):
    """Closed product stages whose output requirements differ."""

    CONNECTION_TEST = "connection_test"
    AGENT_DECISION = "agent_decision"
    FINAL_ANSWER = "final_answer"
    # Page-by-page document reading asks for one strict JSON object per page.
    # Like Decision/Final Answer it demands grounded, bounded output, so
    # adapters may disable thinking to keep the whole output budget for the
    # JSON payload itself (V086-203 fix).
    PAGE_READING = "page_reading"
    # Learning draft generation (first layer / wings / correction / review)
    # also asks for one bounded JSON object per call.  Overnight real-corpus
    # round (2026-09-27): with thinking enabled the structured draft calls
    # exceeded the 30 s transport cap three times in a row (V086-203 family).
    LEARNING_DRAFT = "learning_draft"


_CURRENT_STAGE: ContextVar[CompletionStage | None] = ContextVar(
    "completion_stage", default=None
)


@contextmanager
def completion_stage_scope(stage: CompletionStage) -> Iterator[None]:
    """Set one stage for the duration of a synchronous completion call."""

    token = _CURRENT_STAGE.set(stage)
    try:
        yield
    finally:
        _CURRENT_STAGE.reset(token)


def current_completion_stage() -> CompletionStage | None:
    """Return the stage active in this execution context, if any."""

    return _CURRENT_STAGE.get()


def is_length_truncated(finish_reason: str | None) -> bool:
    """Return whether a provider explicitly reported output truncation."""

    return bool(finish_reason and finish_reason.strip().casefold() == "length")
