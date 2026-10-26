"""Local drawing of one associated figure and crop editing over its original page."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from collections.abc import Callable
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


def render_crop_editor(
    source: Path | str, *, page_id: int, regions: list[dict], key: str,
    number: str = "", scope: str | None = None,
    default_role: str = "stem", roles: list[str] | None = None,
    title: str = "查看原始扫描大图／修改截图方框",
    caption: str = (
        "在原始大图上移动或调整方框，也可拖出新范围；不同图片使用不同颜色。"
        "保存后同步本题所有位置。"
    ),
    open_label: str = "打开截图方框编辑",
    toast: str = "截图方框已保存，已同步本题关联图片。",
    on_saved: Callable[[QuestionRegionStore], None] | None = None,
) -> None:
    """Edit colored rectangles on the untouched full-size source scan.

    ``scope`` overrides the printed-question-number scope so a caller can keep
    its own box list (e.g. 作答截图) separate from the question-stem crops.
    ``on_saved`` runs after a successful commit and receives the store, so the
    caller can persist the freshly resolved references right away.
    """

    if not Path(source).is_file():
        return
    if scope is None and not number.strip():
        return
    with st.expander(title):
        st.caption(caption)
        if not st.checkbox(open_label, key=f"{key}_open"):
            return
        try:
            store = QuestionRegionStore(source, number, regions, scope=scope)
            state = store.state()
            value = _crop_component(
                original=_data_url(store.source.read_bytes()), source_hash=store.source_sha256,
                scope=store.scope, revision=state["revision"], regions=state["regions"],
                colors=BOX_COLORS, key=f"{key}_boxes", default=None,
                default_role=default_role, roles=roles,
            )
            if _new_event(value, key):
                st.session_state[f"{key}_consumed"] = value["request_id"]
                _check_event(value, store, state["revision"])
                store.save_regions(value.get("regions"), expected_revision=state["revision"])
                if on_saved is not None:
                    on_saved(store)
                st.toast(toast)
                st.rerun()
        except (OSError, QuestionImageError) as exc:
            st.error(f"截图范围处理失败：{exc}")


def render_question_crop_editor(
    source: Path | str, *, page_id: int, number: str, regions: list[dict], key: str,
    title: str = "查看原始扫描大图／修改截图方框",
    open_label: str = "打开截图方框编辑",
    on_saved: Callable[[QuestionRegionStore], None] | None = None,
) -> None:
    """Open the displayed figure's real source and publish only successful edits.

    Round 4 adds the replica actions: 生成复刻图 (one bounded cloud pipeline
    per missing figure) and 人工确认 for replicas that the automated visual
    check could not clear.
    """

    if not Path(source).is_file() or not number.strip():
        return
    columns = st.columns(2)
    if columns[0].button(
        "✏️ 修改图片", key=f"{key}_edit",
        help="调整来源照片及选框，保存后自动重新识别并生成复刻",
    ):
        _figure_selection_dialog(source, page_id=page_id, number=number, regions=regions, key=key)
    if columns[1].button(
        "📐 生成复刻图", key=f"{key}_replica",
        help="为缺少已核验复刻图的题图生成教材插图复刻（每次每图最多 4 次云端调用）",
    ):
        _generate_replicas_action(source, page_id=page_id, number=number,
                                  regions=regions, key=key)
    _render_replica_acceptance(source, regions=regions, key=key)


def _render_replica_acceptance(source: Path | str, *, regions: list[dict], key: str) -> None:
    """Offer explicit human acceptance for replicas awaiting review."""

    from src.figure_replica_service import accept_replica, load_replica, replica_png

    pending = []
    for index, (source_path, data, _page_ref, _region) in enumerate(
        _replica_region_crops(source, regions)
    ):
        replica = load_replica(source_path, data)
        if replica and replica.get("status") == "failed_visual":
            pending.append((index, source_path, data, replica))
    if not pending:
        return
    with st.expander(f"⏳ {len(pending)} 张复刻图等待你的人工核验", expanded=False):
        st.caption(
            "自动结构核验未通过的复刻图不会作为默认展示图；你人工核对无异后可确认采用。"
        )
        for index, source_path, data, replica in pending:
            png = replica_png(source_path, data)
            if png:
                st.image(png, caption=f"图 {index + 1}（待核验）", width="stretch")
            verification = replica.get("verification") or {}
            for field, label in (("missing", "缺失"), ("extra", "多余"),
                                 ("wrong", "差异"), ("text_errors", "文字")):
                for item in verification.get(field, []):
                    st.caption(f"{label}: {item}")
            if st.button(f"✅ 人工确认图 {index + 1} 复刻可用",
                         key=f"{key}_accept_{index}"):
                accept_replica(source_path, data)
                st.toast("已按你的确认采用该复刻图（记录为人工核验通过）。")
                st.rerun()


def _replica_region_crops(source: Path | str, regions: list[dict]) -> list[tuple]:
    """Resolve each figure region to (source_path, crop_bytes, page_ref)."""

    from src.question_content_ui import _region_image_path
    from src.question_visual_regions import crop_region

    crops = []
    for region in regions:
        if region.get("role") not in ("stem", "shared", "option", "question"):
            continue
        source_path = _region_image_path(source, region)
        if source_path is None:
            continue
        data = crop_region(source_path, region)
        if data is None:
            continue
        page_ref = f"page:{region.get('source_page_id') or region.get('page_id', 0)}"
        crops.append((source_path, data, page_ref, region))
    return crops


def _generate_replicas_action(
    source: Path | str, *, page_id: int, number: str, regions: list[dict], key: str,
) -> None:
    """User-initiated, bounded replica generation for this question's figures."""

    from src.figure_replica_service import (
        STATUS_VERIFIED,
        generate_figure_replica,
        load_replica,
    )
    from src.runtime import application_question_vision_provider

    provider = application_question_vision_provider()
    if provider is None:
        st.error("未配置云端读图模型，无法生成教材插图复刻。")
        return
    crops = _replica_region_crops(source, regions)
    if not crops:
        st.warning("本题没有可复刻的题图区域。")
        return
    outcomes: list[str] = []
    with st.status("正在生成教材插图复刻（每图最多 4 次云端调用）…",
                   expanded=True) as status:
        for index, (source_path, data, page_ref, _region) in enumerate(crops):
            existing = load_replica(source_path, data)
            if existing and existing.get("status") in (STATUS_VERIFIED, "accepted"):
                outcomes.append(f"图 {index + 1}：已有已核验复刻，跳过")
                continue
            st.write(f"图 {index + 1}：识别结构并渲染复刻…")
            try:
                record = generate_figure_replica(
                    provider, data, source_path=source_path, page_ref=page_ref,
                    provenance={"question": number, "page_id": page_id,
                                "trigger": "manual_replica_button"},
                )
            except Exception as exc:  # noqa: BLE001 - one region never blocks the rest
                logging.getLogger(__name__).exception("复刻生成失败：page=%s", page_id)
                outcomes.append(f"图 {index + 1}：生成失败（{exc}）")
                continue
            label = {
                "verified": "已通过自动结构核验",
                "failed_visual": "已生成，待人工核验（自动核验发现差异）",
                "failed_parse": "生成失败（模型输出无效），可稍后再试",
                "unsupported": "该图不适合结构化复刻（照片类插图）",
            }.get(record["status"], record["status"])
            outcomes.append(f"图 {index + 1}：{label}")
        status.update(label="复刻生成完成", state="complete", expanded=True)
    for line in outcomes:
        if "已通过" in line or "跳过" in line:
            st.success(line)
        elif "待人工核验" in line or "不适合" in line:
            st.info(line)
        elif "失败" in line:
            st.error(line)
        else:
            st.write(line)
    st.rerun()


@st.dialog("修改图片", width="large")
def _figure_selection_dialog(
    anchor: Path | str, *, page_id: int, number: str, regions: list[dict], key: str,
) -> None:
    """Keep uncommitted source/rectangle choices inside the editor only."""

    from src.question_figure_service import (
        FigureSelectionStore,
        available_figure_sources,
        editable_bounds,
        resolve_figure_source,
        save_figure_selection,
    )
    from src.runtime import (
        application_database,
        application_pages_dir,
        application_question_vision_provider,
    )

    try:
        database, pages_dir = application_database(), application_pages_dir()
        store = FigureSelectionStore(anchor, number, regions)
        state = store.state()
        all_regions = state["regions"]
        choices = [i for i, r in enumerate(all_regions)
                   if r["role"] in ("stem", "shared", "option")]
        if not choices:
            choices = list(range(len(all_regions)))
        selected = (st.selectbox(
            "选择要修改的题图", choices,
            format_func=lambda i: f"图 {choices.index(i) + 1} · "
            + (all_regions[i].get("source_label") or all_regions[i].get("description") or "本页图"),
            key=f"{key}_{state['revision']}_region",
        ) if len(choices) > 1 else choices[0] if choices else None)
        region = all_regions[selected] if selected is not None else {}
        sources = available_figure_sources(database, pages_dir)
        source_map = {s.page_id: s for s in sources}
        # A broken explicit source must be shown as an error, never preselect
        # the stem page. The student may explicitly choose a replacement.
        initial = None
        try:
            initial = resolve_figure_source(database, pages_dir, page_id, region)
        except QuestionImageError as exc:
            st.warning(str(exc))
        identifiers = list(source_map)
        if not identifiers:
            raise QuestionImageError("当前资料库没有可用的原始照片。")
        source_id = st.selectbox(
            "来源照片（可选择其它导入批次）", identifiers,
            index=identifiers.index(initial.page_id) if initial else None,
            placeholder="请选择可访问的已导入照片", format_func=lambda i: source_map[i].label,
            key=f"{key}_{state['revision']}_{selected}_source",
        )
        if source_id is None:
            if st.button("不保存", key=f"{key}_cancel_empty"):
                st.rerun()
            return
        actual = source_map[source_id]
        st.caption(f"当前编辑来源：{actual.label} · 来源页 ID {actual.page_id}")
        st.caption("移动框内可拖动，拖动边角可缩放；也可添加新框。保存前不会修改题目或调用 AI。")
        default_boxes = []
        if initial is not None and actual.page_id == initial.page_id and selected is not None:
            identifier = hashlib.sha256(
                json.dumps(region, sort_keys=True).encode(),
            ).hexdigest()[:32]
            default_boxes = [{"id": identifier, "bounds": editable_bounds(actual, region),
                              "role": region["role"], "option_label": region["option_label"],
                              "description": region["description"]}]
        image_store = QuestionRegionStore(actual.path, number)
        token = f"{state['revision']}:{actual.page_id}:{selected}"
        value = _crop_component(
            original=_data_url(actual.path.read_bytes()), source_hash=image_store.source_sha256,
            scope=image_store.scope, revision=token, regions=default_boxes,
            colors=BOX_COLORS, key=f"{key}_source_boxes", default=None,
            default_role=region.get("role", "stem"),
            roles=["stem", "shared", "option", "question"],
            save_label="保存", allow_cancel=True,
        )
        if _new_event(value, key):
            st.session_state[f"{key}_consumed"] = value["request_id"]
            if value.get("action") == "cancel":
                st.rerun()
            _check_event(value, image_store, token)
            try:
                with st.spinner("正在识别所选区域并生成教材插图复刻…"):
                    saved = save_figure_selection(
                        store, database=database, pages_dir=pages_dir, anchor_page_id=page_id,
                        number=number, selected_index=selected, source_page_id=actual.page_id,
                        boxes=value.get("regions"), expected_revision=state["revision"],
                        provider=application_question_vision_provider(),
                    )
            except Exception as exc:  # Paid calls are never retried after a failed save.
                logging.getLogger(__name__).exception("题图保存失败：page=%s", page_id)
                st.error(f"保存失败，原有题图和内容已保留：{exc}")
            else:
                outcomes = saved.get("replica_statuses") or []
                summary = {
                    "verified": "已核验", "accepted": "已采用", "failed_visual": "待人工核验",
                    "failed_parse": "复刻生成失败", "unsupported": "不适合结构化复刻",
                    "error": "复刻生成出错",
                }
                parts = [summary.get(item, item) for item in outcomes] or ["未生成复刻"]
                st.toast(f"来源和选框已保存，区域识别已更新；复刻图：{'、'.join(parts)}。")
                st.rerun()
    except (OSError, ValueError, RuntimeError) as exc:
        st.error(f"无法编辑题图：{exc}")
