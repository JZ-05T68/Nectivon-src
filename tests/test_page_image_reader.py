"""All pages and file types use image input, persistent blocks and safe merges."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from src.agent_document_reader import AgentDocumentReadingError, AgentReadingStore
from src.ai.provider import AIExecutionError
from src.database import Database
from src.page_image_coordinates import coordinate_image, normalized_payload
from src.page_image_reader import (
    PageImageReader,
    _bound_figures_to_questions,
    build_page_image_prompt,
    merge_candidate_corrections,
)
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateStore,
    parse_candidates_payload,
)
from src.question_visual_regions import crop_region
from src.recognition_json import repair_latex_json_escapes


def test_image_prompt_and_correction_prompt_share_atomic_granularity(tmp_path: Path) -> None:
    from src.question_candidate_service import build_extraction_prompt
    from src.question_recognition_rules import QUESTION_GRANULARITY_RULES

    reader, document = make_reader(tmp_path, "exam.pdf", count=1)
    page = reader.database.list_pages(document.id)[0]
    prompt = build_page_image_prompt(page, 1, coordinate_image(page.image_path.read_bytes()))
    assert QUESTION_GRANULARITY_RULES in prompt
    assert QUESTION_GRANULARITY_RULES in build_extraction_prompt("人工修订", "")
    assert "shared_stem: 真正共同条件或空字符串" in prompt


def test_json_latex_command_before_a_digit_is_not_consumed_as_a_tab() -> None:
    response = r'{"stem":"$2\times3$"}'
    assert json.loads(repair_latex_json_escapes(response))["stem"] == r"$2\times3$"


def test_undecided_parent_context_is_not_saved_as_a_fresh_image_reading(tmp_path: Path) -> None:
    class UndecidedParent(VisionProvider):
        def complete_vision(self, prompt, image, **kwargs):
            return SimpleNamespace(text=json.dumps({
                "coordinate_space": "pixels", "page_text": "大题原文",
                "candidates": [{
                    "number": "8", "stem": "第一小问自己的条件",
                    "children": [
                        {"number": "8(1)", "stem": "第一小问自己的条件"},
                        {"number": "8(2)", "stem": "第二小问自己的条件"},
                    ],
                }],
            }, ensure_ascii=False))

    reader, document = make_reader(tmp_path, "exam.pdf", count=1, provider=UndecidedParent())
    with pytest.raises(AgentDocumentReadingError, match="未判断大题是否有公共题干"):
        reader.read_document(document.id, force=True)
    assert reader.candidates.page_candidates(1) is None
    assert reader.readings.page_reading(1) is None
    assert len(list((reader.readings.root / "image-responses").glob("page_1_*.json"))) == 1


def test_shared_figure_completion_stops_before_child_question() -> None:
    items = [{
        "question_bbox": [50, 100, 950, 600],
        "visual_regions": [{"role": "shared", "bbox": [200, 200, 800, 300]}],
        "children": [{"question_bbox": [50, 340, 950, 430]}],
    }]
    _bound_figures_to_questions(items)
    assert items[0]["visual_regions"][0]["clip_bbox"] == [50, 100, 950, 320]


@pytest.mark.parametrize("error_class", ["network", "transport", "network_error"])
def test_no_network_is_reported_without_text_fallback(tmp_path: Path, error_class: str) -> None:
    class OfflineProvider(VisionProvider):
        def complete_vision(self, *args, **kwargs):
            raise AIExecutionError("Provider 网络请求失败。", error_class=error_class)

    reader, document = make_reader(tmp_path, "试卷.pdf", count=1, provider=OfflineProvider())
    with pytest.raises(AgentDocumentReadingError, match="无网络环境"):
        reader.read_document(document.id, force=True)
    assert reader.candidates.page_candidates(1) is None
    assert reader.readings.page_reading(1) is None


def test_flow_chart_retains_disconnected_arrows_and_final_label(tmp_path: Path) -> None:
    path = tmp_path / "flow.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    for left in (100, 190, 280, 370, 460):
        draw.line((left, 300, left + 80, 300), fill="black", width=2)
        draw.line((left + 75, 295, left + 80, 300), fill="black", width=2)
        draw.line((left + 75, 305, left + 80, 300), fill="black", width=2)
    draw.rectangle((550, 295, 554, 305), fill="blue")
    image.save(path)
    cropped = Image.open(io.BytesIO(crop_region(path, {
        "role": "stem", "bbox": [90, 280, 510, 335],
    })))
    assert (0, 0, 255) in {color for _, color in cropped.getcolors(maxcolors=1_000_000)}
    assert cropped.width > 450


def test_number_line_crop_excludes_next_answer_line(tmp_path: Path) -> None:
    path = tmp_path / "axis.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.line((100, 200, 800, 200), fill="black", width=2)
    for x in range(100, 800, 50):
        draw.line((x, 190, x, 200), fill="black", width=2)
        draw.rectangle((x, 210, x + 4, 219), fill="black")
    draw.line((400, 248, 700, 248), fill="blue", width=2)
    image.save(path)
    cropped = Image.open(io.BytesIO(crop_region(path, {
        "role": "shared", "bbox": [90, 185, 805, 250],
    })))
    assert (0, 0, 255) not in {color for _, color in cropped.getcolors(maxcolors=1_000_000)}
    assert cropped.height < 60


def test_figure_completion_recovers_long_axis_despite_underestimated_question_box(
    tmp_path: Path,
) -> None:
    path = tmp_path / "axis-clipped.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.line((150, 300, 850, 300), fill="black", width=2)
    draw.rectangle((845, 310, 850, 320), fill="blue")
    draw.line((200, 360, 700, 360), fill="red", width=2)
    image.save(path)
    cropped = Image.open(io.BytesIO(crop_region(path, {
        "role": "shared", "bbox": [150, 295, 550, 305],
        "clip_bbox": [100, 290, 570, 350],
    })))
    colors = {color for _, color in cropped.getcolors(maxcolors=1_000_000)}
    assert (0, 0, 255) in colors
    assert (255, 0, 0) not in colors
    assert cropped.width > 700


def test_figure_completion_retains_all_frames_on_one_row_and_excludes_next_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cards-clipped.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    for left in range(150, 550, 80):
        draw.rectangle((left, 200, left + 60, 250), outline="black", width=2)
    draw.rectangle((495, 215, 500, 225), fill="blue")
    draw.line((100, 280, 600, 280), fill="red", width=2)
    image.save(path)
    cropped = Image.open(io.BytesIO(crop_region(path, {
        "role": "shared", "bbox": [145, 202, 370, 236],
        "clip_bbox": [100, 190, 380, 238],
    })))
    colors = {color for _, color in cropped.getcolors(maxcolors=1_000_000)}
    assert (0, 0, 255) in colors
    assert (255, 0, 0) not in colors
    assert cropped.width > 375
    assert cropped.height >= 50


def test_flow_chart_handwritten_connector_cannot_pull_in_next_question(tmp_path: Path) -> None:
    path = tmp_path / "flow-with-pen-connector.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 200, 500, 350), outline="black", width=2)
    draw.line((200, 350, 200, 380), fill="black", width=2)
    draw.line((100, 380, 800, 380), fill="red", width=2)
    image.save(path)
    cropped = Image.open(io.BytesIO(crop_region(path, {
        "role": "stem", "bbox": [95, 195, 505, 370],
        "clip_bbox": [90, 190, 900, 370],
    })))
    colors = {color for _, color in cropped.getcolors(maxcolors=1_000_000)}
    assert (255, 0, 0) not in colors
    assert cropped.height >= 155
    assert cropped.width >= 400


class VisionProvider:
    provider_id = "qwen"
    default_model = "qwen3.8-max"

    def __init__(self, fail_page: int = 0, exam: bool = True):
        self.calls: list[dict] = []
        self.fail_page = fail_page
        self.exam = exam

    def complete(self, *args, **kwargs):
        pytest.fail("OCR/text completion must never replace image reading")

    def complete_vision(self, prompt, image, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        page_id = int(kwargs["target_refs"][0].split(":")[1])
        if page_id == self.fail_page:
            raise RuntimeError("模拟某页网络失败")
        assert image.startswith("data:image/jpeg;base64,")
        assert "错误OCR" not in prompt and "文本层噪声" not in prompt
        data = {
            "coordinate_space": "pixels",
            "page_text": r"求 $\sin\alpha+x^{2}$。",
            "summary": "原图内容摘要",
            "keywords": ["正弦"],
            "key_facts": [],
            "blocks": [{"kind": "text", "text": "正文片段", "bbox": [70, 70, 210, 170]}],
            "candidates": [
                {
                    "number": str(page_id),
                    "stem": r"求 $x^{2}$（如图）。",
                    "completeness": "complete",
                    "visual_dependency": "required",
                    "visual_regions": [{"role": "stem", "bbox": [90, 110, 230, 170]}],
                    "is_multiple_choice": True,
                    "options": [
                        {"label": label, "text": rf"$\cos\theta+{i}$"}
                        for i, label in enumerate("ABCD")
                    ],
                }
            ]
            if self.exam
            else [],
        }
        return SimpleNamespace(text=json.dumps(data, ensure_ascii=False))


def make_reader(tmp_path: Path, filename: str, *, count: int = 4, provider=None):
    db = Database(tmp_path / "database" / "knowledge.db")
    document = db.create_document(
        title="通用来源",
        filename=filename,
        source_path=tmp_path / filename,
        sha256="a" * 64,
        page_count=count,
        import_status="completed",
    )
    for number in range(1, count + 1):
        image_path = tmp_path / f"page_{number}.png"
        image = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(image).line((50, 100, 180, 100), fill="black", width=2)
        image.save(image_path)
        db.create_page(
            document_id=document.id,
            page_number=number,
            image_path=image_path,
            extracted_text="文本层噪声",
            ocr_text="错误OCR",
        )
    reader = PageImageReader(
        database=db,
        provider=provider or VisionProvider(),
        readings=AgentReadingStore(tmp_path / "agent-readings"),
        candidates=QuestionCandidateStore(tmp_path / "question-candidates"),
    )
    return reader, document


@pytest.mark.parametrize(
    "filename", ["试卷.pdf", "讲义.docx", "讲义.doc", "课件.pptx", "课件.ppt", "照片.png"]
)
def test_all_source_types_read_every_page_once_and_reuse(tmp_path: Path, filename: str):
    reader, document = make_reader(tmp_path, filename)
    before = [
        (p.image_path.read_bytes(), p.ocr_text, p.extracted_text)
        for p in reader.database.list_pages(document.id)
    ]
    report = reader.read_document(document.id)
    assert report.newly_read_pages == 4 and report.state.completed
    assert len(reader.provider.calls) == 4
    for page in reader.database.list_pages(document.id):
        reading = reader.readings.page_reading(page.id)
        assert reading.transcript and reading.blocks
        assert (
            reading.source_image_sha256 == hashlib.sha256(page.image_path.read_bytes()).hexdigest()
        )
        candidate = reader.candidates.page_candidates(page.id)[0]
        assert not candidate.user_edited
        assert all(f"\n\n{label}. " in candidate.stem for label in "ABCD")
        assert crop_region(page.image_path, candidate.visual_regions[0])
    assert before == [
        (p.image_path.read_bytes(), p.ocr_text, p.extracted_text)
        for p in reader.database.list_pages(document.id)
    ]
    resumed = reader.read_document(document.id)
    assert resumed.reused_pages == 4 and len(reader.provider.calls) == 4


def test_failed_page_does_not_skip_later_pages_or_retry_semantic_output(tmp_path: Path):
    provider = VisionProvider(fail_page=2)
    reader, document = make_reader(tmp_path, "试卷.pdf", provider=provider)
    with pytest.raises(AgentDocumentReadingError, match="2"):
        reader.read_document(document.id)
    assert len(provider.calls) == 4
    assert reader.readings.document_state(document.id).failed_page_numbers == (2,)
    assert reader.readings.page_reading(3) and reader.readings.page_reading(4)
    provider.fail_page = 0
    report = reader.read_document(document.id)
    assert report.newly_read_pages == 1 and report.reused_pages == 3
    assert len(provider.calls) == 5


def test_non_exam_page_produces_blocks_without_inventing_questions(tmp_path: Path):
    reader, document = make_reader(
        tmp_path, "课件.pptx", count=1, provider=VisionProvider(exam=False)
    )
    reader.read_document(document.id)
    assert reader.candidates.page_candidates(1) == []
    assert reader.readings.page_reading(1).blocks[0]["text"] == "正文片段"


def test_failed_force_reread_cannot_be_counted_as_completed_old_result(tmp_path: Path):
    provider = VisionProvider()
    reader, document = make_reader(tmp_path, "exam.pdf", provider=provider)
    reader.read_document(document.id)
    provider.fail_page = 2
    with pytest.raises(AgentDocumentReadingError):
        reader.read_document(document.id, force=True)
    state = reader.readings.document_state(document.id)
    assert state.status == "failed" and state.read_pages == 3
    assert not reader.is_fresh(reader.database.get_page(2))
    provider.fail_page = 0
    report = reader.read_document(document.id)
    assert report.newly_read_pages == 1 and report.reused_pages == 3
    assert len(provider.calls) == 9


def test_human_edits_status_and_missing_manual_question_survive():
    old = [
        QuestionCandidate(
            "10",
            "人工核准 $m^{7}$",
            "complete",
            user_edited=True,
            status="added",
            visual_dependency="none",
            binding_confirmed=True,
        ),
        QuestionCandidate("99", "人工补题", "complete", user_edited=True),
    ]
    fresh = [
        QuestionCandidate(
            "10",
            "AI新题干",
            "complete",
            visual_dependency="required",
            visual_regions=[{"role": "stem", "bbox": [50, 50, 90, 90]}],
        )
    ]
    merged = merge_candidate_corrections(fresh, old)
    assert merged[0].stem == old[0].stem and merged[0].status == "added"
    assert merged[0].image_recognized_stem == "AI新题干"
    assert merged[0].user_edited and merged[0].visual_regions == []
    assert merged[1].number == "99" and merged[1].user_edited


def test_subquestion_separator_does_not_duplicate_an_added_question():
    old = [QuestionCandidate("21(3)", "old", "complete", status="added")]
    fresh = parse_candidates_payload(json.dumps({"candidates": [{
        "number": "21", "stem": "shared", "has_shared_stem": True,
        "children": [{"number": "21-(3)", "stem": "$x^{2}$", "completeness": "complete"}],
    }]}))
    result = merge_candidate_corrections(fresh, old)
    assert len(result) == 1 and result[0].children[0].number == "21(3)"
    assert result[0].children[0].status == "added"


def test_coordinate_units_are_explicit_and_original_pixels_are_preserved():
    original = Image.new("RGB", (300, 600), "white")
    ImageDraw.Draw(original).rectangle((50, 100, 180, 180), fill="black")
    data = io.BytesIO()
    original.save(data, format="PNG")
    image = coordinate_image(data.getvalue())
    payload = normalized_payload({"coordinate_space": "pixels", "bbox": [98, 148, 228, 228]}, image)
    assert payload["bbox"] == pytest.approx(
        [50 / 300 * 1000, 100 / 600 * 1000, 180 / 300 * 1000, 180 / 600 * 1000]
    )
    with Image.open(io.BytesIO(image.data)) as framed:
        assert framed.crop((48, 48, 348, 648)).tobytes() == original.tobytes()
    with pytest.raises(ValueError, match="像素坐标"):
        normalized_payload({"coordinate_space": "normalized"}, image)


def test_bad_latex_json_escapes_cannot_turn_fractions_into_control_characters():
    raw = (r'{"candidates":[{"number":"1","stem":"$\frac{1}{2}+\sin\alpha+\nabla f$",'
           r'"completeness":"complete"}]}')
    candidate = parse_candidates_payload(raw)[0]
    assert r"\frac" in candidate.stem and r"\sin\alpha" in candidate.stem
    assert "\f" not in candidate.stem and r"\nabla" in candidate.stem
    valid = json.dumps({"formula": r"\frac{1}{2}", "plain": "换行\n说明"}, ensure_ascii=False)
    assert repair_latex_json_escapes(valid) == valid


def test_new_image_text_is_searchable_and_raw_sources_remain_unchanged(tmp_path: Path):
    reader, document = make_reader(tmp_path, "扫描.pdf", count=1)
    reader.read_document(document.id)
    results = reader.database.search('"求"', terms=("求",))
    assert results and r"\sin\alpha" in results[0].content
    assert reader.database.get_page(1).ocr_text == "错误OCR"


def test_crop_recovers_a_clipped_triangle_vertex_without_ocr(tmp_path: Path):
    image = Image.new("RGB", (500, 700), "white")
    ImageDraw.Draw(image).polygon([(250, 80), (180, 180), (320, 180)], outline="black", width=3)
    path = tmp_path / "triangle.png"
    image.save(path)
    # The AI box starts in the middle of the triangle; border inspection must
    # follow the clipped sides upward to their vertex.
    data = crop_region(path, {"role": "stem", "bbox": [330, 145, 660, 275]})
    with Image.open(io.BytesIO(data)) as result:
        assert result.height > 700 * (275 - 145) / 1000
