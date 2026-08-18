"""Tests for safe application settings."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from mortgage_servicing_dashboard.config import AppSettings, EnvironmentName, LogLevel


def test_safe_defaults_and_summary() -> None:
    settings = AppSettings(
        environment=EnvironmentName.TEST,
        log_level=LogLevel.INFO,
        edgar_identity=None,
    )

    assert settings.safe_summary() == {
        "environment": "test",
        "log_level": "INFO",
        "edgar_identity_configured": False,
    }


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


@pytest.mark.parametrize(
    "identity",
    ["anonymous", "Servicing Lens", "Servicing Lens contact@", "Name\ncontact@example.test"],
)
def test_malformed_identity_fails_before_live_use(identity: str) -> None:
    with pytest.raises(ValidationError, match="EDGAR_IDENTITY"):
        AppSettings(edgar_identity=identity)


def test_missing_edgar_identity_fails_closed() -> None:
    settings = AppSettings(edgar_identity=None)
    with pytest.raises(ValueError, match="EDGAR_IDENTITY"):
        settings.require_edgar_identity()
