"""Knowledge connection workspace over Nectivon's existing local assets."""

from __future__ import annotations

import logging

import streamlit as st

import src.runtime as runtime
from src.ai.provider import AIError
from src.database import Database
from src.knowledge_graph_service import NODE_KINDS, KnowledgeGraph, KnowledgeGraphService
from src.knowledge_math_service import (
    KnowledgeMathAIService,
    knowledge_math_display,
    schedule_knowledge_math,
)
from src.knowledge_mining_service import (
    KnowledgeMiningAIService,
    KnowledgeMiningDraft,
    KnowledgeMiningError,
    save_mining_draft,
)
from src.knowledge_object_service import (
    KnowledgeObjectError,
    KnowledgeObjectService,
)
from src.knowledge_subject_service import KnowledgeSubjectService
from src.knowledge_taxonomy import PRIMARY_SUBJECTS
from src.learning_workflow_service import QuestionService
from src.math_display import render_math_markdown
from src.models import KnowledgeEpistemicBasis, KnowledgeRelationType

LOGGER = logging.getLogger(__name__)
_DO_LABELS = {"no_evidence": "尚未验证", "needs_practice": "需要再练",
              "independent": "能独立完成", "basically_ok": "基本会做"}
_EXPLAIN_LABELS = {"complete": "能完整讲清", "basically_clear": "基本讲清",
                   "gaps": "讲解有缺口", "unverified": "尚未验证"}


def _select_node(identifier: str) -> None:
    st.session_state["graph_node_picker"] = identifier


def _reset_knowledge_draft() -> None:
    """Discard unsaved fields without touching any persisted knowledge."""

    st.session_state["graph_new_knowledge_title"] = ""
    st.session_state["graph_new_knowledge_content"] = ""
    st.session_state["graph_new_knowledge_basis"] = KnowledgeEpistemicBasis.PERSONAL_JUDGMENT
    st.session_state["graph_new_knowledge_source"] = None


def _knowledge_ai_service() -> KnowledgeMathAIService | None:
    """Reuse the optional audited provider without making it a page dependency."""

    try:
        provider = runtime.application_ai_provider()
        return KnowledgeMathAIService(provider) if provider is not None else None
    except Exception as exc:  # noqa: BLE001 - saving remains independent of optional AI
        LOGGER.warning("知识点排版 AI 暂不可用：error_type=%s", type(exc).__name__)
        return None


def _render_knowledge_math(database: Database, identifier: int) -> None:
    """Automatically refresh a saved object's math while preserving its raw text."""

    cache_root = runtime.application_settings().cache_dir
    knowledge = KnowledgeObjectService(database).get_view(identifier).knowledge_object
    initial = knowledge_math_display(cache_root, database.database_path, knowledge)

    @st.fragment(run_every=1.0 if initial.status == "pending" else None)
    def reading() -> None:
        current = KnowledgeObjectService(database).get_view(identifier).knowledge_object
        display = knowledge_math_display(cache_root, database.database_path, current)
        if initial.status == "pending" and display.status != "pending":
            st.rerun()
        st.subheader(display.title)
        st.caption("知识点")
        render_math_markdown(display.content or "暂未填写内容。")
        if display.status == "pending":
            st.info("已保存，AI 正在自动排版数学公式；完成后这里会自动更新。")
        elif display.status == "ready":
            st.caption("AI 数学排版 · 原文已保留，复杂公式请对照原文核对。")
            if display.rejected:
                st.warning("部分公式未通过排版检查，已保留原文。")
        elif display.status in {"failed", "interrupted"}:
            st.warning("AI 数学排版未完成，已保存的原文不受影响。")
            if st.button("重试 AI 排版", key=f"graph_math_retry_{identifier}"):
                task = schedule_knowledge_math(database, cache_root, identifier,
                                               _knowledge_ai_service(), retry=True)
                if task is not None:
                    st.rerun()
                st.info("当前 AI 未启用或排版任务无法启动，请检查 AI 设置。")
        with st.expander("查看保存的原文"):
            st.text(current.title)
            st.text(current.content)

    reading()


def render_graph_details(database: Database, graph: KnowledgeGraph, selected: str) -> None:
    """Read live content and display directed relations with source navigation."""

    node = graph.nodes[selected]
    kind, raw_id = selected.split(":")
    identifier = int(raw_id)
    if kind == "knowledge":
        _render_knowledge_math(database, identifier)
    else:
        st.subheader(node["title"])
        st.caption(NODE_KINDS[kind])
        st.markdown(node["summary"] or "暂未填写内容。")
    if node["tags"]:
        st.caption("学科分类：" + "；".join(tag.replace("/", " → ") for tag in node["tags"]))
    if kind == "question":
        states = node["mastery"]
        st.caption(
            f"会做：{_DO_LABELS.get(states['do_state'], '尚未验证')} · "
            f"会讲：{_EXPLAIN_LABELS.get(states['explain_state'], '尚未验证')}"
        )
        question = QuestionService(database).get_question_item(identifier)
        if question.source_available and st.button("查看本题来源", key="graph_question_source"):
            st.switch_page("pages/17_我的资料.py", query_params={
                "document": str(question.document_id), "page_id": str(question.page_id),
                "tab": "阅读",
            })
    elif kind == "page":
        page = database.get_page(identifier)
        if page and st.button("打开来源页面", key="graph_page_source"):
            st.switch_page("pages/17_我的资料.py", query_params={
                "document": str(page.document_id), "page_id": str(page.id), "tab": "阅读",
            })
    elif kind == "document" and st.button("打开来源文档", key="graph_document_source"):
        st.switch_page("pages/17_我的资料.py", query_params={"document": raw_id, "tab": "阅读"})
    elif kind == "knowledge":
        view = KnowledgeObjectService(database).get_view(identifier)
        st.caption(f"形成依据：{view.knowledge_object.epistemic_basis.label}")
        for source in view.sources:
            st.caption(f"来源核对：{source.status.label}")
        _render_subject_links(database, identifier)
    edges = [edge for edge in graph.links if selected in (edge["source"], edge["target"])]
    st.markdown("**关联与来源**")
    if not edges:
        st.caption("当前视图暂无关联。可在下方添加已经核对的关系。")
    for index, edge in enumerate(edges):
        outgoing = edge["source"] == selected
        other = edge["target"] if outgoing else edge["source"]
        direction = "→" if outgoing else "←"
        st.write(f"{direction} {edge['label']} · {graph.nodes[other]['title']}")
        if edge["note"]:
            st.caption(edge["note"])
        st.button("查看关联节点", key=f"graph_edge_{index}",
                  on_click=_select_node, args=(other,))
    if st.button("前往学习整理", key="graph_learning"):
        st.switch_page("pages/18_学习整理.py")


def _render_subject_links(database: Database, identifier: int) -> None:
    """Append confirmed subject associations without changing saved content."""

    with st.expander("关联更多学科"):
        with st.form(f"graph_subject_links_{identifier}"):
            subjects = st.multiselect(
                "追加学科", PRIMARY_SUBJECTS, key=f"graph_subjects_{identifier}",
                help="涉及多个学科时可多选，保存后同一个知识点会连接这些星云。",
            )
            if st.form_submit_button("保存学科关联"):
                if not subjects:
                    st.info("请先选择要关联的学科。")
                    return
                try:
                    added = KnowledgeSubjectService(database).add_subjects(identifier, subjects)
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001 - show local persistence failure
                    LOGGER.exception("保存知识点学科关联失败")
                    st.error(f"保存学科关联失败：{exc}")
                else:
                    if added:
                        st.rerun()
                    st.info("所选学科已经关联。")


def _knowledge_identifier(raw: object) -> int | None:
    """Return the knowledge object id behind a star id, or None for other kinds."""

    if not isinstance(raw, str) or not raw.startswith("knowledge:"):
        return None
    try:
        return int(raw.split(":", 1)[1])
    except ValueError:
        return None


def apply_star_relation_command(database: Database, request: dict) -> None:
    """Apply one 连接/断开 command issued inside the star map.

    Gesture links wait for on-page confirmation after sequential selection.
    Spoken/typed links apply at once; removal always requires confirmation.
    """

    action = request.get("action")
    source_id = _knowledge_identifier(request.get("source"))
    target_id = _knowledge_identifier(request.get("target"))
    source_title = str(request.get("source_title") or request.get("source") or "起点")
    target_title = str(request.get("target_title") or request.get("target") or "终点")
    if action not in ("link", "unlink") or source_id is None or target_id is None:
        st.toast("星图关系动作目前只支持知识点星子；题目与来源连线由本地资料自动生成。",
                 icon="⚠️")
        return
    service = KnowledgeObjectService(database)
    if source_id == target_id or service.get(source_id) is None or service.get(target_id) is None:
        st.toast("请选择两颗不同且仍存在的知识点星。", icon="⚠️")
        return
    if action == "link":
        relation_type = str(request.get("relation_type")
                            or KnowledgeRelationType.RELATES_TO.value)
        try:
            KnowledgeRelationType(relation_type)
            if request.get("require_confirmation"):
                st.session_state.pop("graph_pending_unlink", None)
                st.session_state["graph_pending_link"] = {
                    **request, "require_confirmation": False,
                    "source_title": source_title, "target_title": target_title,
                }
                st.rerun()
                return
            service.add_relation(source_id, target_id, relation_type=relation_type,
                                 description="星图动作")
        except KnowledgeObjectError as exc:
            st.toast(f"星图连接未保存:{exc}", icon="⚠️")
            return
        except Exception as exc:  # noqa: BLE001 - keep the map usable on local failures
            LOGGER.exception("星图动作保存知识关系失败")
            st.toast(f"星图连接未保存:{exc}", icon="⚠️")
            return
        label = KnowledgeRelationType(relation_type).label
        st.session_state["graph_relation_notice"] = (
            "✅", f"已连接:{source_title} {label} {target_title}")
        st.rerun()
        return
    try:
        matching = [relation for relation in service.relations(source_id)
                    if {relation.source_ko_id, relation.target_ko_id} == {source_id, target_id}]
    except KnowledgeObjectError as exc:
        st.toast(f"星图断开未执行:{exc}", icon="⚠️")
        return
    if not matching:
        st.toast("这两颗星之间没有已保存的关系。", icon="⚠️")
        return
    st.session_state.pop("graph_pending_link", None)
    st.session_state["graph_pending_unlink"] = {
        "relation_ids": [relation.id for relation in matching],
        "source_title": source_title,
        "target_title": target_title,
    }
    st.rerun()


def render_pending_relation_removal(database: Database) -> None:
    """Confirm sequential gesture links and all star-map relation removals."""

    pending_link = st.session_state.get("graph_pending_link")
    if pending_link:
        st.info(f"两星已选好，待确认连接：{pending_link['source_title']} ↔ "
                f"{pending_link['target_title']}（相关）。")
        confirm_link, cancel_link = st.columns(2)
        if confirm_link.button("确认连接", key="graph_link_confirm", type="primary"):
            st.session_state.pop("graph_pending_link", None)
            apply_star_relation_command(database, pending_link)
        if cancel_link.button("取消", key="graph_link_cancel"):
            st.session_state.pop("graph_pending_link", None)
            st.toast("已取消连接。", icon="✅")

    pending = st.session_state.get("graph_pending_unlink")
    if not pending:
        return
    ids = list(pending.get("relation_ids", ()))
    st.warning(
        f"待确认断开:{pending.get('source_title', '')} ↔ {pending.get('target_title', '')}"
        f"(共 {len(ids)} 条关系)。删除后需要重新建立。"
    )
    confirm, cancel = st.columns(2)
    if confirm.button("确认断开", key="graph_unlink_confirm", type="primary"):
        service = KnowledgeObjectService(database)
        removed = 0
        for relation_id in ids:
            try:
                service.remove_relation(relation_id)
            except KnowledgeObjectError:
                LOGGER.exception("星图动作断开知识关系失败:relation_id=%s", relation_id)
            else:
                removed += 1
        st.session_state.pop("graph_pending_unlink", None)
        if removed:
            st.session_state["graph_relation_notice"] = ("✅", f"已断开 {removed} 条关系。")
            st.rerun()
        st.toast("这些关系已不存在,无需断开。", icon="⚠️")
    if cancel.button("取消", key="graph_unlink_cancel"):
        st.session_state.pop("graph_pending_unlink", None)
        st.toast("已保留现有关系。", icon="✅")


def render_material_mining(database: Database) -> None:
    """AI proposes knowledge points and relations; saving needs explicit confirmation."""

    with st.expander("AI 从资料归纳知识点与关系（可选）"):
        documents = database.list_documents()
        if not documents:
            st.caption("还没有导入资料。先在「导入资料」完成 PDF 导入与文字提取，再回来归纳。")
            return
        labels = {f"{document.title}（{document.page_count} 页）": document.id
                  for document in documents}
        document_id = labels[st.selectbox("选择资料", list(labels), key="graph_mining_document")]
        try:
            provider = runtime.application_ai_provider()
        except Exception as exc:  # noqa: BLE001 - mining stays optional, never blocks reading
            LOGGER.warning("知识归纳 AI 暂不可用：error_type=%s", type(exc).__name__)
            provider = None
        if provider is None:
            st.info("当前 AI 未启用，暂不能自动归纳。知识串联的其他功能不受影响；"
                    "也可以在下方手动新增知识点。")
            return
        draft: KnowledgeMiningDraft | None = st.session_state.get(
            f"graph_mining_draft_{document_id}")
        if st.button("生成归纳草稿", key=f"graph_mining_generate_{document_id}"):
            with st.spinner("正在归纳资料中的知识点与关系（单次调用，可随时停止）…"):
                try:
                    draft = KnowledgeMiningAIService(provider).mine_document(
                        database, document_id)
                except AIError as exc:
                    st.error(f"AI 归纳未完成：{exc}")
                except (ValueError, KnowledgeMiningError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state[f"graph_mining_draft_{document_id}"] = draft
                    st.rerun()
        if draft is None:
            st.caption("点击「生成归纳草稿」后，AI 会从这份资料的文字中提炼知识点、"
                       "按重要性归类，并划分知识点之间的关系。")
            return
        st.caption(f"《{draft.document_title}》的归纳草稿（AI 生成，尚未保存）："
                   f"{len(draft.points)} 个知识点、{len(draft.relations)} 条关系。"
                   + ("资料较长，超出一部分的文字未参与归纳。" if draft.truncated else ""))
        for point in draft.points:
            pages = ""
            if point.page_numbers:
                pages = " · 来源页 " + ", ".join(map(str, point.page_numbers))
            classification = "；".join(
                subject + (f" → {child}" if child else "")
                for subject, child in point.subject_classifications
            ) or "未归类"
            st.markdown(f"- **{point.title}**（{point.importance}{pages} · "
                        f"{classification}）：{point.content}")
        if draft.relations:
            st.markdown("**知识点关系**")
            for relation in draft.relations:
                st.markdown(f"- {relation.source_title} → {relation.relation_type.label} → "
                            f"{relation.target_title}"
                            + (f"：{relation.description}" if relation.description else ""))
        save_column, discard_column = st.columns([1, 1])
        if save_column.button("确认保存到知识库", type="primary", use_container_width=True,
                              key=f"graph_mining_save_{document_id}"):
            try:
                result = save_mining_draft(database, draft)
            except Exception as exc:  # noqa: BLE001 - report exactly what failed to save
                LOGGER.exception("保存 AI 归纳草稿失败")
                st.error(f"保存归纳结果失败：{exc}")
            else:
                st.session_state.pop(f"graph_mining_draft_{document_id}", None)
                st.session_state["graph_mining_saved"] = (
                    f"已保存 {len(result.knowledge_ids)} 个知识点和 "
                    f"{result.relation_count} 条关系到本地知识库，星图已更新。"
                )
                st.rerun()
        if discard_column.button("放弃草稿", use_container_width=True,
                                 key=f"graph_mining_discard_{document_id}"):
            st.session_state.pop(f"graph_mining_draft_{document_id}", None)
            st.rerun()


def render_graph_editor(database: Database, graph: KnowledgeGraph) -> None:
    """Save only explicit form submissions into the shared local data foundation."""

    knowledge = [key for key, node in graph.nodes.items() if node["entity_kind"] == "knowledge"]
    questions = [key for key, node in graph.nodes.items() if node["entity_kind"] == "question"]
    label = lambda key: graph.nodes[key]["title"]  # noqa: E731
    if st.session_state.pop("graph_new_knowledge_reset", False):
        _reset_knowledge_draft()
    if st.session_state.pop("graph_new_knowledge_saved", False):
        st.success("知识点已保存到本地知识库。")
    if st.session_state.pop("graph_knowledge_math_unavailable", False):
        st.info("当前 AI 未启用或排版任务无法启动：原文已保存；"
                "启用 AI 后，新保存的知识点会自动排版。")
    # With discard first, disable Enter submission so it cannot accidentally discard a draft.
    with st.expander("新增知识点", expanded=not knowledge), st.form(
        "graph_new_knowledge", enter_to_submit=False,
    ):
        title = st.text_input("知识点名称", max_chars=200, key="graph_new_knowledge_title")
        content = st.text_area("知识点内容", key="graph_new_knowledge_content")
        basis = st.selectbox("形成依据", [KnowledgeEpistemicBasis.PERSONAL_JUDGMENT,
                                        KnowledgeEpistemicBasis.SOURCE_DERIVED,
                                        KnowledgeEpistemicBasis.DIRECT_OBSERVATION],
                             format_func=lambda value: value.label,
                             key="graph_new_knowledge_basis")
        source_page = st.selectbox(
            "来源页（可选）", [None, *[key for key in graph.nodes if key.startswith("page:")]],
            format_func=lambda key: "暂不关联来源" if key is None else label(key),
            key="graph_new_knowledge_source",
            placeholder="暂不关联来源",
        )
        st.caption("保存后自动使用已启用的 AI 排版数学公式，保留原文；内容与来源由你核对。")
        discard_column, _, save_column = st.columns([1.2, 4, 1.2])
        discard_column.form_submit_button(
            "放弃修改", type="secondary", width="stretch", on_click=_reset_knowledge_draft,
        )
        save = save_column.form_submit_button("保存修改", type="primary", width="stretch")
        if save:
            try:
                if basis == KnowledgeEpistemicBasis.SOURCE_DERIVED and source_page is None:
                    raise ValueError("选择“来源提炼”时，请关联一个来源页。")
                sources = ([("page", int(source_page.split(":")[1]), "知识串联中人工关联")]
                           if source_page else ())
                view = KnowledgeObjectService(database).create(
                    kind="concept", title=title, content=content, epistemic_basis=basis,
                    source_links=sources,
                )
            except Exception as exc:
                LOGGER.exception("保存星图知识点失败")
                st.error(f"保存知识点失败：{exc}")
            else:
                # The authoritative save is complete before any provider work is scheduled.
                try:
                    task = schedule_knowledge_math(
                        database, runtime.application_settings().cache_dir,
                        view.knowledge_object.id, _knowledge_ai_service(),
                    )
                except Exception as exc:  # noqa: BLE001 - never misreport a successful save
                    LOGGER.warning("知识点排版任务未启动：error_type=%s", type(exc).__name__)
                    task = None
                st.session_state["graph_knowledge_math_unavailable"] = task is None
                st.session_state["graph_saved_knowledge_node"] = (
                    f"knowledge:{view.knowledge_object.id}"
                )
                st.session_state["graph_new_knowledge_reset"] = True
                st.session_state["graph_new_knowledge_saved"] = True
                st.rerun()
    with st.expander("添加题目与知识点关联"):
        if not knowledge or not questions:
            st.caption("先在学习整理保存题目，并在知识库或这里保存知识点。")
        else:
            with st.form("graph_question_link"):
                question = st.selectbox("题目", questions, format_func=label)
                target = st.selectbox("涉及的知识点", knowledge, format_func=label)
                note = st.text_input("关联说明", max_chars=1000)
                if st.form_submit_button("保存题目关联"):
                    try:
                        KnowledgeGraphService(database).link_question(
                            int(question.split(":")[1]), int(target.split(":")[1]), note=note,
                        )
                    except Exception as exc:
                        LOGGER.exception("保存题目知识关联失败")
                        st.error(f"保存关联失败：{exc}")
                    else:
                        st.rerun()
    with st.expander("添加知识点之间的关系"):
        st.caption("不同学科的知识点也可以直接关联，请选择具体的起点和终点。")
        if len(knowledge) < 2:
            st.caption("至少保存两个知识点后，可以关联它们。")
        else:
            with st.form("graph_knowledge_relation"):
                def subject_label(key: str) -> str:
                    tags = graph.nodes[key]["tags"]
                    subjects = "、".join(dict.fromkeys(tag.split("/", 1)[0] for tag in tags))
                    return label(key) + f"（{subjects or '未归类'}）"

                source = st.selectbox("起点知识点", knowledge, format_func=subject_label)
                target = st.selectbox("终点知识点", knowledge, index=1, format_func=subject_label)
                relation = st.selectbox("关系", list(KnowledgeRelationType),
                                        format_func=lambda value: value.label)
                description = st.text_input("关系说明", max_chars=1000)
                if st.form_submit_button("保存知识关系"):
                    try:
                        KnowledgeObjectService(database).add_relation(
                            int(source.split(":")[1]), int(target.split(":")[1]),
                            relation_type=relation, description=description,
                        )
                    except Exception as exc:
                        LOGGER.exception("保存知识关系失败")
                        st.error(f"保存关系失败：{exc}")
                    else:
                        st.rerun()
