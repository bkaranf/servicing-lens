"""Configure compliant SEC access before the first lazy ``edgar`` import."""

from __future__ import annotations

import importlib
import os
import re
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from types import ModuleType

from pydantic import SecretStr

from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterConfigurationError,
    AdapterIdentityError,
    AdapterState,
)

_DEFAULT_LOCAL_DATA_PATH = Path("edgartools") / "data"
_MAX_REQUESTS_PER_SECOND = 9
_EXPECTED_EDGARTOOLS_VERSION = "5.48.0"
_ASCII_CONTROL_LIMIT = 32
_ASCII_DELETE = 127
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_IDENTITY_PATTERN = re.compile(r"\S(?:.*\S)?\s+[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_CUSTOM_MIRROR_VARIABLES = (
    "EDGAR_BASE_URL",
    "EDGAR_DATA_URL",
    "EDGAR_XBRL_URL",
)


@dataclass(frozen=True, slots=True)
class EdgarBootstrapConfig:
    """Secret identity and application state root for the edgartools cache.

    ``identity`` is intentionally excluded from the dataclass representation.  It is
    accepted only as a ``SecretStr`` so a caller cannot accidentally pass a printable
    plaintext identity through configuration or diagnostics.
    """

    identity: SecretStr = field(repr=False)
    runtime_root: Path = Path(".msi")

    @property
    def local_data_root(self) -> Path:
        """Return the cache path below the already-resolved application state root."""
        return (self.runtime_root.resolve() / _DEFAULT_LOCAL_DATA_PATH).resolve()


class EdgarBootstrap:
    """One lazy, centrally configured import boundary for public ``edgar``."""

    def __init__(self, config: EdgarBootstrapConfig) -> None:
        """Store import-time configuration without importing edgartools."""
        self._config = config
        self._module: ModuleType | None = None

    def load(self) -> ModuleType:
        """Configure CRAWL network access and local caching, then import edgartools.

        Returns:
            The lazily imported public ``edgar`` module.

        Raises:
            AdapterConfigurationError: If safe import-time configuration cannot be
                guaranteed.
            AdapterIdentityError: If the caller did not provide a usable identity.
        """
        if self._module is not None:
            return self._module
        if "edgar" in sys.modules:
            message = "edgartools was imported before compliant bootstrap"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            )
        self._configure_environment()
        try:
            installed_version = distribution_version("edgartools")
        except PackageNotFoundError as error:
            message = "edgartools 5.48.0 is required for live SEC access"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            ) from error
        if installed_version != _EXPECTED_EDGARTOOLS_VERSION:
            message = "installed edgartools version must be exactly 5.48.0"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            )
        self._module = importlib.import_module("edgar")
        return self._module

    def _configure_environment(self) -> None:
        identity = self._config.identity.get_secret_value().strip()
        if not identity or _IDENTITY_PATTERN.fullmatch(identity) is None:
            message = "EDGAR_IDENTITY must contain a name and contact email"
            raise AdapterIdentityError(
                message,
                state=AdapterState.IDENTITY_NOT_CONFIGURED,
                operation="bootstrap",
            )
        if any(
            ord(character) < _ASCII_CONTROL_LIMIT or ord(character) == _ASCII_DELETE
            for character in identity
        ):
            message = "EDGAR_IDENTITY contains invalid control characters"
            raise AdapterIdentityError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            )

        configured_mirror = next(
            (name for name in _CUSTOM_MIRROR_VARIABLES if name in os.environ),
            None,
        )
        if configured_mirror is not None:
            message = f"custom SEC mirror setting {configured_mirror} is prohibited"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            )

        rate_value = os.environ.get("EDGAR_RATE_LIMIT_PER_SEC")
        if rate_value is not None:
            normalized_rate = rate_value.strip()
            if _POSITIVE_INTEGER.fullmatch(normalized_rate) is None:
                message = "EDGAR_RATE_LIMIT_PER_SEC must be a positive integer"
                raise AdapterConfigurationError(
                    message,
                    state=AdapterState.CONFIGURATION_ERROR,
                    operation="bootstrap",
                )
            if int(normalized_rate) > _MAX_REQUESTS_PER_SECOND:
                message = "EDGAR_RATE_LIMIT_PER_SEC cannot exceed the edgartools default of 9"
                raise AdapterConfigurationError(
                    message,
                    state=AdapterState.CONFIGURATION_ERROR,
                    operation="bootstrap",
                )

        data_root = self._config.local_data_root
        try:
            data_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            message = "edgartools local storage could not be prepared"
            raise AdapterConfigurationError(
                message,
                state=AdapterState.CONFIGURATION_ERROR,
                operation="bootstrap",
            ) from error

        # edgartools 5.48 reads these supported settings at import time. Keep the
        # library's own cache and bounded retry machinery; no application retry loop
        # or alternate host is configured here. EDGAR_USE_LOCAL_DATA is deliberately
        # disabled: that mode is a bulk-data-only lookup, and Company.get_facts()
        # returns None on an empty bulk directory instead of using its SEC network
        # path. EDGAR_LOCAL_DATA_DIR still owns edgartools' ignored HTTP cache.
        os.environ["EDGAR_IDENTITY"] = identity
        os.environ["EDGAR_ACCESS_MODE"] = "CRAWL"
        os.environ["EDGAR_LOCAL_DATA_DIR"] = str(data_root)
        os.environ["EDGAR_USE_LOCAL_DATA"] = "0"
        os.environ["EDGARTOOLS_STRICT_ERRORS"] = "1"
