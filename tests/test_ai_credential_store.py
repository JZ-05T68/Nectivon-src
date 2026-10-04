"""Credential tests use synthetic values and never touch real OS credentials."""

from __future__ import annotations

import pytest

from src.ai.credential_store import (
    CredentialStoreChain,
    CredentialStoreError,
    CredentialStoreKind,
    CredentialStoreUnavailable,
    DpapiCredentialStore,
    MemoryCredentialStore,
    SecretCredential,
)
from src.ai.model_registry import ProviderId

_SENTINEL = "unit-test-credential-sentinel-9f7b"


class _XorProtector:
    """Deterministic test double; production uses current-user Windows DPAPI."""

    _MASK = 0xA7

    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ self._MASK for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ self._MASK for value in ciphertext)


class _UnavailableStore:
    @property
    def kind(self) -> CredentialStoreKind:
        return CredentialStoreKind.WINDOWS_CREDENTIAL_MANAGER

    def get(self, provider_id):
        raise CredentialStoreUnavailable("模拟不可用")

    def set(self, provider_id, credential):
        raise CredentialStoreUnavailable("模拟不可用")

    def delete(self, provider_id):
        raise CredentialStoreUnavailable("模拟不可用")


def test_secret_wrapper_never_exposes_value_in_string_representations() -> None:
    credential = SecretCredential(_SENTINEL)

    assert credential.reveal() == _SENTINEL
    assert _SENTINEL not in repr(credential)
    assert _SENTINEL not in str(credential)


def test_dpapi_store_round_trip_ciphertext_and_verified_delete(tmp_path) -> None:
    path = tmp_path / "credentials" / "ai-v1.bin"
    store = DpapiCredentialStore(path, protector=_XorProtector())

    store.set(ProviderId.QWEN, SecretCredential(_SENTINEL))

    assert store.get(ProviderId.QWEN).reveal() == _SENTINEL  # type: ignore[union-attr]
    assert _SENTINEL.encode() not in path.read_bytes()
    assert _SENTINEL not in repr(store)

    store.delete(ProviderId.QWEN)

    assert store.get(ProviderId.QWEN) is None
    assert not path.exists()


def test_chain_falls_back_to_session_memory_without_leaking_secret() -> None:
    memory = MemoryCredentialStore()
    chain = CredentialStoreChain((_UnavailableStore(), memory))

    selected = chain.set(ProviderId.DEEPSEEK, SecretCredential(_SENTINEL))

    assert selected is CredentialStoreKind.SESSION_MEMORY
    assert chain.get(ProviderId.DEEPSEEK).reveal() == _SENTINEL  # type: ignore[union-attr]
    assert _SENTINEL not in repr(memory)
    assert _SENTINEL not in repr(chain)


def test_memory_delete_verifies_absence() -> None:
    memory = MemoryCredentialStore()
    memory.set(ProviderId.KIMI, SecretCredential(_SENTINEL))

    memory.delete(ProviderId.KIMI)

    assert memory.get(ProviderId.KIMI) is None


def test_corrupt_encrypted_file_error_does_not_include_contents(tmp_path) -> None:
    path = tmp_path / "ai-v1.bin"
    path.write_bytes(_SENTINEL.encode())
    store = DpapiCredentialStore(path, protector=_XorProtector())

    with pytest.raises(CredentialStoreError) as captured:
        store.get(ProviderId.HUNYUAN)

    assert _SENTINEL not in str(captured.value)
