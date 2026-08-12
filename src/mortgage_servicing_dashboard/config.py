"""Validated, non-secret application settings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MINIMUM_SEC_IDENTITY_LENGTH = 8


def validate_sec_user_agent(value: str) -> str:
    """Validate and normalize the identifying SEC HTTP User-Agent.

    Args:
        value: Application and contact identity sent to official SEC hosts.

    Returns:
        The stripped identity string.

    Raises:
        ValueError: If the value lacks the established application/contact shape.
    """
    normalized = value.strip()
    if (
        "@" not in normalized
        or len(normalized) < _MINIMUM_SEC_IDENTITY_LENGTH
        or "\r" in normalized
        or "\n" in normalized
    ):
        msg = "SEC User-Agent must identify an application and contact email"
        raise ValueError(msg)
    return normalized


class EnvironmentName(StrEnum):
    """Supported deployment environment labels."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Application log levels exposed through configuration."""

    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class AppSettings(BaseSettings):
    """Load the application's safe, non-secret settings.

    Provider credentials are intentionally absent. Provider integrations read credentials
    from a secret manager or their own environment contract and must never serialize them
    through this model.
    """

    model_config = SettingsConfigDict(
        env_prefix="MSD_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    environment: EnvironmentName = EnvironmentName.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    model: str | None = None
    enable_model_calls: bool = False
    enable_deep_agent: bool = False
    enable_langgraph_persistence: bool = False
    max_prompt_chars: int = Field(default=2_000, ge=1, le=8_000)
    sec_user_agent: str | None = Field(default=None, repr=False)

    @field_validator("model", "sec_user_agent", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: Any) -> Any:
        """Treat an empty optional string environment variable as unconfigured.

        Args:
            value: Raw settings-source value.

        Returns:
            `None` for blank strings, otherwise the original value.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("sec_user_agent")
    @classmethod
    def require_identifying_sec_user_agent(cls, value: str | None) -> str | None:
        """Apply the controlled SEC client's established identity rule.

        Args:
            value: Normalized optional User-Agent setting.

        Returns:
            A validated identity or `None` while live acquisition is disabled.
        """
        return None if value is None else validate_sec_user_agent(value)

    @field_validator("model")
    @classmethod
    def require_provider_qualified_model(cls, value: str | None) -> str | None:
        """Require the profile-safe `provider:model` identifier shape.

        Args:
            value: Normalized model identifier.

        Returns:
            The validated model identifier or `None`.

        Raises:
            ValueError: If a configured identifier cannot select a harness profile exactly.
        """
        if value is None:
            return None
        provider, separator, model_name = value.partition(":")
        if not separator or not provider or not model_name or ":" in model_name:
            msg = "MSD_MODEL must use the provider:model format"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> AppSettings:
        """Reject live-call enablement without a provider-qualified model.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If model calls are enabled without a model identifier.
        """
        if self.enable_model_calls and self.model is None:
            msg = "MSD_MODEL is required when MSD_ENABLE_MODEL_CALLS is true"
            raise ValueError(msg)
        if self.enable_deep_agent and not self.enable_model_calls:
            msg = "MSD_ENABLE_DEEP_AGENT requires MSD_ENABLE_MODEL_CALLS"
            raise ValueError(msg)
        return self

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return only values approved for CLI output and logs.

        Returns:
            An allow-listed configuration summary without model names or secrets.
        """
        return {
            "environment": self.environment.value,
            "log_level": self.log_level.value,
            "model_configured": self.model is not None,
            "model_calls_enabled": self.enable_model_calls,
            "deep_agent_enabled": self.enable_deep_agent,
            "langgraph_persistence_enabled": self.enable_langgraph_persistence,
            "max_prompt_chars": self.max_prompt_chars,
            "sec_user_agent_configured": self.sec_user_agent is not None,
            "remote_tracing_allowed": False,
        }

    def require_sec_user_agent(self) -> str:
        """Return the configured identity or fail closed for an explicit live run.

        Returns:
            The validated SEC application/contact identity.

        Raises:
            ValueError: If `MSD_SEC_USER_AGENT` was not configured.
        """
        if self.sec_user_agent is None:
            msg = "MSD_SEC_USER_AGENT is required for live SEC access"
            raise ValueError(msg)
        return self.sec_user_agent
