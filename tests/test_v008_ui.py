"""v0.0.8 first-use empty states and maintenance-page integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.ai.credential_store import MemoryCredentialStore
from src.ai.model_registry import ProviderId
from src.ai.provider_config import ProviderConfigStore, ProviderSettings
from src.ai.provider_settings_service import ProviderSettingsService
from src.backup_service import BackupService
from src.config import Settings
from src.database import Database
from src.diagnostic_service import DiagnosticService
from src.document_service import DocumentService
from src.evidence_basket_service import EvidenceBasketService


def _button(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


@pytest.fixture
def empty_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        raw_dir=data / "raw",
        pages_dir=data / "pages",
        markdown_dir=data / "markdown",
        database_dir=data / "database",
        database_path=data / "database" / "knowledge.db",
        backups_dir=tmp_path / "backups",
        logs_dir=tmp_path / "logs",
        log_path=tmp_path / "logs" / "engineering-kb.log",
        runtime_dir=tmp_path / "runtime",
        pid_path=tmp_path / "runtime" / "engineering-kb.pid.json",
        port=49341,
        _env_file=None,
    )
    settings.ensure_directories()
    database = Database(settings.database_path)
    document_service = DocumentService(
        database,
        settings.raw_dir,
        settings.pages_dir,
        settings.markdown_dir,
    )
    evidence_service = EvidenceBasketService(database)
    backup_service = BackupService(
        app_version=settings.app_version,
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        database_path=settings.database_path,
        backups_dir=settings.backups_dir,
        host=settings.host,
        port=settings.port,
    )
    diagnostic_service = DiagnosticService(
        app_version=settings.app_version,
        project_root=tmp_path,
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        database_path=settings.database_path,
        backups_dir=settings.backups_dir,
        logs_dir=settings.logs_dir,
        log_path=settings.log_path,
        host=settings.host,
        port=settings.port,
        listener_addresses=lambda port: (),
        health_check=lambda port: False,
        port_is_open=lambda port: False,
    )

    import src.runtime as runtime

    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(runtime, "application_document_service", lambda: document_service)
    monkeypatch.setattr(
        runtime,
        "application_evidence_basket_service",
        lambda: evidence_service,
    )
    monkeypatch.setattr(runtime, "application_backup_service", lambda: backup_service)
    monkeypatch.setattr(
        runtime,
        "application_diagnostic_service",
        lambda: diagnostic_service,
    )
    provider_settings_service = ProviderSettingsService(
        config_store=ProviderConfigStore(tmp_path / "ai-providers-v1.json"),
        credential_store=MemoryCredentialStore(),
        application_settings=settings,
        transport=lambda *args: pytest.fail("UI render must not call provider network"),
        invalidate_runtime=lambda: None,
    )
    monkeypatch.setattr(
        runtime,
        "application_provider_settings_service",
        lambda: provider_settings_service,
    )
    return settings


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ("app.py", "第 1 步：添加资料"),
        ("pages/1_导入资料.py", "选择文件后点一下开始导入"),
        ("pages/3_浏览资料.py", "还没有可浏览的文档"),
        ("pages/4_检索资料.py", "暂无可检索内容"),
        ("pages/5_待整理页面.py", "还没有资料"),
        ("pages/9_标签管理.py", "创建第一个标签"),
        ("pages/10_项目管理.py", "创建第一个本地项目"),
        ("pages/7_证据篮.py", "证据篮为空"),
        # 维护页空备份提示包含当前应用版本号，从 settings 派生而非硬编码
        ("pages/12_系统维护.py", None),
    ],
)
def test_major_pages_have_actionable_empty_states(
    empty_runtime: Settings, page: str, expected: str | None
) -> None:
    if expected is None:
        expected = f"还没有 v{empty_runtime.app_version} 完整备份"
    app = AppTest.from_file(page).run(timeout=10)

    messages = [
        element.value
        for element in (*app.info, *app.success, *app.warning, *app.markdown)
    ]
    assert any(expected in message for message in messages)
    assert not app.exception


def test_maintenance_page_creates_verified_backup_and_runs_read_only_diagnostics(
    empty_runtime: Settings,
) -> None:
    app = AppTest.from_file("pages/12_系统维护.py").run(timeout=10)

    _button(app, "创建并验证完整备份").click().run(timeout=20)
    assert list(empty_runtime.backups_dir.glob("*/manifest.json"))
    assert not app.exception

    _button(app, "运行完整只读诊断").click().run(timeout=20)
    assert any("诊断完成" in element.value for element in (*app.warning, *app.error))
    assert not app.exception


def test_runtime_page_exposes_guarded_storage_location_controls(
    empty_runtime: Settings,
) -> None:
    app = AppTest.from_file("pages/13_运行说明.py").run(timeout=10)

    labels = {item.label for item in app.text_input}
    assert {
        "新的数据目录位置",
        "新的数据库位置",
        "新的原 PDF位置",
        "新的页面图片位置",
        "新的Markdown位置",
        "新的日志位置",
        "新的运行状态位置",
    }.issubset(labels)
    assert {"API Key", "Base URL"}.issubset(labels)
    for label in ("数据目录", "数据库", "原 PDF", "页面图片", "Markdown", "日志", "运行状态"):
        assert _button(app, f"修改{label}位置").disabled
    assert any("测试注入环境" in item.value for item in app.info)
    assert not app.exception


def test_runtime_page_enables_individual_controls_for_staging(
    empty_runtime: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.runtime as runtime

    staging = empty_runtime.model_copy(update={"port": 8511})
    monkeypatch.setattr(runtime, "application_settings", lambda: staging)
    app = AppTest.from_file("pages/13_运行说明.py").run(timeout=10)

    storage_inputs = [
        item for item in app.text_input if item.label.startswith("新的")
    ]
    assert len(storage_inputs) == 7
    assert all(not item.disabled for item in storage_inputs)
    assert any("仅作用于 8511" in item.value for item in app.info)
    assert not app.exception


def test_runtime_page_lists_five_providers_and_never_refills_saved_key(
    empty_runtime: Settings,
) -> None:
    import src.runtime as runtime

    service = runtime.application_provider_settings_service()
    service.save(
        ProviderSettings.default_for(ProviderId.QWEN),
        new_api_key="synthetic-ui-saved-key-91fa",
        make_active=True,
    )

    app = AppTest.from_file("pages/13_运行说明.py").run(timeout=10)

    provider_select = next(item for item in app.selectbox if item.label == "服务商")
    assert len(provider_select.options) == 5
    # v0.8.6: GLM joins as the fifth peer provider in the same selectbox.
    assert "GLM / 智谱" in provider_select.options
    provider_select.set_value(ProviderId.QWEN).run(timeout=10)
    api_key = next(item for item in app.text_input if item.label == "API Key")
    assert api_key.value == ""
    assert any("API Key 已安全保存" in item.value for item in app.success)
    visible = "\n".join(
        item.value for item in (*app.markdown, *app.caption, *app.success, *app.info)
    )
    assert "synthetic-ui-saved-key-91fa" not in visible


def test_runtime_page_custom_model_reveals_model_id_input(
    empty_runtime: Settings,
) -> None:
    app = AppTest.from_file("pages/13_运行说明.py").run(timeout=10)
    model_select = next(item for item in app.selectbox if item.label == "模型")

    model_select.set_value("自定义 Model ID").run(timeout=10)

    assert any(item.label == "自定义 Model ID" for item in app.text_input)
    assert not app.exception
