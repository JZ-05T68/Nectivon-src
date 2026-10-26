"""Check complete notice access from settings without credentials or network I/O."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.config import Settings

_CHAPTERS = (
    "一、平台定位与 AI 辅助边界",
    "二、知识来源与证据可靠性",
    "三、专业应用与工程安全",
    "四、版权与知识产权边界",
    "五、信息资源与协作边界",
    "六、数据安全与隐私保护",
    "七、功能边界与版本更新",
    "八、用户反馈与责任说明",
)


def test_settings_notice_is_complete_and_non_blocking_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every chapter, numbered boundary and red warning reachable offline."""

    import src.provider_settings_ui as providers
    import src.runtime as runtime
    import src.workspace_ui as workspace

    data = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        data_dir=data,
        database_path=data / "database" / "knowledge.db",
        raw_dir=data / "raw",
        pages_dir=data / "pages",
        markdown_dir=data / "markdown",
        logs_dir=tmp_path / "logs",
        runtime_dir=tmp_path / "runtime",
        port=49342,
    )
    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    # Other settings controls have their own integration tests; the notice
    # must need neither their credentials nor the shared workspace runtime.
    monkeypatch.setattr(providers, "render_provider_settings", lambda: None)
    monkeypatch.setattr(workspace, "render_workspace", lambda _: None)

    app = AppTest.from_file("pages/13_运行说明.py").run(timeout=10)

    assert not app.exception
    notice = app.expander[0]
    assert notice.label == "关于 / 使用声明与风险提示"
    assert not notice.proto.expanded
    content = notice.markdown[0].value
    assert re.findall(r"^### (.+)$", content, flags=re.MULTILINE) == list(_CHAPTERS)
    copyright_section = content.split("### 四、", 1)[1].split("### 五、", 1)[0]
    collaboration_section = content.split("### 五、", 1)[1].split("### 六、", 1)[0]
    assert re.findall(r"^(\d)\. ", copyright_section, flags=re.MULTILINE) == list("123456")
    assert re.findall(r"^(\d)\. ", collaboration_section, flags=re.MULTILINE) == list("1234567")
    for warning in (
        "AI 生成、修改、整理或改编的内容，并不当然意味着",
        "未经授权，不得擅自上传、分享、转发或公开他人的非公开信息资源。",
        "信息的可获取性不等于可传播性；",
        "使用联网功能或第三方 AI 模型服务时，",
        "本声明旨在向用户说明平台主要功能边界和使用风险，",
    ):
        assert f":red[**{warning}" in content
    assert "不免除开发维护方依法应承担的责任。" in content
    assert "积累可追溯的知识与经验。" in content
    assert len(notice.button) == len(notice.checkbox) == 0
    assert any(item.label == "阅读字号" for item in app.select_slider)
    assert not settings.data_dir.exists()
