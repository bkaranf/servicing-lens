"""Validated, non-secret application settings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MINIMUM_SEC_IDENTITY_LENGTH = 8
_EDGAR_TOOLS_BASE_URL = "https://api.edgar.tools/v1/"


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
    """Load the application's safe settings and SEC acquisition configuration."""

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
    sec_user_agent: str | None = Field(default=None, repr=False)
    edgar_identity: SecretStr | None = Field(
        default=None,
        validation_alias="EDGAR_IDENTITY",
        repr=False,
    )
    edgar_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="EDGAR_API_KEY",
        repr=False,
    )
    edgar_api_base_url: str = _EDGAR_TOOLS_BASE_URL

    @field_validator("sec_user_agent", "edgar_identity", "edgar_api_key", mode="before")
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

    @field_validator("edgar_api_base_url")
    @classmethod
    def require_canonical_edgar_tools_base_url(cls, value: str) -> str:
        """Reject alternate provider hosts or paths.

        Args:
            value: Configured EdgarTools REST base URL.

        Returns:
            The one accepted hosted API base URL.

        Raises:
            ValueError: If an alternate host, scheme, or path is configured.
        """
        if value != _EDGAR_TOOLS_BASE_URL:
            msg = f"EdgarTools base URL must be {_EDGAR_TOOLS_BASE_URL}"
            raise ValueError(msg)
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

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return only values approved for CLI output and logs.

        Returns:
            An allow-listed configuration summary without model names or secrets.
        """
        return {
            "environment": self.environment.value,
            "log_level": self.log_level.value,
            "sec_user_agent_configured": self.sec_user_agent is not None,
            "edgar_identity_configured": self.edgar_identity is not None,
            "edgar_api_key_configured": self.edgar_api_key is not None,
            "edgar_api_base_url": self.edgar_api_base_url,
        }

    def require_edgar_api_key(self) -> SecretStr:
        """Return the configured EdgarTools secret or fail closed.

        Returns:
            The secret-bearing API key wrapper.

        Raises:
            ValueError: If `EDGAR_API_KEY` was not configured.
        """
        if self.edgar_api_key is None:
            msg = "EDGAR_API_KEY is required for live EdgarTools access"
            raise ValueError(msg)
        return self.edgar_api_key

    def require_edgar_identity(self) -> SecretStr:
        """Return the configured open-source edgartools identity or fail closed.

        Returns:
            The secret-bearing identity wrapper.

        Raises:
            ValueError: If `EDGAR_IDENTITY` was not configured.
        """
        if self.edgar_identity is None:
            msg = "EDGAR_IDENTITY is required for live SEC access through edgartools"
            raise ValueError(msg)
        return self.edgar_identity

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
