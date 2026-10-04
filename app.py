"""Local knowledge workspace: overview, search and useful next actions."""

from __future__ import annotations

import logging
from datetime import date

import streamlit as st

from src import __version__
from src.runtime import (
    application_database,
    application_settings,
    application_startup_reconciliation,
)
from src.workspace_ui import empty_panel, render_workspace, section_heading

LOGGER = logging.getLogger(__name__)


def _render_footer() -> None:
    """Keep the product version visible on both first-use and normal home pages."""

    st.markdown(
        '<div class="ekb-footer"><span>我的资料和经验 · 由我保存，为我所用</span>'
        f'<span>Nectivon v{__version__}</span></div>', unsafe_allow_html=True,
    )


st.set_page_config(page_title=f"工作台 · Nectivon v{__version__}", page_icon="📚", layout="wide")
render_workspace("app.py")

try:
    settings = application_settings()
    database = application_database()
    quarantine_reconciliation = application_startup_reconciliation()
    stats = database.dashboard_stats()
    recent_documents = database.list_documents(sort_by="imported_desc")[:5]
    recent_pages = database.recent_edited_pages(5)
    next_review_page = next(iter(database.list_review_pages()), None)
except Exception as exc:
    LOGGER.exception("应用初始化失败")
    st.error(f"应用初始化失败：{exc}")
    st.stop()

if quarantine_reconciliation is not None and quarantine_reconciliation.has_attention:
    st.warning(
        "检测到需要人工处理的删除操作残留：系统未自动删除或覆盖这些文件，"
        "请前往“备份与修复”页查看详情。"
    )

if stats.documents == 0:
    st.markdown(
        '<div class="ekb-intro"><div><h1>第 1 步：添加资料</h1>'
        '<p>先选择一个 PDF、Word 或 PowerPoint 文件，其他事情交给 Agent。</p>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="home_first_use"):
        section_heading("第一次使用，只需要三步", "")
        for number, title, detail in (
            ("01", "添加资料", "从电脑中选择一份文件。"),
            ("02", "让 Agent 阅读", "点击一次，等待 Agent 按页读完。"),
            ("03", "开始提问", "用自己的话提问，并查看答案来自哪一页。"),
        ):
            st.markdown(
                f'<div class="ekb-step"><span class="ekb-step-num">{number}</span>'
                f'<div><b>{title}</b><p>{detail}</p></div></div>',
                unsafe_allow_html=True,
            )
        if st.button(
            "添加资料",
            icon=":material/upload_file:",
            type="primary",
            use_container_width=True,
            key="first_use_add_document",
        ):
            st.switch_page("pages/1_导入资料.py")
        st.caption("资料读完后，页面会直接带你去问 Agent。")
    _render_footer()
    st.stop()

today = date.today()
weekday = "一二三四五六日"[today.weekday()]
st.markdown(
    '<div class="ekb-intro"><div><h1>你的知识，正在成为可以反复调用的资产。</h1>'
    '<p>在这里沉淀资料、保留上下文，需要时再让 AI 帮你理解和调用。</p></div>'
    f'<span class="ekb-date">{today:%Y年%m月%d日} · 星期{weekday}</span></div>',
    unsafe_allow_html=True,
)

with st.container(key="home_search"), st.form("home_search_form", border=True):
    query_column, action_column = st.columns([6, 1])
    query = query_column.text_input(
        "搜索我的资料", placeholder="搜索资料名称或记得的一句话…",
        label_visibility="collapsed", key="home_search_query",
    )
    search_submitted = action_column.form_submit_button(
        "搜索", icon=":material/search:", type="primary", use_container_width=True,
    )
if search_submitted:
    if query.strip():
        # Keep the existing search-state handoff, including its destination validation.
        st.session_state["pending_search_query_params"] = {"q": query.strip()[:500]}
        st.switch_page("pages/4_检索资料.py")
    else:
        st.info("请输入你想找的内容。")

st.markdown(
    '<div class="ekb-knowledge-flow">'
    '<div class="ekb-flow-copy"><div class="ekb-flow-kicker">KNOWLEDGE WORKSPACE</div>'
    '<h2>知识先沉淀，问题才有可靠上下文。</h2>'
    '<p>文件是长期资产，回答只是一次调用。每个结论都应能回到来源与原文。</p></div>'
    '<div class="ekb-flow-grid" aria-label="知识到提问的工作流">'
    '<div class="ekb-flow-node"><i>01</i><b>知识</b><span>保存文件与校对内容</span></div>'
    '<div class="ekb-flow-node"><i>02</i><b>上下文</b><span>组织来源与适用条件</span></div>'
    '<div class="ekb-flow-node"><i>03</i><b>提问</b><span>带着依据获得答案</span></div>'
    '</div><div class="ekb-flow-foot"><span>文件保存在本机</span>'
    '<span>回答附带出处</span><span>保存由你决定</span></div></div>',
    unsafe_allow_html=True,
)
with st.container(key="home_actions"):
    actions = st.columns([1.3, 1.1, 2.8], vertical_alignment="center")
    if actions[0].button("基于知识提问", type="primary", use_container_width=True):
        st.switch_page("pages/0_知识Agent.py")
    if actions[1].button("添加文件", icon=":material/add:", use_container_width=True):
        st.switch_page("pages/1_导入资料.py")
    actions[2].caption("先积累可靠知识，再让 AI 帮你查找、解释和连接。")

section_heading("知识概览", "保存在这台电脑里的长期知识资产")
with st.container(key="home_metrics"):
    for column, label, value in zip(
        st.columns(4), ("资料", "已保存页面", "已手录页", "可查看页面"),
        (stats.documents, stats.pages, stats.noted_pages, stats.review_pages), strict=True,
    ):
        column.metric(label, value)

left, right = st.columns([1.7, 1], gap="medium")
with left, st.container(key="home_recent"):
    section_heading("最近知识", "最近加入的 5 个知识对象")
    if recent_documents:
        for document in recent_documents:
            if st.button(
                f"{document.title} · {document.page_count} 页 · {document.status_label}",
                icon=":material/description:", key=f"recent_document_{document.id}",
                use_container_width=True,
            ):
                st.switch_page(
                    "pages/17_我的资料.py", query_params={"document": str(document.id)}
                )
    else:
        empty_panel(
            "你的下一次积累，从这里开始",
            "添加讲义、说明书或学习资料，让 Agent 从第一页开始读。",
        )
        st.info("这里还没有资料。添加第一份文件后就可以开始提问。")
        if st.button("添加第一份资料", icon=":material/upload_file:", use_container_width=True):
            st.switch_page("pages/1_导入资料.py")
    if st.button("查看全部文件 →", key="home_browse_all", use_container_width=True):
        st.switch_page("pages/17_我的资料.py")

with right, st.container(key="home_workflow"):
    section_heading("知识状态", "从原始文件到可核对内容")
    for number, title, detail in (
        ("01", "原始文件", f"{stats.documents} 份资料保存在本机。"),
        ("02", "可调用内容", f"{stats.pages} 个页面已经进入知识空间。"),
        ("03", "待人工确认", "有内容需要整理。" if next_review_page else "当前没有待处理内容。"),
    ):
        st.markdown(
            f'<div class="ekb-step"><span class="ekb-step-num">{number}</span>'
            f'<div><b>{title}</b><p>{detail}</p></div></div>', unsafe_allow_html=True,
        )
    if st.button("处理待整理内容", disabled=next_review_page is None,
                 use_container_width=True):
        st.switch_page(
            "pages/5_待整理页面.py", query_params={"page_id": str(next_review_page.id)}
        )
    if next_review_page is None:
        st.caption("当前没有新的识别结果需要提醒。")

with st.container(key="home_edits"):
    section_heading("最近更新", "我亲手校对过的知识页面")
    if recent_pages:
        for page in recent_pages:
            document = database.get_document(page.document_id)
            if document and st.button(
                f"{document.title} · 第 {page.page_number} 页", icon=":material/edit_note:",
                key=f"recent_page_{page.id}", use_container_width=True,
            ):
                st.switch_page(
                    "pages/17_我的资料.py",
                    query_params={"document": str(document.id), "page": str(page.page_number)},
                )
    else:
        st.caption("还没有修改过页面。发现识别文字有误时再修改即可。")

_render_footer()
with st.expander("资料保存在哪里"):
    st.caption(
        f"资料保存在本机 {settings.data_dir}。"
        f"这个页面只在 {settings.host}:{settings.port} 打开。"
    )
    if st.button("修改存储位置", use_container_width=True):
        st.switch_page("pages/13_运行说明.py")
