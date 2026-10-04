"""Safe storage-location configuration and relocation tests."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from src import config
from src.config import Settings, StorageConfigurationError, storage_layout
from src.database import Database
from src.models import PageStatus
from src.storage_location_service import (
    StorageLocationError,
    relocate_storage,
    relocate_storage_path,
)


def _settings(root: Path) -> Settings:
    data = root / "data"
    settings = Settings(
        data_dir=data,
        raw_dir=data / "raw",
        pages_dir=data / "pages",
        markdown_dir=data / "markdown",
        agent_readings_dir=data / "agent-readings",
        database_dir=data / "database",
        database_path=data / "database" / "knowledge.db",
        backups_dir=root / "backups",
        logs_dir=root / "logs",
        log_path=root / "logs" / "engineering-kb.log",
        runtime_dir=root / "runtime",
        pid_path=root / "runtime" / "engineering-kb.pid.json",
        port=49341,
        _env_file=None,
    )
    settings.ensure_directories()
    return settings


def _library(root: Path) -> Settings:
    settings = _settings(root)
    pdf = settings.raw_dir / "manual.pdf"
    pdf.write_bytes(b"local source")
    image = settings.pages_dir / "1" / "page_0001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    markdown = settings.markdown_dir / "1" / "page_0001.md"
    markdown.parent.mkdir(parents=True)
    markdown.write_text("# local note", encoding="utf-8")
    database = Database(settings.database_path)
    document = database.create_document(
        title="本地手册",
        filename=pdf.name,
        source_path=pdf,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        markdown_path=markdown,
        markdown_content="# local note",
        status=PageStatus.REVIEWED,
    )
    database.update_document_page_count(document.id, 1)
    reading = settings.agent_readings_dir / "pages" / "page_1.json"
    reading.parent.mkdir(parents=True)
    reading.write_text('{"local": true}', encoding="utf-8")
    migration_backup = settings.database_dir / "backups" / "knowledge.v14.db"
    migration_backup.parent.mkdir(parents=True)
    migration_backup.write_bytes(b"rollback")
    (settings.data_dir / ".deletion-quarantine").mkdir()
    return settings


def test_formal_storage_root_derives_one_coherent_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "external" / "ekb-data"
    monkeypatch.setenv("EKB_STORAGE_DIR", str(target))
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()

    assert settings.data_dir == target.resolve()
    assert settings.raw_dir == target.resolve() / "raw"
    assert settings.pages_dir == target.resolve() / "pages"
    assert settings.markdown_dir == target.resolve() / "markdown"
    assert settings.agent_readings_dir == target.resolve() / "agent-readings"
    assert settings.database_path == target.resolve() / "database" / "knowledge.db"
    assert settings.backups_dir == config.PROJECT_ROOT / "backups"


def test_storage_layout_rejects_relative_and_disk_root_paths() -> None:
    with pytest.raises(StorageConfigurationError, match="绝对路径"):
        storage_layout(Path("relative-data"))
    with pytest.raises(StorageConfigurationError, match="磁盘根目录"):
        storage_layout(Path(Path.cwd().anchor))


def test_formal_loader_applies_each_individual_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    raw = data / "pdf-custom"
    pages = data / "images-custom"
    markdown = data / "notes-custom"
    database = data / "db-custom" / "library.db"
    logs = tmp_path / "logs-custom"
    runtime = tmp_path / "runtime-custom"
    for key, value in {
        "EKB_STORAGE_DIR": data,
        "EKB_STORAGE_RAW_DIR": raw,
        "EKB_STORAGE_PAGES_DIR": pages,
        "EKB_STORAGE_MARKDOWN_DIR": markdown,
        "EKB_STORAGE_DATABASE_PATH": database,
        "EKB_STORAGE_LOGS_DIR": logs,
        "EKB_STORAGE_RUNTIME_DIR": runtime,
    }.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()

    assert settings.raw_dir == raw.resolve()
    assert settings.pages_dir == pages.resolve()
    assert settings.markdown_dir == markdown.resolve()
    assert settings.database_path == database.resolve()
    assert settings.database_dir == database.resolve().parent
    assert settings.logs_dir == logs.resolve()
    assert settings.log_path == logs.resolve() / "engineering-kb.log"
    assert settings.runtime_dir == runtime.resolve()
    assert settings.pid_path == runtime.resolve() / "engineering-kb.pid.json"


def test_formal_loader_rejects_individual_asset_path_outside_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EKB_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EKB_STORAGE_RAW_DIR", str(tmp_path / "outside-raw"))
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )
    config.get_settings.cache_clear()
    try:
        with pytest.raises(StorageConfigurationError, match="数据目录内"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_relocation_copies_validated_data_rebases_paths_and_retains_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    env_path = project / ".env"
    env_path.write_text("EKB_AI_MODE=manual\n", encoding="utf-8")
    target = tmp_path / "external" / "ekb-data"

    result = relocate_storage(
        settings,
        target,
        env_path=env_path,
        project_root=project,
    )

    assert settings.database_path.is_file()
    assert (target / "database" / "knowledge.db").is_file()
    assert (target / "agent-readings" / "pages" / "page_1.json").is_file()
    assert (target / "database" / "backups" / "knowledge.v14.db").is_file()
    assert result.database_summary.integrity_check == "ok"
    assert result.database_summary.documents == 1
    assert result.database_summary.pages == 1
    assert result.backup_path.is_dir()
    assert "EKB_AI_MODE=manual" in env_path.read_text(encoding="utf-8")
    configured = Settings(_env_file=env_path)
    assert configured.storage_dir == target.resolve()

    with sqlite3.connect(target / "database" / "knowledge.db") as connection:
        source_path = Path(connection.execute("SELECT source_path FROM documents").fetchone()[0])
        image_path, markdown_path = connection.execute(
            "SELECT image_path, markdown_path FROM pages"
        ).fetchone()
    assert source_path == target / "raw" / "manual.pdf"
    assert Path(image_path) == target / "pages" / "1" / "page_0001.png"
    assert Path(markdown_path) == target / "markdown" / "1" / "page_0001.md"


def test_staging_relocation_writes_only_the_staging_pointer(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    env_path = project / ".env"
    formal = tmp_path / "formal-data"
    env_path.write_text(
        f'EKB_STORAGE_DIR="{formal.as_posix()}"\n',
        encoding="utf-8",
    )
    target = tmp_path / "external" / "staging-data"

    relocate_storage(
        settings,
        target,
        env_path=env_path,
        env_key="EKB_STAGING_STORAGE_DIR",
        project_root=project,
    )

    configured = Settings(_env_file=env_path)
    assert configured.storage_dir == formal
    assert configured.staging_storage_dir == target.resolve()


def test_raw_location_can_move_independently_and_rebases_database_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    target = settings.data_dir / "raw-custom"
    env_path = project / ".env"

    result = relocate_storage_path(
        settings,
        "raw",
        target,
        env_path=env_path,
        project_root=project,
    )

    assert (target / "manual.pdf").read_bytes() == b"local source"
    assert settings.raw_dir.joinpath("manual.pdf").is_file()
    with sqlite3.connect(settings.database_path) as connection:
        recorded = Path(connection.execute("SELECT source_path FROM documents").fetchone()[0])
    assert recorded == target / "manual.pdf"
    assert result.backup_path.is_dir()
    assert Settings(_env_file=env_path).storage_raw_dir == target.resolve()


def test_database_location_can_move_independently(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    target = settings.data_dir / "database-custom" / "library.db"
    env_path = project / ".env"

    relocate_storage_path(
        settings,
        "database",
        target,
        env_path=env_path,
        project_root=project,
    )

    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert settings.database_path.is_file()
    assert Settings(_env_file=env_path).storage_database_path == target.resolve()


@pytest.mark.parametrize(("location", "field"), [("logs", "storage_logs_dir"),
                                                   ("runtime", "storage_runtime_dir")])
def test_operational_locations_can_move_independently(
    tmp_path: Path,
    location: str,
    field: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    source = settings.logs_dir if location == "logs" else settings.runtime_dir
    source.mkdir(parents=True, exist_ok=True)
    (source / "marker.txt").write_text("keep", encoding="utf-8")
    target = tmp_path / "external" / location
    env_path = project / ".env"

    relocate_storage_path(
        settings,
        location,  # type: ignore[arg-type]
        target,
        env_path=env_path,
        project_root=project,
    )

    assert (target / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert source.joinpath("marker.txt").is_file()
    assert getattr(Settings(_env_file=env_path), field) == target.resolve()


@pytest.mark.parametrize("kind", ["nonempty", "inside_source", "inside_project"])
def test_relocation_rejects_unsafe_targets(tmp_path: Path, kind: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    if kind == "nonempty":
        target = tmp_path / "external" / "data"
        target.mkdir(parents=True)
        (target / "keep.txt").write_text("keep", encoding="utf-8")
    elif kind == "inside_source":
        target = settings.data_dir / "nested"
    else:
        target = project / "custom-data"

    with pytest.raises(StorageLocationError):
        relocate_storage(
            settings,
            target,
            env_path=project / ".env",
            project_root=project,
        )

    assert settings.database_path.is_file()


def test_relocation_refuses_pending_deletion_quarantine(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    pending = settings.data_dir / ".deletion-quarantine" / "pending"
    pending.mkdir()

    with pytest.raises(StorageLocationError, match="删除隔离区"):
        relocate_storage(
            settings,
            tmp_path / "external" / "data",
            env_path=project / ".env",
            project_root=project,
        )


def test_relocation_refuses_process_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _library(project)
    monkeypatch.setenv("EKB_STORAGE_DIR", str(tmp_path / "managed-elsewhere"))

    with pytest.raises(StorageLocationError, match="系统环境变量"):
        relocate_storage(
            settings,
            tmp_path / "external" / "data",
            env_path=project / ".env",
            project_root=project,
        )
