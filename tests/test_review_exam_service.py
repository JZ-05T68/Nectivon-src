"""Whole-document assembly must retain content and honest source boundaries."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.question_candidate_service import QuestionCandidate, QuestionCandidateError
from src.question_candidate_service import QuestionCandidateStore as Store
from src.review_exam_service import (
    ReviewExamService,
    assemble_exam,
    build_document_plan,
    exam_candidate,
    normalize_document_blocks,
    parse_page_blocks,
    publish_exam_candidates,
    question_text,
)


def block(identifier: str, page: int, kind: str, text: str, **kwargs) -> dict:
    return {"id": identifier, "page_id": page, "page_number": page, "kind": kind,
            "text": text, "number": "", "starts": False, "choice": False,
            "uncertain": False, "region": {"role": "question", "bbox": [10, 20, 900, 950],
                                           "page_id": page}, **kwargs}


def paper() -> tuple[list[dict], dict]:
    blocks = [
        block("article1", 1, "material", "Passage 1\nThe first half,", starts=True),
        block("header", 2, "decoration", "67"),
        block("article2", 2, "material", "continued faithfully. The final paragraph."),
        block("q47", 2, "question", "47. What do the authors find?", number="47",
              starts=True, choice=True),
        block("ab", 2, "question", "A) First choice.\nB) Second choice.", choice=True),
        block("cd", 3, "question", "C) Third choice.\nD) Fourth choice.", choice=True),
        block("figure", 3, "figure", "（原题图表）"),
        block("q48", 3, "question", "48. Another question.", number="48", starts=True),
    ]
    plan = {"materials": [{"id": "m1", "block_ids": ["article1", "article2"],
                           "complete": True}],
            "questions": [
                {"number": "47", "block_ids": ["q47", "ab", "cd", "figure"],
                 "material_ids": ["m1"], "complete": True},
                {"number": "48", "block_ids": ["q48"], "material_ids": ["m1"],
                 "complete": True},
            ], "excluded": [{"block_id": "header", "reason": "页码"}]}
    return blocks, plan


def test_cross_page_stem_options_article_and_figure_are_preserved() -> None:
    report = assemble_exam(*paper())
    question = report["questions"][0]
    assert not question["issues"] and not report["unassigned"]
    text = question_text(report, question)
    assert "first half," in text and "final paragraph." in text
    assert all(f"{label})" in text for label in "ABCD")
    assert "48. Another" not in text
    candidate = exam_candidate(report, question)
    assert candidate.page_refs == [1, 2, 3]
    assert candidate.completeness == "complete"
    assert candidate.status == "pending" and not candidate.user_edited
    assert candidate.visual_regions[0]["page_id"] == 3
    assert report["questions"][1]["material_ids"] == ["m1"]


def test_missing_options_are_never_promoted_by_model_complete_claim() -> None:
    blocks, plan = paper()
    blocks[5]["text"] = "C) Third choice."
    report = assemble_exam(blocks, plan)
    candidate = exam_candidate(report, report["questions"][0])
    assert candidate.completeness == "incomplete"
    assert "D" in candidate.incomplete_reason


def test_unassigned_source_content_blocks_normal_question_status() -> None:
    blocks, plan = paper()
    blocks.append(block("orphan", 4, "unknown", "Unattached continuation"))
    report = assemble_exam(blocks, plan)
    assert report["unassigned"] == ["orphan"]
    assert exam_candidate(report, report["questions"][0]).completeness == "incomplete"


def test_different_questions_cannot_be_merged_as_a_normal_question() -> None:
    blocks, plan = paper()
    plan["questions"][0]["block_ids"].append("q48")
    plan["questions"].pop()
    report = assemble_exam(blocks, plan)
    assert "边界不唯一" in exam_candidate(report, report["questions"][0]).incomplete_reason


@pytest.mark.parametrize("fault", ["duplicate", "missing", "exclude_body", "material_question"])
def test_invalid_plan_is_rejected_instead_of_losing_content(fault: str) -> None:
    blocks, plan = paper()
    if fault == "duplicate":
        plan["questions"][1]["block_ids"].append("cd")
    elif fault == "missing":
        plan["questions"][0]["block_ids"].append("invented")
    elif fault == "exclude_body":
        plan["questions"][0]["block_ids"].remove("cd")
        plan["excluded"].append({"block_id": "cd", "reason": "不重要"})
    else:
        plan["materials"][0]["block_ids"].append("q47")
    with pytest.raises(QuestionCandidateError):
        assemble_exam(blocks, plan)


def test_wrong_or_missing_article_relation_is_flagged() -> None:
    blocks, plan = paper()
    plan["questions"][0]["material_ids"] = []
    report = assemble_exam(blocks, plan)
    assert "前置共享材料" in exam_candidate(report, report["questions"][0]).incomplete_reason


def test_ambiguous_transcription_cannot_become_complete() -> None:
    blocks, plan = paper()
    blocks[2]["uncertain"] = True
    report = assemble_exam(blocks, plan)
    assert exam_candidate(report, report["questions"][1]).completeness == "incomplete"


def test_publish_replaces_ai_fragments_and_preserves_human_work(tmp_path: Path) -> None:
    store = Store(tmp_path / "candidates")
    store.save_page_candidates(2, [QuestionCandidate("47", "old fragment", "incomplete")])
    store.save_page_candidates(3, [
        QuestionCandidate("47", "old continuation fragment", "incomplete"),
        QuestionCandidate("48", "Human version", "complete", user_edited=True),
        QuestionCandidate("49", "Already added", "complete", status="added"),
    ])
    report = assemble_exam(*paper())
    report["pages"] = [{"id": p} for p in (1, 2, 3)]
    publish_exam_candidates(report, store, tmp_path / "backup")
    assert store.page_candidates(1) == []
    assert "Fourth choice" in store.page_candidates(2)[0].stem
    assert [(c.number, c.stem, c.status) for c in store.page_candidates(3)] == [
        ("48", "Human version", "pending"), ("49", "Already added", "added"),
    ]
    assert len(list((tmp_path / "backup").glob("*.json"))) == 2


def test_single_page_question_retains_all_options() -> None:
    blocks = [block("q1", 1, "question", "1. Question\nA. a\nB. b\nC. c\nD. d",
                    number="1", starts=True, choice=True)]
    report = assemble_exam(blocks, {"questions": [{"number": "1", "block_ids": ["q1"],
                                                  "complete": True}]})
    candidate = exam_candidate(report, report["questions"][0])
    assert candidate.completeness == "complete" and candidate.page_refs == [1]


def test_source_identity_cannot_be_supplied_by_model() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, format="PNG")
    blocks = parse_page_blocks({"blocks": [{"kind": "question", "text": "1. Real source",
                               "bbox": [10, 20, 900, 950], "page_id": 999,
                               "uncertain": False}]},
                               SimpleNamespace(id=5, page_number=2), buf.getvalue())
    assert blocks[0]["page_id"] == 5 and blocks[0]["page_number"] == 2
    assert blocks[0]["region"]["page_id"] == 5


def test_scan_caches_sources_without_semantic_retry(tmp_path: Path) -> None:
    class Provider:
        default_model = "fake-vision"

        def __init__(self):
            self.calls = []

        def complete_vision(self, *args, **kwargs):
            self.calls.append("vision")
            return SimpleNamespace(text=json.dumps({"blocks": [{
                "kind": "question", "text": "1. Complete source", "number": "1",
                "starts": True, "uncertain": False, "bbox": [10, 10, 900, 900],
            }]}))

        def complete(self, *args, **kwargs):
            self.calls.append("text")
            return SimpleNamespace(text=json.dumps({"questions": [{
                "number": "1", "block_ids": ["p1b0"], "complete": True,
            }]}))

    path = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(path)
    pages = [SimpleNamespace(id=1, page_number=1, image_path=path, markdown_content="")]
    provider = Provider()
    service = ReviewExamService(tmp_path / "drafts", provider)
    first = service.scan(1, pages)
    assert service.scan(1, pages) == first
    assert provider.calls == ["vision"]
    assert first["questions"][0]["verified"] is False


def test_truncated_response_is_rejected() -> None:
    with pytest.raises(QuestionCandidateError, match="截断"):
        ReviewExamService._check_response(SimpleNamespace(finish_reason="length"))


def test_ordered_plan_joins_continuation_options_and_does_not_flush_at_page_edge() -> None:
    blocks, _ = paper()
    blocks[5]["kind"] = "material"
    blocks[5]["region"]["bbox"] = [90, 58, 732, 96]
    normalized = normalize_document_blocks(blocks)
    report = assemble_exam(normalized, build_document_plan(normalized))
    assert len(report["questions"]) == 2
    assert not report["unassigned"]
    first = exam_candidate(report, report["questions"][0])
    assert first.completeness == "complete" and first.page_refs == [1, 2, 3]
    assert "D) Fourth" in first.stem and "48. Another" not in first.stem


def test_standalone_footer_number_is_not_article_content() -> None:
    footer = block("footer", 1, "material", "66")
    footer["region"]["bbox"] = [482, 663, 509, 693]
    assert normalize_document_blocks([footer])[0]["kind"] == "decoration"


def test_ambiguous_prose_after_question_is_not_silently_merged() -> None:
    blocks, _ = paper()
    blocks.insert(6, block("ambiguous", 3, "material", "Whose continuation is this?"))
    report = assemble_exam(blocks, build_document_plan(blocks))
    assert "ambiguous" in report["unassigned"]
    assert exam_candidate(report, report["questions"][0]).completeness == "incomplete"


def test_inline_options_are_all_retained_and_displayed_separately() -> None:
    blocks, plan = paper()
    blocks[4]["text"] = "A) First.   B) Second.   C) Third.   D) Fourth."
    blocks[5]["text"] = "（续页说明）"
    report = assemble_exam(blocks, plan)
    candidate = exam_candidate(report, report["questions"][0])
    assert candidate.completeness == "complete"
    assert all(f"\n\n{letter})" in candidate.stem for letter in "ABCD")


def test_repeated_question_numbers_in_different_passages_stay_separate() -> None:
    blocks = [
        block("title1", 1, "material", "Passage 1", starts=True),
        block("article1", 1, "material", "The first article."),
        block("q1", 1, "question", "36. First question.", number="36", starts=True),
        block("title2", 2, "instruction", "Passage 2", starts=True),
        block("article2", 2, "material", "The second article."),
        block("q2", 2, "question", "36. Second question.", number="36", starts=True),
    ]
    report = assemble_exam(blocks, build_document_plan(blocks))
    assert [q["number"] for q in report["questions"]] == ["36", "36"]
    assert report["questions"][0]["material_ids"] != report["questions"][1]["material_ids"]
    assert "second article" not in question_text(report, report["questions"][0])


def test_printed_number_overrides_false_ai_start_flag() -> None:
    blocks = [block("q1", 1, "question", "1. First.", number="1", starts=True),
              block("q2", 1, "question", "2. Second.", number="2", starts=False)]
    normalized = normalize_document_blocks(blocks)
    report = assemble_exam(normalized, build_document_plan(normalized))
    assert [q["number"] for q in report["questions"]] == ["1", "2"]
    assert not any(q["issues"] for q in report["questions"])
