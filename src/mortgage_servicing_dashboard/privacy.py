"""Fail-closed prompt and tracing boundaries."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from langchain.agents.middleware import PIIMiddleware


class DataClassification(StrEnum):
    """Five explicit data classifications at the application boundary."""

    PUBLIC = "public_filing"
    PUBLIC_REGULATORY = "public_regulatory"
    ISSUER_PUBLIC = "issuer_public"
    SYNTHETIC = "synthetic_test"
    RESTRICTED_PRIVATE = "restricted_private"


class SensitiveContentError(ValueError):
    """Report a rejected category without retaining the matched value."""

    def __init__(self, category: str) -> None:
        """Initialize a safe rejection.

        Args:
            category: Non-sensitive detector category.
        """
        self.category = category
        msg = f"Content rejected by the privacy boundary (category: {category})"
        super().__init__(msg)


class UnsafeTracingConfigurationError(RuntimeError):
    """Report that remote tracing must be disabled before framework invocation."""


_APPROVAL_TOKEN = object()


class ApprovedPrompt:
    """Prompt text that has passed the application's explicit privacy boundary."""

    __slots__ = ("_classification", "_text")

    def __init__(
        self,
        text: str,
        classification: DataClassification,
        *,
        _approval_token: object | None = None,
    ) -> None:
        """Create an approved prompt through `PromptBoundary` only.

        Args:
            text: Already-screened public or synthetic text.
            classification: Permitted data classification.
            _approval_token: Internal construction token.

        Raises:
            TypeError: If instantiated outside `PromptBoundary.approve`.
        """
        if _approval_token is not _APPROVAL_TOKEN:
            msg = "Use PromptBoundary.approve to construct ApprovedPrompt"
            raise TypeError(msg)
        self._text = text
        self._classification = classification

    @property
    def text(self) -> str:
        """Return screened text for the model boundary.

        Returns:
            The approved prompt body.
        """
        return self._text

    @property
    def classification(self) -> DataClassification:
        """Return the approved data classification.

        Returns:
            The prompt's public or synthetic classification.
        """
        return self._classification

    def __repr__(self) -> str:
        """Represent metadata only so prompt text is not leaked accidentally.

        Returns:
            A content-free representation.
        """
        return (
            f"ApprovedPrompt(classification={self.classification.value!r}, length={len(self.text)})"
        )


_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    ("us_ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "phone_number",
        re.compile(r"(?<!\w)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)"),
    ),
    (
        "long_numeric_identifier",
        re.compile(r"(?<!\d)(?:\d[ -]?){8,19}\d(?!\d)"),
    ),
    (
        "ip_address",
        re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "secret_in_url",
        re.compile(r"(?i)https?://[^\s]*[?&](?:token|api[_-]?key|signature|secret)=[^\s&]+"),
    ),
)

_PUBLIC_IDENTIFIER_PATTERN = re.compile(
    r"(?i)(?:\bCIK\s*[:#]?\s*\d{10}\b|\baccession\s*[:#]?\s*\d{10}-\d{2}-\d{6}\b)"
)
_CORPORATE_CONTACT_LINE = re.compile(
    r"(?im)^.*(?:investor relations|media contact|press contact).*(?:\r?\n|$)"
)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_TRACING_ENV_NAMES = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
_FALSE_VALUES = {"", "0", "false", "no", "off"}
_MINIMUM_SECRET_LENGTH = 8


class PromptBoundary:
    """Approve only public or synthetic text after deterministic screening.

    Pattern screening is defense in depth. Callers remain responsible for removing
    sensitive prose that cannot be identified reliably, such as personal names or street
    addresses.
    """

    def __init__(
        self,
        *,
        max_chars: int,
        secret_environment: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize the boundary without retaining environment variable names.

        Args:
            max_chars: Maximum accepted prompt length.
            secret_environment: Environment mapping used to detect accidental secret reuse.

        Raises:
            ValueError: If `max_chars` is not positive.
        """
        if max_chars <= 0:
            msg = "max_chars must be positive"
            raise ValueError(msg)
        environment = os.environ if secret_environment is None else secret_environment
        self._max_chars = max_chars
        self._secret_values = tuple(
            value
            for name, value in environment.items()
            if value
            and len(value) >= _MINIMUM_SECRET_LENGTH
            and any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
        )

    def approve(
        self,
        text: str,
        *,
        classification: DataClassification,
    ) -> ApprovedPrompt:
        """Validate text before it becomes a model message.

        Args:
            text: De-identified public or synthetic text.
            classification: Explicit classification asserted by the trusted caller.

        Returns:
            An approved prompt accepted by `DashboardAgent.invoke`.

        Raises:
            SensitiveContentError: If text violates the privacy boundary.
            TypeError: If the classification is not a permitted enum value.
        """
        classification_candidate: object = classification
        if not isinstance(classification_candidate, DataClassification):
            msg = "classification must be DataClassification.PUBLIC or SYNTHETIC"
            raise TypeError(msg)

        if classification is DataClassification.RESTRICTED_PRIVATE:
            category = "restricted_private_data"
            raise SensitiveContentError(category)

        normalized_text = strip_corporate_contact_blocks(text).strip()
        if not normalized_text:
            category = "empty_input"
            raise SensitiveContentError(category)
        if len(normalized_text) > self._max_chars:
            category = "input_too_long"
            raise SensitiveContentError(category)
        if _CONTROL_CHARACTERS.search(normalized_text):
            category = "control_character"
            raise SensitiveContentError(category)

        screened_text = normalized_text
        if classification is not DataClassification.SYNTHETIC:
            screened_text = _PUBLIC_IDENTIFIER_PATTERN.sub("PUBLIC_IDENTIFIER", screened_text)
        for category, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(screened_text):
                raise SensitiveContentError(category)
        if any(secret in normalized_text for secret in self._secret_values):
            category = "environment_secret"
            raise SensitiveContentError(category)

        return ApprovedPrompt(
            normalized_text,
            classification,
            _approval_token=_APPROVAL_TOKEN,
        )


def strip_corporate_contact_blocks(text: str) -> str:
    """Remove obvious public corporate contact headers before model use."""
    return _CORPORATE_CONTACT_LINE.sub("", text)


def assert_remote_tracing_disabled(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Fail before invocation when an environment tracing switch is enabled.

    Args:
        environment: Environment mapping to inspect. Defaults to the process environment.

    Raises:
        UnsafeTracingConfigurationError: If a known remote tracing switch is truthy.
    """
    active_environment = os.environ if environment is None else environment
    for name in _TRACING_ENV_NAMES:
        value = active_environment.get(name, "").strip().lower()
        if value not in _FALSE_VALUES:
            msg = "Remote tracing must be disabled before framework invocation"
            raise UnsafeTracingConfigurationError(msg)


def _blocking_pii_middleware(
    pii_type: str,
    *,
    detector: str | None = None,
) -> PIIMiddleware[Any, Any]:
    """Construct one consistently fail-closed PII middleware instance."""
    return PIIMiddleware(
        pii_type,
        strategy="block",
        detector=detector,
        apply_to_input=True,
        apply_to_output=True,
        apply_to_tool_results=True,
    )


def build_privacy_middleware() -> tuple[PIIMiddleware[Any, Any], ...]:
    """Build blocking middleware for input, output, and tool-result surfaces.

    Returns:
        LangChain middleware that rejects common PII and secret patterns.
    """
    middleware: list[PIIMiddleware[Any, Any]] = [
        _blocking_pii_middleware("email"),
        _blocking_pii_middleware("credit_card"),
        _blocking_pii_middleware("ip"),
        _blocking_pii_middleware("mac_address"),
    ]
    for category, pattern in _SENSITIVE_PATTERNS:
        if category in {"email", "ip_address", "long_numeric_identifier"}:
            continue
        middleware.append(
            _blocking_pii_middleware(category, detector=pattern.pattern),
        )
    return tuple(middleware)
