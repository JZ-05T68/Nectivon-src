"""Versioned, non-secret configuration for optional AI providers.

API credentials are intentionally absent from every public type and from the
JSON schema.  They are owned exclusively by :mod:`src.ai.credential_store`.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from src.ai.model_registry import (
    CapabilitySupport,
    ModelPurpose,
    ProviderId,
    get_capability_profile,
    get_model_preset,
    get_provider_definition,
    is_hunyuan_model_id,
    list_model_presets,
)
from src.config import runtime_private_root

__all__ = [
    "AI_PROVIDER_CONFIG_VERSION",
    "AIProviderConfig",
    "ModelSelectionKind",
    "ProviderConfigStore",
    "ProviderSettings",
    "default_provider_config_path",
    "image_provider_settings",
]

AI_PROVIDER_CONFIG_VERSION = 1
_FORBIDDEN_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "credential", "credentials", "secret", "token"}
)


class ModelSelectionKind(StrEnum):
    """Whether a model id came from the curated registry or manual input."""

    PRESET = "preset"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Non-secret settings for one provider."""

    provider_id: ProviderId
    base_url: str
    model_id: str
    model_selection: ModelSelectionKind

    def __post_init__(self) -> None:
        normalized_url = _validate_base_url(self.base_url)
        normalized_model = self.model_id.strip()
        if not normalized_model:
            raise ValueError("Model ID 不能为空")
        preset = get_model_preset(self.provider_id, normalized_model)
        if self.model_selection is ModelSelectionKind.PRESET and preset is None:
            raise ValueError("preset 模型必须来自 Model Registry")
        if self.model_selection is ModelSelectionKind.CUSTOM and preset is not None:
            raise ValueError("已登记模型必须使用 preset 类型")
        if (
            self.provider_id is ProviderId.HUNYUAN
            and preset is None
            and not is_hunyuan_model_id(normalized_model)
        ):
            raise ValueError("Hunyuan Provider 的自定义 Model ID 必须是腾讯混元模型")
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "model_id", normalized_model)

    @classmethod
    def default_for(cls, provider_id: ProviderId | str) -> ProviderSettings:
        """Build the vendor's current new-user default without any credential."""

        definition = get_provider_definition(provider_id)
        if definition.default_model_id is None:
            raise ValueError(
                f"{definition.display_name} 暂无已确认可用 preset，请输入控制台 Model ID"
            )
        return cls(
            provider_id=definition.provider_id,
            base_url=definition.default_base_url,
            model_id=definition.default_model_id,
            model_selection=ModelSelectionKind.PRESET,
        )

    @property
    def uses_legacy_endpoint(self) -> bool:
        """Report legacy compatibility without making it a new-user default."""

        definition = get_provider_definition(self.provider_id)
        return self.base_url in definition.legacy_base_urls


@dataclass(frozen=True, slots=True)
class AIProviderConfig:
    """The complete version-1 non-secret configuration document."""

    providers: tuple[ProviderSettings, ...] = ()
    active_provider_id: ProviderId | None = None
    config_version: int = AI_PROVIDER_CONFIG_VERSION
    image_settings: ProviderSettings | None = None

    def __post_init__(self) -> None:
        if self.config_version != AI_PROVIDER_CONFIG_VERSION:
            raise ValueError(f"不支持的 AI Provider 配置版本：{self.config_version}")
        provider_ids = [settings.provider_id for settings in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("AI Provider 配置中存在重复 provider_id")
        if self.active_provider_id is not None and self.active_provider_id not in provider_ids:
            raise ValueError("active_provider_id 必须指向已配置 Provider")
        if (
            self.image_settings is not None
            and get_capability_profile(
                self.image_settings.provider_id,
                self.image_settings.model_id,
            ).effective.vision
            is not CapabilitySupport.SUPPORTED
        ):
            raise ValueError("读图模型必须支持图片输入且已接入图片接口")

    def get(self, provider_id: ProviderId | str) -> ProviderSettings | None:
        """Return settings for one provider without exposing credentials."""

        normalized = ProviderId(provider_id)
        return next((item for item in self.providers if item.provider_id is normalized), None)

    def with_provider(
        self, settings: ProviderSettings, *, make_active: bool = False
    ) -> AIProviderConfig:
        """Return a validated document with one provider added or replaced."""

        items = {
            item.provider_id: item
            for item in self.providers
            if item.provider_id is not settings.provider_id
        }
        items[settings.provider_id] = settings
        active = settings.provider_id if make_active else self.active_provider_id
        return replace(
            self,
            providers=tuple(items[key] for key in sorted(items, key=lambda item: item.value)),
            active_provider_id=active,
        )

    def without_provider(self, provider_id: ProviderId | str) -> AIProviderConfig:
        """Remove one provider and clear active selection only when it matched."""

        normalized = ProviderId(provider_id)
        return replace(
            self,
            providers=tuple(item for item in self.providers if item.provider_id is not normalized),
            active_provider_id=(
                None if self.active_provider_id is normalized else self.active_provider_id
            ),
            image_settings=(
                None
                if self.image_settings is not None and self.image_settings.provider_id is normalized
                else self.image_settings
            ),
        )

    def with_image_provider(self, settings: ProviderSettings) -> AIProviderConfig:
        """Select image reading independently of text settings and credentials."""

        return replace(self, image_settings=settings)


def image_provider_settings(config: AIProviderConfig) -> ProviderSettings | None:
    """Preserve legacy Qwen-first image routing until an explicit image choice."""

    if config.image_settings is not None:
        return config.image_settings
    if config.active_provider_id is None:
        return None
    for provider_id in dict.fromkeys((ProviderId.QWEN, config.active_provider_id)):
        configured = config.get(provider_id)
        presets = list_model_presets(provider_id, purpose=ModelPurpose.IMAGE)
        if configured is None or not presets:
            continue
        model_id = (
            configured.model_id
            if any(preset.model_id == configured.model_id for preset in presets)
            else presets[0].model_id
        )
        return replace(configured, model_id=model_id, model_selection=ModelSelectionKind.PRESET)
    return None


def default_provider_config_path() -> Path:
    """Return the instance-private Provider configuration path."""

    return runtime_private_root() / "config" / "ai-providers-v1.json"


class ProviderConfigStore:
    """Atomic JSON persistence for versioned non-secret provider settings."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_provider_config_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AIProviderConfig:
        """Load the document, returning an empty v1 document when absent."""

        if not self._path.exists():
            return AIProviderConfig()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("AI Provider 配置文件无法读取或格式无效") from exc
        return _decode_document(raw)

    def save(self, config: AIProviderConfig) -> None:
        """Atomically replace the JSON document after a secret-field check."""

        payload = _encode_document(config)
        _assert_no_secret_fields(payload)
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Base URL 必须是无凭据、无查询参数的 HTTPS 地址")
    return normalized


def _encode_document(config: AIProviderConfig) -> dict[str, object]:
    return {
        "config_version": config.config_version,
        "active_provider_id": (
            config.active_provider_id.value if config.active_provider_id else None
        ),
        "image_settings": (
            {
                "provider_id": config.image_settings.provider_id.value,
                "base_url": config.image_settings.base_url,
                "model_id": config.image_settings.model_id,
                "model_selection": config.image_settings.model_selection.value,
            }
            if config.image_settings is not None
            else None
        ),
        "providers": {
            settings.provider_id.value: {
                "base_url": settings.base_url,
                "model_id": settings.model_id,
                "model_selection": settings.model_selection.value,
            }
            for settings in config.providers
        },
    }


def _decode_document(raw: object) -> AIProviderConfig:
    if not isinstance(raw, dict):
        raise ValueError("AI Provider 配置根节点必须是对象")
    _assert_no_secret_fields(raw)
    version = raw.get("config_version")
    providers_raw = raw.get("providers")
    active_raw = raw.get("active_provider_id")
    if version != AI_PROVIDER_CONFIG_VERSION or not isinstance(providers_raw, dict):
        raise ValueError("AI Provider 配置版本或 providers 字段无效")
    providers: list[ProviderSettings] = []
    for provider_raw, settings_raw in providers_raw.items():
        if not isinstance(provider_raw, str) or not isinstance(settings_raw, dict):
            raise ValueError("AI Provider 配置条目无效")
        try:
            provider_id = ProviderId(provider_raw)
            model_id = str(settings_raw["model_id"])
            model_selection = ModelSelectionKind(settings_raw["model_selection"])
            if provider_id is ProviderId.DEEPSEEK and model_id in {
                "deepseek-flash",
                "deepseek-v4-pro",
            }:
                # v0.8.5: both ids are now official registry presets.  Historical
                # entries must map onto the preset (never be dropped), and a
                # stored CUSTOM selection for a registered id is normalized to
                # PRESET because ProviderSettings forbids CUSTOM for presets.
                model_selection = ModelSelectionKind.PRESET
            if (
                provider_id is ProviderId.QWEN
                and model_selection is ModelSelectionKind.PRESET
                and model_id == "qwen3.7-plus"
            ):
                model_id = "qwen3.8-max"
            settings = ProviderSettings(
                provider_id=provider_id,
                base_url=str(settings_raw["base_url"]),
                model_id=model_id,
                model_selection=model_selection,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"AI Provider 配置条目无效：{provider_raw}") from exc
        providers.append(settings)
    try:
        active = ProviderId(active_raw) if active_raw is not None else None
        return AIProviderConfig(
            providers=tuple(providers),
            active_provider_id=active,
            config_version=version,
            image_settings=_decode_image_settings(raw.get("image_settings")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("AI Provider active_provider_id 无效") from exc


def _decode_image_settings(raw: object) -> ProviderSettings | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("读图模型配置必须为对象")
    return ProviderSettings(
        provider_id=ProviderId(raw["provider_id"]),
        base_url=raw["base_url"],
        model_id=raw["model_id"],
        model_selection=ModelSelectionKind(raw["model_selection"]),
    )


def _assert_no_secret_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _FORBIDDEN_SECRET_KEYS:
                raise ValueError("非秘密配置中禁止出现凭据字段")
            _assert_no_secret_fields(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _assert_no_secret_fields(child)
