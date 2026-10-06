"""Local drawing of one associated figure and crop editing over its original page."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

import streamlit as st
from streamlit.components.v1 import declare_component

from src.question_image_store import MAX_IMAGE_BYTES, QuestionImageError
from src.question_region_store import BOX_COLORS, QuestionRegionStore

_COMPONENTS = Path(__file__).parent / "components"
_draw_component = declare_component(
    "question_image_editor", path=_COMPONENTS / "question_image_editor",
)
_crop_component = declare_component(
    "question_crop_editor", path=_COMPONENTS / "question_crop_editor",
)


def _data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _new_event(value: object, key: str) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("request_id"), str)
            and value["request_id"] != st.session_state.get(f"{key}_consumed"))


def _check_event(value: dict, store: QuestionRegionStore, revision: str) -> None:
    if (value.get("source_hash") != store.source_sha256
            or value.get("revision") != revision or value.get("scope") != store.scope):
        raise QuestionImageError("本题图片已改变，请重新打开工具后再修改。")


def render_question_image_editor(
    source: Path | str, *, page_id: int, number: str, regions: list[dict], key: str,
) -> None:
    """Keep the drawing tools intact, showing only one of this question's crops."""

    if not Path(source).is_file() or not number.strip():
        return
    with st.expander("题目图片：对照绘图修订"):
        st.caption("只显示本题关联的截图；保存后同步候选、学习整理、讲题和训练。")
        try:
            store = QuestionRegionStore(source, number, regions)
            state = store.state()
            figures = state["regions"]
            if not figures:
                st.caption("本题尚无关联截图，请在下方「修改截图方框」中添加范围。")
                return
            labels = {
                item["id"]: f"图 {i + 1}"
                + (f" · 选项 {item['option_label']}" if item["role"] == "option" else "")
                + (f" · {item['description']}" if item["description"] else "")
                for i, item in enumerate(figures)
            }
            identifier = st.selectbox(
                "选择本题图片", list(labels), format_func=labels.get, key=f"{key}_figure",
            ) if len(figures) > 1 else figures[0]["id"]
            if not st.checkbox("打开对照绘图工具", key=f"{key}_open"):
                return
            st.caption("左侧原图，右侧修改图。支持吸管取色、调色板、画笔和几何图形；点击保存才生效。")
            original, current = store.crop(identifier, original=True), store.crop(identifier)
            if original is None or current is None:
                raise QuestionImageError("截图已改变，请重新打开工具。")
            value = _draw_component(
                original=_data_url(original), current=_data_url(current),
                source_hash=store.source_sha256, scope=store.scope, region_id=identifier,
                revision=state["revision"], key=f"{key}_canvas", default=None,
            )
            if _new_event(value, key):
                st.session_state[f"{key}_consumed"] = value["request_id"]
                _check_event(value, store, state["revision"])
                if value.get("region_id") != identifier:
                    raise QuestionImageError("图片选择已改变，请重新打开绘图工具。")
                encoded = value.get("data_url", "")
                if (not isinstance(encoded, str) or not encoded.startswith("data:image/png;base64,")
                        or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 40):
                    raise QuestionImageError("绘图结果格式无效或过大，未保存。")
                try:
                    data = base64.b64decode(encoded.split(",", 1)[1], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise QuestionImageError("绘图结果数据无效，未保存。") from exc
                store.save_crop(identifier, data, expected_revision=state["revision"])
                st.toast("绘图修订已保存，已同步本题关联图片。")
                st.rerun()
            st.download_button(
                "下载当前题目显示图", data=current,
                file_name=f"{store.source.stem}_{number}_题目显示图.png", mime="image/png",
                key=f"{key}_download",
            )
            if st.button("恢复原始扫描图显示", key=f"{key}_restore"):
                store.restore_crop(identifier, expected_revision=state["revision"])
                st.toast("已恢复这张截图的原图显示，历史修订仍保留。")
                st.rerun()
        except (OSError, QuestionImageError) as exc:
            st.error(f"题目图片处理失败：{exc}")


def render_question_crop_editor(
    source: Path | str, *, page_id: int, number: str, regions: list[dict], key: str,
) -> None:
    """Edit differently colored rectangles on the untouched full-size source scan."""

    if not Path(source).is_file() or not number.strip():
        return
    with st.expander("查看原始扫描大图／修改截图方框"):
        st.caption("在原始大图上移动或调整方框，也可拖出新范围；不同图片使用不同颜色。保存后同步本题所有位置。")
        if not st.checkbox("打开截图方框编辑", key=f"{key}_open"):
            return
        try:
            store = QuestionRegionStore(source, number, regions)
            state = store.state()
            value = _crop_component(
                original=_data_url(store.source.read_bytes()), source_hash=store.source_sha256,
                scope=store.scope, revision=state["revision"], regions=state["regions"],
                colors=BOX_COLORS, key=f"{key}_boxes", default=None,
            )
            if _new_event(value, key):
                st.session_state[f"{key}_consumed"] = value["request_id"]
                _check_event(value, store, state["revision"])
                store.save_regions(value.get("regions"), expected_revision=state["revision"])
                st.toast("截图方框已保存，已同步本题关联图片。")
                st.rerun()
        except (OSError, QuestionImageError) as exc:
            st.error(f"截图范围处理失败：{exc}")
