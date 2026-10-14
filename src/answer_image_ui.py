"""学习整理「我的作答」图片：阅读卡展示与管理、编辑表单内缩略图。

管理入口放在阅读卡「我的作答」正下方（保存后自动回到阅读卡）。编辑
表单是 ``st.form``，Streamlit 禁止表单内放按钮，且表单外的任何交互都
会让表单内未保存的文字回退——所以表单内只显示缩略图与指引，工具一律
在阅读卡展开。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import streamlit as st
from PIL import Image

from src.answer_image_service import (
    ANSWER_UPLOAD_LIMIT,
    AnswerImageError,
    AnswerUploadStore,
    answer_crop_scope,
    normalize_answer_material,
    remove_crop_reference,
    remove_upload_reference,
    sync_crop_references,
)
from src.question_visual_regions import crop_region

LOGGER = logging.getLogger(__name__)
_UPLOAD_EXTENSIONS = ["png", "jpg", "jpeg", "webp"]
_ANSWER_ROLE = "answer"


def answer_material_of(question) -> dict:
    """Read and validate the attachment block stored on this question."""

    draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
    return normalize_answer_material(draft.get("answer_material"))


def _resolve_views(
    material: dict, *, question, database, pages_dir: Path,
) -> list[dict]:
    """Build one render plan entry per attached image, crops first."""

    views: list[dict] = []
    for region in material["regions"]:
        view = {
            "kind": "crop", "ref": region, "data": None,
            "label": "", "problem": "",
        }
        page = None
        try:
            page = database.get_page(int(region["page_id"]))
        except Exception:  # noqa: BLE001 - a broken source must not hide the list
            LOGGER.exception("读取作答截图来源页失败：page_id=%s", region["page_id"])
        if page is None:
            view["problem"] = "这张作答截图的原页已随资料删除；引用可清除，图片不再显示。"
        else:
            document = database.get_document(page.document_id)
            title = getattr(document, "title", None) or "原卷"
            view["label"] = f"来自《{title}》第 {page.page_number} 页"
            image_path = Path(page.image_path)
            if not image_path.is_file():
                view["problem"] = "原页图像文件缺失，这张作答截图暂时无法显示。"
            else:
                data = crop_region(image_path, region)
                if data is None:
                    view["problem"] = "这处作答截图暂时无法裁切，请对照原页核对。"
                else:
                    view["data"] = data
        views.append(view)
    uploads = AnswerUploadStore(pages_dir, question.id)
    for entry in material["uploads"]:
        view = {
            "kind": "upload", "ref": entry, "data": None,
            "label": "上传的作答图片", "problem": "",
        }
        try:
            view["data"] = uploads.read(entry)
        except AnswerImageError as exc:
            view["problem"] = str(exc)
        views.append(view)
    return views


def _display_width(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as image:
        return min(650, max(260, image.width))


def _render_views(views: list[dict], *, question, question_service, key_prefix: str,
                  database, pages_dir: Path) -> None:
    """Show every attached image with a small source line and delete button."""

    for view in views:
        ref = view["ref"]
        if view["data"] is not None:
            st.image(view["data"], width=_display_width(view["data"]))
        caption, delete_col = st.columns([4, 1])
        caption.caption(view["label"] if view["problem"] == "" else view["problem"])
        identifier = ref["region_id"] if view["kind"] == "crop" else ref["id"]
        if delete_col.button(
            "删除", key=f"answer_del_{key_prefix}_{view['kind']}_{identifier}",
            help="只移除这条作答图片引用；原始页面与已存文件不会被删除。",
        ):
            material = answer_material_of(question)
            if view["kind"] == "crop":
                page = database.get_page(int(ref["page_id"]))
                new_material = remove_crop_reference(
                    material, page_id=int(ref["page_id"]), region_id=ref["region_id"],
                    page_image=Path(page.image_path) if page is not None else "",
                    question_id=question.id,
                )
            else:
                new_material = remove_upload_reference(material, ref["id"])
            question_service.update_answer_material(question.id, new_material)
            st.toast("已移除这张作答图片（原始文件保留）。")
            st.rerun()


def _render_upload_tool(
    material: dict, *, question, question_service, key_prefix: str, pages_dir: Path,
) -> None:
    # The browser re-sends a file_uploader's selection on every rerun, so the
    # only reliable way to clear it after processing is rotating the widget
    # key; skipping that would re-process (and re-report) the same files.
    nonce = st.session_state.get(f"answer_files_nonce_{key_prefix}", 0)
    uploader_key = f"answer_files_{key_prefix}_{nonce}"
    notes_key = f"answer_upload_notes_{key_prefix}"
    # Skipped-file reasons must survive the save rerun to stay visible.
    for note in st.session_state.pop(notes_key, []):
        st.warning(note)
    uploaded = st.file_uploader(
        "选择作答图片（可多选；PNG / JPG / WebP，单张 ≤ 24MB）",
        type=_UPLOAD_EXTENSIONS, accept_multiple_files=True, key=uploader_key,
    )
    if not uploaded:
        return
    store = AnswerUploadStore(pages_dir, question.id)
    saved, skipped = 0, []
    for upload in uploaded:
        name = getattr(upload, "name", "图片")
        if len(material["uploads"]) + saved >= ANSWER_UPLOAD_LIMIT:
            skipped.append(f"{name}：每题最多 {ANSWER_UPLOAD_LIMIT} 张作答图片。")
            continue
        try:
            entry = store.save(upload.getvalue())
        except AnswerImageError as exc:
            skipped.append(f"{name}：{exc}")
            continue
        if any(item["sha256"] == entry["sha256"] for item in material["uploads"]):
            skipped.append(f"{name}：与已保存的图片相同，未重复保存。")
            continue
        material["uploads"].append(entry)
        saved += 1
    st.session_state[f"answer_files_nonce_{key_prefix}"] = nonce + 1
    if saved:
        question_service.update_answer_material(question.id, material)
        if skipped:
            st.session_state[notes_key] = skipped
        st.toast(f"已保存 {saved} 张作答图片。")
        st.rerun()
    for note in skipped:
        st.warning(note)


def _render_crop_tool(
    material: dict, *, question, question_service, key_prefix: str, database,
) -> None:
    documents = database.list_documents()
    if not documents:
        st.info("还没有导入任何资料。请先到「导入资料」导入原卷，再回来框选作答。")
        return
    titles = {document.id: document.title for document in documents}
    options = [document.id for document in documents]
    default = question.document_id if question.document_id in options else options[0]
    document_id = st.selectbox(
        "① 选择原卷文件", options,
        index=options.index(default), format_func=lambda i: titles.get(i, str(i)),
        key=f"answer_doc_{key_prefix}",
    )
    pages = database.list_pages(document_id)
    if not pages:
        st.info("这份资料没有页面记录，无法框选。")
        return
    page_options = [page.id for page in pages]
    labels = {page.id: f"第 {page.page_number} 页" for page in pages}
    default_page = (
        question.page_id
        if document_id == question.document_id and question.page_id in page_options
        else page_options[0]
    )
    page_id = st.selectbox(
        "② 选择页码", page_options,
        index=page_options.index(default_page), format_func=lambda i: labels[i],
        key=f"answer_page_{key_prefix}_d{document_id}",
    )
    page = database.get_page(page_id)
    if page is None or not Path(page.image_path).is_file():
        st.warning("这一页的图像文件缺失，无法框选；可换一页或到「我的资料」核对。")
        return
    st.caption("③ 在下方大图上拖出方框圈住你的作答（可拖出多个方框；换页保存可继续追加）。")

    def on_saved(store) -> None:
        refs = store.regions(page_id=page_id)
        question_service.update_answer_material(
            question.id, sync_crop_references(material, page_id, refs),
        )

    from src.question_image_editor import render_crop_editor

    render_crop_editor(
        page.image_path, page_id=page_id, regions=[
            region for region in material["regions"]
            if region.get("page_id") == page_id
        ],
        key=f"answer_crop_{key_prefix}_p{page_id}",
        scope=answer_crop_scope(question.id),
        default_role=_ANSWER_ROLE, roles=[_ANSWER_ROLE],
        title="框选我的作答（在原卷大图上拖出方框）",
        caption=(
            "点击「添加截图方框」后在图上拖出范围；方框可移动、可调整、"
            "可删除，颜色仅用于区分。保存后立即显示在「我的作答」下方。"
        ),
        open_label="打开框选工具",
        toast="作答截图已保存。",
        on_saved=on_saved,
    )


def render_answer_image_section(
    question, *, question_service, database, pages_dir: Path, key_prefix: str,
) -> None:
    """阅读卡「我的作答」下方：图片展示 + 上传/原卷截图入口 + 逐图删除。"""

    material = answer_material_of(question)
    views = _resolve_views(material, question=question, database=database,
                           pages_dir=pages_dir)
    upload_open_key = f"answer_upload_open_{key_prefix}"
    crop_open_key = f"answer_crop_open_{key_prefix}"
    if views:
        st.markdown("**我的作答图片**")
        _render_views(views, question=question, question_service=question_service,
                      key_prefix=key_prefix, database=database, pages_dir=pages_dir)
    left, right = st.columns(2)
    if left.button(
        "📷 上传作答图片", key=f"answer_upload_btn_{key_prefix}",
        type="primary" if st.session_state.get(upload_open_key) else "secondary",
    ):
        st.session_state[upload_open_key] = not st.session_state.get(upload_open_key)
        st.session_state[crop_open_key] = False
        st.rerun()
    if right.button(
        "✂️ 原卷截图（选文件 → 选页码 → 框选）", key=f"answer_crop_btn_{key_prefix}",
        type="primary" if st.session_state.get(crop_open_key) else "secondary",
    ):
        st.session_state[crop_open_key] = not st.session_state.get(crop_open_key)
        st.session_state[upload_open_key] = False
        st.rerun()
    if not views and not (st.session_state.get(upload_open_key)
                          or st.session_state.get(crop_open_key)):
        st.caption(
            "作答可以附图片：上传答题纸照片，或从原卷任意一页框选截图"
            "（支持多个方框）。图片只保存在本机，不会进入 AI 调用。"
        )
    if st.session_state.get(upload_open_key):
        _render_upload_tool(
            material, question=question, question_service=question_service,
            key_prefix=key_prefix, pages_dir=pages_dir,
        )
    if st.session_state.get(crop_open_key):
        _render_crop_tool(
            material, question=question, question_service=question_service,
            key_prefix=key_prefix, database=database,
        )


def render_answer_edit_hint(
    question, *, database, pages_dir: Path, key_prefix: str,
) -> None:
    """编辑表单内 ② 输入框下方：已附图片缩略图 + 去阅读卡管理的指引。

    表单内不能放按钮（Streamlit 限制），且表单外交互会让未保存文字回退；
    所以这里只做静态展示，管理动作统一放在阅读卡。
    """

    material = answer_material_of(question)
    crops, uploads = len(material["regions"]), len(material["uploads"])
    if not crops and not uploads:
        st.caption(
            "作答可以附图片：保存后回到阅读卡，在「我的作答」下方用"
            "「上传作答图片」或「原卷截图」（选文件 → 选页码 → 框选多个方框）。"
        )
        return
    st.caption(
        f"已附作答图片 {crops + uploads} 张（原卷截图 {crops} · 上传 {uploads}）。"
        "添加或删除请保存后回到阅读卡，在「我的作答」下方操作。"
    )
    views = _resolve_views(material, question=question, database=database,
                           pages_dir=pages_dir)
    if views:
        columns = st.columns(min(4, len(views)))
        for column, view in zip(columns, views[:4], strict=False):
            if view["data"] is not None:
                column.image(view["data"], width=180)
        if len(views) > 4:
            st.caption(f"……其余 {len(views) - 4} 张在阅读卡查看。")
