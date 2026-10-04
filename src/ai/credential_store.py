"""Credential storage for optional AI providers.

Storage order on Windows is Credential Manager, current-user DPAPI, then an
in-memory session store.  No implementation writes a plaintext credential to
JSON or includes credential material in ``repr``, exception messages, or logs.
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import uuid
from ctypes import wintypes
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from src.ai.model_registry import ProviderId
from src.config import STAGING_ENV_VAR, runtime_private_root

__all__ = [
    "CredentialStore",
    "CredentialStoreChain",
    "CredentialStoreError",
    "CredentialStoreKind",
    "CredentialStoreUnavailable",
    "CurrentUserDpapiProtector",
    "DpapiCredentialStore",
    "MemoryCredentialStore",
    "SecretCredential",
    "WindowsCredentialManagerStore",
    "build_default_credential_store",
    "default_dpapi_credential_path",
]

_TARGET_PREFIX = "Nectivon/AI/"
_DPAPI_FILE_MAGIC = b"NECTIVON-DPAPI-V1\n"
_ERROR_NOT_FOUND = 1168


class CredentialStoreError(RuntimeError):
    """A safe credential-store failure; messages never contain credentials."""


class CredentialStoreUnavailable(CredentialStoreError):
    """The requested operating-system credential facility is unavailable."""


class CredentialStoreKind(StrEnum):
    WINDOWS_CREDENTIAL_MANAGER = "windows_credential_manager"
    CURRENT_USER_DPAPI = "current_user_dpapi"
    SESSION_MEMORY = "session_memory"


class SecretCredential:
    """An explicit secret wrapper with redacted string representations."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("凭据不能为空")
        self._value = normalized

    def reveal(self) -> str:
        """Return the secret only at the final transport boundary."""

        return self._value

    def __repr__(self) -> str:
        return "SecretCredential(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


class CredentialStore(Protocol):
    """Minimal CRUD contract shared by all credential backends."""

    @property
    def kind(self) -> CredentialStoreKind: ...

    def get(self, provider_id: ProviderId | str) -> SecretCredential | None: ...

    def set(
        self, provider_id: ProviderId | str, credential: SecretCredential
    ) -> None: ...

    def delete(self, provider_id: ProviderId | str) -> None: ...


class MemoryCredentialStore:
    """Last-resort store whose contents disappear with the current process."""

    def __init__(self) -> None:
        self._values: dict[ProviderId, str] = {}
        self._lock = threading.RLock()

    @property
    def kind(self) -> CredentialStoreKind:
        return CredentialStoreKind.SESSION_MEMORY

    def get(self, provider_id: ProviderId | str) -> SecretCredential | None:
        normalized = ProviderId(provider_id)
        with self._lock:
            value = self._values.get(normalized)
        return SecretCredential(value) if value is not None else None

    def set(
        self, provider_id: ProviderId | str, credential: SecretCredential
    ) -> None:
        normalized = ProviderId(provider_id)
        with self._lock:
            self._values[normalized] = credential.reveal()

    def delete(self, provider_id: ProviderId | str) -> None:
        normalized = ProviderId(provider_id)
        with self._lock:
            self._values.pop(normalized, None)
            if normalized in self._values:
                raise CredentialStoreError("会话凭据删除验证失败")

    def __repr__(self) -> str:
        return "MemoryCredentialStore(<redacted>)"


class _CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialManagerStore:
    """Windows Credential Manager backend using the native Win32 API."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreUnavailable("Windows Credential Manager 不可用")
        try:
            self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except OSError as exc:
            raise CredentialStoreUnavailable("Windows Credential Manager 不可用") from exc
        self._configure_api()

    @property
    def kind(self) -> CredentialStoreKind:
        return CredentialStoreKind.WINDOWS_CREDENTIAL_MANAGER

    def get(self, provider_id: ProviderId | str) -> SecretCredential | None:
        target = _target_name(provider_id)
        credential_pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._cred_read(
            target,
            self._CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            error_code = ctypes.get_last_error()
            if error_code == _ERROR_NOT_FOUND:
                return None
            raise CredentialStoreUnavailable(
                f"Windows Credential Manager 读取失败（错误码 {error_code}）"
            )
        try:
            record = credential_pointer.contents
            blob = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)
            try:
                return SecretCredential(blob.decode("utf-16-le"))
            except (UnicodeError, ValueError) as exc:
                raise CredentialStoreError("Windows 凭据内容无效") from exc
        finally:
            self._cred_free(credential_pointer)

    def set(
        self, provider_id: ProviderId | str, credential: SecretCredential
    ) -> None:
        target = _target_name(provider_id)
        blob = credential.reveal().encode("utf-16-le")
        if len(blob) > 2_560:
            raise CredentialStoreError("凭据长度超过 Windows Credential Manager 限制")
        blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        record = _CREDENTIALW()
        record.Type = self._CRED_TYPE_GENERIC
        record.TargetName = target
        record.CredentialBlobSize = len(blob)
        record.CredentialBlob = ctypes.cast(
            blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
        )
        record.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        record.UserName = "Nectivon"
        if not self._cred_write(ctypes.byref(record), 0):
            error_code = ctypes.get_last_error()
            raise CredentialStoreUnavailable(
                f"Windows Credential Manager 写入失败（错误码 {error_code}）"
            )

    def delete(self, provider_id: ProviderId | str) -> None:
        target = _target_name(provider_id)
        if not self._cred_delete(target, self._CRED_TYPE_GENERIC, 0):
            error_code = ctypes.get_last_error()
            if error_code != _ERROR_NOT_FOUND:
                raise CredentialStoreUnavailable(
                    f"Windows Credential Manager 删除失败（错误码 {error_code}）"
                )
        if self.get(provider_id) is not None:
            raise CredentialStoreError("Windows 凭据删除验证失败")

    def __repr__(self) -> str:
        return "WindowsCredentialManagerStore(<redacted>)"

    def _configure_api(self) -> None:
        pointer = ctypes.POINTER(_CREDENTIALW)
        self._cred_read = self._advapi32.CredReadW
        self._cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(pointer),
        ]
        self._cred_read.restype = wintypes.BOOL
        self._cred_write = self._advapi32.CredWriteW
        self._cred_write.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._cred_write.restype = wintypes.BOOL
        self._cred_delete = self._advapi32.CredDeleteW
        self._cred_delete.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._cred_delete.restype = wintypes.BOOL
        self._cred_free = self._advapi32.CredFree
        self._cred_free.argtypes = [ctypes.c_void_p]
        self._cred_free.restype = None


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DataProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class CurrentUserDpapiProtector:
    """Win32 DPAPI bound to the current Windows user profile."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    _ENTROPY = b"Nectivon AI credentials v1"

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreUnavailable("Windows DPAPI 不可用")
        try:
            self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        except OSError as exc:
            raise CredentialStoreUnavailable("Windows DPAPI 不可用") from exc
        self._configure_api()

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, protect=False)

    def _transform(self, value: bytes, *, protect: bool) -> bytes:
        input_blob, input_buffer = _make_blob(value)
        entropy_blob, entropy_buffer = _make_blob(self._ENTROPY)
        output_blob = _DATA_BLOB()
        if protect:
            succeeded = self._crypt_protect(
                ctypes.byref(input_blob),
                None,
                ctypes.byref(entropy_blob),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        else:
            succeeded = self._crypt_unprotect(
                ctypes.byref(input_blob),
                None,
                ctypes.byref(entropy_blob),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        del input_buffer, entropy_buffer
        if not succeeded:
            error_code = ctypes.get_last_error()
            raise CredentialStoreUnavailable(
                f"Windows DPAPI 操作失败（错误码 {error_code}）"
            )
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._local_free(output_blob.pbData)

    def _configure_api(self) -> None:
        blob_pointer = ctypes.POINTER(_DATA_BLOB)
        self._crypt_protect = self._crypt32.CryptProtectData
        self._crypt_protect.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt_protect.restype = wintypes.BOOL
        self._crypt_unprotect = self._crypt32.CryptUnprotectData
        self._crypt_unprotect.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt_unprotect.restype = wintypes.BOOL
        self._local_free = self._kernel32.LocalFree
        self._local_free.argtypes = [ctypes.c_void_p]
        self._local_free.restype = ctypes.c_void_p


class DpapiCredentialStore:
    """An encrypted on-disk mapping protected for the current Windows user."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        protector: DataProtector | None = None,
    ) -> None:
        self._path = path or default_dpapi_credential_path()
        self._protector = protector or CurrentUserDpapiProtector()
        self._lock = threading.RLock()

    @property
    def kind(self) -> CredentialStoreKind:
        return CredentialStoreKind.CURRENT_USER_DPAPI

    @property
    def path(self) -> Path:
        return self._path

    def get(self, provider_id: ProviderId | str) -> SecretCredential | None:
        normalized = ProviderId(provider_id)
        with self._lock:
            values = self._read_values()
            value = values.get(normalized.value)
        return SecretCredential(value) if value is not None else None

    def set(
        self, provider_id: ProviderId | str, credential: SecretCredential
    ) -> None:
        normalized = ProviderId(provider_id)
        with self._lock:
            values = self._read_values()
            values[normalized.value] = credential.reveal()
            self._write_values(values)

    def delete(self, provider_id: ProviderId | str) -> None:
        normalized = ProviderId(provider_id)
        with self._lock:
            values = self._read_values()
            values.pop(normalized.value, None)
            if values:
                self._write_values(values)
            else:
                self._path.unlink(missing_ok=True)
            if self.get(normalized) is not None:
                raise CredentialStoreError("DPAPI 凭据删除验证失败")

    def __repr__(self) -> str:
        return f"DpapiCredentialStore(path={self._path!r}, contents=<redacted>)"

    def _read_values(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            payload = self._path.read_bytes()
            if not payload.startswith(_DPAPI_FILE_MAGIC):
                raise CredentialStoreError("DPAPI 凭据文件格式无效")
            plaintext = self._protector.unprotect(payload[len(_DPAPI_FILE_MAGIC) :])
            decoded = json.loads(plaintext.decode("utf-8"))
            if not isinstance(decoded, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in decoded.items()
            ):
                raise CredentialStoreError("DPAPI 凭据文件内容无效")
            return decoded
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("DPAPI 凭据文件无法读取") from exc

    def _write_values(self, values: dict[str, str]) -> None:
        try:
            plaintext = json.dumps(
                values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            payload = _DPAPI_FILE_MAGIC + self._protector.protect(plaintext)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(
                f".{self._path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path)
            finally:
                temporary.unlink(missing_ok=True)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("DPAPI 凭据文件无法写入") from exc


class CredentialStoreChain:
    """Priority/fallback composition across persistent and session stores."""

    def __init__(self, stores: tuple[CredentialStore, ...]) -> None:
        if not stores:
            raise ValueError("Credential Store fallback 链不能为空")
        self._stores = stores

    @property
    def stores(self) -> tuple[CredentialStore, ...]:
        return self._stores

    def get(self, provider_id: ProviderId | str) -> SecretCredential | None:
        normalized = ProviderId(provider_id)
        for store in self._stores:
            try:
                value = store.get(normalized)
            except CredentialStoreError:
                continue
            if value is not None:
                return value
        return None

    def set(
        self, provider_id: ProviderId | str, credential: SecretCredential
    ) -> CredentialStoreKind:
        normalized = ProviderId(provider_id)
        for store in self._stores:
            try:
                store.set(normalized, credential)
                saved = store.get(normalized)
                if saved is None or saved.reveal() != credential.reveal():
                    raise CredentialStoreError("凭据写入验证失败")
                return store.kind
            except CredentialStoreError:
                continue
        raise CredentialStoreUnavailable("所有凭据存储层均不可用")

    def delete(self, provider_id: ProviderId | str) -> None:
        normalized = ProviderId(provider_id)
        failures = 0
        for store in self._stores:
            try:
                store.delete(normalized)
                if store.get(normalized) is not None:
                    raise CredentialStoreError("凭据删除验证失败")
            except CredentialStoreError:
                failures += 1
        if failures:
            raise CredentialStoreError("无法验证所有凭据存储层均已删除")

    def __repr__(self) -> str:
        kinds = ", ".join(store.kind.value for store in self._stores)
        return f"CredentialStoreChain(stores=[{kinds}], contents=<redacted>)"


def default_dpapi_credential_path() -> Path:
    """Return the encrypted credential file path for this app instance."""

    try:
        return runtime_private_root() / "credentials" / "ai-v1.bin"
    except ValueError as exc:
        raise CredentialStoreUnavailable("无法定位凭据存储目录") from exc


def build_default_credential_store() -> CredentialStoreChain:
    """Build the persistent credential chain for this app instance.

    Formal 8501 retains the historical Credential Manager -> DPAPI order.
    Staging uses only its instance-private DPAPI file (plus process memory),
    so saving or deleting a test credential can never mutate production.
    """

    stores: list[CredentialStore] = []
    if os.environ.get(STAGING_ENV_VAR) != "1":
        try:
            stores.append(WindowsCredentialManagerStore())
        except CredentialStoreUnavailable:
            pass
    try:
        stores.append(DpapiCredentialStore())
    except CredentialStoreUnavailable:
        pass
    stores.append(MemoryCredentialStore())
    return CredentialStoreChain(tuple(stores))


def _target_name(provider_id: ProviderId | str) -> str:
    return f"{_TARGET_PREFIX}{ProviderId(provider_id).value}"


def _make_blob(value: bytes) -> tuple[_DATA_BLOB, object]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    blob = _DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer
