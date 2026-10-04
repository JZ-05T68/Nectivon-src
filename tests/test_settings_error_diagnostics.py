"""Settings error display diagnostics (v0.8.6 pre-simulation fix run).

Reproduction record for the Hunyuan 401: the saved credential round-trips
intact (51-char ``sk-`` shaped value, no whitespace), the request contract
matches the official TokenHub documentation (Bearer auth, TokenHub endpoint,
valid ``hy3``/``hy4-preview`` ids), a bare ``GET /v1/models`` with the saved
key also returns 401, and no other provider shares the credential value.
The key value itself is therefore rejected by Tencent's auth layer - the
UI now says exactly that instead of a generic message.
"""

from __future__ import annotations

from src.ai.model_registry import ProviderId
from src.ai.provider import ProviderCallError, ProviderErrorCode, SafeProviderError
from src.ai.provider_settings_service import safe_settings_error


def _auth_error(provider_id: str) -> ProviderCallError:
    return ProviderCallError(
        SafeProviderError(
            code=ProviderErrorCode.AUTHENTICATION_FAILED,
            provider_id=provider_id,
            http_status=401,
        )
    )


def test_hunyuan_auth_error_carries_tokenhub_console_hint() -> None:
    message = safe_settings_error(_auth_error(ProviderId.HUNYUAN.value))

    assert "身份验证失败" in message
    assert "tokenhub/apikey" in message


def test_other_providers_keep_the_generic_auth_message() -> None:
    for provider_id in ("deepseek", "qwen", "kimi", "glm"):
        message = safe_settings_error(_auth_error(provider_id))
        assert "tokenhub" not in message, provider_id
        assert message.endswith("（HTTP 401）")


def test_hint_never_appears_for_non_auth_failures() -> None:
    error = ProviderCallError(
        SafeProviderError(
            code=ProviderErrorCode.MODEL_NOT_FOUND,
            provider_id=ProviderId.HUNYUAN.value,
            http_status=404,
        )
    )
    assert "tokenhub" not in safe_settings_error(error)
