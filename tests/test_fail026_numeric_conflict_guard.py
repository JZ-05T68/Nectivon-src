"""FAIL-026 minimal fix regression (Night 2).

Three layers are guarded:

- the vision prompt carries the CJK digit-gap prior (026-A perception);
- the visual adapter emits a deterministic numeric-consistency note when the
  vision reading and the page text layer disagree in the digit-gap shape;
- the final answer rules forbid unilateral "以图片为准" arbitration (026-B).

The detection is generic over all numbers: no fixture name, no 26/55/30
hardcoding, true decimals corroborated by the text layer stay untouched, and
pure-visual pages without a text layer are unaffected.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from src.agent.tools.adapters.page_visual import (
    NUMERIC_CONFLICT_HEADER,
    PageVisualAdapter,
    numeric_conflict_note,
)
from src.ai.rag_prompt_builder import _RAG_EXTRA_RULES, RagPromptBuilder
from src.database import Database
from src.knowledge_context_packager import KnowledgeContextPackager
from src.search_service import SearchService


@dataclass
class _VisionResult:
    text: str
    model: str = "vision-test"
    usage: object = None
    finish_reason: str = "stop"
    retry_count: int = 0


class _StubVision:
    """Records the prompt; returns a fixed visual fact sheet."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def complete_vision(self, prompt: str, image_png_base64: str, **kwargs):
        self.calls.append(prompt)
        return _VisionResult(text=self.text)


# ---------------------------------------------------------------- detectors


def test_repro_cases_digit_gap_misread_fire() -> None:
    source = "图2-1 训练损失随训练轮次变化\n1\n90\n2\n55\n3\n38\n4\n30\n5\n27\n6\n26"
    for decimal, integer in (("2.6", "26"), ("5.5", "55"), ("3.0", "30")):
        note = numeric_conflict_note(source, f"第 6 轮损失约为 {decimal}。")
        assert note is not None, decimal
        assert NUMERIC_CONFLICT_HEADER in note
        assert f"图中读取 {decimal}" in note
        assert f"文字层记录 {integer}" in note


def test_generalizes_beyond_known_values() -> None:
    assert numeric_conflict_note("标准值 47", "图中读作 4.7") is not None
    assert numeric_conflict_note("上限 123", "图中读作 12.3") is not None
    assert numeric_conflict_note("记录 1005", "图中读作 10.05") is not None


def test_corroborated_true_decimal_does_not_fire() -> None:
    source = "数据点标签：2.6、5.5、10.2；横轴 1-3。"
    assert numeric_conflict_note(source, "损失为 2.6，中间点 5.5，末点 10.2") is None


def test_matching_integer_reading_does_not_fire() -> None:
    assert numeric_conflict_note("标签 26", "柱顶标签为 26") is None


def test_pure_visual_without_text_layer_is_unaffected() -> None:
    assert numeric_conflict_note("", "图中读作 2.6") is None
    assert numeric_conflict_note("   ", "图中读作 2.6") is None


def test_version_like_tokens_are_ignored() -> None:
    assert numeric_conflict_note("见第 11 页", "该图为 v1.1 版本图") is None


def test_fullwidth_digits_and_dot_are_normalized() -> None:
    note = numeric_conflict_note("标签 26", "图中读作 ２.６")
    assert note is not None
    assert "文字层记录 26" in note


def test_conflict_list_is_bounded() -> None:
    source = "数值 12 34 56 78"
    visual = "读数 1.2 3.4 5.6 7.8"
    note = numeric_conflict_note(source, visual)
    assert note is not None
    assert note.count("图中读取") == 3


def test_visual_without_numbers_does_not_fire() -> None:
    assert numeric_conflict_note("正文 26", "本图为流程图，无数据点") is None


# ------------------------------------------------------------- vision prompt


def test_vision_prompt_carries_digit_gap_prior(tmp_path: Path) -> None:
    database, pages_dir = _database_with_visual_page(tmp_path)
    stub = _StubVision("柱顶标签为 26。")
    adapter = _adapter(database, pages_dir, stub)
    adapter(
        _tool_input("训练损失"),
        _context(),
    )
    prompt = stub.calls[0]
    assert "字形" in prompt and "间隙" in prompt
    assert "整数 26" in prompt
    assert "只有看到明确的小数点" in prompt
    assert "同一原始页面直接提取的文字层" not in prompt
    assert "训练轮次变化" not in prompt


# ------------------------------------------------------- adapter integration


def test_adapter_appends_note_when_layers_disagree(tmp_path: Path) -> None:
    database, pages_dir = _database_with_visual_page(tmp_path)
    database.update_page(1, markdown_content=database.get_page(1).extracted_text)
    stub = _StubVision("第 6 轮损失约为 2.6。")
    adapter = _adapter(database, pages_dir, stub)
    result = adapter(_tool_input("训练损失"), _context())
    row = result.data["results"][0]
    assert NUMERIC_CONFLICT_HEADER in row["content"]
    assert "图中读取 2.6" in row["content"]
    assert "文字层记录 26" in row["content"]
    assert row["numeric_consistency_note"].startswith(NUMERIC_CONFLICT_HEADER)


def test_adapter_stays_silent_when_layers_agree(tmp_path: Path) -> None:
    database, pages_dir = _database_with_visual_page(tmp_path)
    stub = _StubVision("第 6 轮损失为 26。")
    adapter = _adapter(database, pages_dir, stub)
    result = adapter(_tool_input("训练损失"), _context())
    row = result.data["results"][0]
    assert "numeric_consistency_note" not in row
    assert NUMERIC_CONFLICT_HEADER not in row["content"]


# ------------------------------------------------- final answer rule (026-B)


def test_rag_rules_forbid_unilateral_image_authority() -> None:
    assert "图文数值冲突" in _RAG_EXTRA_RULES
    assert "以图片为准" in _RAG_EXTRA_RULES
    assert "并列" in _RAG_EXTRA_RULES
    assert "数值一致性提示" in _RAG_EXTRA_RULES


def test_rag_rule_reaches_the_built_prompt() -> None:
    from src.knowledge_context import ContextItem, ContextItemType

    item = ContextItem(
        type=ContextItemType.KNOWLEDGE_OBJECT,
        local_id=1,
        stable_id="kb-test:knowledge_object:1",
        title="人工智能基础课程讲义 · 第 3 页",
        content="图2-1 训练损失随训练轮次变化（图中读数 2.6，文字层记录 26）",
        kind="fact",
        kind_label="事实",
        status="active",
        status_label="现行",
        importance="primary",
        updated_at=None,
        revision_ref="第 1 版",
        source_anchors=(),
        relation_refs=(),
    )
    package = KnowledgeContextPackager().build([item], question="第 6 轮损失是多少？")
    prompt = RagPromptBuilder().build("第 6 轮损失是多少？", package)
    assert "图文数值冲突" in prompt
    assert "禁止未经披露就只采用其中一个" in prompt


# ------------------------------------------------------------------ helpers


def _database_with_visual_page(tmp_path: Path) -> tuple[Database, Path]:
    database_dir = tmp_path / "db"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = Database(database_dir / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    image_path = pages_dir / "page_0001.png"
    image_path.write_bytes(png)
    document = database.create_document(
        title="人工智能基础课程讲义",
        filename="ai101.pdf",
        source_path="data/raw/ai101.pdf",
        sha256="b" * 64,
        page_count=1,
    )
    database.create_page(
        document_id=document.id,
        page_number=3,
        image_path=image_path,
        extracted_text=(
            "图2-1 训练损失随训练轮次变化\n"
            "1\n90\n2\n55\n3\n38\n4\n30\n5\n27\n6\n26"
        ),
    )
    return database, pages_dir


def _adapter(database: Database, pages_dir: Path, stub: _StubVision) -> PageVisualAdapter:
    return PageVisualAdapter(
        SearchService(database),
        kb_uuid=database.get_knowledge_base_uuid(),
        vision_provider=stub,
        pages_dir=pages_dir,
        vision_model="vision-test",
    )


def _tool_input(query: str):
    from src.agent.tools.contracts import ToolInput

    return ToolInput(tool_name="page_visual_search", arguments={"query": query})


def _context():
    from src.agent.tools.contracts import ToolContext

    return ToolContext(run_id="run-f026", request_id="req-f026")
