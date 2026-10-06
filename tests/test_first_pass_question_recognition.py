"""First-pass image recognition, semantic context and stored option regressions."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.database import Database
from src.learning_workflow_service import QuestionService
from src.migrations import migrate_database
from src.question_candidate_service import (
    QuestionCandidateError,
    QuestionCandidateStore,
    build_extraction_prompt,
    extract_candidates,
    parse_candidates_payload,
)
from src.question_structure_service import (
    context_for_question_item,
    link_atomic_question,
    materialize_candidate_tree,
)


class RecordingProvider:
    provider_id = "qwen"
    default_model = "qwen3.8-max"

    def __init__(self, payload: dict | None = None, *, fail: bool = False):
        self.payload = payload or {"candidates": []}
        self.fail = fail
        self.calls: list[tuple[str, str, dict]] = []

    def complete(self, prompt: str, **kwargs):
        return self._record("text", prompt, kwargs)

    def complete_vision(self, prompt: str, image: str, **kwargs):
        assert image.startswith("data:image/jpeg;base64,")
        return self._record("image", prompt, kwargs)

    def _record(self, mode: str, prompt: str, kwargs: dict):
        self.calls.append((mode, prompt, kwargs))
        if self.fail:
            raise RuntimeError("模拟传输失败")
        return SimpleNamespace(text=json.dumps(self.payload, ensure_ascii=False))


@pytest.fixture()
def page_image(tmp_path: Path) -> Path:
    path = tmp_path / "page.png"
    Image.new("RGB", (200, 300), "white").save(path)
    return path


def test_first_pass_reads_image_without_forwarding_ocr(page_image: Path) -> None:
    provider = RecordingProvider({"candidates": [{
        "number": "1", "stem": "题干", "completeness": "complete",
    }]})
    candidates = extract_candidates(
        provider, page_text="噪声OCR上标错位", diagram_text="旧解析噪声",  # type: ignore[arg-type]
        page_id=7, image_path=page_image,
    )
    assert len(provider.calls) == 1
    mode, prompt, kwargs = provider.calls[0]
    assert mode == "image"
    assert "噪声OCR上标错位" not in prompt
    assert "旧解析噪声" not in prompt
    assert "直接识别原图" in prompt
    assert kwargs["target_refs"] == ("page:7",)
    assert kwargs["model"] == provider.default_model
    assert candidates[0].recognition_source == "page_image"


def test_manual_correction_remains_authoritative_with_image(page_image: Path) -> None:
    provider = RecordingProvider()
    extract_candidates(
        provider, page_text="人工核准的指数x^3", diagram_text="",  # type: ignore[arg-type]
        page_id=7, image_path=page_image, user_corrected_text=True,
    )
    assert "人工核准的指数x^3" in provider.calls[0][1]
    assert "冲突时以人工校对内容为准" in provider.calls[0][1]


def test_image_only_page_can_be_recognised(page_image: Path) -> None:
    provider = RecordingProvider()
    assert extract_candidates(
        provider, page_text="", diagram_text="",  # type: ignore[arg-type]
        page_id=7, image_path=page_image,
    ) == []
    assert provider.calls[0][0] == "image"


def test_text_only_model_does_not_silently_receive_ocr(page_image: Path) -> None:
    provider = RecordingProvider()
    provider.default_model = "qwen-plus"
    with pytest.raises(QuestionCandidateError, match="不支持直接识别原页图片"):
        extract_candidates(
            provider, page_text="OCR资料", diagram_text="",  # type: ignore[arg-type]
            page_id=7, image_path=page_image,
        )
    assert not provider.calls


def test_text_only_model_still_accepts_human_corrected_text(page_image: Path) -> None:
    provider = RecordingProvider()
    provider.default_model = "qwen-plus"
    extract_candidates(
        provider, page_text="人工校对资料", diagram_text="",  # type: ignore[arg-type]
        page_id=7, image_path=page_image, user_corrected_text=True,
    )
    assert provider.calls[0][0] == "text"
    assert "人工校对资料" in provider.calls[0][1]
    assert "不得猜补" in provider.calls[0][1]


def test_vision_failure_never_launches_an_extra_text_call(page_image: Path) -> None:
    provider = RecordingProvider(fail=True)
    with pytest.raises(RuntimeError, match="模拟传输失败"):
        extract_candidates(
            provider, page_text="文字", diagram_text="",  # type: ignore[arg-type]
            page_id=7, image_path=page_image,
        )
    assert len(provider.calls) == 1


def test_initial_structured_options_are_persisted_on_separate_lines(tmp_path: Path) -> None:
    long_text = "这个选项包含两句话。第二句话也必须完整保留。" * 12
    candidates = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "9", "stem": "请选择。", "completeness": "complete",
        "is_multiple_choice": True,
        "options": [{"label": label, "text": long_text} for label in "ABCD"],
    }]}, ensure_ascii=False))
    store = QuestionCandidateStore(tmp_path)
    store.save_page_candidates(7, candidates)
    saved = store.page_candidates(7)[0]
    assert saved.stem.split("\n\n") == ["请选择。", *(
        f"{label}. {long_text}" for label in "ABCD"
    )]
    assert not saved.user_edited


def test_missing_choice_stays_missing_and_cannot_claim_complete() -> None:
    candidate = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "9", "stem": "请选择。", "completeness": "complete",
        "is_multiple_choice": True, "options": [{"label": "A", "text": "甲"}],
    }]}, ensure_ascii=False))[0]
    assert candidate.completeness == "incomplete"
    assert candidate.incomplete_reason == "未识别到选项：B、C、D"
    assert "B." not in candidate.stem


@pytest.mark.parametrize("options", ["A 甲", [{"label": "A", "text": "甲"}] * 2])
def test_invalid_option_structure_is_rejected(options) -> None:
    with pytest.raises(QuestionCandidateError):
        parse_candidates_payload(json.dumps({"candidates": [{
            "number": "1", "stem": "题干", "options": options,
        }]}, ensure_ascii=False))


@pytest.mark.parametrize("label", ["7", "41", "三"])
@pytest.mark.parametrize("shared", [True, False])
def test_ai_semantic_decision_survives_storage_and_joining(
    tmp_path: Path, label: str, shared: bool,
) -> None:
    # This shared material begins with a numbered-looking token. A regex
    # must not override the model's semantic judgment of a genuine condition.
    common = "(1)号装置与(2)号装置均采用同一材料。"
    root = parse_candidates_payload(json.dumps({"candidates": [{
        "number": label, "stem": "", "completeness": "complete",
        "has_shared_stem": shared, "shared_stem": common if shared else "",
        "children": [
            {"number": "(1)", "stem": "独立条件一。", "completeness": "complete"},
            {"number": "(2)", "stem": "独立条件二。", "completeness": "complete"},
        ],
    }]}, ensure_ascii=False))[0]
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(7, [root])
    root = store.page_candidates(7)[0]
    assert root.has_shared_stem is shared
    assert root.stem == (common if shared else "")
    database = Database(tmp_path / "knowledge.db")
    document = database.create_document(
        title="测试", filename="test.pdf", source_path=tmp_path / "test.pdf", sha256="b" * 64,
    )
    page = database.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "p.png", status="ready",
    )
    mapping = materialize_candidate_tree(
        database, document_id=document.id, source_page_id=page.id, root_candidate=root,
    )
    question = QuestionService(database).create_question_item(
        document_id=document.id, page_id=page.id, question_kind="error",
        stem_text=root.children[1].stem,
    )
    link_atomic_question(database, node_id=mapping["0.1"], question_item_id=question.id)
    context = context_for_question_item(database, question.id)
    assert context.context_text == (f"{label}\n{common}" if shared else "")
    assert "独立条件一" not in context.context_text


def test_v33_upgrade_keeps_legacy_context_unknown_and_raw_text_intact(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE question_nodes DROP COLUMN has_shared_stem")
        connection.execute("DELETE FROM schema_migrations WHERE version=34")
    backup = migrate_database(path)
    assert backup is not None and backup.is_file()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(question_nodes)")}
        assert "has_shared_stem" in columns
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 34


def test_first_pass_prompt_covers_semantics_and_advanced_mathematics() -> None:
    prompt = build_extraction_prompt("资料", "")
    for requirement in (
        "按语义判断", "shared_stem", "options", "各独占一行", "希腊字母",
        r"\sin", r"\cos", r"\int", r"\sum", r"\lim", "完整指数放在花括号内",
    ):
        assert requirement in prompt


def test_numbered_choice_content_and_multiple_blanks_stay_atomic() -> None:
    candidates = parse_candidates_payload(json.dumps({"candidates": [
        {
            "number": "8", "stem": "请选择正确组合。", "completeness": "complete",
            "is_multiple_choice": True,
            "options": [{"label": label, "text": "(1)条件甲；(2)条件乙。"} for label in "ABCD"],
            "children": [],
        },
        {
            "number": "9", "stem": "正数集合{____}；负数集合{____}。",
            "completeness": "complete", "children": [],
        },
    ]}, ensure_ascii=False))
    assert len(candidates) == 2
    assert all(
        candidate.question_kind == "atomic" and not candidate.children for candidate in candidates
    )
    assert candidates[0].stem.count("(1)条件甲；(2)条件乙。") == 4


@pytest.mark.parametrize("heading", ["计算：", "化简", "解答下列各题：", "（16 分）计算："])
def test_section_heading_cannot_become_shared_conditions(heading: str) -> None:
    parent = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "任意题号", "stem": "", "shared_stem": heading,
        "has_shared_stem": True,
        "children": [{"number": "(1)", "stem": "$x^{2}$"}],
    }]}, ensure_ascii=False))[0]
    assert not parent.stem
    assert parent.has_shared_stem is False
    assert parent.children[0].stem == "$x^{2}$"


def test_calculation_condition_is_not_confused_with_a_section_heading() -> None:
    common = "计算所需的共同条件：$x+y=2$。"
    parent = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "任意题号", "stem": "", "shared_stem": common,
        "has_shared_stem": True,
        "children": [{"number": "(1)", "stem": "求 $2x+2y$。"}],
    }]}, ensure_ascii=False))[0]
    assert parent.stem == common
    assert parent.has_shared_stem is True


def test_shared_diagram_does_not_add_redundant_whole_child_crops() -> None:
    parent = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "22", "has_shared_stem": True, "shared_stem": "根据图中的卡片作答。",
        "visual_regions": [{"role": "shared", "bbox": [100, 100, 900, 300]}],
        "children": [{
            "number": "(1)", "stem": "先阅读共同图形。", "has_shared_stem": True,
            "children": [{
                "number": "①", "stem": "求卡片组成的最大数。",
                "visual_dependency": "required", "question_bbox": [100, 300, 900, 400],
            }],
        }],
    }]}, ensure_ascii=False))[0]
    assert parent.visual_regions[0]["role"] == "shared"
    child = parent.children[0]
    assert not child.visual_regions
    assert not child.children[0].visual_regions
    assert child.children[0].visual_dependency == "required"


def test_missing_option_crop_still_has_fallback_with_shared_diagram() -> None:
    parent = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "22", "has_shared_stem": True, "shared_stem": "根据共同图形选择。",
        "visual_regions": [{"role": "shared", "bbox": [100, 100, 900, 300]}],
        "children": [{
            "number": "(1)", "stem": "选择正确图形。", "is_multiple_choice": True,
            "options": [{"label": label, "requires_image": True} for label in "ABCD"],
            "question_bbox": [100, 300, 900, 600],
        }],
    }]}, ensure_ascii=False))[0]
    child = parent.children[0]
    assert child.completeness == "incomplete"
    assert "图像选项位置待核对" in child.incomplete_reason
    assert child.visual_regions[0]["role"] == "question"


def test_independent_required_diagram_keeps_original_question_fallback() -> None:
    candidate = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "5", "stem": "根据图形选择。", "visual_dependency": "required",
        "question_bbox": [100, 300, 900, 600],
    }]}, ensure_ascii=False))[0]
    assert candidate.visual_regions[0]["role"] == "question"
