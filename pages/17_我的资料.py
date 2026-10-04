"""Simple document reader and per-page correction surface for ordinary users."""

from __future__ import annotations

import html
import logging
from pathlib import Path

import streamlit as st

from src import __version__
from src.agent.local_client import LocalDocumentAgentClient
from src.agent_document_reader import (
    AgentReadingStore,
    document_ai_reading_label,
    page_reread_availability,
)
from src.ai.provider import AIUnavailableError
from src.ai_ui import render_ai_unconfigured_notice
from src.page_jump_ui import render_page_jump
from src.runtime import (
    application_ai_provider,
    application_database,
    application_document_service,
    application_settings,
)
from src.visual_provenance import (
    format_visual_detection_provenance,
    load_stage1_state,
)
from src.workspace_ui import render_workspace, section_heading

LOGGER = logging.getLogger(__name__)

_DOCUMENT_SELECTOR_KEY = "simple_reader_document_id"
_PAGE_SELECTOR_KEY = "simple_reader_page_number"
_REREAD_FLASH_KEY = "simple_reader_reread_flash"


def _remember_document_selection() -> None:
    """Keep a document choice and its URL in sync during the same user action."""

    st.query_params["document"] = str(st.session_state[_DOCUMENT_SELECTOR_KEY])
    if "page" in st.query_params:
        del st.query_params["page"]
    st.session_state.pop(_PAGE_SELECTOR_KEY, None)


def _remember_page_selection() -> None:
    """Write the newly selected page before Streamlit reruns the page."""

    st.query_params["page"] = str(st.session_state[_PAGE_SELECTOR_KEY])


def _go_to_page(page_number: int) -> None:
    """Use the same state transition for the buttons and the page selector."""

    st.query_params["page"] = str(page_number)


def _agent_client() -> LocalDocumentAgentClient:
    settings = application_settings()
    provider = application_ai_provider()
    return LocalDocumentAgentClient(
        database=application_database(),
        provider=provider,
        readings=AgentReadingStore(settings.agent_readings_dir),
        model=provider.default_model if provider is not None else "",
    )


def _render_agent_reading(page_id: int) -> None:
    """Show the stored Agent page reading (summary / facts) when it exists.

    V086-206: a pending re-read flash is consumed here, right before the
    reading block renders, so the post-rerun screen shows the fresh result
    together with its confirmation — never「已重新读完」on top of「还没有
    阅读结果」.
    Entirely auxiliary: any failure (for example a test harness without a
    runtime) silently skips the section instead of breaking the page.
    """
    flash = st.session_state.pop(_REREAD_FLASH_KEY, None)
    if flash:
        st.success(flash)

    try:
        readings_dir = application_settings().agent_readings_dir
        reading = AgentReadingStore(readings_dir).page_reading(page_id)
    except Exception:  # noqa: BLE001 - auxiliary display must never break the page
        LOGGER.debug("读取 Agent 阅读结果失败：page_id=%s", page_id, exc_info=True)
        return
    with st.expander("Agent 阅读结果（AI 生成，仅供参考）"):
        if reading is None:
            st.caption("这一页还没有 Agent 阅读结果。")
            return
        st.markdown(f"**摘要**：{reading.summary}")
        if reading.key_facts:
            st.markdown("**可核对事实**")
            for fact in reading.key_facts:
                st.markdown(f"- {fact}")
        if reading.keywords:
            st.caption("关键词：" + "、".join(reading.keywords))


st.set_page_config(
    page_title=f"我的资料 · Nectivon v{__version__}", page_icon="📖", layout="wide"
)
render_workspace("pages/17_我的资料.py")
st.title("文件与知识对象")
st.caption("每份文件都会保留来源、处理状态和更新时间；需要时可以回到任意原文页面核对。")

try:
    database = application_database()
    document_service = application_document_service()
    documents = database.list_documents(sort_by="imported_desc")
except Exception as exc:
    LOGGER.exception("打开资料失败")
    st.error(f"暂时无法打开资料：{exc}")
    st.stop()

# V086-306: AI reading progress for the document cards. Auxiliary: any
# failure (e.g. AppTest harnesses without a runtime) degrades to the
# neutral label instead of breaking the page.
_reading_store = None
try:
    _reading_store = AgentReadingStore(application_settings().agent_readings_dir)
except Exception:  # noqa: BLE001 - auxiliary info only
    LOGGER.debug("AI 阅读状态不可用", exc_info=True)

if not documents:
    st.info("这里还没有资料。先添加一份 PDF、Word 或 PowerPoint 文件吧。")
    if st.button("添加资料", type="primary", use_container_width=True):
        st.switch_page("pages/1_导入资料.py")
    st.stop()

document_by_id = {document.id: document for document in documents}
requested_document = str(st.query_params.get("document", ""))
try:
    requested_document_id = int(requested_document)
except ValueError:
    requested_document_id = documents[0].id
    if requested_document:
        # F6 honesty rule: a link that names a document which cannot be
        # opened must SAY so — silently showing the most-recent document
        # reads as "跳错文档" to an ordinary user.
        st.warning("链接指向的资料不存在或已删除，已显示最近的资料。")
if requested_document_id not in document_by_id:
    requested = database.get_document(requested_document_id)
    if requested is not None:
        document_by_id[requested.id] = requested
    else:
        if requested_document:
            st.warning("链接指向的资料不存在或已删除，已显示最近的资料。")
        requested_document_id = documents[0].id

with st.container(key="knowledge_object_overview"):
    section_heading("知识对象", "最近加入的文件及其知识状态")
    overview_metrics = st.columns(3)
    overview_metrics[0].metric("本地文件", len(documents))
    overview_metrics[1].metric(
        "总页数", sum(document.page_count for document in documents)
    )
    overview_metrics[2].metric(
        "已完成整理",
        sum(
            document.page_count > 0
            and document.processed_page_count >= document.page_count
            for document in documents
        ),
    )
    card_columns = st.columns(min(3, len(documents)), gap="medium")
    for column, recent_document in zip(card_columns, documents[:3], strict=False):
        updated_at = recent_document.updated_at or recent_document.created_at
        updated_label = (
            updated_at.strftime("%Y-%m-%d")
            if hasattr(updated_at, "strftime")
            else "未记录"
        )
        file_type = Path(recent_document.filename).suffix.lstrip(".").upper() or "FILE"
        # V086-306: show AI reading progress next to the import status so
        # 「导入完成」 never hides 「Agent 阅读 0/N 页」. Fail-safe: any store
        # problem degrades to the neutral label instead of breaking cards.
        reading_label = "AI 未阅读"
        if _reading_store is not None:
            try:
                reading_label = document_ai_reading_label(_reading_store, recent_document)
            except Exception:  # noqa: BLE001 - auxiliary info only
                reading_label = "AI 未阅读"
        with column:
            st.markdown(
                '<div class="ekb-file-card">'
                '<div class="ekb-file-card-top">'
                f'<span class="ekb-file-type">{html.escape(file_type)}</span>'
                f'<span class="ekb-file-status">{html.escape(recent_document.status_label)}</span>'
                '</div>'
                f'<h3>{html.escape(recent_document.title)}</h3>'
                f'<div class="ekb-file-name" title="{html.escape(recent_document.filename)}">'
                f'{html.escape(recent_document.filename)}</div>'
                '<div class="ekb-file-meta">'
                f'<span>{recent_document.page_count} 页</span>'
                f'<span>{html.escape(reading_label)}</span>'
                f'<span>更新于 {updated_label}</span>'
                '</div></div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "打开知识对象",
                key=f"open_knowledge_object_{recent_document.id}",
                type="primary" if recent_document.id == requested_document_id else "secondary",
                use_container_width=True,
            ):
                st.query_params["document"] = str(recent_document.id)
                if "page" in st.query_params:
                    del st.query_params["page"]
                st.session_state[_DOCUMENT_SELECTOR_KEY] = recent_document.id
                st.session_state.pop(_PAGE_SELECTOR_KEY, None)
                st.rerun()
    if len(documents) > 3:
        st.caption(f"另有 {len(documents) - 3} 个知识对象，可在下方选择并查看。")

section_heading("阅读与校对", "在原文和可编辑文字之间逐页核对")

if st.session_state.get(_DOCUMENT_SELECTOR_KEY) != requested_document_id:
    st.session_state[_DOCUMENT_SELECTOR_KEY] = requested_document_id
document_id = st.selectbox(
    "选择资料",
    options=list(document_by_id),
    key=_DOCUMENT_SELECTOR_KEY,
    format_func=lambda value: (
        f"{document_by_id[value].title}（{document_by_id[value].page_count} 页）"
    ),
    on_change=_remember_document_selection,
)
document = document_by_id[document_id]
if str(st.query_params.get("document", "")) != str(document.id):
    st.query_params["document"] = str(document.id)

pages = sorted(database.list_pages(document.id), key=lambda item: item.page_number)
if not pages:
    st.warning("这份资料还没有可以查看的页面。")
    st.stop()

page_by_number = {page.page_number: page for page in pages}
requested_page = str(st.query_params.get("page", ""))
try:
    initial_page = int(requested_page)
except ValueError:
    initial_page = pages[0].page_number
    if requested_page:
        st.warning("链接里的页码无法识别，已回到第 1 页。")
if initial_page not in page_by_number:
    if requested_page:
        st.warning(
            "链接里的页码在这份资料中不存在，已回到第 1 页。"
        )
    initial_page = pages[0].page_number
if st.session_state.get(_PAGE_SELECTOR_KEY) != initial_page:
    st.session_state[_PAGE_SELECTOR_KEY] = initial_page

st.markdown(f"### {document.title}")
st.caption(f"原文件：{document.filename}　·　共 {len(pages)} 页")
# §44: a document whose import succeeded but with zero organized questions
# must say so honestly and offer the next step, never stay silent.
try:
    from src.learning_workflow_service import QuestionService

    _doc_question_count = len(
        QuestionService(database).list_questions_for_document(document.id)
    )
    if _doc_question_count > 0:
        st.caption(f"已加入学习整理的题目：{_doc_question_count} 道。")
    else:
        st.info(
            "这份资料还没有提取到题目。导入成功只代表文件已保存和识别，"
            "题目需要逐页拆出：打开左侧「待核对」，翻到对应页面，"
            "在「加入学习整理」区点「AI 拆分本页题目候选」，"
            "拆好后逐题加入；纯文字 PDF 也可以直接核对识别文字后手动整理。"
        )
except Exception:  # noqa: BLE001 - status line must never break reading
    pass

navigation = st.columns([1, 1, 3])
page_numbers = list(page_by_number)
current_index = page_numbers.index(initial_page)
navigation[0].button(
    "← 上一页",
    disabled=current_index == 0,
    use_container_width=True,
    on_click=_go_to_page,
    args=(page_numbers[max(0, current_index - 1)],),
)
navigation[1].button(
    "下一页 →",
    disabled=current_index == len(page_numbers) - 1,
    use_container_width=True,
    on_click=_go_to_page,
    args=(page_numbers[min(len(page_numbers) - 1, current_index + 1)],),
)
page_number = navigation[2].selectbox(
    "页码",
    options=page_numbers,
    key=_PAGE_SELECTOR_KEY,
    format_func=lambda value: f"第 {value} 页",
    label_visibility="collapsed",
    on_change=_remember_page_selection,
)
if str(st.query_params.get("page", "")) != str(page_number):
    st.query_params["page"] = str(page_number)
page = page_by_number[page_number]

jump_target = render_page_jump(
    total_pages=len(pages), key_prefix=f"simple_reader_jump_{document.id}"
)
if jump_target is not None:
    _go_to_page(jump_target)
    st.rerun()

saved_correction = page.markdown_content.strip()
image_column, text_column = st.columns([1.08, 1], gap="large")
with image_column:
    st.subheader(f"原文第 {page.page_number} 页")
    if page.image_path.is_file():
        st.image(
            str(page.image_path),
            caption=f"{document.title} · 第 {page.page_number} 页",
            width="stretch",
        )
    else:
        st.warning("这一页的图片暂时无法显示。")
    # Stage-1 provenance (V086-302-PRT1): the user must be able to tell
    # 「检测过但结果不确定」from「从来没跑」on every page-reading surface.
    _stage1_state = load_stage1_state(database, page.id)
    if _stage1_state is not None:
        st.caption(format_visual_detection_provenance(_stage1_state))
    # Geography G1 WORKFLOW CHANGE 2: the raw OCR text block is no longer
    # rendered on the user-facing surface.  The text stays stored and is
    # still used by search / splitting / AI reading / provenance; the Agent
    # reading (a distinct product) remains visible below.
    st.caption("系统识别出的原文由本机保留，用于检索与来源追溯（不再整页展示）。")
    _render_agent_reading(page.id)

with text_column:
    st.subheader("修改这一页的文字")
    st.caption(
        "如果图片中的文字有识别错误，直接在下面改正后保存。"
        "保存后，检索与 Agent 阅读会优先使用你修正的文字；识别原文由系统保留用于追溯。"
    )
    corrected_text = st.text_area(
        "修改后的文字",
        value=saved_correction,
        height=560,
        key=f"simple_page_correction_{page.id}",
        placeholder=(
            "留空表示这一页暂无人工修正。可以对照左边的图片补正。"
        ),
    )
    if corrected_text != saved_correction:
        st.warning("修改还没有保存。")
    else:
        st.caption("当前文字已保存。" if saved_correction else "还没有人工修正。")
    # Save and AI re-read are two independent actions (V086-205): saving is a
    # local operation and must give an immediate, unambiguous confirmation;
    # a failed AI re-read never rolls back or hides the saved correction.
    save_clicked = st.button(
        "保存修改",
        type="primary",
        use_container_width=True,
        disabled=not corrected_text.strip() or corrected_text == saved_correction,
    )
    if save_clicked:
        try:
            document_service.save_page_markdown(
                document_id=document.id,
                page_number=page.page_number,
                markdown_content=corrected_text,
            )
        except Exception as exc:
            LOGGER.exception("保存页面修改失败：page_id=%s", page.id)
            st.error(f"修改没有保存成功：{exc}")
        else:
            st.success("已保存。你的修正文字已生效，后续检索与 Agent 阅读将优先使用它。")
    # V086-211: a page-level re-read is decoupled from manual corrections —
    # the user may re-run the Agent for their own reasons (provider switch,
    # an unsatisfying earlier result, updated visual parsing). The only hard
    # precondition (enforced by read_page itself) is readable text; a
    # disabled state always carries an explicit inline reason, never silent.
    _reread_available, _reread_reason = page_reread_availability(page)
    reread_clicked = st.button(
        "让 Agent 重读这一页（可选）",
        use_container_width=True,
        disabled=not _reread_available,
        help=None if _reread_available else _reread_reason,
    )
    if not _reread_available:
        st.caption(_reread_reason)
    if reread_clicked:
        # V086-207: the label says "this page", so the action now really
        # re-reads exactly this page (page-level read_page), not the whole
        # document. Progress honesty for a single page: one indeterminate
        # step instead of a fake multi-page counter.
        with st.spinner("正在让 Agent 重读本页……"):
            try:
                _agent_client().read_page(page.id)
            except AIUnavailableError:
                st.info("你的修改已保存，不受影响。AI 未配置时重读不可用。")
                render_ai_unconfigured_notice()
            except Exception as exc:
                LOGGER.exception("Agent 重读页面失败：page_id=%s", page.id)
                st.warning(f"你的修改已保存，不受影响。Agent 暂时没有读完本页：{exc}")
            else:
                # V086-206: rerun after completion so the same screen shows
                # the fresh reading result instead of contradicting itself.
                st.session_state[_REREAD_FLASH_KEY] = "Agent 已重读完本页，结果已更新。"
                st.rerun()
