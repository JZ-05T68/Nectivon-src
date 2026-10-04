"""Tools-area navigation order and delete-entry contract (v0.8.6 RUN 2).

The user-ratified tools order is fixed:
添加文件 → 删除文件 → 待核对 → 备份与修复.
These tests lock the order and verify the delete entry reuses the existing
safe document-deletion page rather than a second implementation.
Note: R4 red team found this file still asserting the pre-rename label
待整理 while the shipped UI (sidebar, breadcrumb, overview CTA) had fully
moved to 待核对; the contract now follows the shipped label.
"""

from __future__ import annotations

from src.workspace_ui import _NAVIGATION


def test_tools_menu_follows_user_ratified_order() -> None:
    tools = next(entries for group, entries in _NAVIGATION if group == "工具")
    labels = [label for _, label, _ in tools]

    assert labels == ["添加文件", "删除文件", "待核对", "备份与修复"]


def test_delete_entry_targets_the_safe_deletion_page() -> None:
    tools = next(entries for group, entries in _NAVIGATION if group == "工具")
    delete_entry = next(entry for entry in tools if entry[1] == "删除文件")

    assert delete_entry[0] == "pages/11_文档管理.py"
