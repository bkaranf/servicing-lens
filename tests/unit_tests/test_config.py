"""Tests for safe application settings."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from mortgage_servicing_dashboard.config import AppSettings, EnvironmentName, LogLevel


def test_safe_defaults_and_summary() -> None:
    settings = AppSettings(
        environment=EnvironmentName.TEST,
        log_level=LogLevel.INFO,
        sec_user_agent=None,
        edgar_identity=None,
        edgar_api_key=None,
    )

    assert settings.safe_summary() == {
        "environment": "test",
        "log_level": "INFO",
        "sec_user_agent_configured": False,
        "edgar_identity_configured": False,
        "edgar_api_key_configured": False,
        "edgar_api_base_url": "https://api.edgar.tools/v1/",
    }


def test_sec_user_agent_is_optional_but_validated_and_not_disclosed() -> None:
    settings = AppSettings(sec_user_agent="  Servicing Lens contact@example.test  ")
    assert settings.sec_user_agent == "Servicing Lens contact@example.test"
    assert settings.safe_summary()["sec_user_agent_configured"] is True
    assert "contact@example.test" not in repr(settings.safe_summary())

    with pytest.raises(ValidationError, match="SEC User-Agent"):
        AppSettings(sec_user_agent="anonymous")
    with pytest.raises(ValidationError, match="SEC User-Agent"):
        AppSettings(sec_user_agent="app@example.test\r\nInjected: value")


def test_edgar_api_key_is_secret_environment_only_and_doctor_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-edgar-tools-test-key"
    monkeypatch.setenv("EDGAR_API_KEY", secret)

    settings = AppSettings()

    assert isinstance(settings.edgar_api_key, SecretStr)
    assert settings.require_edgar_api_key().get_secret_value() == secret
    assert settings.safe_summary()["edgar_api_key_configured"] is True
    assert settings.safe_summary()["edgar_api_base_url"] == "https://api.edgar.tools/v1/"
    assert secret not in repr(settings)
    assert secret not in repr(settings.model_dump())
    assert secret not in settings.model_dump_json()
    assert secret not in repr(settings.safe_summary())


def test_edgar_identity_is_secret_environment_only_and_doctor_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = "Servicing Lens synthetic-contact@example.test"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)

    settings = AppSettings()

    assert isinstance(settings.edgar_identity, SecretStr)
    assert settings.require_edgar_identity().get_secret_value() == identity
    assert settings.safe_summary()["edgar_identity_configured"] is True
    assert identity not in repr(settings)
    assert identity not in repr(settings.model_dump())
    assert identity not in settings.model_dump_json()
    assert identity not in repr(settings.safe_summary())


def test_missing_edgar_identity_fails_closed() -> None:
    settings = AppSettings(edgar_identity=None)
    with pytest.raises(ValueError, match="EDGAR_IDENTITY"):
        settings.require_edgar_identity()


def test_edgar_tools_base_url_is_fixed_and_missing_key_fails_closed() -> None:
    settings = AppSettings(edgar_api_key=None)
    with pytest.raises(ValueError, match="EDGAR_API_KEY"):
        settings.require_edgar_api_key()

    with pytest.raises(ValidationError, match="EdgarTools base URL"):
        AppSettings(edgar_api_base_url="https://example.test/v1/")
