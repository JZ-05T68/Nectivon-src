"""Import documents and let the local Agent read every page.

Import state semantics (V086-101/203 fix): the file import itself and the AI
page reading are two different statuses and must be reported separately.
A document whose import completed is never presented as "导入失败" when only
the AI reading step failed; the honest message is "已导入，AI 阅读未完成".

V086-301 fix: the batch is now a durable queue (``import_queue`` table,
schema v21).  Every uploaded file is persisted as a row **before**
processing starts, so navigating away can pause a batch but can never
silently erase it.  Returning to the page shows the persisted batch state
(总数 / 已完成 / 进行中 / 等待 / 失败), interrupted files are marked
honestly, and the remaining files can be resumed with one click.  Files
are isolated: one file's import/OCR/reading failure is recorded on its own
row and never swallows the rest of the batch (V086-301 §6.4).
"""

from __future__ import annotations

import logging

import streamlit as st

from src import __version__
from src.agent.local_client import LocalDocumentAgentClient
from src.agent_document_reader import AgentReadingStore
from src.ai.provider import AIUnavailableError
from src.import_queue_service import (
    DEDUP_NOTICE,
    ImportQueueEntry,
    ImportQueueService,
    new_run_id,
)
from src.ocr_engine import OcrUnavailable
from src.runtime import (
    application_ai_provider,
    application_database,
    application_document_service,
    application_settings,
)
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)

#: Per-file outcome icons shown in the durable batch table.
_STATUS_ICONS = {
    "queued": "⏳",
    "importing": "🔄",
    "imported": "⚠️",
    "complete": "✅",
    "failed": "❌",
    "interrupted": "⏸️",
}
_STATUS_LABELS = {
    "queued": "等待",
    "importing": "处理中",
    "imported": "已导入，AI 阅读未完成",
    "complete": "完成",
    "failed": "导入失败",
    "interrupted": "已暂停（可继续）",
}
_RUN_ID_KEY = "import_page_run_id"
_BATCH_ID_KEY = "import_page_batch_id"


def _queue_service() -> ImportQueueService:
    return ImportQueueService(application_database())


def _local_agent_client() -> LocalDocumentAgentClient:
    """Build the in-process Agent from the same local database as the page."""

    settings = application_settings()
    provider = application_ai_provider()
    return LocalDocumentAgentClient(
        database=application_database(),
        provider=provider,
        readings=AgentReadingStore(settings.agent_readings_dir),
        model=provider.default_model if provider is not None else "",
    )


def _read_one_document(document_id: int, page_count: int) -> tuple[bool, str]:
    """Run the page-by-page AI reading; return (fully_read, user_message)."""

    progress = st.progress(0, text="正在读取……")

    def update_progress(current: int, total: int) -> None:
        ratio = current / total if total else 0
        progress.progress(ratio, text=f"正在读取 {current} / {total} 页")

    try:
        _local_agent_client().read_document(
            document_id, progress_callback=update_progress
        )
    except AIUnavailableError:
        progress.empty()
        return False, "AI 未配置：资料已保存，AI 阅读未执行；配置后可在「我的资料」重试。"
    except Exception as exc:  # noqa: BLE001 - honest per-file reading outcome
        LOGGER.exception("Agent 读取资料失败：document_id=%s", document_id)
        progress.empty()
        # The import itself completed at this point; only the AI reading of
        # some pages failed. Keep the two statuses separate (V086-101/203).
        return False, f"资料已导入，{exc}"
    progress.progress(1.0, text=f"已读完 {page_count} / {page_count} 页")
    # V086-212: success is expressed by the queue status (✅ 完成), never by
    # writing text into error_message.
    return True, ""


def _import_ocr_one_file(
    entry: ImportQueueEntry, file_content: bytes
) -> tuple[int | None, bool]:
    """Import one file and run per-page OCR.

    Returns ``(document_id, duplicate)``. Import failures raise; per-page
    OCR failures are isolated so one broken page never blocks the file
    (and one broken file never blocks the batch).
    """

    document_service = application_document_service()
    result = document_service.import_document(
        file_content=file_content,
        filename=entry.filename,
    )
    if not result.duplicate:
        for page in result.pages:
            try:
                document_service.run_page_ocr(page.id)
            except OcrUnavailable:
                # Text pages remain readable without OCR. A page with no
                # usable text is reported honestly by the reading step.
                pass
            except Exception:  # noqa: BLE001 - OCR failure is page-local
                LOGGER.exception(
                    "单页 OCR 失败（不影响后续处理）：entry=%s page_id=%s",
                    entry.id,
                    page.id,
                )
    return result.document.id, bool(result.duplicate)


def _process_queue_entries(
    entries: list[ImportQueueEntry], uploads_by_name: dict[str, object]
) -> None:
    """Process queue rows one by one with honest per-row state transitions."""

    if not entries:
        return
    run_id = st.session_state.get(_RUN_ID_KEY) or new_run_id()
    st.session_state[_RUN_ID_KEY] = run_id
    queue = _queue_service()
    overall = st.progress(0.0, text="正在处理上传队列……")
    total = len(entries)
    for index, entry in enumerate(entries):
        overall.progress(
            index / total if total else 1.0,
            text=f"正在处理 {index + 1} / {total} 份：{entry.filename}",
        )
        queue.start_entry(entry.id, run_id=run_id)
        upload = uploads_by_name.get(entry.filename)
        if upload is None:
            queue.mark_failed(entry.id, message="导入失败：文件内容不可用（请重新选择文件）。")
            continue
        try:
            document_id, duplicate = _import_ocr_one_file(entry, upload.getvalue())
        except Exception as exc:
            LOGGER.exception("资料导入失败：filename=%s", entry.filename)
            queue.mark_failed(entry.id, message=f"导入失败：{exc}")
            continue
        if document_id is None:
            queue.mark_failed(entry.id, message="导入失败：未知错误。")
            continue
        if duplicate:
            # Content dedup (V086-105): the document already exists; never
            # re-trigger a paid AI reading for it. The neutral dedup note is
            # informational (kept out of "success text" semantics, V086-212).
            queue.mark_complete(
                entry.id,
                document_id=document_id,
                message=DEDUP_NOTICE,
            )
            continue
        fully_read, message = _read_one_document(
            document_id, upload.page_count if hasattr(upload, "page_count") else 0
        )
        if fully_read:
            queue.mark_complete(entry.id, document_id=document_id, message=message)
        else:
            # Honest separation (V086-306): the document exists and is
            # usable; only the AI reading is not done.
            queue.mark_imported(entry.id, document_id=document_id, message=message)
    overall.progress(1.0, text="上传队列处理完成。")


def _enqueue_and_process(uploads: list) -> list[ImportQueueEntry]:
    """Enqueue uploaded files into the durable batch and process them now.

    Shared by the general file uploader and the photo/image entry so both
    paths have identical queue, dedup and resume semantics (§17: never a
    second image backend).
    """

    uploads_by_name = {upload.name: upload for upload in uploads}
    open_batch_id = queue.latest_open_batch_id()
    reusable: list[ImportQueueEntry] = []
    new_names = list(uploads_by_name)
    if open_batch_id:
        pending = queue.queued_entries(open_batch_id)
        pending_names = {entry.filename for entry in pending}
        reusable = [entry for entry in pending if entry.filename in uploads_by_name]
        new_names = [name for name in uploads_by_name if name not in pending_names]
    batch_id = open_batch_id or ""
    if new_names:
        batch_id = queue.enqueue_batch(new_names, batch_id=open_batch_id)
    st.session_state[_BATCH_ID_KEY] = batch_id
    if open_batch_id:
        fresh = [
            entry
            for entry in queue.list_batch(batch_id)
            if entry.status == "queued" and entry not in reusable
        ]
        entries = reusable + fresh
    else:
        entries = queue.list_batch(batch_id)
    _process_queue_entries(entries, uploads_by_name)
    # The queue section must reflect post-processing reality in the SAME run.
    return queue.list_batch(batch_id)


st.set_page_config(
    page_title=f"导入资料 · Nectivon v{__version__}", page_icon="📥", layout="wide"
)
render_workspace("pages/1_导入资料.py")
st.title("添加一份资料")
st.caption(
    "可以一次选择多个文件；点一下后 Agent 会逐份按页读完；原文件和每一页都保存在本机。"
    "图片（JPG/PNG/WEBP）按原样保存为一份一页资料。"
)

uploaded_documents = st.file_uploader(
    "选择 PDF、Word、PowerPoint 或图片文件（可多选）",
    type=["pdf", "doc", "docx", "ppt", "pptx", "jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    help=(
        "支持 PDF、DOC、DOCX、PPT、PPTX，以及 JPG/JPEG/PNG/WEBP 图片；"
        "相同内容不会重复保存，重复上传会明确提示。图片按原样保存为一份一页资料。"
    ),
)

# ---- 上传图片入口：原图保存为不可更改的证据，走同一 image 后端 ----
st.subheader("上传图片")
st.caption(
    "选择电脑里的图片（JPG/PNG/WEBP），确认后按原样保存为本机资料；"
    "相同内容的图片不会重复导入。"
)
uploaded_images = st.file_uploader(
    "选择图片文件（可多选）",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    key="image_only_uploader",
)
image_uploads: list = []
if uploaded_images:
    image_uploads.extend(uploaded_images)
if image_uploads:
    try:
        st.image(image_uploads[0].getvalue(), width=220, caption=image_uploads[0].name)
    except Exception:  # noqa: BLE001 - preview must never block import
        st.caption("（无法预览该图片，但不影响导入）")
    st.caption(
        f"已选择 {len(image_uploads)} 张图片："
        + "、".join(u.name for u in image_uploads)
    )
    image_confirmed = st.checkbox(
        "确认使用这些图片（选错请重新选择）", key="image_confirm"
    )
    if image_confirmed and st.button(
        "导入这些图片", key="import_images", type="primary"
    ):
        try:
            batch_entries = _enqueue_and_process(image_uploads)
        except Exception as exc:  # noqa: BLE001 - queue failures surface honestly
            LOGGER.exception("图片导入队列处理失败")
            st.error(f"图片导入队列处理失败：{exc}")
        else:
            st.session_state[_RUN_ID_KEY] = new_run_id()
            complete = sum(1 for e in batch_entries if e.status == "complete")
            st.success(f"图片处理完成：{complete} / {len(batch_entries)} 份成功。")

# ---- durable queue state: recovered before anything renders (V086-301) ----
try:
    st.session_state.setdefault(_RUN_ID_KEY, new_run_id())
    queue = _queue_service()
    # A dead run's in-flight rows are marked interrupted so the batch tells
    # the truth instead of pretending nothing happened.
    queue.recover_stale_inflight(str(st.session_state[_RUN_ID_KEY]))
    open_batch_id = queue.latest_open_batch_id()
    current_batch_id = open_batch_id or queue.latest_batch_id()
    batch_entries = queue.list_batch(current_batch_id) if current_batch_id else []
except Exception as exc:  # noqa: BLE001 - queue outage must not kill the page
    LOGGER.exception("导入队列初始化失败")
    st.error(f"导入队列暂时不可用：{exc}")
    queue = None
    batch_entries = []

if uploaded_documents is None and not batch_entries:
    st.info("先选择一份资料。普通文字会直接读取，图片或手写页面会自动尝试识别。")
    st.stop()

if uploaded_documents:
    st.caption(
        f"已选择 {len(uploaded_documents)} 个文件："
        + "、".join(upload.name for upload in uploaded_documents)
    )

if queue is not None and uploaded_documents and st.button(
    "导入并让 Agent 逐份阅读", type="primary"
):
    try:
        batch_entries = _enqueue_and_process(uploaded_documents)
    except Exception as exc:  # noqa: BLE001 - queue failures surface honestly
        LOGGER.exception("导入队列处理失败")
        st.error(f"导入队列处理失败：{exc}")
    else:
        st.session_state[_RUN_ID_KEY] = new_run_id()  # next run is a new run

# Resume is honest about browser limits (V086-301 §6.3): uploaded bytes do
# not survive navigation, so continuing a batch means re-selecting the same
# files — matching rows are then continued, finished ones skipped.
if batch_entries and any(
    entry.status in ("queued", "interrupted") for entry in batch_entries
):
    waiting = sum(
        1 for entry in batch_entries if entry.status in ("queued", "interrupted")
    )
    st.info(
        f"本批还有 {waiting} 份未处理完。请重新选择同样的文件并点击「导入并让 Agent 逐份阅读」——"
        "未完成的文件会继续处理，已完成的资料会通过内容查重自动跳过，不会重复导入。"
    )

if batch_entries:
    counts = ImportQueueService.summarize(batch_entries)
    total = len(batch_entries)
    st.subheader("导入队列")
    # V086-311: state the real execution model — processing is driven by
    # this page being open; no background worker exists by design in
    # v0.8.6. Pause / resume / no-loss semantics, in one honest sentence.
    st.caption(
        "批量处理会在当前导入页面打开期间继续；离开本页会暂停未完成的文件，"
        "返回后重新选择同样的文件即可继续处理，已完成的内容不会丢失。"
    )
    st.caption(
        f"本批共 {total} 份 · 已完成 {counts['complete']} · 已导入未读完 "
        f"{counts['imported']} · 等待 {counts['queued'] + counts['interrupted']}"
        f" · 失败 {counts['failed']}"
        + (" · 资料导入与 AI 阅读是两个独立状态。" if counts["imported"] else "")
    )
    for entry in batch_entries:
        icon = _STATUS_ICONS.get(entry.status, "•")
        label = _STATUS_LABELS.get(entry.status, entry.status)
        line = f"{icon} **{entry.filename}** —— {label}"
        if entry.error_message:
            line += f"：{entry.error_message}"
        st.markdown(line)
        if entry.document_id is not None and entry.status in ("complete", "imported"):
            if st.button(
                "查看这份资料",
                key=f"open_document_{entry.id}_{entry.document_id}",
            ):
                st.switch_page(
                    "pages/17_我的资料.py",
                    query_params={"document": str(entry.document_id)},
                )
elif not uploaded_documents:
    st.info("选择文件后点一下开始导入。")
