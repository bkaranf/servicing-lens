"""Validated, non-secret application settings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MINIMUM_SEC_IDENTITY_LENGTH = 8
_ASCII_CONTROL_LIMIT = 32
_ASCII_DELETE = 127


def validate_edgar_identity(value: str) -> str:
    """Validate the sole live SEC identity without returning it in diagnostics."""
    normalized = value.strip()
    local, separator, domain = normalized.rpartition(" ")
    if (
        len(normalized) < _MINIMUM_SEC_IDENTITY_LENGTH
        or not separator
        or not local.strip()
        or "@" not in domain
        or domain.startswith("@")
        or domain.endswith("@")
        or "." not in domain.rsplit("@", maxsplit=1)[-1]
        or any(
            ord(character) < _ASCII_CONTROL_LIMIT or ord(character) == _ASCII_DELETE
            for character in normalized
        )
    ):
        msg = "EDGAR_IDENTITY must contain an application name and contact email"
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
    edgar_identity: SecretStr | None = Field(
        default=None,
        validation_alias="EDGAR_IDENTITY",
        repr=False,
    )

    @field_validator("edgar_identity", mode="before")
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

    @field_validator("edgar_identity")
    @classmethod
    def require_identifying_edgar_identity(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject malformed identity before constructing a live adapter or database."""
        if value is None:
            return None
        validate_edgar_identity(value.get_secret_value())
        return value

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return only values approved for CLI output and logs.

        Returns:
            An allow-listed configuration summary without model names or secrets.
        """
        return {
            "environment": self.environment.value,
            "log_level": self.log_level.value,
            "edgar_identity_configured": self.edgar_identity is not None,
        }

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
