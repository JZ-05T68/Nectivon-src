"""知识串联入口：共享学习资产的离线知识星图。"""

from __future__ import annotations

import logging

import streamlit as st

from src import __version__
from src.knowledge_graph_service import KnowledgeGraphService
from src.knowledge_graph_ui import (
    render_graph_details,
    render_graph_editor,
    render_material_mining,
)
from src.knowledge_starmap_component import render_starmap
from src.runtime import application_database
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)
st.set_page_config(page_title=f"知识串联 · Nectivon v{__version__}", page_icon="🌌", layout="wide")
render_workspace("pages/20_知识串联.py")
st.title("知识串联")
st.caption("知识星图 · 把题目、知识点、归纳族和来源连起来，沿用学习整理中的掌握记录；"
           "一级学科组成大星云，可展开查看二级方向；未标学科的节点归入「未归类」。")

saved_node = st.session_state.pop("graph_saved_knowledge_node", None)
if saved_node:
    # Show the saved object's conversion even when the previous search excluded it.
    st.session_state["graph_query"] = ""
filters = st.columns([4, 2, 1])
query = filters[0].text_input("查找节点", placeholder="输入题干或知识点关键词", key="graph_query")
limit = filters[1].selectbox("最多显示学习与知识节点", [300, 600, 1000])
if filters[2].button("刷新星图", use_container_width=True):
    st.rerun()
try:
    database = application_database()
    graph = KnowledgeGraphService(database).snapshot(query=query, limit=limit)
except Exception as exc:
    LOGGER.exception("知识串联初始化失败")
    st.error(f"知识串联暂时无法读取本地知识：{exc}")
    st.stop()

st.caption(f"当前 {graph.shown} / {graph.total} 个学习与知识节点 · "
           f"{len(graph.links)} 条关联（含来源）")
if graph.shown < graph.total:
    st.info("为保持星图流畅，当前按类别显示部分节点。缩小关键词范围或提高上限可查看更多。")
if not graph.nodes:
    st.info("还没有可串联的知识。可以先在学习整理保存题目，或在下方新增知识点。")
else:
    if saved_node in graph.nodes:
        st.session_state["graph_node_picker"] = saved_node
    st.caption("拖动旋转、滚轮缩放；点击星星选择，再次点击打开详情。下方列表也能选择节点。"
               "连线展示关联，具体方向和类型见节点详情。")
    st.caption("星图内可选启用手势与语音控制：右下角「开启手势控制」隔空浏览（画面仅在本机处理）；"
               "底部控制条或按住空格说话——直接说出节点标题或内容的一部分（题干、知识点、页面文字均可）"
               "即可选中对应节点，也说「下一个星座」「回到全景」（首次使用需允许麦克风；"
               "语音识别由浏览器提供，Chrome/Edge 需联网；打字输入随时可用）。")
    roots = [None, *graph.primary_subjects()]
    if st.session_state.get("graph_nebula_subject") not in roots:
        st.session_state["graph_nebula_subject"] = None
    subject = st.selectbox("展开一级星云", roots,
                           format_func=lambda value: value or "一级星云全景",
                           key="graph_nebula_subject")
    st.caption("选择一级学科查看其二级方向；方向尚未确定的节点进入「待细分」。")
    event = render_starmap(graph.star_payload(subject=subject))
    identifiers = list(graph.nodes)
    if isinstance(event, dict) and event.get("node_id") in graph.nodes:
        event_id = event["node_id"]
        if event != st.session_state.get("graph_last_star_event"):
            st.session_state["graph_node_picker"] = event_id
            st.session_state["graph_last_star_event"] = event
    if st.session_state.get("graph_node_picker") not in graph.nodes:
        st.session_state["graph_node_picker"] = identifiers[0]
    selected = st.selectbox("查看节点", identifiers,
                            format_func=lambda key: graph.nodes[key]["title"],
                            key="graph_node_picker")
    with st.container(key="graph_details"):
        render_graph_details(database, graph, selected)
mining_saved = st.session_state.pop("graph_mining_saved", None)
if mining_saved:
    st.success(mining_saved)
render_material_mining(database)
render_graph_editor(database, graph)
