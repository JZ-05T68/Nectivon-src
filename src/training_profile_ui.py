"""Streamlit user interface component for learner profile configuration.

Implements Phase 1 of the Targeted Training / One-to-Many Workflow:
1. Top-level education type selection (Basic Education | Higher Education).
2. Basic education configuration (Region, Grade, Qiangji Plan, Olympiad).
3. Higher education configuration (School Search, Level, Major, Graduate Discipline).
4. Strict state machine: View Mode (read-only) vs. Edit Mode.
5. Dirty-state unsaved changes interceptor: "Save Data", "Discard Changes", "Cancel".
6. Complete mutual exclusion between Basic and Higher education data.
"""

from __future__ import annotations

import logging

import streamlit as st

from src.browser_geolocation import render_city_geolocation_button
from src.training_profile_data import (
    CITIES_BY_PROVINCE,
    COMPETITION_STAGES_BY_SUBJECT,
    COMPETITION_SUBJECTS,
    EDUCATION_STAGES,
    GRADES_BY_STAGE,
    GRADUATE_DISCIPLINE_CATEGORIES,
    HIGHER_EDUCATION_LEVELS,
    PROVINCES,
    QIANGJI_SCHOOLS,
    QIANGJI_UNIVERSITIES_AND_MAJORS,
    get_first_level_disciplines,
    is_valid_school_name,
    is_valid_undergraduate_major,
    load_official_schools,
    load_undergraduate_majors,
    search_official_schools,
    search_undergraduate_majors,
)
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
    ProfileValidationError,
)
from src.training_profile_service import TrainingProfileService

LOGGER = logging.getLogger(__name__)


def render_training_profile_page(service: TrainingProfileService) -> None:
    """Render the full profile configuration UI with state machine and boundary checks."""

    # 1. 加载或初始化当前保存的 Profile
    saved_profile = service.get_profile(check_grade_upgrade=True)

    # 2. 初始化 Session State 状态机
    if "profile_mode" not in st.session_state:
        # 如果已有保存数据，进入查看状态；否则进入编辑状态
        st.session_state["profile_mode"] = "edit" if saved_profile.is_empty else "view"

    if "profile_pending_switch_type" not in st.session_state:
        st.session_state["profile_pending_switch_type"] = None

    if "show_unsaved_dialog" not in st.session_state:
        st.session_state["show_unsaved_dialog"] = False

    if "unsaved_action_target" not in st.session_state:
        st.session_state["unsaved_action_target"] = None

    if "profile_edu_type" not in st.session_state:
        if saved_profile.education_type:
            st.session_state["profile_edu_type"] = (
                "基础教育"
                if saved_profile.education_type == EducationType.BASIC
                else "高等教育"
            )
        else:
            st.session_state["profile_edu_type"] = "基础教育"

    current_mode = st.session_state["profile_mode"]
    is_editing = current_mode == "edit"

    # ==========================================================================
    # 未保存修改确认弹窗/浮层
    # ==========================================================================
    if st.session_state.get("show_unsaved_dialog"):
        _render_unsaved_modal(service, saved_profile)
        return

    # ==========================================================================
    # 页面标题与模式状态提示
    # ==========================================================================
    header_col, status_col = st.columns([4, 1.5], vertical_alignment="center")
    with header_col:
        st.subheader("训练配置")
        st.caption("配置所在地区、教学阶段与年级。学科在待核对资料时单独确认；所有信息仅存储在本机。")
    with status_col:
        if is_editing:
            st.info("当前状态：编辑中 ✍️")
        else:
            st.success("当前状态：已保存（只读） 🔒")

    # ==========================================================================
    # 一、顶部教育类型选择：基础教育 | 高等教育
    # ==========================================================================
    st.markdown("---")
    st.markdown("##### 学习阶段类型")

    edu_options = ["基础教育", "高等教育"]
    selected_edu_type = st.radio(
        "选择教育类型",
        edu_options,
        index=edu_options.index(st.session_state["profile_edu_type"]),
        horizontal=True,
        disabled=not is_editing,
        key="edu_type_radio_input",
    )

    # 检查是否切换了类型
    if selected_edu_type != st.session_state["profile_edu_type"]:
        # 如果当前在编辑模式且有修改，拦截并提醒
        st.session_state["profile_edu_type"] = selected_edu_type
        st.rerun()

    st.markdown("---")

    # ==========================================================================
    # 二、根据教育类型分别渲染表单
    # ==========================================================================
    if st.session_state["profile_edu_type"] == "基础教育":
        _render_basic_education_section(service, saved_profile, is_editing)
    else:
        _render_higher_education_section(service, saved_profile, is_editing)


def _render_unsaved_modal(service: TrainingProfileService, saved_profile: LearnerProfile) -> None:
    """Render the unsaved changes interceptor modal with 3 mandatory options."""

    st.warning("⚠️ 当前修改尚未保存，是否保存？")
    st.write("若不保存，未提交的修改内容将会丢失。请选择后续操作：")

    col_save, col_discard, col_cancel = st.columns(3)

    target_action = st.session_state.get("unsaved_action_target")

    with col_save:
        if st.button("保存数据", type="primary", use_container_width=True):
            # 执行必填项校验与保存
            success = _trigger_save_from_state(service)
            if success:
                st.session_state["show_unsaved_dialog"] = False
                st.session_state["unsaved_action_target"] = None
                if target_action == "view":
                    st.session_state["profile_mode"] = "view"
                elif target_action and target_action.startswith("switch:"):
                    st.session_state["profile_edu_type"] = target_action.split(":", 1)[1]
                st.rerun()

    with col_discard:
        if st.button("放弃修改", use_container_width=True):
            # 清理草稿缓存
            _clear_draft_states()
            st.session_state["show_unsaved_dialog"] = False
            st.session_state["unsaved_action_target"] = None
            if target_action == "view":
                st.session_state["profile_mode"] = "view"
            elif target_action and target_action.startswith("switch:"):
                st.session_state["profile_edu_type"] = target_action.split(":", 1)[1]
            st.rerun()

    with col_cancel:
        if st.button("取消", use_container_width=True):
            # 取消操作，留在编辑状态
            st.session_state["show_unsaved_dialog"] = False
            st.session_state["unsaved_action_target"] = None
            st.rerun()


def _clear_draft_states() -> None:
    """Reset every form widget and mirror value so saved data is reloaded.

    Streamlit widget keys are themselves the draft.  Clearing only auxiliary
    ``draft_*`` names leaves edited values visible in read-only mode even when
    the database was never updated.
    """

    keys_to_clear = [
        "edu_type_radio_input",
        "basic_province_select", "basic_city_select", "basic_stage_select",
        "basic_grade_select", "basic_sb_radio", "basic_sb_school_select",
        "basic_sb_subject_select", "basic_comp_radio",
        "basic_province_val", "basic_city_val", "basic_stage_val", "basic_grade_val",
        "basic_sb_val", "basic_sb_school_val", "basic_sb_subject_val", "basic_comp_val",
        "higher_school_search_box", "higher_school_dropdown", "higher_school_readonly",
        "higher_level_select", "higher_major_search_box", "higher_major_dropdown",
        "higher_major_readonly", "higher_discipline_select", "higher_first_level_select",
        "higher_school_query", "higher_school_selected", "higher_level_val",
        "higher_major_query", "higher_major_selected", "higher_disc_val",
        "higher_first_level_val", "profile_city_geolocation",
        "profile_pending_location", "profile_location_success", "profile_location_error",
        "profile_location_request_id",
    ]
    for sub in COMPETITION_SUBJECTS:
        keys_to_clear.extend((f"basic_comp_check_{sub}", f"basic_comp_stage_{sub}"))
    for k in keys_to_clear:
        st.session_state.pop(k, None)


def _apply_pending_location_state() -> None:
    """Apply a city-level location result before the region widgets are created.

    Streamlit does not allow a widget's keyed value to be changed after that
    widget has been instantiated in the current run.  The location button is
    rendered after both region selectboxes, so it records a pending result and
    triggers a rerun.  This helper consumes that result at the beginning of the
    next render, where updating the widget keys is safe.

    Only province and city names enter session state.  Browser coordinates are
    deliberately discarded by the resolver and are never persisted.
    """

    pending = st.session_state.pop("profile_pending_location", None)
    if not isinstance(pending, (tuple, list)) or len(pending) != 2:
        return

    province, city = str(pending[0]), str(pending[1])
    province_code = next((code for code, name in PROVINCES if name == province), "")
    valid_cities = {name for _, name in CITIES_BY_PROVINCE.get(province_code, [])}
    if not province_code or city not in valid_cities:
        st.session_state["profile_location_error"] = "定位结果无法匹配到受支持的省市数据。"
        return

    st.session_state["basic_province_select"] = province
    st.session_state["basic_city_select"] = city
    st.session_state["basic_province_val"] = province
    st.session_state["basic_city_val"] = city
    st.session_state["profile_location_success"] = f"已根据城市定位更新为：{province} {city}"


def _handle_browser_location_result(result: dict[str, object] | None) -> bool:
    """Validate one browser component result and schedule a safe widget rerun.

    Returns ``True`` only when a new valid city result is ready to apply.  The
    payload contract intentionally has no coordinate fields.
    """

    if not result:
        return False
    request_id = str(result.get("request_id") or "")
    if not request_id or request_id == st.session_state.get("profile_location_request_id"):
        return False
    st.session_state["profile_location_request_id"] = request_id

    if result.get("status") != "success":
        st.session_state["profile_location_error"] = str(
            result.get("message") or "定位失败，请重试。"
        )
        return False

    province = str(result.get("province") or "")
    city = str(result.get("city") or "")
    st.session_state["profile_pending_location"] = (province, city)
    return True


def _trigger_save_from_state(service: TrainingProfileService) -> bool:
    """Validate and execute save according to the active education type."""

    edu_type = st.session_state.get("profile_edu_type")
    try:
        if edu_type == "基础教育":
            prov_name = st.session_state.get("basic_province_val", "")
            city_name = st.session_state.get("basic_city_val", "")
            prov_code = ""
            for code, name in PROVINCES:
                if name == prov_name:
                    prov_code = code
                    break
            city_code = ""
            for c_code, c_name in CITIES_BY_PROVINCE.get(prov_code, []):
                if c_name == city_name:
                    city_code = c_code
                    break

            stage = st.session_state.get("basic_stage_val", "")
            grade = st.session_state.get("basic_grade_val", "")
            if stage == "高中":
                in_sb = st.session_state.get("basic_sb_val", False)
                sb_school = st.session_state.get("basic_sb_school_val") if in_sb else None
                sb_subject = st.session_state.get("basic_sb_subject_val") if in_sb else None

                in_comp = st.session_state.get("basic_comp_val", False)
                comp_subjects = {}
                if in_comp:
                    for sub in COMPETITION_SUBJECTS:
                        if st.session_state.get(f"basic_comp_check_{sub}", False):
                            stage_val = st.session_state.get(f"basic_comp_stage_{sub}")
                            if stage_val:
                                comp_subjects[sub] = stage_val
            else:
                in_sb = False
                sb_school = None
                sb_subject = None
                in_comp = False
                comp_subjects = {}

            profile = BasicEducationProfile(
                province=prov_name,
                province_code=prov_code,
                city=city_name,
                city_code=city_code,
                stage=stage,
                grade=grade,
                in_strong_base=in_sb,
                strong_base_school=sb_school,
                strong_base_subject=sb_subject,
                in_competition=in_comp,
                competition_subjects=comp_subjects,
            )
            service.save_basic_profile(profile)
            st.session_state["profile_mode"] = "view"
            st.toast("基础教育配置已成功保存！高等教育数据已按隔离规则清空。")
            return True
        else:
            if not load_official_schools() or not load_undergraduate_majors():
                st.error("当前公开发行版未包含该目录数据，暂不能新增或修改高等教育配置。")
                return False
            school = st.session_state.get("higher_school_selected", "")
            level = st.session_state.get("higher_level_val", "")
            major = st.session_state.get("higher_major_selected") if level == "本科" else None
            disc = st.session_state.get("higher_disc_val") if level == "研究生" else None
            first_level = (
                st.session_state.get("higher_first_level_val") if level == "研究生" else None
            )

            profile_higher = HigherEducationProfile(
                school=school,
                education_level=level,
                major=major,
                discipline_category=disc,
                discipline_first_level=first_level,
            )
            service.save_higher_profile(profile_higher)
            st.session_state["profile_mode"] = "view"
            st.toast("高等教育配置已成功保存！基础教育数据已按隔离规则清空。")
            return True
    except ProfileValidationError as e:
        st.error(f"校验失败：{e}")
        return False
    except Exception as e:
        st.error(f"保存发生异常：{e}")
        return False


# ==============================================================================
# 基础教育界面渲染
# ==============================================================================


def _render_basic_education_section(
    service: TrainingProfileService, saved: LearnerProfile, is_editing: bool
) -> None:
    """Render the Basic Education form and read-only view."""

    st.markdown("#### 基础教育配置")

    # 提取已保存的值作为默认值
    saved_b = saved.basic if saved.education_type == EducationType.BASIC else None

    # 定位结果必须在 keyed selectbox 实例化前应用，否则 Streamlit 会继续
    # 使用旧的控件值，造成“点击定位但省市不变”。
    _apply_pending_location_state()

    # 第一行：地区选择（省份 ▼  城市 ▼）+ 定位辅助
    st.markdown("**第一行：地区选择**")
    col_prov, col_city, col_loc = st.columns([3, 3, 2], vertical_alignment="bottom")

    # 省份列表（按代码升序排列）
    prov_names = [name for _, name in PROVINCES]
    def_prov_idx = 0
    if saved_b and saved_b.province in prov_names:
        def_prov_idx = prov_names.index(saved_b.province)

    with col_prov:
        selected_province = st.selectbox(
            "省份 ▼",
            prov_names,
            index=def_prov_idx,
            disabled=not is_editing,
            key="basic_province_select",
        )
        st.session_state["basic_province_val"] = selected_province

    # 获取对应省份代码
    prov_code = ""
    for code, name in PROVINCES:
        if name == selected_province:
            prov_code = code
            break

    # 城市列表联动（同样按代码升序排列）
    city_list = [name for _, name in CITIES_BY_PROVINCE.get(prov_code, [])]
    current_city_widget = st.session_state.get("basic_city_select")
    if city_list and current_city_widget not in city_list:
        # 省份变化后立刻清除上一省份的城市控件状态，避免出现
        # “北京市 / 南京市”这类瞬时非法组合。
        st.session_state["basic_city_select"] = city_list[0]
    def_city_idx = 0
    if saved_b and saved_b.city in city_list and saved_b.province == selected_province:
        def_city_idx = city_list.index(saved_b.city)

    with col_city:
        selected_city = st.selectbox(
            "城市 ▼",
            city_list,
            index=def_city_idx,
            disabled=not is_editing,
            key="basic_city_select",
        )
        st.session_state["basic_city_val"] = selected_city

    with col_loc:
        if is_editing:
            location_result = render_city_geolocation_button(key="profile_city_geolocation")
            if _handle_browser_location_result(location_result):
                st.rerun()

        location_success = st.session_state.pop("profile_location_success", None)
        location_error = st.session_state.pop("profile_location_error", None)
        if location_success:
            st.success(location_success)
        if location_error:
            st.error(location_error)

    # 第二行：教育阶段与年级
    st.markdown("---")
    st.markdown("**第二行：教育阶段与年级**")
    col_stage, col_grade = st.columns(2)

    def_stage_idx = 2  # 默认高中
    if saved_b and saved_b.stage in EDUCATION_STAGES:
        def_stage_idx = EDUCATION_STAGES.index(saved_b.stage)

    with col_stage:
        selected_stage = st.selectbox(
            "教育阶段 ▼",
            EDUCATION_STAGES,
            index=def_stage_idx,
            disabled=not is_editing,
            key="basic_stage_select",
        )
        st.session_state["basic_stage_val"] = selected_stage

    # 年级根据教育阶段动态联动
    grade_list = GRADES_BY_STAGE.get(selected_stage, [])
    def_grade_idx = 0
    if saved_b and saved_b.grade in grade_list and saved_b.stage == selected_stage:
        def_grade_idx = grade_list.index(saved_b.grade)

    with col_grade:
        selected_grade = st.selectbox(
            "年级 ▼",
            grade_list,
            index=def_grade_idx,
            disabled=not is_editing,
            key="basic_grade_select",
        )
        st.session_state["basic_grade_val"] = selected_grade

    # 只有基础教育阶段为“高中”时，才允许出现强基计划与学科竞赛配置入口
    is_high_school = selected_stage == "高中"

    if is_high_school:
        # 第三行：是否参与强基计划
        st.markdown("---")
        st.markdown("**第三行：是否参与强基计划**")
        sb_options = ["否", "是"]
        def_sb_idx = 1 if (saved_b and saved_b.in_strong_base) else 0

        sb_choice = st.radio(
            "是否参与强基计划？",
            sb_options,
            index=def_sb_idx,
            horizontal=True,
            disabled=not is_editing,
            key="basic_sb_radio",
        )
        in_strong_base = sb_choice == "是"
        st.session_state["basic_sb_val"] = in_strong_base

        # 若选择“是”：新增 3.5 行
        if in_strong_base:
            st.markdown("**3.5 行：强基目标配置**")
            col_sb_sch, col_sb_sub = st.columns(2)

            def_sb_sch_idx = 0
            if saved_b and saved_b.strong_base_school in QIANGJI_SCHOOLS:
                def_sb_sch_idx = QIANGJI_SCHOOLS.index(saved_b.strong_base_school)

            with col_sb_sch:
                selected_sb_school = st.selectbox(
                    "强基目标学校 ▼",
                    QIANGJI_SCHOOLS,
                    index=def_sb_sch_idx,
                    disabled=not is_editing,
                    key="basic_sb_school_select",
                )
                st.session_state["basic_sb_school_val"] = selected_sb_school

            # 强基目标学科根据学校真实强基招生方向联动
            allowed_majors = QIANGJI_UNIVERSITIES_AND_MAJORS.get(selected_sb_school, [])
            def_sb_sub_idx = 0
            if (
                saved_b
                and saved_b.strong_base_subject in allowed_majors
                and saved_b.strong_base_school == selected_sb_school
            ):
                def_sb_sub_idx = allowed_majors.index(saved_b.strong_base_subject)

            with col_sb_sub:
                selected_sb_subject = st.selectbox(
                    "强基目标学科 ▼",
                    allowed_majors,
                    index=def_sb_sub_idx,
                    disabled=not is_editing,
                    key="basic_sb_subject_select",
                )
                st.session_state["basic_sb_subject_val"] = selected_sb_subject

        # 第四行：是否参与学科竞赛
        st.markdown("---")
        st.markdown("**第四行：是否参与学科竞赛**")
        comp_options = ["否", "是"]
        def_comp_idx = 1 if (saved_b and saved_b.in_competition) else 0

        comp_choice = st.radio(
            "是否参与学科竞赛？",
            comp_options,
            index=def_comp_idx,
            horizontal=True,
            disabled=not is_editing,
            key="basic_comp_radio",
        )
        in_comp = comp_choice == "是"
        st.session_state["basic_comp_val"] = in_comp

        # 若选择“是”：新增 4.5 行
        if in_comp:
            st.markdown("**4.5 行：竞赛学科选择**")
            st.caption("请选择你需要参加竞赛的学科（至少选择一个）：")

            for sub in COMPETITION_SUBJECTS:
                is_sub_selected = (
                    saved_b is not None
                    and saved_b.in_competition
                    and sub in saved_b.competition_subjects
                )
                sub_col_chk, sub_col_stage = st.columns([2, 5], vertical_alignment="center")

                with sub_col_chk:
                    chk_val = st.checkbox(
                        sub,
                        value=is_sub_selected,
                        disabled=not is_editing,
                        key=f"basic_comp_check_{sub}",
                    )

                with sub_col_stage:
                    if chk_val:
                        stages = COMPETITION_STAGES_BY_SUBJECT.get(sub, [])
                        def_stage_idx = 0
                        if (
                            saved_b
                            and sub in saved_b.competition_subjects
                            and saved_b.competition_subjects[sub] in stages
                        ):
                            def_stage_idx = stages.index(saved_b.competition_subjects[sub])

                        st.selectbox(
                            f"{sub} 竞赛备赛阶段 ▼",
                            stages,
                            index=def_stage_idx,
                            disabled=not is_editing,
                            key=f"basic_comp_stage_{sub}",
                            label_visibility="collapsed",
                        )
                    else:
                        st.empty()
    else:
        # 非高中阶段（小学/初中）：彻底隐藏强基与竞赛入口，并清除残留状态
        st.session_state["basic_sb_val"] = False
        st.session_state["basic_sb_school_val"] = None
        st.session_state["basic_sb_subject_val"] = None
        st.session_state["basic_comp_val"] = False
        for sub in COMPETITION_SUBJECTS:
            st.session_state[f"basic_comp_check_{sub}"] = False
            st.session_state.pop(f"basic_comp_stage_{sub}", None)

    # 底部操作栏（左下角编辑，右下角保存）
    _render_bottom_actions(service, is_editing)


# ==============================================================================
# 高等教育界面渲染
# ==============================================================================


def _render_higher_education_section(
    service: TrainingProfileService, saved: LearnerProfile, is_editing: bool
) -> None:
    """Render the Higher Education form and read-only view."""

    st.markdown("#### 高等教育配置")

    saved_h = saved.higher if saved.education_type == EducationType.HIGHER else None

    if not load_official_schools() or not load_undergraduate_majors():
        st.info(
            "当前公开发行版未包含学校、本科专业及目录来源数据。"
            "高等教育目录查询与新增配置暂不可用；已有资料保留，基础教育配置和其他核心功能可继续使用。"
        )
        if is_editing:
            st.caption("可在上方选择基础教育；目录缺失不代表所输入的学校或专业不存在。")
            if st.button("取消编辑", key="higher_catalog_unavailable_cancel"):
                _clear_draft_states()
                st.session_state["profile_mode"] = "view"
                st.rerun()
            return

    # 第一行：选择就读学校 + 学历 ▼
    st.markdown("**第一行：选择就读学校与学历**")
    col_sch, col_lvl = st.columns([5, 3])

    with col_sch:
        st.caption("学校（关键词搜索 + 下拉匹配，只能选择真实收录的高校，禁止自由创建）：")
        current_sch_name = saved_h.school if saved_h else ""

        if is_editing:
            search_query = st.text_input(
                "输入学校关键词：",
                value=st.session_state.get("higher_school_query", current_sch_name),
                placeholder="例如：北京邮电、清华、南京大学…",
                key="higher_school_search_box",
            )
            st.session_state["higher_school_query"] = search_query

            # 搜索匹配真实学校
            matched_schools = search_official_schools(search_query) if search_query.strip() else []

            if search_query.strip() and not matched_schools:
                st.error(
                    f"无法匹配到高校“{search_query}”。教育部全国普通高等学校名单中不存在此校，"
                    "请核对名称或输入正规全称。"
                )
                st.session_state["higher_school_selected"] = ""
            elif matched_schools:
                selected_sch = st.selectbox(
                    "匹配到的真实高校（请从中选择）：",
                    matched_schools,
                    key="higher_school_dropdown",
                )
                st.session_state["higher_school_selected"] = selected_sch
            elif current_sch_name and is_valid_school_name(current_sch_name):
                st.session_state["higher_school_selected"] = current_sch_name
            else:
                st.session_state["higher_school_selected"] = ""
        else:
            st.text_input(
                "就读学校：",
                value=current_sch_name,
                disabled=True,
                key="higher_school_readonly",
            )
            st.session_state["higher_school_selected"] = current_sch_name

    with col_lvl:
        def_lvl_idx = 0
        if saved_h and saved_h.education_level in HIGHER_EDUCATION_LEVELS:
            def_lvl_idx = HIGHER_EDUCATION_LEVELS.index(saved_h.education_level)

        selected_level = st.selectbox(
            "学历 ▼",
            HIGHER_EDUCATION_LEVELS,
            index=def_lvl_idx,
            disabled=not is_editing,
            key="higher_level_select",
        )
        st.session_state["higher_level_val"] = selected_level

    # 第二行分支联动：本科路径 vs 研究生路径
    st.markdown("---")
    if selected_level == "本科":
        st.markdown("**本科路径：第二行 - 专业**")
        st.caption("专业（关键词搜索 + 下拉匹配，匹配教育部普通高等学校本科专业目录）：")
        current_major = saved_h.major if saved_h and saved_h.major else ""

        if is_editing:
            major_query = st.text_input(
                "输入专业关键词：",
                value=st.session_state.get("higher_major_query", current_major),
                placeholder="例如：计算机、软件工程、人工智能、哲学…",
                key="higher_major_search_box",
            )
            st.session_state["higher_major_query"] = major_query
            matched_majors = search_undergraduate_majors(major_query) if major_query.strip() else []

            if major_query.strip() and not matched_majors:
                st.error(f"未在教育部专业目录中找到匹配的专业“{major_query}”。")
                st.session_state["higher_major_selected"] = ""
            elif matched_majors:
                selected_m = st.selectbox(
                    "匹配到的官方备案专业：",
                    matched_majors,
                    key="higher_major_dropdown",
                )
                st.session_state["higher_major_selected"] = selected_m
            elif current_major and is_valid_undergraduate_major(current_major):
                st.session_state["higher_major_selected"] = current_major
            else:
                st.session_state["higher_major_selected"] = ""
        else:
            st.text_input(
                "专业：",
                value=current_major,
                disabled=True,
                key="higher_major_readonly",
            )
            st.session_state["higher_major_selected"] = current_major

    elif selected_level == "研究生":
        st.markdown("**研究生路径：第二行 - 学科门类**")
        st.caption("使用国务院学位委员会/教育部现行研究生教育学科目录（共14项门类）：")

        def_disc_idx = 7  # 默认工学
        if saved_h and saved_h.discipline_category in GRADUATE_DISCIPLINE_CATEGORIES:
            def_disc_idx = GRADUATE_DISCIPLINE_CATEGORIES.index(saved_h.discipline_category)

        col_disc, col_first = st.columns(2)
        with col_disc:
            selected_disc = st.selectbox(
                "学科门类 ▼",
                GRADUATE_DISCIPLINE_CATEGORIES,
                index=def_disc_idx,
                disabled=not is_editing,
                key="higher_discipline_select",
            )
            st.session_state["higher_disc_val"] = selected_disc

        # 预留的一级学科扩展接口
        first_level_options = get_first_level_disciplines(selected_disc)
        with col_first:
            if first_level_options:
                def_first_idx = 0
                if saved_h and saved_h.discipline_first_level in first_level_options:
                    def_first_idx = first_level_options.index(saved_h.discipline_first_level)
                selected_first = st.selectbox(
                    "一级学科（预留扩展接口）▼",
                    first_level_options,
                    index=def_first_idx,
                    disabled=not is_editing,
                    key="higher_first_level_select",
                )
                st.session_state["higher_first_level_val"] = selected_first
            else:
                st.session_state["higher_first_level_val"] = None

    # 底部操作栏
    _render_bottom_actions(service, is_editing)


# ==============================================================================
# 底部操作栏：左下角编辑，右下角保存
# ==============================================================================


def _render_bottom_actions(service: TrainingProfileService, is_editing: bool) -> None:
    """Render the standard bottom action buttons implementing the state machine."""

    st.markdown("---")
    col_left, _, col_right = st.columns([2, 5, 2])

    with col_left:
        if is_editing:
            if st.button("放弃修改 / 取消编辑", use_container_width=True, key="btn_cancel_edit"):
                # 如果有未保存修改，弹出确认；否则直接返回查看状态
                st.session_state["show_unsaved_dialog"] = True
                st.session_state["unsaved_action_target"] = "view"
                st.rerun()
        else:
            if st.button(
                "编辑数据",
                icon=":material/edit:",
                use_container_width=True,
                key="btn_enter_edit",
            ):
                st.session_state["profile_mode"] = "edit"
                st.rerun()

    with col_right:
        if is_editing:
            if st.button(
                "保存数据",
                type="primary",
                icon=":material/save:",
                use_container_width=True,
                key="btn_save_data",
            ):
                _trigger_save_from_state(service)
                st.rerun()
        else:
            st.button(
                "保存数据",
                disabled=True,
                use_container_width=True,
                key="btn_save_disabled",
                help="查看状态下不可保存，请先点击左侧“编辑数据”",
            )
