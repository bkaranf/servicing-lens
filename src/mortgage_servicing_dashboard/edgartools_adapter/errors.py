"""Secret-safe application errors for the public-edgartools boundary.

This module deliberately does not import :mod:`edgar`.  The adapter catches library
exceptions at its lazy import boundary and passes them to :func:`map_edgar_exception`.
"""

from __future__ import annotations

import sys
from enum import StrEnum


class AdapterState(StrEnum):
    """Stable, serializable state for every adapter failure."""

    INVALID_REQUEST = "INVALID_REQUEST"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    IDENTITY_NOT_CONFIGURED = "IDENTITY_NOT_CONFIGURED"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    PARSING_ERROR = "PARSING_ERROR"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"
    INTEGRITY_ERROR = "INTEGRITY_ERROR"
    LIBRARY_ERROR = "LIBRARY_ERROR"


class EdgarToolsAdapterError(RuntimeError):
    """Base error containing only stable, non-secret operation context."""

    def __init__(self, message: str, *, state: AdapterState, operation: str) -> None:
        """Initialize a safe error with stable state and operation metadata."""
        super().__init__(message)
        self.state = state
        self.operation = operation


class AdapterValidationError(EdgarToolsAdapterError, ValueError):
    """An identifier or selector was invalid before acquisition."""


class AdapterConfigurationError(EdgarToolsAdapterError):
    """The adapter could not be bootstrapped safely."""


class AdapterIdentityError(AdapterConfigurationError):
    """The caller did not provide a usable SEC identity."""


class AdapterNotFoundError(EdgarToolsAdapterError, LookupError):
    """An exact company, filing, or attachment did not exist."""


class AdapterRateLimitError(EdgarToolsAdapterError):
    """SEC rate limiting was surfaced without an application retry loop."""


class AdapterTransportError(EdgarToolsAdapterError):
    """The public library could not obtain an SEC response."""


class AdapterParsingError(EdgarToolsAdapterError):
    """The public library could not parse acquired content."""


class AdapterSelectionError(EdgarToolsAdapterError):
    """A library result did not match the exact requested identity."""


class AdapterIntegrityError(EdgarToolsAdapterError):
    """Acquired or retained bytes failed an integrity check."""


class AdapterLibraryError(EdgarToolsAdapterError):
    """Another explicit edgartools failure crossed the adapter boundary."""


_VALIDATION_ERRORS = frozenset({"InvalidDateError", "ValidationError"})
_NOT_FOUND_ERRORS = frozenset(
    {
        "AttachmentNotFoundError",
        "CompanyNotFoundError",
        "FilingNotFoundError",
        "NotFoundError",
    }
)
_IDENTITY_ERRORS = frozenset({"IdentityError", "IdentityNotSetError", "SECIdentityError"})
_PARSING_ERRORS = frozenset({"ParsingError", "XBRLProcessingError"})
_PUBLIC_TRANSPORT_ERRORS: dict[str, frozenset[str]] = {
    "httpcore": frozenset(
        {
            "ConnectError",
            "ConnectTimeout",
            "ConnectionNotAvailable",
            "LocalProtocolError",
            "NetworkError",
            "PoolTimeout",
            "ProtocolError",
            "ProxyError",
            "ReadError",
            "ReadTimeout",
            "RemoteProtocolError",
            "TimeoutException",
            "UnsupportedProtocol",
            "WriteError",
            "WriteTimeout",
        }
    ),
    "httpx": frozenset(
        {
            "CloseError",
            "ConnectError",
            "ConnectTimeout",
            "LocalProtocolError",
            "NetworkError",
            "PoolTimeout",
            "ProtocolError",
            "ProxyError",
            "ReadError",
            "ReadTimeout",
            "RemoteProtocolError",
            "TimeoutException",
            "TransportError",
            "UnsupportedProtocol",
            "WriteError",
            "WriteTimeout",
        }
    ),
}


def map_edgar_exception(
    error: BaseException,
    *,
    operation: str,
) -> EdgarToolsAdapterError:
    """Map an edgartools exception without copying its possibly sensitive message.

    Classification uses exception MRO names so this module remains import-safe before
    bootstrap.  Callers must use ``raise mapped from error`` to preserve the original
    exception for internal diagnostics without placing its text in the public error.

    Args:
        error: Exception caught at the lazy edgartools boundary.
        operation: Stable adapter operation name; never caller data or an identity.

    Returns:
        A typed, secret-safe application exception.

    Raises:
        TypeError: If ``error`` is not an edgartools domain exception.
    """
    class_names = {exception_class.__name__ for exception_class in type(error).__mro__}

    if "TooManyRequestsError" in class_names:
        mapped: EdgarToolsAdapterError = AdapterRateLimitError(
            "SEC request rate limit was reached",
            state=AdapterState.RATE_LIMITED,
            operation=operation,
        )
    elif class_names & _IDENTITY_ERRORS:
        mapped = AdapterIdentityError(
            "SEC identity is not configured",
            state=AdapterState.IDENTITY_NOT_CONFIGURED,
            operation=operation,
        )
    elif class_names & _VALIDATION_ERRORS:
        mapped = AdapterValidationError(
            "edgartools rejected the requested selector",
            state=AdapterState.INVALID_REQUEST,
            operation=operation,
        )
    elif class_names & _NOT_FOUND_ERRORS:
        mapped = AdapterNotFoundError(
            "the requested SEC resource was not found",
            state=AdapterState.NOT_FOUND,
            operation=operation,
        )
    elif class_names & _PARSING_ERRORS:
        mapped = AdapterParsingError(
            "edgartools could not parse the SEC resource",
            state=AdapterState.PARSING_ERROR,
            operation=operation,
        )
    elif "TransportError" in class_names:
        mapped = AdapterTransportError(
            "edgartools could not reach the SEC resource",
            state=AdapterState.TRANSPORT_ERROR,
            operation=operation,
        )
    elif "EdgarError" in class_names:
        mapped = AdapterLibraryError(
            "edgartools reported an acquisition failure",
            state=AdapterState.LIBRARY_ERROR,
            operation=operation,
        )
    else:
        msg = "map_edgar_exception accepts only edgartools domain exceptions"
        raise TypeError(msg)
    return mapped


def map_public_transport_exception(
    error: BaseException,
    *,
    operation: str,
) -> AdapterTransportError:
    """Map transport failures escaping public edgartools without importing a client.

    Edgartools can surface an underlying ``httpx``/``httpcore`` streaming error
    directly after its public API has started reading a response.  The application
    does not call either transport library; this narrow mapper merely keeps that
    dependency failure inside the same secret-safe adapter boundary.
    """
    for module_name, allowed_names in _PUBLIC_TRANSPORT_ERRORS.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for class_name in allowed_names:
            exception_type = getattr(module, class_name, None)
            if (
                isinstance(exception_type, type)
                and issubclass(exception_type, BaseException)
                and isinstance(error, exception_type)
            ):
                return AdapterTransportError(
                    "edgartools could not complete the SEC response",
                    state=AdapterState.TRANSPORT_ERROR,
                    operation=operation,
                )
    msg = "map_public_transport_exception accepts only edgartools transport dependencies"
    raise TypeError(msg)
