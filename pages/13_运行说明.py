"""Show local runtime, storage, and privacy information."""

from __future__ import annotations

import logging

import streamlit as st

from src import __version__
from src.config import OFFICIAL_PORT, STAGING_PORT, settings_env_path
from src.factory_reset_service import FactoryResetError, reset_failed_import_traces
from src.font_size_preferences import read_font_level, write_font_level
from src.migrations import SCHEMA_VERSION
from src.provider_settings_ui import render_provider_settings
from src.runtime import application_database, application_settings
from src.storage_location_service import (
    StorageLocationError,
    relocate_storage,
    relocate_storage_path,
)
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)

st.set_page_config(page_title=f"运行说明 · Nectivon v{__version__}", page_icon="⚙️", layout="wide")
render_workspace("pages/13_运行说明.py")
st.title("设置与运行说明")

settings = application_settings()
st.success(f"服务正常运行：{settings.host}:{settings.port}")
st.caption("健康检查由本机 Streamlit 内置端点 `/_stcore/health` 提供，不包含用户资料。")

render_provider_settings()

st.subheader("恢复出厂设置")
st.caption(
    "仅清除失败的导入历史、失败的导入队列条目和本地日志内容。"
    "不会删除原 PDF、页面图片、Markdown、文档/页面数据、笔记、学习整理、"
    "知识串联、备份或其他文件；待处理、中断、已完成和部分完成的导入记录会保留。"
)
factory_reset_confirmed = st.checkbox(
    "我确认清除失败导入记录并清空日志文件内容（保留文件本身）",
    key="factory_reset_failed_imports_confirmed",
)
if st.button(
    "恢复出厂设置",
    type="secondary",
    use_container_width=True,
    disabled=not factory_reset_confirmed,
):
    try:
        reset_result = reset_failed_import_traces(
            application_database(), settings.logs_dir
        )
    except FactoryResetError as exc:
        st.error(str(exc))
    except Exception as exc:
        LOGGER.exception("恢复出厂设置时清理失败记录未完成")
        st.error(f"清理失败记录未完成：{exc}")
    else:
        st.session_state["factory_reset_last_result"] = reset_result

reset_result = st.session_state.get("factory_reset_last_result")
if reset_result is not None:
    st.success(
        "清理完成：删除失败导入历史 "
        f"{reset_result.failed_import_records} 条、失败队列条目 "
        f"{reset_result.failed_queue_entries} 条；清空日志文件 "
        f"{reset_result.cleared_log_files} 个（{reset_result.cleared_log_bytes} 字节）。"
    )
    if reset_result.log_failures:
        st.warning(
            "以下日志文件未能清空，请检查文件占用或权限："
            + "、".join(reset_result.log_failures)
        )

st.subheader("本地位置")
st.code(
    "\n".join(
        (
            f"数据目录：{settings.data_dir}",
            f"数据库：{settings.database_path}",
            f"原 PDF：{settings.raw_dir}",
            f"页面图片：{settings.pages_dir}",
            f"Markdown：{settings.markdown_dir}",
            f"日志：{settings.logs_dir}",
            f"运行状态：{settings.runtime_dir}",
        )
    )
)

st.markdown("#### 修改资料存储位置")
st.caption(
    "下面 7 个位置可以分别修改。每次只修改一项，重启当前服务后再继续修改下一项。"
    "原位置不会自动删除。"
)
storage_change_available = settings.port in {OFFICIAL_PORT, STAGING_PORT}
is_staging = settings.port == STAGING_PORT
if is_staging:
    st.info("当前修改仅作用于 8511 隔离测试实例，不会读取或改写 8501 正式资料。")
elif not storage_change_available:
    st.info("当前是测试注入环境，只展示修改入口，不会写入本地配置。")

location_specs = (
    ("data", "数据目录", settings.data_dir, "新的数据目录会同时迁移数据库和全部资料。"),
    ("database", "数据库", settings.database_path, "请输入数据目录内一个尚不存在的 .db 文件。"),
    ("raw", "原 PDF", settings.raw_dir, "请输入数据目录内一个不存在或为空的目录。"),
    ("pages", "页面图片", settings.pages_dir, "请输入数据目录内一个不存在或为空的目录。"),
    ("markdown", "Markdown", settings.markdown_dir, "请输入数据目录内一个不存在或为空的目录。"),
    ("logs", "日志", settings.logs_dir, "请输入程序目录外一个不存在或为空的目录。"),
    ("runtime", "运行状态", settings.runtime_dir, "请输入程序目录外一个不存在或为空的目录。"),
)
pending_relocation = st.session_state.get("runtime_storage_relocation")
requested_change: tuple[str, str, str] | None = None
for location_name, location_label, current_path, location_help in location_specs:
    with st.expander(f"{location_label}｜{current_path}"):
        with st.form(f"storage_location_{location_name}", border=False):
            target_value = st.text_input(
                f"新的{location_label}位置",
                value=str(current_path),
                help=location_help,
                disabled=(not storage_change_available or pending_relocation is not None),
            )
            confirmed = st.checkbox(
                "我确认复制完成前不关闭程序，并会在完成后立即重启当前服务。",
                key=f"storage_confirm_{location_name}",
                disabled=(not storage_change_available or pending_relocation is not None),
            )
            submitted = st.form_submit_button(
                f"修改{location_label}位置",
                use_container_width=True,
                disabled=(
                    not storage_change_available
                    or pending_relocation is not None
                    or not confirmed
                    or not target_value.strip()
                ),
            )
        if submitted:
            requested_change = (location_name, location_label, target_value.strip())

if requested_change is not None:
    location_name, location_label, target_value = requested_change
    try:
        with st.spinner("正在创建备份并复制资料，请不要关闭程序…"):
            if location_name == "data":
                env_key = (
                    "EKB_STAGING_STORAGE_DIR" if is_staging else "EKB_STORAGE_DIR"
                )
                relocation = relocate_storage(
                    settings,
                    target_value,
                    env_path=settings_env_path(),
                    env_key=env_key,
                )
                source_path = relocation.source_data_dir
                target_path = relocation.target_data_dir
            else:
                relocation = relocate_storage_path(
                    settings,
                    location_name,
                    target_value,
                    env_path=settings_env_path(),
                    staging=is_staging,
                )
                source_path = relocation.source_path
                target_path = relocation.target_path
    except StorageLocationError as exc:
        LOGGER.warning("修改存储位置未完成：%s", exc)
        st.error(str(exc))
    except Exception as exc:
        LOGGER.exception("修改存储位置发生未预期错误")
        st.error(f"修改存储位置失败，原位置保持不变：{exc}")
    else:
        st.session_state["runtime_storage_relocation"] = {
            "label": location_label,
            "source": source_path,
            "target": target_path,
            "backup": relocation.backup_path,
        }

relocation = st.session_state.get("runtime_storage_relocation")
if isinstance(relocation, dict):
    st.success(f"{relocation['label']}的新副本已验证：{relocation['target']}")
    st.write(
        f"已保留切换前备份：`{relocation['backup']}`。"
        f"原位置仍保留在 `{relocation['source']}`。"
    )
    st.warning("新的存储位置将在重启当前服务后生效；重启前本页面仍使用原位置。")
    if is_staging:
        st.code(
            ".\\停止Nectivon_测试服_8511.bat\n"
            ".\\启动Nectivon_测试服_8511.bat",
            language="powershell",
        )
    else:
        st.code(
            ".\\停止Nectivon.bat\n.\\启动Nectivon.bat",
            language="powershell",
        )

st.subheader("字体大小")
st.caption("五级调节，立即作用于阅读页面和数学公式；只保存在当前实例的本地资料目录。")
font_level = st.select_slider(
    "阅读字号",
    options=[1, 2, 3, 4, 5],
    value=read_font_level(settings.data_dir),
    format_func=lambda value: ("最小", "偏小", "标准", "偏大", "最大")[value - 1],
    key="reading_font_level",
)
if font_level != read_font_level(settings.data_dir):
    try:
        write_font_level(settings.data_dir, font_level)
    except OSError as exc:
        st.error(f"字体大小未保存：{exc}")
    else:
        st.rerun()

st.subheader("日常运行")
st.markdown(
    """
- 双击项目根目录的 `启动Nectivon.bat` 启动，浏览器会自动打开。
- 双击 `停止Nectivon.bat` 只停止本项目记录的进程。
- `启用开机自启.bat` 与 `关闭开机自启.bat` 管理当前用户登录后的可选计划任务。
- 服务仅绑定 `127.0.0.1`，不会默认向局域网开放。
"""
)
st.info(
    f"v{__version__} 不包含账号、登录、云同步或联网必需功能。"
    "AI 模型服务默认不启用：未配置 API Key 时全部本地知识功能仍可离线使用。"
    "可按需接入 DeepSeek、Qwen、Kimi 或腾讯混元，应用不会在后台自动测试连接，"
    "Agent 也不会自动执行任何多步操作。"
)
st.caption(
    f"当前数据库为 schema v{SCHEMA_VERSION}（含 AI 调用台账、AI 输出锚点与知识关联表）。"
    "已有旧 schema 数据库升级时会先创建一致性备份，再按版本顺序纯增量迁移；"
    f"正式恢复只接受与当前版本兼容且同为 schema v{SCHEMA_VERSION} 的完整备份。"
)
if st.button("🛡️ 打开系统维护、备份与诊断", use_container_width=True):
    st.switch_page("pages/12_系统维护.py")
