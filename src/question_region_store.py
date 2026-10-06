"""Per-question crop bounds and manual drawing revisions over immutable scans."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from src.question_image_store import MAX_IMAGE_BYTES, QuestionImageError, QuestionImageStore
from src.question_visual_regions import REGION_ROLES, normalize_regions, region_pixel_bounds

BOX_COLORS = ("#d32f2f", "#1565c0", "#2e7d32", "#7b1fa2", "#e65100", "#00838f")


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{32}", value))


def question_crop_scope(number: str) -> str:
    """Use a printed question number consistently across all local surfaces."""

    number = number.translate(str.maketrans("（）．", "().")).strip().rstrip(".")
    number = re.sub(r"[\s\-−–—]+(?=\()", "", number)
    if not number:
        raise QuestionImageError("题目缺少稳定题号，无法保存图片修订。")
    return hashlib.sha256(number.encode("utf-8")).hexdigest()[:32]


class QuestionRegionStore:
    """One authoritative crop list per question; patches never affect other items."""

    def __init__(
        self, source: Path | str, number: str = "", defaults: list[dict] | None = None,
        *, scope: str | None = None,
    ):
        self.page_store = QuestionImageStore(source)
        self.source = self.page_store.source
        self.source_sha256 = self.page_store.source_sha256
        self.size = self.page_store.size
        self.scope = scope or question_crop_scope(number)
        if not _identifier(self.scope):
            raise QuestionImageError("题目图片标识无效。")
        self.root = self.page_store.root / "questions" / self.scope
        self.defaults = defaults or []

    def _bounds(self, supplied: object) -> list[int]:
        if (not isinstance(supplied, list) or len(supplied) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in supplied)):
            raise QuestionImageError("截图方框坐标无效，未保存。")
        bounds = [round(v) for v in supplied]
        x1, y1, x2, y2 = bounds
        if not (0 <= x1 < x2 <= self.size[0] and 0 <= y1 < y2 <= self.size[1]
                and x2 - x1 >= 4 and y2 - y1 >= 4):
            raise QuestionImageError("截图方框须位于原图内，宽高至少为 4 像素。")
        return bounds

    def _region(self, supplied: dict) -> dict:
        if not isinstance(supplied, dict) or not _identifier(supplied.get("id")):
            raise QuestionImageError("截图方框标识无效。")
        role = supplied.get("role", "stem")
        label = str(supplied.get("option_label", ""))
        if (not isinstance(role, str) or role not in REGION_ROLES
                or (role == "option" and not re.fullmatch(r"[A-Z]", label))):
            raise QuestionImageError("截图用途或选项编号无效。")
        return {"id": supplied["id"], "bounds": self._bounds(supplied.get("bounds")),
                "role": role, "option_label": label if role == "option" else "",
                "description": str(supplied.get("description", ""))[:240]}

    def state(self) -> dict:
        """Read checked current state, or seed actual visible AI crops without writes."""

        path = self.root / "active.json"
        if not path.exists():
            regions = []
            for region in normalize_regions(self.defaults):
                if region.get("image_sha256", self.source_sha256) != self.source_sha256:
                    continue
                bounds = region.get("crop_bounds") or region_pixel_bounds(self.source, region)
                if bounds is None:
                    continue
                identifier = region.get("region_id") or hashlib.sha256(json.dumps(
                    [region["role"], region["bbox"], region["option_label"]],
                    separators=(",", ":"),
                ).encode()).hexdigest()[:32]
                regions.append(self._region({**region, "id": identifier, "bounds": list(bounds)}))
            return {"revision": "original", "regions": regions, "source_sha256": self.source_sha256}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (value["source_sha256"] != self.source_sha256 or value["scope"] != self.scope
                    or not _identifier(value["revision"]) or not isinstance(value["regions"], list)
                    or len(value["regions"]) > 40):
                raise ValueError("Invalid crop record")
            seen = set()
            for item in value["regions"]:
                self._region(item)
                if item["id"] in seen:
                    raise ValueError("Duplicate crop")
                seen.add(item["id"])
                patch = item.get("patch")
                if patch:
                    if (not _identifier(patch["revision"])
                            or not re.fullmatch(r"[0-9a-f]{64}", patch["sha256"])):
                        raise ValueError("Invalid drawing revision")
                    self._bounds(patch["bounds"])
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuestionImageError("本题截图修订记录不可用，请核对原始扫描图。") from exc

    def _save(self, regions: list[dict]) -> dict:
        value = {"revision": uuid4().hex, "scope": self.scope, "source_sha256": self.source_sha256,
                 "regions": regions, "saved_at": datetime.now(UTC).isoformat(), "origin": "manual"}
        self.root.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(value, ensure_ascii=False, indent=2)
        (self.root / f"history-{value['revision']}.json").write_text(serialized, encoding="utf-8")
        temporary = self.root / f"active-{value['revision']}.tmp"
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(self.root / "active.json")
        return value

    def _current(self, expected_revision: str) -> dict:
        value = self.state()
        if value["revision"] != expected_revision:
            raise QuestionImageError("本题图片已在其它位置修改，请重新打开工具再保存。")
        return value

    def save_regions(self, supplied: list[dict], *, expected_revision: str) -> dict:
        """Confirm exact human boxes, keeping drawings anchored to source pixels."""

        value = self._current(expected_revision)
        if not isinstance(supplied, list) or len(supplied) > 40:
            raise QuestionImageError("一题最多支持 40 个截图方框。")
        old = {item["id"]: item for item in value["regions"]}
        regions = [self._region(item) for item in supplied]
        if len({r["id"] for r in regions}) != len(regions):
            raise QuestionImageError("截图方框标识重复，未保存。")
        for item in regions:
            if old.get(item["id"], {}).get("patch"):
                item["patch"] = copy.deepcopy(old[item["id"]]["patch"])
        return self._save(regions)

    def crop(
        self, identifier: str, *, original: bool = False, fallback: dict | None = None,
    ) -> bytes | None:
        """Return only this figure; expanding a box retains existing manual strokes."""

        value = self.state()
        if fallback and fallback.get("image_sha256", self.source_sha256) != self.source_sha256:
            return None
        item = next((r for r in value["regions"] if r["id"] == identifier), None)
        if item is None and value["revision"] == "original" and fallback:
            item = self._region({**fallback, "id": identifier,
                                 "bounds": fallback.get("crop_bounds")})
        if item is None:
            return None
        path = self.source if original else self.page_store.display_path()
        with Image.open(path) as image:
            image = image.convert("RGB")
        patch = None if original else item.get("patch")
        if patch:
            data = (self.root / f"{patch['revision']}.png").read_bytes()
            if hashlib.sha256(data).hexdigest() != patch["sha256"]:
                raise QuestionImageError("本题绘图副本校验失败，请核对原图。")
            with Image.open(io.BytesIO(data)) as drawing:
                x1, y1, x2, y2 = patch["bounds"]
                if drawing.size != (x2 - x1, y2 - y1):
                    raise QuestionImageError("本题绘图副本尺寸不一致。")
                image.paste(drawing.convert("RGB"), (x1, y1))
        output = io.BytesIO()
        image.crop(tuple(item["bounds"])).save(output, format="PNG")
        return output.getvalue()

    def save_crop(self, identifier: str, data: bytes, *, expected_revision: str) -> dict:
        """Save one explicitly drawn figure without changing its neighbors."""

        value = self._current(expected_revision)
        item = next((r for r in value["regions"] if r["id"] == identifier), None)
        if item is None or not data or len(data) > MAX_IMAGE_BYTES:
            raise QuestionImageError("绘图结果或截图标识无效。")
        x1, y1, x2, y2 = item["bounds"]
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.size != (x2 - x1, y2 - y1):
                    raise QuestionImageError("绘图尺寸与当前截图方框不一致，请重新打开绘图工具。")
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                canonical = output.getvalue()
        except (OSError, Image.DecompressionBombError) as exc:
            raise QuestionImageError("无法读取绘图结果。") from exc
        revision = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{revision}.png").write_bytes(canonical)
        item["patch"] = {"revision": revision, "bounds": item["bounds"].copy(),
                         "sha256": hashlib.sha256(canonical).hexdigest()}
        return self._save(value["regions"])

    def restore_crop(self, identifier: str, *, expected_revision: str) -> dict:
        """Remove only the selected drawing overlay, retaining bounds and history."""

        value = self._current(expected_revision)
        item = next((r for r in value["regions"] if r["id"] == identifier), None)
        if item is None:
            raise QuestionImageError("截图已删除，请重新打开工具。")
        item.pop("patch", None)
        return self._save(value["regions"])

    def regions(self, *, page_id: int | None = None) -> list[dict]:
        """Attach stable references consumed by every existing question renderer."""

        width, height = self.size
        result = []
        for item in self.state()["regions"]:
            x1, y1, x2, y2 = item["bounds"]
            result.append({"role": item["role"], "option_label": item["option_label"],
                           "description": item["description"],
                           "bbox": [x1 * 1000 / width, y1 * 1000 / height,
                                    x2 * 1000 / width, y2 * 1000 / height],
                           "crop_scope": self.scope,
                           "region_id": item["id"], "crop_bounds": item["bounds"].copy(),
                           "image_sha256": self.source_sha256,
                           **({"page_id": page_id} if page_id is not None else {})})
        return result


def resolve_question_regions(
    source: Path | str | None, number: str, defaults: list[dict], *, page_id: int,
    committed_only: bool = False,
) -> list[dict]:
    """Resolve the same committed boxes in candidates, learning, and training."""

    if source is None or not number.strip() or not Path(source).is_file():
        return normalize_regions(defaults)
    try:
        store = QuestionRegionStore(source, number, defaults)
        if committed_only and store.state()["revision"] == "original":
            return normalize_regions(defaults)
        return store.regions(page_id=page_id)
    except (OSError, QuestionImageError):
        logging.getLogger(__name__).exception("本题图片修订不可用，显示原始裁图")
        return normalize_regions(defaults)
