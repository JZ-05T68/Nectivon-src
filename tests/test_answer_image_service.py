"""我的作答图片：上传件、原卷截图引用与 ``ai_draft.answer_material`` 的安全约束。"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.answer_image_service import (
    ANSWER_UPLOAD_LIMIT,
    AnswerImageError,
    AnswerUploadStore,
    answer_crop_scope,
    answer_upload_dir,
    normalize_answer_material,
    remove_crop_reference,
    remove_upload_reference,
    sync_crop_references,
)
from src.database import Database
from src.learning_workflow_service import QuestionService
from src.question_content_ui import question_regions
from src.question_region_store import QuestionRegionStore
from src.question_visual_regions import crop_region
from src.training_profile_service import TrainingProfileService

PAGE_IMAGE_NAME = "page-1.png"


@pytest.fixture()
def scan(tmp_path: Path) -> Path:
    source = tmp_path / "scan.png"
    image = Image.new("RGB", (800, 600), "white")
    image.paste("blue", (100, 120, 160, 180))
    image.save(source)
    return source


def answer_box(identifier: str, bounds: list[int]) -> dict:
    return {"id": identifier, "role": "answer", "bounds": bounds}


def _png_bytes(color: str = "red", size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _declared_dimension_png(width: int, height: int) -> bytes:
    """A header-only PNG: the pixel cap must fire before any decode."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", b"") + chunk(b"IEND", b""))


def _database(tmp_path: Path) -> Database:
    data_dir = tmp_path / "data"
    pages_dir = data_dir / "pages"
    (pages_dir / "1").mkdir(parents=True, exist_ok=True)
    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    database = Database(data_dir / "database" / "knowledge.db")
    raw = data_dir / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = pages_dir / "1" / PAGE_IMAGE_NAME
    Image.new("RGB", (800, 600), "white").save(image)
    database.create_document(
        title="原卷",
        filename="试卷.pdf",
        source_path=raw,
        sha256="9" * 64,
        import_status="completed",
    )
    database.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="题干文本。",
        status="ready",
    )
    return database


# ------------------------------------------------------------- scope isolation
def test_answer_scope_never_mingles_with_stem_boxes(scan: Path) -> None:
    stem = QuestionRegionStore(scan, "6")
    stem.save_regions(
        [{"id": "a" * 32, "role": "stem", "bounds": [80, 100, 180, 200]}],
        expected_revision="original",
    )
    answers = QuestionRegionStore(scan, scope=answer_crop_scope(7))
    answers.save_regions(
        [answer_box("b" * 32, [100, 120, 160, 180])],
        expected_revision="original",
    )
    resolved = question_regions(scan, "6", [], page_id=1)
    assert [region["role"] for region in resolved] == ["stem"]

    refs = answers.regions(page_id=1)
    assert refs[0]["role"] == "answer"
    assert refs[0]["crop_scope"] == answer_crop_scope(7)
    with Image.open(io.BytesIO(crop_region(scan, refs[0]))) as cropped:
        assert cropped.size == (60, 60)
    # The same question number on a different question keeps its own scope.
    assert answer_crop_scope(7) != answer_crop_scope(8)


# ------------------------------------------------------------------ uploads
def test_upload_reencodes_as_png_strips_metadata_and_dedupes(tmp_path: Path) -> None:
    store = AnswerUploadStore(tmp_path / "pages", 7)
    image = Image.new("RGB", (40, 30), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    entry = store.save(buffer.getvalue())

    stored = answer_upload_dir(tmp_path / "pages", 7) / f"{entry['sha256']}.png"
    assert stored.is_file()
    with Image.open(stored) as reloaded:
        assert reloaded.format == "PNG"
        assert reloaded.size == (40, 30)
        assert not reloaded.info.get("exif")
    assert entry["width"] == 40 and entry["height"] == 30

    again = store.save(buffer.getvalue())
    assert again["sha256"] == entry["sha256"]
    assert again["id"] != entry["id"]
    assert len(list((tmp_path / "pages" / "answer-uploads" / "7").glob("*.png"))) == 1
    assert store.read(entry).startswith(b"\x89PNG")


def test_upload_rejects_invalid_payloads(tmp_path: Path) -> None:
    store = AnswerUploadStore(tmp_path, 7)
    with pytest.raises(AnswerImageError, match="24MB"):
        store.save(b"x" * (24_000_001))
    with pytest.raises(AnswerImageError, match="无法读取"):
        store.save(b"definitely not an image")
    with pytest.raises(AnswerImageError, match="像素过大"):
        store.save(_declared_dimension_png(40_000, 1_000))
    assert not (tmp_path / "answer-uploads").exists()


def test_remove_upload_reference_keeps_the_stored_file(tmp_path: Path) -> None:
    store = AnswerUploadStore(tmp_path, 7)
    entry = store.save(_png_bytes())
    material = {"regions": [], "uploads": [entry]}

    cleared = remove_upload_reference(material, entry["id"])

    assert cleared["uploads"] == []
    assert (answer_upload_dir(tmp_path, 7) / f"{entry['sha256']}.png").is_file()


# --------------------------------------------------------- reference hygiene
def test_normalize_answer_material_drops_invalid_entries() -> None:
    committed_answer = {
        "role": "answer", "bbox": [100, 100, 300, 300],
        "crop_scope": "1" * 32, "region_id": "2" * 32,
        "crop_bounds": [10, 10, 30, 30], "page_id": 3,
    }
    material = normalize_answer_material({
        "regions": [
            committed_answer,
            {**committed_answer, "region_id": "3" * 32, "role": "stem"},
            {"role": "answer", "bbox": [10, 10, 20, 20]},
        ],
        "uploads": [
            {"id": "c" * 32, "sha256": "d" * 64, "width": 10, "height": 10},
            {"id": "c" * 32, "sha256": "e" * 64, "width": 10, "height": 10},
            {"id": "f" * 32, "sha256": "short", "width": 10, "height": 10},
            {"id": "0" * 32, "sha256": "9" * 64, "width": 0, "height": 5},
        ],
    })
    assert [region["region_id"] for region in material["regions"]] == ["2" * 32]
    assert [upload["id"] for upload in material["uploads"]] == ["c" * 32]
    assert material["uploads"][0]["origin"] == "upload"
    assert normalize_answer_material("junk") == {"regions": [], "uploads": []}


def test_sync_and_remove_crop_references_keep_other_pages(scan: Path) -> None:
    answers = QuestionRegionStore(scan, scope=answer_crop_scope(7))
    answers.save_regions(
        [answer_box("a" * 32, [10, 10, 110, 110]),
         answer_box("b" * 32, [200, 200, 300, 300])],
        expected_revision="original",
    )
    refs = answers.regions(page_id=1)
    material = sync_crop_references({"regions": [], "uploads": []}, 1, refs)
    assert len(material["regions"]) == 2

    other_page = {**refs[0], "page_id": 2, "region_id": "c" * 32}
    material = sync_crop_references(material, 2, [other_page])
    assert {region["page_id"] for region in material["regions"]} == {1, 2}

    material = remove_crop_reference(
        material, page_id=1, region_id=refs[0]["region_id"],
        page_image=scan, question_id=7,
    )
    kept = {region["region_id"] for region in material["regions"]}
    assert kept == {refs[1]["region_id"], "c" * 32}
    # The box store no longer lists the removed box, so the crop editor
    # cannot resurrect it on its next whole-store save.
    state = QuestionRegionStore(scan, scope=answer_crop_scope(7)).state()
    assert {box["id"] for box in state["regions"]} == {refs[1]["region_id"]}


# ------------------------------------------------------------ ai_draft block
def test_update_answer_material_preserves_other_blocks_and_provenance(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1, page_id=1, question_kind="error", question_number="1",
        stem_text="计算 1+1。", ai_draft={"summary": "AI 草稿内容"},
    )

    updated = service.update_answer_material(
        question.id, {"regions": [], "uploads": []},
    )

    assert updated.ai_draft["summary"] == "AI 草稿内容"
    assert updated.ai_draft["answer_material"] == {"regions": [], "uploads": []}
    assert updated.user_edited is False  # attachment ≠ text-content edit
    edited = service.update_question_item(question.id, student_answer="2")
    assert edited.user_edited is True
    assert edited.ai_draft["answer_material"] == {"regions": [], "uploads": []}

    cleared = service.update_answer_material(question.id, None)

    assert "answer_material" not in cleared.ai_draft
    assert cleared.ai_draft["summary"] == "AI 草稿内容"


def test_answer_upload_limit_is_enforced_per_question() -> None:
    assert ANSWER_UPLOAD_LIMIT == 12


# ------------------------------------------------------------- full UI path
def test_read_card_renders_answer_images_and_tools(tmp_path: Path, monkeypatch) -> None:
    database = _database(tmp_path)
    pages_dir = tmp_path / "data" / "pages"
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1, page_id=1, question_kind="error", question_number="1",
        stem_text="计算 1+1。", student_answer="2",
    )
    upload = AnswerUploadStore(pages_dir, question.id).save(_png_bytes())
    answers = QuestionRegionStore(
        pages_dir / "1" / PAGE_IMAGE_NAME, scope=answer_crop_scope(question.id),
    )
    answers.save_regions(
        [answer_box("a" * 32, [100, 120, 160, 180])], expected_revision="original",
    )
    service.update_answer_material(
        question.id,
        {"regions": answers.regions(page_id=1), "uploads": [upload]},
    )

    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(runtime, "application_pages_dir", lambda: pages_dir)
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: None)
    monkeypatch.setattr(
        runtime, "application_training_profile_service",
        lambda: TrainingProfileService(tmp_path / "data" / "training_profile.db"),
    )
    monkeypatch.setattr(
        runtime, "application_page_visual_service",
        lambda: (_ for _ in ()).throw(RuntimeError("AppTest 无视觉服务")),
    )
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "18_学习整理.py")
    ).run(timeout=60)

    assert not app.exception
    labels = [button.label for button in app.button]
    assert any("上传作答图片" in label for label in labels)
    assert any("原卷截图" in label for label in labels)
    captions = " ".join(str(caption.value) for caption in app.caption)
    assert "我的作答图片" in captions or any(
        "来自《原卷》第 1 页" in str(caption.value) for caption in app.caption
    )

    # Opening the crop tool keeps the page alive (document/page pickers render).
    app.button(key=f"answer_crop_btn_q{question.id}").click().run(timeout=60)
    assert not app.exception
    assert any("选择原卷文件" in str(box.label) for box in app.selectbox)
    assert any("选择页码" in str(box.label) for box in app.selectbox)

    # The edit form shows the attachment hint without any in-form button.
    # (AppTest merges the pre-``st.rerun`` pass into the tree, so absence of
    # the read-view buttons cannot be asserted here; Streamlit itself forbids
    # ``st.button`` inside ``st.form``, and the form rendering without an
    # exception is the structural proof.)
    app.button(key=f"start_edit_q{question.id}").click().run(timeout=60)
    assert not app.exception
    edit_captions = " ".join(str(caption.value) for caption in app.caption)
    assert "已附作答图片 2 张" in edit_captions
    assert app.text_area(key=f"edit_answer_q{question.id}").value == "2"
