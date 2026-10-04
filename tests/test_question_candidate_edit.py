"""Geography G1: user edits & manual add on question candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateError,
    QuestionCandidateStore,
    iter_atomic_leaves,
)


def _store(tmp_path: Path) -> QuestionCandidateStore:
    return QuestionCandidateStore(tmp_path / "question-candidates")


def _seed(store: QuestionCandidateStore, page_id: int = 1) -> None:
    store.save_page_candidates(
        page_id,
        [
            QuestionCandidate(
                number="12",
                stem="读长江流域示意图，回答问题。",
                completeness="complete",
                figure_refs=["图 1"],
                status="added",
            ),
            QuestionCandidate(
                number="13",
                stem="（未识别到题干）",
                completeness="incomplete",
                incomplete_reason="题干被截断",
            ),
        ],
    )


def test_update_candidate_edits_fields_and_marks_user_edited(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.update_candidate(
        1, "13", new_number="13-1", stem="根据图 1 判断甲地地形类型。"
    )
    candidates = store.page_candidates(1)
    by_number = {c.number: c for c in candidates}
    assert "13-1" in by_number and "13" not in by_number
    edited = by_number["13-1"]
    assert edited.stem == "根据图 1 判断甲地地形类型。"
    assert edited.user_edited is True
    # untouched candidate keeps its AI provenance
    assert by_number["12"].user_edited is False


def test_update_candidate_keeps_lifecycle_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.update_candidate(1, "12", stem="修订后的题干。")
    candidates = store.page_candidates(1)
    assert next(c for c in candidates if c.number == "12").status == "added"
    assert next(c for c in candidates if c.number == "12").user_edited is True


def test_update_candidate_missing_number_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    with pytest.raises(QuestionCandidateError):
        store.update_candidate(1, "99", stem="x")


def test_add_manual_candidate_marks_user_provenance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    added = store.add_manual_candidate(1, "14", "分析该地区河流的汛期成因。")
    assert added.user_edited is True
    assert added.completeness == "complete"
    assert added.status == "pending"
    assert "14" in {c.number for c in store.page_candidates(1)}


def test_add_manual_candidate_rejects_duplicate_number(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    with pytest.raises(QuestionCandidateError):
        store.add_manual_candidate(1, "12", "重复题号")


def test_add_manual_candidate_rejects_same_blank_number_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_manual_candidate(2, "", "见原图")
    with pytest.raises(QuestionCandidateError):
        store.add_manual_candidate(2, "", "见原图")


def test_page_paths_keep_repeated_subquestion_labels_independent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.save_page_candidates(
        3,
        [
            QuestionCandidate(
                number="20",
                stem="计算",
                completeness="complete",
                question_kind="composite",
                children=[
                    QuestionCandidate(
                        number="(2)", stem="式子甲", completeness="complete"
                    )
                ],
            ),
            QuestionCandidate(
                number="21",
                stem="行程",
                completeness="complete",
                question_kind="composite",
                children=[
                    QuestionCandidate(
                        number="(2)", stem="问题乙", completeness="complete"
                    )
                ],
            ),
        ],
    )

    loaded = store.page_candidates(3)
    assert loaded is not None
    assert [entry[2] for entry in iter_atomic_leaves(loaded)] == ["0.0", "1.0"]

    store.update_candidate_at_path(
        3, "0.0", new_number="20(2)", stem="校正后的式子甲"
    )
    store.mark_status_at_path(3, "0.0", "added")
    updated = store.page_candidates(3)
    assert updated is not None
    assert updated[0].children[0].number == "20(2)"
    assert updated[0].children[0].stem == "校正后的式子甲"
    assert updated[0].children[0].status == "added"
    assert updated[1].children[0].number == "21(2)"
    assert updated[1].children[0].stem == "问题乙"
    assert updated[1].children[0].status == "pending"
