"""Original-pixel option crops survive first-pass recognition and learning."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import src.question_content_ui as content_ui
import src.runtime as runtime
from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.model_registry import ProviderId
from src.ai.provider_config import AIProviderConfig, ProviderConfigStore, ProviderSettings
from src.ai.provider_factory import resolve_qwen_runtime
from src.config import Settings
from src.database import Database
from src.learning_entry_ui import _visual_material_block
from src.learning_workflow_service import QuestionService
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateStore,
    extract_candidates,
    parse_candidates_payload,
)
from src.question_visual_regions import bind_regions, crop_region, normalize_regions
from src.training_profile_service import TrainingProfileService


def test_overlapping_figures_share_one_original_crop_and_options_stay_separate() -> None:
    regions = [
        {"role": "stem", "bbox": [100, 200, 400, 300]},
        {"role": "stem", "bbox": [250, 100, 500, 300]},
        {"role": "option", "option_label": "A", "bbox": [100, 400, 400, 500]},
        {"role": "option", "option_label": "B", "bbox": [250, 400, 500, 500]},
    ]
    grouped = content_ui._coalesce_overlapping_figures(regions)
    assert len(grouped) == 3
    assert grouped[0]["bbox"] == [100, 100, 500, 300]
    assert [region["option_label"] for region in grouped[1:]] == ["A", "B"]
    assert regions[0]["bbox"] == [100, 200, 400, 300]


def test_visual_grouping_never_merges_regions_from_different_original_pages() -> None:
    regions = [
        {"role": "shared", "bbox": [100, 200, 400, 300], "page_id": page_id}
        for page_id in (1, 2)
    ]
    assert len(content_ui._coalesce_overlapping_figures(regions)) == 2


def test_duplicate_option_boxes_and_neighbor_crop_boundaries() -> None:
    boxes = [
        {"role": "option", "option_label": "A", "bbox": [100, 100, 400, 200]},
        {"role": "option", "option_label": "B", "bbox": [600, 100, 900, 200]},
        {"role": "option", "option_label": "C", "bbox": [100, 230, 400, 330]},
        {"role": "option", "option_label": "D", "bbox": [600, 230, 900, 330]},
    ]
    regions = normalize_regions([*boxes, {**boxes[0], "description": "another description"}])
    assert len(regions) == 4
    assert regions[0]["clip_bbox"] == [0, 0, 500, 215]
    assert regions[3]["clip_bbox"] == [500, 215, 1000, 1000]


def _payload() -> dict:
    return {"candidates": [{
        "number": "3", "stem": r"已知 $a+b+c=0$，位置不可能是（ ）", "completeness": "complete",
        "visual_dependency": "required", "is_multiple_choice": True,
        "options": [{"label": label, "text": "", "requires_image": True,
                     "image_bbox": [100 + i * 200, 100, 250 + i * 200, 250]}
                    for i, label in enumerate("ABCD")],
        "visual_regions": [{"role": "stem", "bbox": [95, 95, 860, 260]}],
        "question_bbox": [80, 50, 900, 300],
    }]}


@pytest.fixture()
def source_image(tmp_path: Path) -> Path:
    path = tmp_path / "original.png"
    image = Image.new("RGB", (1000, 1000), "white")
    for i, color in enumerate(["red", "green", "blue", "black"]):
        image.paste(color, (100 + i * 200, 100, 250 + i * 200, 250))
    image.save(path)
    return path


def test_graph_choices_are_complete_without_invented_text() -> None:
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    assert candidate.completeness == "complete"
    assert candidate.stem.endswith("D. （见原图）")
    assert [r["option_label"] for r in candidate.visual_regions] == list("ABCD")
    assert all(r["role"] == "option" for r in candidate.visual_regions)


def test_missing_graph_box_preserves_original_question_fallback() -> None:
    payload = _payload()
    payload["candidates"][0]["visual_regions"] = []
    payload["candidates"][0]["options"][3]["image_bbox"] = [999, 300, 200, 300]
    candidate = parse_candidates_payload(json.dumps(payload))[0]
    assert candidate.completeness == "incomplete"
    assert "D" in candidate.incomplete_reason
    assert candidate.visual_regions[-1]["role"] == "question"
    assert candidate.visual_regions[-1]["bbox"] == [80, 50, 900, 300]


@pytest.mark.parametrize("bbox", [
    [float("nan"), 0, 20, 20], [0, 0, float("inf"), 20],
    [-1, 0, 50, 50], [1, 1, 1001, 10], [80, 0, 10, 20],
    [0, 0, 2, 50], [True, 0, 50, 50], "bad", None,
])
def test_unusable_regions_cannot_crop(bbox) -> None:
    assert normalize_regions([{"role": "stem", "bbox": bbox}]) == []


def test_single_call_binds_only_actual_image_and_page(source_image: Path) -> None:
    class Provider:
        provider_id = "qwen"
        default_model = "qwen3.8-max"
        calls = 0

        def complete_vision(self, prompt, image, **kwargs):
            self.calls += 1
            assert "0～1000" in prompt and "四个不同选项" in prompt
            assert "OCR噪声" not in prompt
            payload = _payload()
            payload["candidates"][0]["visual_regions"][0].update(
                page_id=999, image_sha256="a" * 64, path="arbitrary-file.png",
            )
            return SimpleNamespace(text=json.dumps(payload))

    provider = Provider()
    candidates = extract_candidates(
        provider, page_text="OCR噪声", diagram_text="", page_id=12, image_path=source_image,
    )
    assert provider.calls == 1
    assert all(r["page_id"] == 12 for r in candidates[0].visual_regions)
    assert all("path" not in r for r in candidates[0].visual_regions)
    assert crop_region(source_image, candidates[0].visual_regions[0])


def test_crop_uses_original_pixels_and_rejects_changed_source(source_image: Path) -> None:
    original = source_image.read_bytes()
    region = bind_regions([{"role": "stem", "bbox": [100, 100, 250, 250]}],
                          page_id=1, image_bytes=original)[0]
    data = crop_region(source_image, region)
    with Image.open(io.BytesIO(data)) as crop:
        assert crop.getpixel((20, 20)) == (255, 0, 0)
    assert source_image.read_bytes() == original
    source_image.write_bytes(original + b"changed")
    assert crop_region(source_image, region) is None


def test_candidate_roundtrip_and_parent_shared_crop(source_image: Path, tmp_path: Path) -> None:
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    candidate.visual_regions = bind_regions(
        candidate.visual_regions, page_id=1, image_bytes=source_image.read_bytes(),
    )
    parent = QuestionCandidate("大题", "共有条件", "complete", question_kind="composite",
                               children=[candidate], visual_regions=[
                                   {"role": "shared", "bbox": [50, 300, 900, 500]},
                               ])
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(1, [parent])
    restored = store.page_candidates(1)[0]
    assert restored.children[0].visual_regions == candidate.visual_regions
    block = _visual_material_block(
        restored.children[0], SimpleNamespace(id=1), ancestors=(restored,),
    )
    assert len(block["regions"]) == 5
    assert block["dependency"] == "required"
    assert block["regions"][-1]["role"] == "shared"


def test_figures_follow_their_option_after_manual_text_edit(
    source_image: Path, monkeypatch,
) -> None:
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    calls: list = []
    monkeypatch.setattr(
        content_ui, "render_question_math_markdown", lambda text: calls.append(text),
    )
    monkeypatch.setattr(content_ui.st, "image", lambda data, **kwargs: calls.append(data))
    edited = "人工修订 $x^{2}$。\n\n" + "\n\n".join(f"{x}. 选项文字已校正" for x in "ABCD")
    assert content_ui.render_question_content(
        edited, image_path=source_image, regions=candidate.visual_regions,
    )
    assert len(calls) == 9
    assert calls[0] == "人工修订 $x^{2}$。"
    for i, color in enumerate([(255, 0, 0), (0, 128, 0), (0, 0, 255), (0, 0, 0)]):
        assert calls[1 + i * 2].startswith("ABCD"[i] + ".")
        with Image.open(io.BytesIO(calls[2 + i * 2])) as crop:
            assert crop.getpixel((20, 20)) == color
    calls.clear()
    assert content_ui.render_question_content(
        "人工只修改了题干。", image_path=source_image, regions=candidate.visual_regions,
    )
    assert len([c for c in calls if isinstance(c, bytes)]) == 4
    assert "D. （见原图）" in calls


def _database(tmp_path: Path, image: Path) -> tuple[Database, object]:
    db = Database(tmp_path / "knowledge.db")
    raw = tmp_path / "original.pdf"
    raw.write_bytes(b"%PDF fixture")
    doc = db.create_document(title="数轴选择测试卷", filename="original.pdf",
                             source_path=raw, sha256="b" * 64, import_status="completed")
    page = db.create_page(document_id=doc.id, page_number=1, image_path=image)
    return db, page


def test_sync_does_not_overwrite_human_content_or_no_figure_override(
    source_image: Path, tmp_path: Path,
) -> None:
    db, page = _database(tmp_path, source_image)
    service = QuestionService(db)
    question = service.create_question_item(
        document_id=page.document_id, page_id=page.id, question_number="3", question_kind="error",
        stem_text="人工校对后的题干", student_answer="人工作答", correction_note="人工订正",
    )
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    candidate.visual_regions = bind_regions(
        candidate.visual_regions, page_id=page.id, image_bytes=source_image.read_bytes(),
    )
    assert service.sync_visual_regions_from_candidates(page.id, [candidate]) == 1
    saved = service.get_question_item(question.id)
    assert (saved.stem_text, saved.student_answer, saved.correction_note) == (
        "人工校对后的题干", "人工作答", "人工订正",
    )
    assert saved.ai_draft["visual_material"]["regions"] == candidate.visual_regions
    override = {"dependency": "none", "binding_provenance": "USER OVERRIDE"}
    service.update_visual_material(question.id, override)
    assert service.sync_visual_regions_from_candidates(page.id, [candidate]) == 0
    assert service.get_question_item(question.id).ai_draft["visual_material"] == override
    service.update_visual_material(question.id, {"dependency": "none"})
    assert service.sync_visual_regions_from_candidates(page.id, [candidate]) == 1
    material = service.get_question_item(question.id).ai_draft["visual_material"]
    assert material["dependency"] == "required"


def test_learning_and_teachback_render_graph_choices(
    source_image: Path, tmp_path: Path, monkeypatch,
) -> None:
    db, page = _database(tmp_path, source_image)
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    candidate.visual_regions = bind_regions(
        candidate.visual_regions, page_id=page.id, image_bytes=source_image.read_bytes(),
    )
    service = QuestionService(db)
    service.create_question_item(
        document_id=page.document_id, page_id=page.id, question_number="3", subject="数学",
        question_kind="error", stem_text=candidate.stem,
        ai_draft={"visual_material": _visual_material_block(candidate, page)},
    )
    monkeypatch.setattr(runtime, "application_database", lambda: db)
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: None)
    monkeypatch.setattr(runtime, "application_training_profile_service",
                        lambda: TrainingProfileService(tmp_path / "profile.db"))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/18_学习整理.py")).run(timeout=60)
    assert not app.exception
    # Four cropped choices in the read view and again in teach-back.
    assert len(app.get("image")) >= 8
    assert sum("D. （见原图）" in m.value for m in app.markdown) >= 2


def test_inactive_qwen_is_used_only_for_explicit_vision_route(tmp_path: Path) -> None:
    store = ProviderConfigStore(tmp_path / "providers.json")
    config = AIProviderConfig().with_provider(ProviderSettings.default_for(ProviderId.QWEN))
    config = config.with_provider(
        ProviderSettings.default_for(ProviderId.DEEPSEEK), make_active=True,
    )
    store.save(config)
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.QWEN, SecretCredential("synthetic-vision-credential"))
    settings = Settings(_env_file=None)
    assert resolve_qwen_runtime(settings, config_store=store, credential_store=credentials) is None
    selected = resolve_qwen_runtime(
        settings, config_store=store, credential_store=credentials, allow_inactive=True,
    )
    assert selected.default_model == "qwen3.8-max"
    assert store.load().active_provider_id is ProviderId.DEEPSEEK
    store.save(AIProviderConfig(providers=config.providers))
    assert resolve_qwen_runtime(
        settings, config_store=store, credential_store=credentials, allow_inactive=True,
    ) is None


def test_question_route_uses_explicit_audited_vision_model(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_mode="api", ai_api_key="synthetic-test-key",
                        ai_vision_model="qwen3-vl-plus")
    resolved = resolve_qwen_runtime(settings, config_store=ProviderConfigStore(tmp_path / "absent"))
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: SimpleNamespace(
        provider_id="deepseek", default_model="deepseek-v4-pro",
    ))
    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    monkeypatch.setattr(runtime, "application_credential_store", lambda: MemoryCredentialStore())
    resolver_calls = []
    def resolve(*args, **kwargs):
        resolver_calls.append(kwargs)
        return resolved
    monkeypatch.setattr(runtime, "resolve_image_provider_runtime", resolve)
    selected = runtime.application_question_vision_provider()
    assert selected.provider_id == "qwen"
    assert selected.default_model == "qwen3.8-max"
    assert "credential_store" in resolver_calls[0]
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: None)
    assert runtime.application_question_vision_provider() is not None
    assert len(resolver_calls) == 2
    monkeypatch.setattr(runtime, "resolve_image_provider_runtime", lambda *args, **kwargs: None)
    assert runtime.application_question_vision_provider() is None


def test_local_training_uses_the_same_original_choice_images(
    source_image: Path, tmp_path: Path, monkeypatch,
) -> None:
    db, page = _database(tmp_path, source_image)
    candidate = parse_candidates_payload(json.dumps(_payload()))[0]
    candidate.visual_regions = bind_regions(
        candidate.visual_regions, page_id=page.id, image_bytes=source_image.read_bytes(),
    )
    item = QuestionService(db).create_question_item(
        document_id=page.document_id, page_id=page.id, question_kind="error", question_number="3",
        stem_text=candidate.stem,
        ai_draft={"visual_material": _visual_material_block(candidate, page)},
    )
    monkeypatch.setattr(runtime, "application_database", lambda: db)
    images = []
    monkeypatch.setattr(content_ui.st, "image", lambda data, **kwargs: images.append(data))
    monkeypatch.setattr(content_ui, "render_question_math_markdown", lambda text: None)
    q = SimpleNamespace(question_item_id=item.id, page_id=page.id, document_id=page.document_id,
                        question_text=candidate.stem)
    content_ui.render_training_question(q)
    assert len(images) == 4
    images.clear()
    q.document_id = 999
    content_ui.render_training_question(q)
    assert not images


def test_training_shared_crop_does_not_repeat_full_source_page(
    source_image: Path, tmp_path: Path, monkeypatch,
) -> None:
    db, page = _database(tmp_path, source_image)
    regions = bind_regions(
        [{"role": "shared", "bbox": [100, 100, 850, 270]}],
        page_id=page.id, image_bytes=source_image.read_bytes(),
    )
    item = QuestionService(db).create_question_item(
        document_id=page.document_id, page_id=page.id, question_kind="error",
        question_number="22(1)", stem_text="根据共同图形作答。",
        ai_draft={"visual_material": {"dependency": "required", "regions": regions}},
    )
    monkeypatch.setattr(runtime, "application_database", lambda: db)
    images = []
    monkeypatch.setattr(content_ui.st, "image", lambda data, **kwargs: images.append(data))
    monkeypatch.setattr(content_ui, "render_question_math_markdown", lambda text: None)
    content_ui.render_training_question(SimpleNamespace(
        question_item_id=item.id, page_id=page.id, document_id=page.document_id,
        question_text=item.stem_text,
    ))
    assert len(images) == 1
    assert isinstance(images[0], bytes)
