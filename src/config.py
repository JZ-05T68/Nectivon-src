"""Application configuration for the local engineering knowledge base."""

from __future__ import annotations

import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from src import __version__
from src.runtime_profile import RuntimeProfile, require_runtime_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_ROOT: Final[Path] = PROJECT_ROOT.parent
INSTALL_VERSION_FILE: Final[Path] = INSTALL_ROOT / "VERSION"
OFFICIAL_HOST: Final[str] = "127.0.0.1"
OFFICIAL_PORT: Final[int] = 8501
STAGING_PORT: Final[int] = 8511
DEFAULT_STAGING_ROOT: Final[Path] = PROJECT_ROOT / "staging-data"
#: Environment flag selecting the staging instance inside an app process.
STAGING_ENV_VAR: Final[str] = "EKB_STAGING_INSTANCE"
#: Environment pair carrying an explicit staging root/port into the spawned
#: app process when the service manager was started with --staging-root and
#: --staging-port (multi-instance test servers, e.g. 8511 / 8512).
STAGING_ROOT_ENV_VAR: Final[str] = "EKB_STAGING_ROOT"
STAGING_PORT_ENV_VAR: Final[str] = "EKB_STAGING_PORT"


def is_installed_distribution(project_root: Path = PROJECT_ROOT) -> bool:
    """Return whether ``project_root`` belongs to the packaged app layout."""

    root = project_root.resolve(strict=False)
    staged_layout = (
        root.name.casefold() == "app"
        and (root.parent / "VERSION").is_file()
    )
    # PyInstaller imports frozen modules from inside the bundle rather than
    # from ``Contents/Resources/app``.  Treat that process as installed even
    # though ``__file__`` does not expose the resource layout.
    frozen_macos_app = bool(getattr(sys, "frozen", False)) and platform.system() == "Darwin"
    return staged_layout or frozen_macos_app


def user_data_root(
    local_app_data: Path | None = None,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the canonical per-user writable root for an installed build.

    ``local_app_data`` remains the injectable Windows base used by the MSI
    tests.  macOS and Linux derive their locations from the current user's
    home directory or XDG environment without hard-coded usernames.
    """

    system = platform_name or platform.system()
    if system == "Windows":
        if local_app_data is None:
            value = os.environ.get("LOCALAPPDATA")
            if not value:
                raise StorageConfigurationError("无法定位 LOCALAPPDATA。")
            local_app_data = Path(value)
        return _safe_user_base(local_app_data, "LOCALAPPDATA") / "Nectivon"
    if system == "Darwin":
        return _safe_user_base(home or Path.home(), "用户主目录") / (
            "Library/Application Support/Nectivon"
        )
    if local_app_data is None:
        value = os.environ.get("XDG_DATA_HOME")
        local_app_data = Path(value) if value else (home or Path.home()) / ".local/share"
    return _safe_user_base(local_app_data, "用户数据目录") / "Nectivon"


def runtime_private_root() -> Path:
    """Return the private config/credential root for this app instance.

    Formal 8501 keeps the established per-user location.  A service-manager
    staging child must keep every mutable setting below its explicit staging
    root; otherwise a v0.8.6 five-provider config can overwrite the v0.8.5
    four-provider production document and make the older runtime unreadable.
    """

    if os.environ.get(STAGING_ENV_VAR) != "1":
        return user_data_root()
    root_value = os.environ.get(STAGING_ROOT_ENV_VAR)
    root = Path(root_value) if root_value else DEFAULT_STAGING_ROOT
    return _safe_user_base(root, STAGING_ROOT_ENV_VAR)


def user_cache_root(
    *, platform_name: str | None = None, home: Path | None = None
) -> Path:
    """Return the platform-standard writable cache directory."""

    system = platform_name or platform.system()
    if system == "Windows":
        value = os.environ.get("LOCALAPPDATA")
        if not value:
            raise StorageConfigurationError("无法定位 LOCALAPPDATA。")
        return _safe_user_base(Path(value), "LOCALAPPDATA") / "Nectivon" / "cache"
    if system == "Darwin":
        return _safe_user_base(home or Path.home(), "用户主目录") / "Library/Caches/Nectivon"
    value = os.environ.get("XDG_CACHE_HOME")
    base = Path(value) if value else (home or Path.home()) / ".cache"
    return _safe_user_base(base, "缓存目录") / "Nectivon"


def user_log_root(
    *, platform_name: str | None = None, home: Path | None = None
) -> Path:
    """Return the platform-standard writable log directory."""

    system = platform_name or platform.system()
    if system == "Darwin":
        return _safe_user_base(home or Path.home(), "用户主目录") / "Library/Logs/Nectivon"
    return user_data_root(platform_name=system, home=home) / "logs"


def _safe_user_base(value: Path, label: str) -> Path:
    base = value.expanduser().resolve(strict=False)
    if base == Path(base.anchor):
        raise StorageConfigurationError(f"{label}不能直接使用磁盘根目录。")
    return base


def settings_env_path(
    project_root: Path = PROJECT_ROOT,
    local_app_data: Path | None = None,
) -> Path:
    """Keep installed configuration outside the immutable program directory."""

    if is_installed_distribution(project_root):
        return user_data_root(local_app_data) / "config" / ".env"
    return project_root / ".env"


def installed_settings_layout(
    root: Path,
    *,
    cache_root: Path | None = None,
    logs_root: Path | None = None,
) -> dict[str, Path]:
    """Return all writable installed-mode locations below one user root."""

    writable_root = root.resolve(strict=False)
    writable_cache = (cache_root or (writable_root / "cache")).resolve(strict=False)
    writable_logs = (logs_root or (writable_root / "logs")).resolve(strict=False)
    layout = storage_layout(writable_root / "data")
    layout.update(
        {
            "config_dir": writable_root / "config",
            "backups_dir": writable_root / "backups",
            "cache_dir": writable_cache,
            "logs_dir": writable_logs,
            "log_path": writable_logs / "engineering-kb.log",
            "runtime_dir": writable_root / "runtime-state",
            "pid_path": writable_root / "runtime-state" / "engineering-kb.pid.json",
        }
    )
    return layout


class OfficialEndpointError(ValueError):
    """Raised when a formal runtime attempts to use a non-official endpoint."""


def require_official_endpoint(host: str, port: int) -> None:
    """Require the one supported endpoint for formal local runtime paths.

    ``Settings`` remains directly constructible with a temporary port so tests
    can isolate their listeners.  Formal entry points use ``get_settings()``,
    which calls this guard before returning configuration.
    """

    if host != OFFICIAL_HOST:
        raise OfficialEndpointError(
            f"正式服务端点必须为 {OFFICIAL_HOST}:{OFFICIAL_PORT}；收到地址 {host}。"
        )
    if port != OFFICIAL_PORT:
        raise OfficialEndpointError(
            f"正式服务端点必须为 {OFFICIAL_HOST}:{OFFICIAL_PORT}；收到端口 {port}。"
        )


class StorageConfigurationError(ValueError):
    """Raised when the configured local storage directory is unsafe."""


def storage_layout(data_dir: Path) -> dict[str, Path]:
    """Return the canonical writable layout below one local data directory."""

    data = data_dir.expanduser()
    if not data.is_absolute():
        raise StorageConfigurationError("存储位置必须使用绝对路径。")
    data = data.resolve(strict=False)
    if data == Path(data.anchor):
        raise StorageConfigurationError("存储位置不能直接使用磁盘根目录。")
    return {
        "data_dir": data,
        "raw_dir": data / "raw",
        "pages_dir": data / "pages",
        "markdown_dir": data / "markdown",
        "agent_readings_dir": data / "agent-readings",
        "database_dir": data / "database",
        "database_path": data / "database" / "knowledge.db",
    }


class Settings(BaseSettings):
    """Settings loaded from environment variables and an optional local ``.env``.

    Every default points to the local project directory.  In particular, the
    application listens only on the loopback interface and does not need an API
    key in manual AI mode.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="EKB_",
        extra="ignore",
    )

    app_title: str = f"Nectivon v{__version__}"
    app_version: str = __version__
    host: Literal["127.0.0.1"] = OFFICIAL_HOST
    port: int = Field(default=OFFICIAL_PORT, ge=1, le=65535)

    data_dir: Path = PROJECT_ROOT / "data"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    pages_dir: Path = PROJECT_ROOT / "data" / "pages"
    markdown_dir: Path = PROJECT_ROOT / "data" / "markdown"
    agent_readings_dir: Path = PROJECT_ROOT / "data" / "agent-readings"
    database_dir: Path = PROJECT_ROOT / "data" / "database"
    database_path: Path = PROJECT_ROOT / "data" / "database" / "knowledge.db"
    config_dir: Path = PROJECT_ROOT
    backups_dir: Path = PROJECT_ROOT / "backups"
    cache_dir: Path = PROJECT_ROOT / "runtime" / "cache"
    logs_dir: Path = PROJECT_ROOT / "logs"
    log_path: Path = PROJECT_ROOT / "logs" / "engineering-kb.log"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    pid_path: Path = PROJECT_ROOT / "runtime" / "engineering-kb.pid.json"
    # Optional user-selected root for all knowledge assets.  The individual
    # path fields remain injectable for tests and isolated tooling; the formal
    # loader applies this root as one coherent layout.
    storage_dir: Path | None = None
    storage_raw_dir: Path | None = None
    storage_pages_dir: Path | None = None
    storage_markdown_dir: Path | None = None
    storage_database_path: Path | None = None
    storage_logs_dir: Path | None = None
    storage_runtime_dir: Path | None = None
    # The 8511 instance has its own opt-in pointer.  Formal settings never use
    # it, and explicit staging roots supplied by tests/tools always win.
    staging_storage_dir: Path | None = None
    staging_raw_dir: Path | None = None
    staging_pages_dir: Path | None = None
    staging_markdown_dir: Path | None = None
    staging_database_path: Path | None = None
    staging_logs_dir: Path | None = None
    staging_runtime_dir: Path | None = None

    minimum_text_length: int = Field(default=20, ge=0)
    pdf_render_dpi: int = Field(default=150, ge=72, le=600)

    # Optional AI layer (v0.5.0). Manual by default: without an API key the
    # application starts and every existing offline feature keeps working.
    ai_mode: Literal["manual", "api"] = "manual"
    ai_provider: Literal["qwen"] = "qwen"
    ai_api_key: SecretStr = SecretStr("")
    ai_llm_model: str = "qwen3.8-max"
    ai_llm_model_hard: str = "qwen3.8-max"
    ai_embedding_model: str = "qwen3.7-text-embedding"
    ai_rerank_model: str = "qwen3-rerank"
    ai_vision_model: str = "qwen3-vl-plus"
    ai_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    # Bounded retry and token budgets (v0.5.3). Budget unit is tokens, never
    # currency; 0 means unlimited. Retry ceiling is capped at 2 extra attempts.
    ai_max_extra_attempts: int = Field(default=2, ge=0, le=2)
    ai_daily_token_budget: int = Field(default=0, ge=0)
    ai_monthly_token_budget: int = Field(default=0, ge=0)

    @property
    def runtime_profile(self) -> RuntimeProfile:
        """Local configuration remains distinct from Hosted server settings."""

        return RuntimeProfile.LOCAL

    def ensure_directories(self) -> None:
        """Create all writable local directories without removing existing data."""

        for directory in (
            self.data_dir,
            self.raw_dir,
            self.pages_dir,
            self.markdown_dir,
            self.agent_readings_dir,
            self.database_dir,
            self.config_dir,
            self.backups_dir,
            self.cache_dir,
            self.logs_dir,
            self.runtime_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def _get_local_settings() -> Settings:
    """Cache the existing Local configuration after the entrypoint guard."""

    try:
        settings = (
            Settings(_env_file=settings_env_path())
            if is_installed_distribution()
            else Settings()
        )
    except ValidationError as exc:
        raise OfficialEndpointError(
            f"正式服务端点必须为 {OFFICIAL_HOST}:{OFFICIAL_PORT}；配置值无效。"
        ) from exc
    if is_installed_distribution():
        writable_root = user_data_root()
        system = platform.system()
        cache_root = (
            writable_root / "cache"
            if system == "Windows"
            else user_cache_root(platform_name=system)
        )
        logs_root = (
            writable_root / "logs"
            if system == "Windows"
            else user_log_root(platform_name=system)
        )
        settings = settings.model_copy(
            update=installed_settings_layout(
                writable_root,
                cache_root=cache_root,
                logs_root=logs_root,
            )
        )
    if settings.storage_dir is not None:
        settings = settings.model_copy(update=storage_layout(settings.storage_dir))
    settings = _apply_individual_storage_overrides(settings, staging=False)
    _validate_effective_storage_layout(settings)
    require_official_endpoint(settings.host, settings.port)
    return settings


def get_settings() -> Settings:
    """Guard the Local entrypoint even on cache hits, preserving Local defaults."""

    require_runtime_profile(RuntimeProfile.LOCAL)
    return _get_local_settings()


# Preserve the existing public cache reset hook used by developer/test workflows.
get_settings.cache_clear = _get_local_settings.cache_clear


def staging_settings(
    root: Path | None = None, *, port: int | None = None
) -> Settings:
    """Build fully isolated AI-staging settings under one separate root.

    Every writable path (data / raw / pages / markdown / database / backups /
    logs / runtime) is derived under ``root`` — never the production
    locations — and the staging endpoint is loopback ``STAGING_PORT`` (8511).
    Explicit constructor arguments outrank any ``EKB_*`` value in ``.env``,
    so a stray path override in the environment cannot break isolation; the
    AI credentials and model settings still resolve from ``.env`` as usual.

    ``port`` (together with an explicit ``root``) selects a second parallel
    test instance such as 8512.  When ``root`` is omitted, an explicit
    ``EKB_STAGING_ROOT`` + ``EKB_STAGING_PORT`` environment pair is honored
    so a service-manager-spawned child process resolves exactly the same
    isolated instance its manager announced.  Explicit staging ports must
    never be the formal 8501.

    The formal-runtime guard (``require_official_endpoint``) deliberately
    does not apply: staging is a non-formal, explicitly separate instance,
    and direct ``Settings`` construction is the existing sanctioned
    extension point. Staging data can be deleted or rebuilt freely without
    affecting production.
    """

    require_runtime_profile(RuntimeProfile.LOCAL)
    if root is None:
        env_root = os.environ.get(STAGING_ROOT_ENV_VAR)
        env_port = os.environ.get(STAGING_PORT_ENV_VAR)
        if env_root:
            if not env_port:
                raise StorageConfigurationError(
                    "EKB_STAGING_ROOT 与 EKB_STAGING_PORT 必须同时提供。"
                )
            root = Path(env_root)
            try:
                port = int(env_port)
            except ValueError as exc:
                raise StorageConfigurationError(
                    "EKB_STAGING_PORT 必须是整数端口。"
                ) from exc
    explicit_root = root is not None
    staging_root = Path(root) if explicit_root else DEFAULT_STAGING_ROOT
    data_dir = staging_root / "data"
    database_dir = data_dir / "database"
    logs_dir = staging_root / "logs"
    runtime_dir = staging_root / "runtime"
    if port is not None:
        _validate_staging_port(port)
    settings = Settings(
        port=STAGING_PORT if port is None else port,
        data_dir=data_dir,
        raw_dir=data_dir / "raw",
        pages_dir=data_dir / "pages",
        markdown_dir=data_dir / "markdown",
        agent_readings_dir=data_dir / "agent-readings",
        database_dir=database_dir,
        database_path=database_dir / "knowledge.db",
        backups_dir=staging_root / "backups",
        logs_dir=logs_dir,
        log_path=logs_dir / "engineering-kb-staging.log",
        runtime_dir=runtime_dir,
        pid_path=runtime_dir / "engineering-kb-staging.pid.json",
    )
    if explicit_root:
        # Explicit test-instance roots (8512, long-chain test servers) skip
        # the .env staging overrides but still refuse to overlap the formal
        # data tree, keeping parallel instances fail-safe by construction.
        formal_data = (PROJECT_ROOT / "data").resolve(strict=False)
        staging_data = settings.data_dir.resolve(strict=False)
        if (
            staging_data == formal_data
            or staging_data.is_relative_to(formal_data)
            or formal_data.is_relative_to(staging_data)
        ):
            raise StorageConfigurationError("测试服存储位置不能与正式数据目录互相包含。")
        _validate_effective_storage_layout(settings)
        return settings
    if settings.staging_storage_dir is not None:
        settings = settings.model_copy(
            update=storage_layout(settings.staging_storage_dir)
        )
    settings = _apply_individual_storage_overrides(settings, staging=True)
    formal_data = (
        storage_layout(settings.storage_dir)["data_dir"]
        if settings.storage_dir is not None
        else (PROJECT_ROOT / "data").resolve(strict=False)
    )
    staging_data = settings.data_dir.resolve(strict=False)
    if (
        staging_data == formal_data
        or staging_data.is_relative_to(formal_data)
        or formal_data.is_relative_to(staging_data)
    ):
        raise StorageConfigurationError("测试服存储位置不能与正式数据目录互相包含。")
    _validate_effective_storage_layout(settings)
    return settings


def _apply_individual_storage_overrides(
    settings: Settings, *, staging: bool
) -> Settings:
    """Apply optional per-location pointers after the shared data-root pointer."""

    prefix = "staging_" if staging else "storage_"
    updates: dict[str, Path] = {}
    for field in ("raw_dir", "pages_dir", "markdown_dir"):
        value = getattr(settings, f"{prefix}{field}")
        if value is not None:
            updates[field] = _absolute_location(value, field)
    database = getattr(settings, f"{prefix}database_path")
    if database is not None:
        database_path = _absolute_location(database, "database_path")
        updates["database_path"] = database_path
        updates["database_dir"] = database_path.parent
    logs = getattr(settings, f"{prefix}logs_dir")
    if logs is not None:
        logs_dir = _absolute_location(logs, "logs_dir")
        updates["logs_dir"] = logs_dir
        updates["log_path"] = logs_dir / (
            "engineering-kb-staging.log" if staging else "engineering-kb.log"
        )
    runtime = getattr(settings, f"{prefix}runtime_dir")
    if runtime is not None:
        runtime_dir = _absolute_location(runtime, "runtime_dir")
        updates["runtime_dir"] = runtime_dir
        updates["pid_path"] = runtime_dir / (
            "engineering-kb-staging.pid.json"
            if staging
            else "engineering-kb.pid.json"
        )
    return settings.model_copy(update=updates) if updates else settings


def _absolute_location(value: Path, label: str) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute():
        raise StorageConfigurationError(f"{label} 必须使用绝对路径。")
    resolved = expanded.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise StorageConfigurationError(f"{label} 不能直接使用磁盘根目录。")
    return resolved


def _validate_staging_port(port: int) -> None:
    """Keep explicit test instances on their own ports, never the formal one."""

    if not 1 <= port <= 65535:
        raise StorageConfigurationError(f"测试实例端口必须在 1-65535 之间：{port}。")
    if port == OFFICIAL_PORT:
        raise StorageConfigurationError("测试实例不能占用正式端口 8501。")


def _validate_effective_storage_layout(settings: Settings) -> None:
    """Keep independently configured paths compatible with safety services."""

    data = settings.data_dir.resolve(strict=False)
    areas = {
        "原 PDF": settings.raw_dir.resolve(strict=False),
        "页面图片": settings.pages_dir.resolve(strict=False),
        "Markdown": settings.markdown_dir.resolve(strict=False),
        "数据库": settings.database_path.resolve(strict=False).parent,
    }
    for label, area in areas.items():
        if area == data or not area.is_relative_to(data):
            raise StorageConfigurationError(f"{label}位置必须位于数据目录内。")
    items = tuple(areas.items())
    for index, (label, area) in enumerate(items):
        for other_label, other in items[index + 1 :]:
            if (
                area == other
                or area.is_relative_to(other)
                or other.is_relative_to(area)
            ):
                raise StorageConfigurationError(
                    f"{label}位置不能与{other_label}位置互相包含。"
                )
    for label, area in (
        ("日志", settings.logs_dir.resolve(strict=False)),
        ("运行状态", settings.runtime_dir.resolve(strict=False)),
    ):
        if area == data or area.is_relative_to(data) or data.is_relative_to(area):
            raise StorageConfigurationError(f"{label}位置不能与数据目录互相包含。")
    logs = settings.logs_dir.resolve(strict=False)
    runtime = settings.runtime_dir.resolve(strict=False)
    if logs == runtime or logs.is_relative_to(runtime) or runtime.is_relative_to(logs):
        raise StorageConfigurationError("日志位置不能与运行状态位置互相包含。")


def runtime_settings() -> Settings:
    """Resolve the settings for one application process.

    A process launched with ``STAGING_ENV_VAR=1`` (the staging service
    manager path) runs entirely on :func:`staging_settings`; every other
    process gets the guarded formal settings exactly as before. Production
    behavior is unchanged when the flag is absent.
    """

    require_runtime_profile(RuntimeProfile.LOCAL)
    if os.environ.get(STAGING_ENV_VAR) == "1":
        return staging_settings()
    return get_settings()
