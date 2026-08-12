"""Governed public-source acquisition clients and bank-adapter contracts."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

import httpx


@dataclass(frozen=True, slots=True)
class AcquiredDocument:
    """Content-addressed public document returned by an acquisition client."""

    url: str
    content: bytes
    media_type: str
    sha256: str
    cache_path: Path


class PublicSourceError(RuntimeError):
    """Safe acquisition failure without response bodies or credentials."""


class SecClient:
    """SEC-compliant client with identity, throttling, retries, and local cache."""

    def __init__(
        self,
        *,
        user_agent: str,
        cache_directory: Path,
        minimum_interval_seconds: float = 0.11,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Configure a bounded, cache-first client."""
        minimum_identity_length = 8
        if "@" not in user_agent or len(user_agent) < minimum_identity_length:
            msg = "SEC User-Agent must identify an application and contact email"
            raise ValueError(msg)
        if minimum_interval_seconds < 0 or max_attempts < 1:
            msg = "SEC throttle and retry settings must be positive"
            raise ValueError(msg)
        cache_directory.mkdir(parents=True, exist_ok=True)
        self._cache_directory = cache_directory
        self._minimum_interval = minimum_interval_seconds
        self._max_attempts = max_attempts
        self._last_request_at = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        """Close network resources."""
        self._client.close()

    def __enter__(self) -> Self:
        """Return the open client for context-manager use."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client when leaving a context."""
        self.close()

    def _cache_key(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self._cache_directory / f"{digest}.bin"

    def acquire(self, url: str) -> AcquiredDocument:
        """Fetch an SEC document after validating host and honoring cache."""
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host not in {"www.sec.gov", "data.sec.gov"}:
            msg = "SEC acquisition permits only official HTTPS SEC hosts"
            raise ValueError(msg)
        cache_path = self._cache_key(url)
        if cache_path.is_file():
            content = cache_path.read_bytes()
            return AcquiredDocument(
                url=url,
                content=content,
                media_type="application/octet-stream",
                sha256=hashlib.sha256(content).hexdigest(),
                cache_path=cache_path,
            )

        for attempt in range(self._max_attempts):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._minimum_interval:
                time.sleep(self._minimum_interval - elapsed)
            self._last_request_at = time.monotonic()
            try:
                response = self._client.get(url)
                if response.status_code in {429, 500, 502, 503, 504}:
                    message = "retryable SEC response"
                    raise httpx.HTTPStatusError(
                        message,
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
            except httpx.HTTPError as error:
                if attempt + 1 == self._max_attempts:
                    message = "SEC acquisition failed after bounded retries"
                    raise PublicSourceError(message) from error
                time.sleep(min(0.25 * (2**attempt), 2.0))
                continue
            content = response.content
            cache_path.write_bytes(content)
            return AcquiredDocument(
                url=url,
                content=content,
                media_type=response.headers.get("content-type", "application/octet-stream"),
                sha256=hashlib.sha256(content).hexdigest(),
                cache_path=cache_path,
            )
        message = "SEC acquisition exhausted without a response"
        raise PublicSourceError(message)


@dataclass(frozen=True, slots=True)
class RegulatoryFact:
    """Reporter-scoped fact returned by a bank regulatory adapter."""

    reporting_entity_id: str
    schedule: str
    item_code: str
    period_end: str
    raw_value: str
    source_url: str


class BankRegulatoryAdapter(Protocol):
    """Common contract for FFIEC CDR, FR Y-9C, and NIC implementations."""

    source_name: str

    def facts(self, *, rssd_id: str, period_end: str) -> tuple[RegulatoryFact, ...]:
        """Return facts for exactly one reporter and period."""


class DisabledBankRegulatoryAdapter:
    """Fail-closed placeholder until a governed endpoint is configured."""

    source_name = "disabled"

    def facts(self, *, rssd_id: str, period_end: str) -> tuple[RegulatoryFact, ...]:
        """Reject use rather than silently cross entity or source boundaries."""
        del rssd_id, period_end
        msg = "bank regulatory adapter is not configured for Stage A recorded-data mode"
        raise PublicSourceError(msg)
