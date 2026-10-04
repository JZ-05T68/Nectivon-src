"""Regression tests for the local-only training-question boundary."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import Mock

import pytest

from src.database import Database
from src.local_training_source_policy import (
    is_local_training_result_payload,
    is_locatable_local_training_question,
)
from src.question_source_models import (
    QuestionSourceCandidate,
    QuestionSourceError,
    VerificationResult,
    VerificationStatus,
    VerifiedQuestion,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_ui import (
    RETRIEVAL_RESULT_SESSION_KEY,
    SOURCE_POLICY_VERSION,
    SOURCE_STATUS_SESSION_KEY,
    render_training_questions_section,
)
from src.targeted_training_models import TrainingTask
from src.training_execution_ui import render_training_session_ui


def _local_question() -> VerifiedQuestion:
    return VerifiedQuestion(
        id="vq_11_33_2026_9",
        question_text="计算题：求这道本地试卷中的式子的值。",
        source_name="用户导入的试卷 · 第2页",
        source_url="pages/3_浏览资料.py?document=11&page=2#q_33",
        year=2026,
        exam_or_contest_name="用户导入的试卷",
        question_number="9",
        subject="数学",
        applicable_scope="基础教育 · 普通",
        document_id=11,
        page_id=22,
        page_number=2,
        question_item_id=33,
    )


def test_local_question_requires_exact_imported_item_link() -> None:
    question = _local_question()
    assert is_locatable_local_training_question(question)
    assert is_locatable_local_training_question(question.to_dict())

    for source_url in (
        "https://example.invalid/pages/3_浏览资料.py?document=11&page=2#q_33",
        "//example.invalid/pages/3_浏览资料.py?document=11&page=2#q_33",
        "pages/3_浏览资料.py?document=12&page=2#q_33",
        "pages/3_浏览资料.py?document=11&page=3#q_33",
        "pages/3_浏览资料.py?document=11&page=2#q_34",
        "pages/3_浏览资料.py?document=11&page=2&external=1#q_33",
        "https://example.invalid/question/33",
    ):
        assert not is_locatable_local_training_question(
            replace(question, source_url=source_url)
        )

    assert not is_locatable_local_training_question(replace(question, page_id=None))
    assert not is_locatable_local_training_question(
        {**question.to_dict(), "verification_status": "candidate"}
    )


def test_stale_cached_online_result_cannot_reach_training() -> None:
    payload = {
        "training_type": "题型族",
        "target": "有理数运算",
        "total_candidates": 1,
        "verified_count": 1,
        "rejected_count": 0,
        "questions": [_local_question().to_dict()],
    }
    assert is_local_training_result_payload(payload)
    assert not is_local_training_result_payload(
        {
            **payload,
            "questions": [
                {**payload["questions"][0], "source_url": "https://example.invalid/q/9"}
            ],
        }
    )
    assert not is_local_training_result_payload({**payload, "verified_count": 2})
    assert not is_local_training_result_payload({**payload, "questions": [{}]})


def test_ui_discards_cached_external_result(monkeypatch) -> None:
    task = TrainingTask(training_type="题型族", target="有理数", subject="数学")
    task_key = "题型族_有理数_数学"
    result_key = f"{RETRIEVAL_RESULT_SESSION_KEY}_{task_key}"
    status_key = f"{SOURCE_STATUS_SESSION_KEY}_{task_key}"
    fake_st = Mock()
    fake_st.session_state = {
        f"targeted_training_source_policy_{task_key}": SOURCE_POLICY_VERSION,
        result_key: {
            "training_type": task.training_type,
            "target": task.target,
            "total_candidates": 1,
            "verified_count": 1,
            "rejected_count": 0,
            "questions": [
                {
                    **_local_question().to_dict(),
                    "source_url": "https://example.invalid/q/9",
                }
            ],
        },
        status_key: "local_done",
    }
    fake_st.container.return_value = nullcontext()
    fake_st.columns.return_value = [nullcontext(), nullcontext()]
    fake_st.button.return_value = False
    monkeypatch.setattr("src.question_source_ui.st", fake_st)

    render_training_questions_section(task, retrieval_service=Mock())

    assert result_key not in fake_st.session_state
    assert status_key not in fake_st.session_state
    fake_st.warning.assert_called_once()


def test_ui_refuses_new_nonlocal_result(monkeypatch) -> None:
    task = TrainingTask(training_type="题型族", target="有理数", subject="数学")
    task_key = "题型族_有理数_数学"
    result_key = f"{RETRIEVAL_RESULT_SESSION_KEY}_{task_key}"
    fake_st = Mock()
    fake_st.session_state = {}
    fake_st.container.return_value = nullcontext()
    fake_st.spinner.return_value = nullcontext()
    fake_st.columns.return_value = [nullcontext(), nullcontext()]
    fake_st.button.side_effect = lambda _label, **kwargs: kwargs.get("key") == (
        f"btn_local_query_{task_key}"
    )
    monkeypatch.setattr("src.question_source_ui.st", fake_st)
    retrieval = Mock()
    retrieval.retrieve_local_only.return_value.to_dict.return_value = {
        "training_type": task.training_type,
        "target": task.target,
        "total_candidates": 1,
        "verified_count": 1,
        "rejected_count": 0,
        "questions": [
            {
                **_local_question().to_dict(),
                "source_url": "https://example.invalid/q/9",
            }
        ],
    }

    render_training_questions_section(task, retrieval_service=retrieval)

    assert result_key not in fake_st.session_state
    fake_st.error.assert_called_once()
    fake_st.rerun.assert_not_called()


def test_ui_blocks_legacy_external_session_without_erasing_it(monkeypatch) -> None:
    service = Mock()
    service.get_session.return_value = Mock(
        questions=[
            replace(_local_question(), source_url="https://example.invalid/q/9")
        ]
    )
    fake_st = Mock()
    fake_st.session_state = {}
    fake_st.button.return_value = False
    result_view = Mock()
    monkeypatch.setattr("src.training_execution_ui.st", fake_st)
    monkeypatch.setattr(
        "src.training_execution_ui.render_training_session_result_view", result_view
    )

    render_training_session_ui("legacy_session", service)

    fake_st.warning.assert_called_once()
    result_view.assert_not_called()
    service.get_session.assert_called_once_with("legacy_session")
    service.evaluate_and_record_attempt.assert_not_called()


@pytest.mark.parametrize("source_mode", ["all", "external_supplement", "external_only"])
def test_legacy_source_modes_fail_before_candidate_lookup(tmp_path, source_mode: str) -> None:
    service = QuestionSourceRetrievalService(Database(tmp_path / "local_only.db"))
    service._fetch_local_candidates = Mock(side_effect=AssertionError("must not query"))
    task = TrainingTask(training_type="题型族", target="有理数", subject="数学")

    with pytest.raises(QuestionSourceError, match="联网题源检索已停用"):
        service.retrieve_and_verify(task, source_mode=source_mode)
    service._fetch_local_candidates.assert_not_called()


def test_retrieval_rejects_nonlocal_question_even_from_verifier(tmp_path) -> None:
    verifier = Mock()
    verifier.verify_candidate.return_value = VerificationResult(
        status=VerificationStatus.VERIFIED,
        verified_question=replace(
            _local_question(), source_url="https://example.invalid/q/9"
        ),
    )
    service = QuestionSourceRetrievalService(
        Database(tmp_path / "local_only.db"), verifier=verifier
    )
    service._fetch_local_candidates = Mock(
        return_value=[
            QuestionSourceCandidate(
                question_text="计算题：求这道本地试卷中的式子的值。",
                source_name="用户导入的试卷 · 第2页",
                source_url="pages/3_浏览资料.py?document=11&page=2#q_33",
                year=2026,
                exam_or_contest_name="用户导入的试卷",
                question_number="9",
                subject="数学",
                document_id=11,
                page_id=22,
                page_number=2,
                question_item_id=33,
            )
        ]
    )

    result = service.retrieve_local_only(
        TrainingTask(training_type="题型族", target="有理数", subject="数学")
    )
    assert result.verified_count == 0
    assert result.questions == []
    assert result.rejected_count == 1
