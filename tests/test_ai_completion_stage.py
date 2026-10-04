"""Completion-stage context is vendor-neutral and cannot leak across calls."""

from src.ai.completion_stage import (
    CompletionStage,
    completion_stage_scope,
    current_completion_stage,
    is_length_truncated,
)


def test_stage_scope_is_nested_and_restored() -> None:
    assert current_completion_stage() is None
    with completion_stage_scope(CompletionStage.AGENT_DECISION):
        assert current_completion_stage() is CompletionStage.AGENT_DECISION
        with completion_stage_scope(CompletionStage.FINAL_ANSWER):
            assert current_completion_stage() is CompletionStage.FINAL_ANSWER
        assert current_completion_stage() is CompletionStage.AGENT_DECISION
    assert current_completion_stage() is None


def test_only_explicit_length_finish_reason_is_truncated() -> None:
    assert is_length_truncated("length")
    assert is_length_truncated(" LENGTH ")
    assert not is_length_truncated("stop")
    assert not is_length_truncated(None)
