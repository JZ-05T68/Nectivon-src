"""Chemistry-specific v0.8.6 regression contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.ai.rag_prompt_builder import _RAG_EXTRA_RULES
from src.question_candidate_service import (
    QuestionCandidate,
    associate_companion_answer_refs,
)


def _atomic(number: str, stem: str) -> QuestionCandidate:
    return QuestionCandidate(number=number, stem=stem, completeness="complete")


def test_companion_reference_answer_is_attached_to_every_leaf_not_answer_card() -> None:
    source = SimpleNamespace(id=1, title="24南京盐城二模化学")
    answer = SimpleNamespace(id=2, title="24南京盐城二模化学答案")
    answer_card = SimpleNamespace(id=3, title="24南京盐城二模化学答题卡")
    pages = {
        1: [SimpleNamespace(page_number=5, searchable_content="16. CaO2 制备")],
        2: [
            SimpleNamespace(page_number=1, searchable_content="14. 参考答案"),
            SimpleNamespace(
                page_number=2,
                searchable_content="16. (1) 无气泡 (2) Fe(OH)3 (3) 冰水浴",
            ),
        ],
        3: [SimpleNamespace(page_number=1, searchable_content="16. 学生手写作答")],
    }

    class _Database:
        def get_document(self, document_id: int):
            return {1: source, 2: answer, 3: answer_card}.get(document_id)

        def list_documents(self):
            return [source, answer, answer_card]

        def list_pages(self, document_id: int):
            return pages[document_id]

    root = QuestionCandidate(
        number="16",
        stem="共享工艺流程",
        completeness="complete",
        question_kind="composite",
        children=[_atomic("(1)", "酸浸1"), _atomic("(3)", "转化")],
    )

    associate_companion_answer_refs(_Database(), source_document_id=1, candidates=[root])

    expected = "24南京盐城二模化学答案 · 第 2 页 · 第16题"
    assert root.answer_refs == [expected]
    assert all(child.answer_refs == [expected] for child in root.children)
    assert all("答题卡" not in ref for child in root.children for ref in child.answer_refs)


def test_ambiguous_answer_pages_fail_closed() -> None:
    source = SimpleNamespace(id=1, title="模考化学")
    answer = SimpleNamespace(id=2, title="模考化学答案")

    class _Database:
        def get_document(self, document_id: int):
            return source

        def list_documents(self):
            return [source, answer]

        def list_pages(self, document_id: int):
            if document_id == 2:
                return [
                    SimpleNamespace(page_number=1, searchable_content="14. A"),
                    SimpleNamespace(page_number=2, searchable_content="14. B"),
                ]
            return []

    root = _atomic("14", "题干")
    associate_companion_answer_refs(_Database(), source_document_id=1, candidates=[root])
    assert root.answer_refs == []


def test_chemistry_prompt_forbids_mhchem_and_protects_structure_images() -> None:
    assert "不得输出 \\ce{...}" in _RAG_EXTRA_RULES
    assert "$\\mathrm{VO}^{2+}$ 与 $\\mathrm{VO}_2^+$ 不得混淆" in _RAG_EXTRA_RULES
    assert "不得猜造分子结构" in _RAG_EXTRA_RULES
    assert "化学讲题不得套模板" in _RAG_EXTRA_RULES


def test_agent_answer_surface_uses_shared_math_renderer() -> None:
    page = (Path(__file__).resolve().parents[1] / "pages" / "0_知识Agent.py").read_text(
        encoding="utf-8"
    )
    assert "render_math_markdown(_answer_paragraphs_markdown(view_model))" in page
