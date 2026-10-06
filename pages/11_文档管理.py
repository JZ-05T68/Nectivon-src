"""全量文档清单与文档生命周期管理（永久删除）入口。"""

from __future__ import annotations

import logging

import streamlit as st

from src import __version__
from src.document_bulk_delete_service import DocumentBulkDeleteService
from src.document_deletion_ui import render_document_deletion_section
from src.runtime import (
    application_database,
    application_document_deletion_service,
)
from src.time_display import format_beijing_time
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)

st.set_page_config(page_title=f"删除文件 · Nectivon v{__version__}", page_icon="🗑️", layout="wide")
render_workspace("pages/11_文档管理.py")
st.title("删除文件")
st.caption("选择一份资料，先查看完整影响预览，确认后才会执行安全删除。学习整理成果属于你，不会随原始资料一起删除。支持多选批量删除。")

try:
    database = application_database()
    deletion_service = application_document_deletion_service()
    documents = database.list_documents()
except Exception as exc:
    LOGGER.exception("读取文档清单失败")
    st.error(f"读取文档清单失败：{exc}")
    st.stop()

# V086-306: the list must show AI reading progress separately from the
# import status, so 「导入完成」 can never hide 「Agent 阅读 0/6」. The
# reading store is auxiliary: any failure (e.g. AppTest harnesses without
# a runtime) degrades to the neutral label instead of breaking the page.
_reading_labels: dict[int, str] = {}
try:
    from src.agent_document_reader import AgentReadingStore, document_ai_reading_label
    from src.runtime import application_settings

    _reading_store = AgentReadingStore(application_settings().agent_readings_dir)
    _reading_labels = {
        document.id: document_ai_reading_label(_reading_store, document)
        for document in documents
    }
except Exception:  # noqa: BLE001 - auxiliary column must never kill the page
    LOGGER.debug("AI 阅读状态列不可用", exc_info=True)

deletion_flash = st.session_state.pop("doc_delete_flash", None)
if deletion_flash:
    flash_message, flash_warnings = deletion_flash
    st.success(flash_message)
    for flash_warning in flash_warnings:
        st.warning(flash_warning)

# Widget keys may only be removed before their widgets are instantiated in a
# run, so a successful deletion defers the cleanup of its confirmation inputs
# to the top of the next run via this flag.
if st.session_state.pop("doc_delete_reset_pending", False):
    for stale_key in [
        key for key in st.session_state if key.startswith("doc_delete_")
    ]:
        del st.session_state[stale_key]

# Selection identity is the document id, never a list position. A deleted
# document's id may still sit in session state after the rerun triggered by
# the deletion flow, so a stale value is dropped before the selectbox is
# instantiated and Streamlit falls back to the first available document.
document_ids = [document.id for document in documents]
if st.session_state.get("doc_manage_selected_document_id") not in document_ids:
    st.session_state.pop("doc_manage_selected_document_id", None)

if not documents:
    st.info("当前还没有已导入的文档。请先在「导入资料」页面导入 PDF 文档。")
    st.stop()

st.dataframe(
    [
        {
            "标题": document.title,
            "原始文件名": document.filename,
            "页数": document.page_count,
            "状态": document.status_label,
            "AI 阅读": _reading_labels.get(document.id, "AI 未阅读"),
            "导入时间": (
                f"{format_beijing_time(document.imported_at or document.created_at)}"
            ),
        }
        for document in documents
    ],
    use_container_width=True,
    hide_index=True,
)

st.divider()
st.header("删除导入文档")
st.caption("以下操作会影响整份文档，请谨慎执行。")

# ------------------------------------------------------------- 批量删除
st.subheader("批量删除")
st.caption(
    "勾选多份资料一次性删除。底层逐份走同一套安全删除流程（先预览、可回滚），"
    "一份失败不影响其它资料。学习整理成果（题目、方法、结论、掌握记录）保留。"
)

_bulk_filter_column, _bulk_action_column = st.columns([3, 1])
_bulk_filter = _bulk_filter_column.text_input(
    "按标题/文件名筛选", key="bulk_filter", value=""
)
filtered_documents = [
    document
    for document in documents
    if _bulk_filter.strip().lower() in document.title.lower()
    or _bulk_filter.strip().lower() in document.filename.lower()
]
filtered_labels = {
    document.id: (
        f"{document.title}（{document.filename}，{document.page_count} 页）"
    )
    for document in filtered_documents
}
if _bulk_action_column.button("全选当前筛选结果", key="bulk_select_all"):
    st.session_state["bulk_selected_ids"] = list(filtered_labels)
    # Deterministic reflection: the rerun instantiates the multiselect with
    # the stored selection (same-run instantiation proved unreliable in the
    # real UI, overnight round 2026-09-27).
    st.rerun()
selected_bulk_ids = st.multiselect(
    "选择要删除的资料（当前筛选："
    f"{len(filtered_documents)} 份 / 共 {len(documents)} 份）",
    options=list(filtered_labels),
    format_func=lambda document_id: filtered_labels.get(document_id, f"资料 {document_id}"),
    placeholder="选择要删除的资料（可多选）",
    key="bulk_selected_ids",
)

bulk_service = DocumentBulkDeleteService(deletion_service)
bulk_preview = None
if not selected_bulk_ids:
    st.caption("还没有勾选任何资料。")
elif st.button("生成批量删除预览", key="bulk_preview", type="primary"):
    try:
        bulk_preview = bulk_service.preview(list(selected_bulk_ids))
    except Exception as exc:  # noqa: BLE001 - preview failures surface honestly
        LOGGER.exception("批量删除预览失败")
        st.error(f"批量删除预览失败：{exc}")
    else:
        st.session_state["bulk_preview_data"] = bulk_preview

bulk_preview = st.session_state.get("bulk_preview_data")
if bulk_preview is not None and selected_bulk_ids:
    stale = set(selected_bulk_ids) != {
        preview.document_id for preview in bulk_preview.previews
    }
    if stale:
        st.info("勾选已变化，请重新生成预览。")
    else:
        size_mb = bulk_preview.total_size_bytes / (1024 * 1024)
        st.markdown(f"**准备删除 {bulk_preview.document_count} 份资料**")
        st.caption(
            f"共 {bulk_preview.page_count} 页，约 {size_mb:.1f} MB。将删除：原始文件、"
            "每一页的图像、系统识别出的文字、Agent 阅读结果、相关笔记与证据关联。"
        )
        st.success(
            "学习整理成果会保留：题目、方法族、题型族、二级结论、掌握记录、复习历史"
            "仍可继续使用；其中“来源”会标记为不可用。"
        )
        for document_id, reason in bulk_preview.errors:
            st.warning(f"资料 {document_id} 无法预览：{reason}")
        anomalies = bulk_preview.missing_or_anomalous()
        if anomalies:
            st.error("部分资料存在路径异常，已阻止删除：" + "；".join(
                f"资料 {document_id}：{'；'.join(reasons)}"
                for document_id, reasons in anomalies
            ))
        confirm_bulk = st.checkbox(
            f"我确认要删除以上 {bulk_preview.document_count} 份资料"
            "（学习整理成果保留）",
            key="bulk_confirm",
        )
        if st.button(
            f"执行批量删除（{bulk_preview.document_count} 份）",
            key="bulk_execute",
            type="primary",
            disabled=not confirm_bulk or bool(anomalies),
        ):
            outcomes = bulk_service.execute(list(selected_bulk_ids))
            st.session_state.pop("bulk_preview_data", None)
            st.session_state["bulk_outcomes"] = outcomes
            st.rerun()

bulk_outcomes = st.session_state.get("bulk_outcomes")
if bulk_outcomes:
    st.markdown(f"**批量删除结果：{bulk_service.summarize(bulk_outcomes)}**")
    for outcome in bulk_outcomes:
        if outcome.deleted:
            st.caption(f"✅ 已删除：{outcome.document_title}")
        else:
            st.warning(f"❌ 未删除（资料 {outcome.document_id}）：{outcome.message}")
    failed = bulk_service.failed_ids(bulk_outcomes)
    if failed and st.button("重试失败项", key="bulk_retry"):
        retry_outcomes = bulk_service.execute(failed)
        previous = [o for o in bulk_outcomes if o.deleted]
        st.session_state["bulk_outcomes"] = previous + retry_outcomes
        st.rerun()

st.divider()


documents_by_id = {document.id: document for document in documents}
selected_document_id = st.selectbox(
    "选择文档",
    options=document_ids,
    format_func=lambda document_id: (
        f"{documents_by_id[document_id].title}"
        f"（{documents_by_id[document_id].filename}，"
        f"{documents_by_id[document_id].page_count} 页）"
    ),
    key="doc_manage_selected_document_id",
)
selected_document = documents_by_id.get(selected_document_id)
if selected_document is None:
    st.error("所选文档已不存在，请重新选择。")
    st.stop()

render_document_deletion_section(
    deletion_service=deletion_service,
    document=selected_document,
)
