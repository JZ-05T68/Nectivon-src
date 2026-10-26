"""Cross-photo provenance, cancellation semantics and atomic failure handling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src.ai.provider import CompletionResult
from src.database import Database
from src.learning_workflow_service import QuestionService
from src.question_content_ui import _coalesce_overlapping_figures, question_regions
from src.question_figure_service import (
    FigureSelectionStore,
    available_figure_sources,
    editable_bounds,
    resolve_figure_source,
    save_figure_selection,
)
from src.question_image_store import QuestionImageError
from src.question_visual_regions import crop_region, normalize_regions


class Vision:
    default_model = "test-vision"

    def __init__(self, response: str | None = None):
        self.calls = []
        self.response = response or json.dumps({
            "description": "原图所示连接", "labels": ["$p_1$"], "warnings": [],
        })

    def complete_vision(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return CompletionResult(self.response, self.default_model)


@pytest.fixture()
def library(tmp_path: Path):
    db = Database(tmp_path / "database" / "knowledge.db")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    pages = []
    for i in range(3):
        path = pages_dir / f"{i}.png"
        image = Image.new("RGB", (400 + 100 * i, 300), "#d8d3c9")
        ImageDraw.Draw(image).rectangle((40, 40, 180, 160), outline="black", width=2)
        image.save(path)
        doc = db.create_document(title=f"批次 {i}", filename=path.name, source_path=path,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 page_count=1, import_status="completed")
        pages.append(db.create_page(document_id=doc.id, page_number=1, image_path=path,
                                    extracted_text="", status="pending"))
    region = {"role": "shared", "bbox": [60, 80, 500, 650], "option_label": "",
              "description": "跨照片图", "source_page_id": pages[1].id,
              "source_document_id": pages[1].document_id}
    service = QuestionService(db)
    question = service.create_question_item(
        document_id=pages[0].document_id, page_id=pages[0].id, subject="工程课",
        question_number="Q-1", question_kind="error", stem_text=r"求 $p_1$",
        ai_draft={"visual_material": {"dependency": "required", "regions": [region]}},
    )
    store = FigureSelectionStore(pages[0].image_path, "Q-1", [region])
    return db, pages_dir, pages, service, question, region, store


def save(library, provider, **updates):
    db, pages_dir, pages, _service, _question, _region, store = library
    args = dict(database=db, pages_dir=pages_dir, anchor_page_id=pages[0].id, number="Q-1",
                selected_index=0, source_page_id=pages[2].id, expected_revision="original",
                provider=provider, boxes=[{"id": "a" * 32, "bounds": [20, 30, 230, 210],
                                         "role": "shared"}])
    return save_figure_selection(store, **(args | updates))


def test_cross_batch_save_refresh_and_reopen_share_the_exact_source(library) -> None:
    db, pages_dir, pages, service, question, region, store = library
    originals = [Path(p.image_path).read_bytes() for p in pages]
    assert resolve_figure_source(db, pages_dir, pages[0].id, region).page_id == pages[1].id
    assert len(available_figure_sources(db, pages_dir)) == 3
    vision = Vision()
    result = save(library, vision)
    # Round 4: the recognition call plus one replica-spec attempt (the fake's
    # recognition JSON is not a valid replica spec, so the pipeline records a
    # failed_parse and stops — exactly one extra bounded call).
    assert len(vision.calls) == 2
    assert vision.calls[0][1]["target_refs"] == (f"page:{pages[2].id}",)
    assert result["replica_statuses"] == ["failed_parse"]
    assert vision.calls[1][1]["source_feature"] == "figure_replica_spec"
    resolved = question_regions(pages[0].image_path, "Q-1", [region], page_id=pages[0].id)
    actual = resolve_figure_source(db, pages_dir, pages[0].id, resolved[0])
    assert actual.page_id == pages[2].id
    assert editable_bounds(actual, resolved[0]) == [20, 30, 230, 210]
    import io

    assert Image.open(io.BytesIO(crop_region(actual.path, resolved[0]))).size == (210, 180)
    reopened = FigureSelectionStore(pages[0].image_path, "Q-1", [])
    assert reopened.state()["revision"] == result["revision"]
    changed = service.get_question_item(question.id)
    assert changed.stem_text == question.stem_text
    assert changed.ai_draft["visual_material"]["regions"] == resolved
    assert service.list_content_revisions(question.id)[-1]["field"] == "visual_material"
    assert [Path(p.image_path).read_bytes() for p in pages] == originals
    assert store.active.is_file()


@pytest.mark.parametrize("failure", ["cloud", "invalid_json", "no_provider", "bad_box"])
def test_failed_processing_preserves_previous_good_image_and_content(library, failure) -> None:
    db, _pages_dir, _pages, service, question, _region, store = library
    good = save(library, Vision())
    previous = store.active.read_bytes()
    item = service.get_question_item(question.id)
    provider = Vision("not JSON") if failure == "invalid_json" else Vision()
    if failure == "cloud":
        def fail(*_args, **_kwargs):
            raise RuntimeError("injected cloud failure")
        provider.complete_vision = fail
    if failure == "no_provider":
        provider = None
    args = {"expected_revision": good["revision"]}
    if failure == "bad_box":
        args["boxes"] = [{"id": "a" * 32, "bounds": [-1, 2, 60, 90]}]
    with pytest.raises((RuntimeError, QuestionImageError)):
        save(library, provider, **args)
    assert store.active.read_bytes() == previous
    assert service.get_question_item(question.id).ai_draft == item.ai_draft
    assert len(service.list_content_revisions(question.id)) == 1
    if failure == "invalid_json":
        assert len(list(store.root.glob("vision-response-*.json"))) == 2
    assert db.get_page(question.page_id) is not None


def test_read_open_and_cancel_make_no_writes_or_calls(library) -> None:
    db, pages_dir, pages, _service, _question, region, store = library
    source = resolve_figure_source(db, pages_dir, pages[0].id, region)
    editable_bounds(source, region)
    store.state()
    assert not store.root.exists()


def test_missing_mismatched_outside_and_stale_sources_never_fall_back(library, tmp_path) -> None:
    db, pages_dir, pages, _service, _question, region, _store = library
    for invalid in [region | {"source_page_id": 9999},
                    region | {"source_page_id": "unresolved"},
                    region | {"source_page_id": False},
                    region | {"source_document_id": pages[0].document_id},
                    region | {"image_sha256": "0" * 64}]:
        with pytest.raises(QuestionImageError):
            resolve_figure_source(db, pages_dir, pages[0].id, normalize_regions([invalid])[0])
    with pytest.raises(QuestionImageError):
        resolve_figure_source(db, tmp_path / "other-instance", pages[0].id, region)
    saved = save(library, Vision())
    with pytest.raises(QuestionImageError, match="其它位置"):
        save(library, Vision())
    assert saved["revision"] != "original"


def test_equal_boxes_from_different_photos_are_never_deduplicated_or_merged() -> None:
    a = {"role": "shared", "bbox": [100, 100, 800, 800], "source_page_id": 8}
    b = a | {"source_page_id": 19}
    assert len(normalize_regions([a, b])) == 2
    assert len(_coalesce_overlapping_figures([a, b])) == 2


def test_old_automatic_boxes_recover_arrow_to_sibling_gutter_without_reimport(library) -> None:
    _db, _pages_dir, pages, _service, _question, _region, _store = library
    # Mirror the ordinary data/pages/document/page layout used by imports.
    root = Path(pages[0].image_path).parent.parent
    page_dir = root / "pages" / "document"
    page_dir.mkdir()
    source = page_dir / "scan.png"
    Image.new("RGB", (800, 600), "white").save(source)
    metadata = root / "question-candidates"
    metadata.mkdir()
    (metadata / "figures_page_1.json").write_text(json.dumps({
        "page_id": 1, "figures": [
            {"name": "fig-A", "bbox": [40, 100, 390, 300]},
            {"name": "fig-B", "bbox": [450, 100, 900, 300]},
        ],
    }), encoding="utf-8")
    automatic = {"role": "shared", "bbox": [40, 100, 390, 300], "page_id": 1,
                 "description": "fig-A", "sibling_clip": [0, 100, 390, 300]}
    resolved = question_regions(source, "new-Q", [automatic], page_id=1)
    assert resolved[0]["sibling_clip"][2] == 420
    assert "crop_scope" not in resolved[0]  # An AI proposal is not a committed human box.


def test_failure_after_pointer_activation_rolls_back_selection(library, monkeypatch) -> None:
    _db, _pages_dir, _pages, service, question, _region, store = library
    good = save(library, Vision())
    previous, draft = store.active.read_bytes(), service.get_question_item(question.id).ai_draft
    real_publish = store.publish

    def interrupted(value, persist):
        def fail_after_activate(activate):
            def failing_activate():
                activate()
                raise OSError("injected commit failure")
            persist(failing_activate)
        real_publish(value, fail_after_activate)

    monkeypatch.setattr(store, "publish", interrupted)
    with pytest.raises(OSError):
        save(library, Vision(), expected_revision=good["revision"])
    assert store.active.read_bytes() == previous
    assert service.get_question_item(question.id).ai_draft == draft
    assert len(service.list_content_revisions(question.id)) == 1
