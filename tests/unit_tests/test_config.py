"""Tests for safe application settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mortgage_servicing_dashboard.config import AppSettings, EnvironmentName, LogLevel


def test_safe_defaults_and_summary() -> None:
    settings = AppSettings(
        environment=EnvironmentName.TEST,
        log_level=LogLevel.INFO,
        model=None,
        enable_model_calls=False,
        enable_deep_agent=False,
        enable_langgraph_persistence=False,
        max_prompt_chars=2_000,
        sec_user_agent=None,
    )

    assert settings.safe_summary() == {
        "environment": "test",
        "log_level": "INFO",
        "model_configured": False,
        "model_calls_enabled": False,
        "deep_agent_enabled": False,
        "langgraph_persistence_enabled": False,
        "max_prompt_chars": 2_000,
        "sec_user_agent_configured": False,
        "remote_tracing_allowed": False,
    }


def test_blank_model_is_normalized() -> None:
    settings = AppSettings(model="   ", enable_model_calls=False)
    assert settings.model is None


@pytest.mark.parametrize("model", ["unqualified", ":missing-provider", "missing-model:"])
def test_model_must_be_provider_qualified(model: str) -> None:
    with pytest.raises(ValidationError, match="provider:model"):
        AppSettings(model=model)


def test_enabled_calls_require_model() -> None:
    with pytest.raises(ValidationError, match="MSD_MODEL is required"):
        AppSettings(model=None, enable_model_calls=True)


def test_deep_agent_requires_live_model_switch() -> None:
    with pytest.raises(ValidationError, match="ENABLE_MODEL_CALLS"):
        AppSettings(
            model="test:foundation",
            enable_model_calls=False,
            enable_deep_agent=True,
        )


def test_safe_summary_hides_model_identifier() -> None:
    settings = AppSettings(model="test:private-deployment", enable_model_calls=False)
    assert "private-deployment" not in repr(settings.safe_summary())


def test_sec_user_agent_is_optional_but_validated_and_not_disclosed() -> None:
    settings = AppSettings(sec_user_agent="  Servicing Lens contact@example.test  ")
    assert settings.sec_user_agent == "Servicing Lens contact@example.test"
    assert settings.safe_summary()["sec_user_agent_configured"] is True
    assert "contact@example.test" not in repr(settings.safe_summary())

    with pytest.raises(ValidationError, match="SEC User-Agent"):
        AppSettings(sec_user_agent="anonymous")
    with pytest.raises(ValidationError, match="SEC User-Agent"):
        AppSettings(sec_user_agent="app@example.test\r\nInjected: value")
