"""Regression coverage for production/staging AI configuration isolation."""

from __future__ import annotations

import json

from src.ai.credential_store import (
    CredentialStoreKind,
    DpapiCredentialStore,
    SecretCredential,
    build_default_credential_store,
    default_dpapi_credential_path,
)
from src.ai.model_registry import MODEL_REGISTRY, ProviderId
from src.ai.provider_config import default_provider_config_path


class _XorProtector:
    _MASK = 0x5D

    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ self._MASK for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ self._MASK for value in ciphertext)


def test_staging_config_and_credentials_do_not_share_production_paths(
    tmp_path, monkeypatch
) -> None:
    local_app_data = tmp_path / "local-app-data"
    staging_root = tmp_path / "staging-8511"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("EKB_STAGING_INSTANCE", raising=False)
    monkeypatch.delenv("EKB_STAGING_ROOT", raising=False)
    production_config = default_provider_config_path()
    production_credential = default_dpapi_credential_path()

    monkeypatch.setenv("EKB_STAGING_INSTANCE", "1")
    monkeypatch.setenv("EKB_STAGING_ROOT", str(staging_root))
    monkeypatch.setenv("EKB_STAGING_PORT", "8511")

    assert default_provider_config_path() == (
        staging_root / "config" / "ai-providers-v1.json"
    ).resolve(strict=False)
    assert default_dpapi_credential_path() == (
        staging_root / "credentials" / "ai-v1.bin"
    ).resolve(strict=False)
    assert default_provider_config_path() != production_config
    assert default_dpapi_credential_path() != production_credential


def test_staging_credential_chain_cannot_write_windows_production_store(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EKB_STAGING_INSTANCE", "1")
    monkeypatch.setenv("EKB_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setenv("EKB_STAGING_PORT", "8511")

    chain = build_default_credential_store()

    assert [store.kind for store in chain.stores] == [
        CredentialStoreKind.CURRENT_USER_DPAPI,
        CredentialStoreKind.SESSION_MEMORY,
    ]


def test_existing_credentials_survive_store_restart(tmp_path) -> None:
    path = tmp_path / "credentials" / "ai-v1.bin"
    first = DpapiCredentialStore(path, protector=_XorProtector())
    first.set(ProviderId.GLM, SecretCredential("synthetic-regression-key"))

    reopened = DpapiCredentialStore(path, protector=_XorProtector())

    assert reopened.get(ProviderId.GLM) is not None
    assert b"synthetic-regression-key" not in path.read_bytes()


def test_parser_import_does_not_modify_existing_provider_config(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EKB_STAGING_INSTANCE", "1")
    monkeypatch.setenv("EKB_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setenv("EKB_STAGING_PORT", "8511")
    path = default_provider_config_path()
    path.parent.mkdir(parents=True)
    payload = {
        "config_version": 1,
        "active_provider_id": "glm",
        "providers": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    __import__("src.office_pdf_converter")

    assert path.read_bytes() == before


def test_v086_keeps_exactly_five_providers_including_glm() -> None:
    assert {provider.value for provider in MODEL_REGISTRY} == {
        "deepseek",
        "qwen",
        "kimi",
        "hunyuan",
        "glm",
    }
