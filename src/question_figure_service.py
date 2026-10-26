"""Strict figure provenance and staged, reversible cross-photo crop revisions."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from src.figure_scan_service import CLEANUP_VERSION, enhance_figure_scan
from src.question_image_store import QuestionImageError
from src.question_region_store import QuestionRegionStore
from src.question_visual_regions import normalize_regions, region_pixel_bounds


@dataclass(frozen=True)
class FigureSource:
    """A live imported page in the current local library, never a guessed path."""

    page_id: int
    document_id: int
    path: Path
    label: str


def resolve_figure_source(database, pages_dir: Path, page_id: int, region: dict) -> FigureSource:
    """Refuse missing, mismatched, deleted or out-of-library source pages."""

    identifier = region.get("source_page_id", region.get("page_id", page_id))
    if type(identifier) is not int or identifier <= 0:
        raise QuestionImageError("图片来源页无效，请重新选择已导入照片。")
    page = database.get_page(identifier)
    document = database.get_document(page.document_id) if page is not None else None
    if page is None or document is None:
        raise QuestionImageError("题图来源照片已不可用；未回退到题干照片。")
    if region.get("source_document_id", page.document_id) != page.document_id:
        raise QuestionImageError("题图来源文档与来源页不一致；未打开其它照片。")
    path = Path(page.image_path).resolve()
    if not path.is_relative_to(pages_dir.resolve()) or not path.is_file():
        raise QuestionImageError("来源照片不在当前资料库内或文件缺失，无法访问。")
    digest = region.get("image_sha256")
    if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise QuestionImageError("来源照片校验不一致，请核对原始资料。")
    return FigureSource(page.id, page.document_id, path,
                        f"《{document.title}》第 {page.page_number} 页")


def available_figure_sources(database, pages_dir: Path) -> list[FigureSource]:
    """Offer only active imports inside this instance, including other batches."""

    sources = []
    for document in database.list_documents():
        for page in database.list_pages(document.id):
            try:
                sources.append(resolve_figure_source(database, pages_dir, page.id, {}))
            except QuestionImageError:
                continue  # Unavailable files cannot be selected or sent to AI.
    return sources


class FigureSelectionStore:
    """One authoritative source/box list anchored to the question's original page.

    Histories, source hashes, exact pixel boxes, clean images and raw model
    responses are append-only. Only the small active pointer is replaced.
    Legacy drawing revisions remain independent and readable.
    """

    def __init__(self, source: Path | str, number: str, defaults: list[dict]):
        anchor = QuestionRegionStore(source, number)
        self.root = anchor.root
        self.anchor_hash = anchor.source_sha256
        self.defaults = normalize_regions(defaults)
        self.active = self.root / "selection-active.json"

    def state(self) -> dict:
        if not self.active.exists():
            return {"revision": "original", "regions": self.defaults}
        try:
            value = json.loads(self.active.read_text(encoding="utf-8"))
            if (value["anchor_sha256"] != self.anchor_hash
                    or (value["revision"] != "original" and len(value["revision"]) != 32)
                    or normalize_regions(value["regions"]) != value["regions"]):
                raise ValueError("Invalid figure selection")
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuestionImageError("图片来源修订记录损坏，请核对资料；未回退旧来源。") from exc

    def require_revision(self, revision: str) -> None:
        if self.state()["revision"] != revision:
            raise QuestionImageError("本题图片已在其它位置修改，请重新打开后保存。")

    def publish(self, value: dict, persist) -> None:
        """Roll back the active pointer if the SQLite transaction cannot commit."""

        self.root.mkdir(parents=True, exist_ok=True)
        previous = self.active.read_bytes() if self.active.exists() else None
        temporary = self.root / f"selection-{value['revision']}.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            persist(lambda: temporary.replace(self.active))
        except Exception:
            if previous is None:
                # Keep a valid original-state pointer; do not delete user files.
                previous = json.dumps({"anchor_sha256": self.anchor_hash,
                                       "revision": "original", "regions": self.defaults}).encode()
            rollback = self.root / f"rollback-{uuid4().hex}.tmp"
            rollback.write_bytes(previous)
            rollback.replace(self.active)
            raise


def editable_bounds(source: FigureSource, region: dict) -> list[int]:
    """Seed the editor with exactly the pixels used by the current figure view."""

    bounds = region_pixel_bounds(source.path, region)
    if bounds is None:
        raise QuestionImageError("无法核实当前题图选框，请核对来源照片。")
    return list(bounds)


def _recognize_figure(provider, raw: bytes, source: FigureSource, response_dir: Path) -> dict:
    """Make one audited visual call; a diagram never replaces the question stem."""

    from src.recognition_json import repair_latex_json_escapes
    from src.visual_input_budget import prepare_page_image

    if provider is None:
        raise QuestionImageError("未配置云端读图模型，未保存修改；原有题图和内容已保留。")
    prepared = prepare_page_image(raw)
    completion = provider.complete_vision(
        "请忠实核对用户框选的工程题图，只依据图片，不补画、不解题。"
        "描述可见的图形结构、连接和箭头，逐项抄录可辨认的编号与变量（数学用 LaTeX）。"
        "无法确认的线条或字符写入 warnings，不能猜测。"
        '仅返回 JSON：{"description":"可见结构描述","labels":["标注"],"warnings":[]}。',
        prepared.data_url, model=provider.default_model, max_completion_tokens=2048,
        json_output=True, source_feature="figure_crop_edit",
        target_refs=(f"page:{source.page_id}",),
    )
    response_dir.mkdir(parents=True, exist_ok=True)
    (response_dir / f"vision-response-{uuid4().hex}.json").write_text(json.dumps({
        "source_page_id": source.page_id, "model": completion.model,
        "resolved_model": completion.resolved_model, "raw_response": completion.text,
        "at": datetime.now(UTC).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        text = completion.text.strip().removeprefix("```json").removeprefix("```")
        payload = json.loads(repair_latex_json_escapes(text.removesuffix("```").strip()))
        if (not isinstance(payload, dict) or not isinstance(payload.get("description"), str)
                or not payload["description"].strip()
                or not isinstance(payload.get("labels"), list)
                or not isinstance(payload.get("warnings"), list)
                or any(not isinstance(x, str) for x in payload["labels"] + payload["warnings"])):
            raise ValueError("Invalid figure reading")
    except (ValueError, TypeError) as exc:
        raise QuestionImageError("云端图形识别结果无效，未保存；原有题图已保留。") from exc
    return {"raw_response": completion.text, "model": completion.model,
            "resolved_model": completion.resolved_model, "reading": payload}


def save_figure_selection(
    store: FigureSelectionStore, *, database, pages_dir: Path, anchor_page_id: int,
    number: str, selected_index: int | None, source_page_id: int, boxes: list[dict],
    expected_revision: str, provider,
) -> dict:
    """Validate, crop, clean and recognize before atomically publishing a change."""

    store.require_revision(expected_revision)
    source = resolve_figure_source(database, pages_dir, source_page_id, {})
    if not isinstance(boxes, list) or not 1 <= len(boxes) <= 40:
        raise QuestionImageError("请保留至少一个有效选框；未保存修改。")
    validator = QuestionRegionStore(source.path, number)
    checked = [validator._region(box) for box in boxes]
    if len({box["id"] for box in checked}) != len(checked):
        raise QuestionImageError("选框标识重复，未保存。")
    digest = validator.source_sha256
    additions, outputs = [], []
    with Image.open(source.path) as photo:
        width, height = photo.size
        for box in checked:
            x1, y1, x2, y2 = box["bounds"]
            region = {"role": box["role"], "option_label": box["option_label"],
                      "description": box["description"],
                      "bbox": [x1 * 1000 / width, y1 * 1000 / height,
                               x2 * 1000 / width, y2 * 1000 / height],
                      "manual_bounds": box["bounds"], "image_sha256": digest,
                      "page_id": anchor_page_id, "source_page_id": source.page_id,
                      "source_document_id": source.document_id, "source_label": source.label}
            raw = io.BytesIO()
            photo.crop((x1, y1, x2, y2)).convert("RGB").save(raw, format="PNG")
            outputs.append((raw.getvalue(), enhance_figure_scan(raw.getvalue())))
            additions.append(region)
    # Each explicit save makes one call for the selected area, even if the
    # student draws several boxes; the union has the same source coordinates.
    union = [min(b["bounds"][0] for b in checked), min(b["bounds"][1] for b in checked),
             max(b["bounds"][2] for b in checked), max(b["bounds"][3] for b in checked)]
    with Image.open(source.path) as photo:
        raw = io.BytesIO()
        photo.crop(tuple(union)).convert("RGB").save(raw, format="PNG")
    recognition = _recognize_figure(provider, raw.getvalue(), source, store.root)
    store.require_revision(expected_revision)
    resolve_figure_source(database, pages_dir, source.page_id, {"image_sha256": digest})
    regions = list(store.state()["regions"])
    if selected_index is None:
        regions.extend(additions)
    elif type(selected_index) is int and 0 <= selected_index < len(regions):
        regions[selected_index:selected_index + 1] = additions
    else:
        raise QuestionImageError("当前图片选择已变化，请重新打开编辑器。")
    if len(regions) > 40:
        raise QuestionImageError("一题最多支持 40 个图形区域。")
    revision = uuid4().hex
    value = {"revision": revision, "anchor_sha256": store.anchor_hash,
             "regions": normalize_regions(regions), "previous_revision": expected_revision,
             "saved_at": datetime.now(UTC).isoformat(), "cleanup_version": CLEANUP_VERSION,
             "recognition": recognition, "source_page_id": source.page_id,
             "source_document_id": source.document_id, "bounds": union}
    store.root.mkdir(parents=True, exist_ok=True)
    for index, (raw_crop, clean) in enumerate(outputs):
        (store.root / f"selection-{revision}-{index}-raw.png").write_bytes(raw_crop)
        (store.root / f"selection-{revision}-{index}-clean.png").write_bytes(clean)
    (store.root / f"selection-{revision}.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    def persist(activate) -> None:
        # The manifest is canonical for candidates and learning alike. SQLite
        # mirrors it for export, question metadata and the existing revision UI.
        with database._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, ai_draft FROM question_items WHERE page_id=? AND question_number=?",
                (anchor_page_id, number),
            ).fetchall()
            for row in rows:
                draft = json.loads(row["ai_draft"] or "{}")
                previous = draft.get("visual_material", {})
                material = {**previous, "regions": value["regions"],
                            "figure_reading": recognition, "figure_revision": revision,
                            "material_notes": "图片来源与选框已修改，云端区域识别已更新。",
                            "binding_confirmed": True, "binding_provenance": "USER CROP SELECTION"}
                draft["visual_material"] = material
                connection.execute(
                    "UPDATE question_items SET ai_draft=?, updated_at=? WHERE id=?",
                    (json.dumps(draft, ensure_ascii=False), value["saved_at"], row["id"]),
                )
                connection.execute(
                    "INSERT INTO question_content_revisions "
                    "(question_id,field,old_value,new_value,change_source,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (row["id"], "visual_material", json.dumps(previous, ensure_ascii=False),
                     json.dumps(material, ensure_ascii=False),
                     "figure_crop_edit", value["saved_at"]),
                )
            store.require_revision(expected_revision)
            activate()

    store.publish(value, persist)

    # Round 4: publish first, then a bounded replica pipeline per saved box.
    # A replica failure must never invalidate the already-committed save; the
    # display falls back to the explicit 待处理/待核验 notice, never the photo.
    replica_statuses: list[str] = []
    if provider is not None:
        from src.figure_replica_service import generate_figure_replica

        for index, (raw_crop, _clean) in enumerate(outputs):
            try:
                record = generate_figure_replica(
                    provider, raw_crop, source_path=source.path,
                    page_ref=f"page:{source.page_id}",
                    provenance={"question": number, "revision": revision,
                                "bounds": checked[index]["bounds"],
                                "trigger": "figure_crop_edit"},
                )
                replica_statuses.append(record["status"])
            except Exception:  # noqa: BLE001 - save stays committed
                import logging

                logging.getLogger(__name__).exception(
                    "题图复刻生成失败：page=%s number=%s", anchor_page_id, number)
                replica_statuses.append("error")
    return {**value, "replica_statuses": replica_statuses}
