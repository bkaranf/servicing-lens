"""Governed acquisition, immutable retention, and recorded-document parsing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, Self, cast

import httpx

from mortgage_servicing_dashboard.domain import (
    ObservationState,
    ParsedObservationCandidate,
    decimal_places,
    normalize_reported_value,
)

_NUMERIC_CELL = re.compile(r"^-?\(?\d[\d,]*(?:\.\d+)?\)?$")


@dataclass(frozen=True, slots=True)
class AcquiredDocument:
    """Exact public-document bytes returned by an acquisition boundary."""

    url: str
    content: bytes
    media_type: str
    sha256: str
    cache_path: Path
    status_code: int = 200
    etag: str | None = None
    last_modified: str | None = None

    @property
    def byte_length(self) -> int:
        """Return the exact retained payload length."""
        return len(self.content)


@dataclass(frozen=True, slots=True)
class RetainedDocument:
    """Verified content-addressed immutable retention result."""

    sha256: str
    byte_length: int
    path: Path


@dataclass(frozen=True, slots=True)
class RecordedSourceDefinition:
    """Validated nonnumeric manifest entry for one retained official document."""

    key: str
    company_id: str
    source_class: str
    accession: str
    url: str
    published_at: datetime
    period_end: str
    media_type: str
    representation: str
    capture_method: str
    fixture_path: Path
    byte_length: int
    content_sha256: str
    locator: str
    parser_name: str
    parser_version: str
    rows: tuple[dict[str, Any], ...]
    quarantine_rows: tuple[dict[str, Any], ...]

    @classmethod
    def from_mapping(
        cls,
        *,
        key: str,
        payload: dict[str, Any],
        config_root: Path,
    ) -> RecordedSourceDefinition:
        """Build one source definition from versioned manifest metadata.

        Args:
            key: Stable manifest source key.
            payload: Parsed YAML mapping containing no financial observations.
            config_root: Directory against which the fixture path resolves.

        Returns:
            Validated typed source definition.
        """
        rows = cast("list[dict[str, Any]]", payload["rows"])
        return cls(
            key=key,
            company_id=str(payload["company_id"]),
            source_class=str(payload["source_class"]),
            accession=str(payload["accession"]),
            url=str(payload["url"]),
            published_at=datetime.fromisoformat(str(payload["published_at"])),
            period_end=str(payload["period_end"]),
            media_type=str(payload["media_type"]),
            representation=str(payload["representation"]),
            capture_method=str(payload["capture_method"]),
            fixture_path=config_root / str(payload["fixture_path"]),
            byte_length=int(payload["byte_length"]),
            content_sha256=str(payload["content_sha256"]),
            locator=str(payload["locator"]),
            parser_name=str(payload["parser_name"]),
            parser_version=str(payload["parser_version"]),
            rows=tuple(rows),
            quarantine_rows=tuple(cast("list[dict[str, Any]]", payload.get("quarantine_rows", []))),
        )


class PublicSourceError(RuntimeError):
    """Safe acquisition or evidence-integrity failure."""


class TransientPublicSourceError(PublicSourceError):
    """Retryable acquisition failure distinct from deterministic integrity errors."""


class RecordedEvidenceAcquirer:
    """Replay retained documents with mandatory length and SHA-256 verification."""

    def acquire(self, source: RecordedSourceDefinition) -> AcquiredDocument:
        """Read a recorded source without opening a socket.

        Args:
            source: Versioned evidence manifest entry.

        Returns:
            Verified exact recorded bytes.

        Raises:
            PublicSourceError: If bytes are absent, changed, or truncated.
        """
        try:
            content = source.fixture_path.read_bytes()
        except OSError as error:
            msg = f"recorded evidence is unavailable: {source.key}"
            raise PublicSourceError(msg) from error
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != source.byte_length or digest != source.content_sha256:
            msg = f"recorded evidence integrity mismatch: {source.key}"
            raise PublicSourceError(msg)
        return AcquiredDocument(
            url=source.url,
            content=content,
            media_type=source.media_type,
            sha256=digest,
            cache_path=source.fixture_path,
        )


class ContentAddressedEvidenceStore:
    """Immutable file retention keyed by SHA-256 rather than source URL."""

    def __init__(self, root: Path) -> None:
        """Create a bounded content-addressed retention root.

        Args:
            root: Application-owned evidence directory.
        """
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def retain(self, document: AcquiredDocument) -> RetainedDocument:
        """Store exact bytes atomically and reject a conflicting existing object.

        Args:
            document: Acquired byte payload and computed identity.

        Returns:
            Immutable retention reference.

        Raises:
            PublicSourceError: If an existing object does not match its identity.
        """
        digest = hashlib.sha256(document.content).hexdigest()
        if digest != document.sha256:
            msg = "acquired document hash does not match its bytes"
            raise PublicSourceError(msg)
        directory = self._root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.bin"
        if target.exists():
            existing = target.read_bytes()
            if existing != document.content:
                msg = "content-addressed retention collision"
                raise PublicSourceError(msg)
        else:
            temporary = directory / f".{digest}.{os.getpid()}.tmp"
            temporary.write_bytes(document.content)
            try:
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return RetainedDocument(digest, len(document.content), target)

    def verify(self, retained: RetainedDocument) -> bytes:
        """Reload and verify one retained object.

        Args:
            retained: Previously returned content address.

        Returns:
            Exact immutable bytes.

        Raises:
            PublicSourceError: If content is missing or changed.
        """
        try:
            content = retained.path.read_bytes()
        except OSError as error:
            msg = "retained evidence is unavailable"
            raise PublicSourceError(msg) from error
        if (
            len(content) != retained.byte_length
            or hashlib.sha256(content).hexdigest() != retained.sha256
        ):
            msg = "retained evidence failed integrity verification"
            raise PublicSourceError(msg)
        return content


class _TableRows(HTMLParser):
    """Small deterministic HTML table text collector for recorded SEC documents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, ...]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start row and cell buffers."""
        del attrs
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        """Finish row and cell buffers."""
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            normalized = " ".join("".join(self._cell).replace("\xa0", " ").split())
            self._row.append(normalized)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            nonempty = tuple(cell for cell in self._row if cell)
            if nonempty:
                self.rows.append(nonempty)
            self._row = None

    def handle_data(self, data: str) -> None:
        """Collect text only while inside a table cell."""
        if self._cell is not None:
            self._cell.append(data)


def _reported_numbers(cells: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for cell in cells[1:]:
        candidate = cell.strip().replace("\N{MINUS SIGN}", "-")
        if not _NUMERIC_CELL.fullmatch(candidate):
            continue
        negative = candidate.startswith("(") and candidate.endswith(")")
        candidate = candidate.strip("()")
        values.append(f"-{candidate}" if negative else candidate)
    return tuple(values)


def _quarter_map(quarters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(quarter["period_end"]): quarter for quarter in quarters}


class StageARecordedDocumentParser:
    """Parse allow-listed servicing table rows from verified recorded SEC HTML."""

    def extract_row_values(
        self,
        *,
        content: bytes,
        raw_label: str,
        occurrence: int = 0,
    ) -> tuple[str, ...]:
        """Return exact numeric cell text for one unambiguously selected row.

        Args:
            content: Verified retained HTML bytes.
            raw_label: Exact first-cell issuer label.
            occurrence: Explicit occurrence when a label appears more than once.

        Returns:
            Numeric cells in document order.

        Raises:
            PublicSourceError: If decoding or row selection fails.
        """
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            msg = "recorded HTML is not UTF-8"
            raise PublicSourceError(msg) from error
        collector = _TableRows()
        collector.feed(text)
        matches = [row for row in collector.rows if row[0] == raw_label]
        try:
            return _reported_numbers(matches[occurrence])
        except IndexError as error:
            msg = f"configured source row was not found: {raw_label}"
            raise PublicSourceError(msg) from error

    def parse(
        self,
        *,
        source: RecordedSourceDefinition,
        content: bytes,
        company: dict[str, Any],
        quarters: list[dict[str, Any]],
    ) -> tuple[ParsedObservationCandidate, ...]:
        """Extract, resolve, and normalize configured labels deterministically.

        Args:
            source: Nonnumeric parser recipe and evidence identity.
            content: Verified retained HTML bytes.
            company: Versioned company/entity/scope identity mapping.
            quarters: Versioned fiscal-period definitions.

        Returns:
            Resolved exact observation candidates.

        Raises:
            PublicSourceError: If a label, occurrence, period, or value is ambiguous.
        """
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            msg = f"recorded HTML is not UTF-8: {source.key}"
            raise PublicSourceError(msg) from error
        collector = _TableRows()
        collector.feed(text)
        by_period = _quarter_map(quarters)
        extracted: list[ParsedObservationCandidate] = []
        for recipe in source.rows:
            raw_label = str(recipe["raw_label"])
            matches = [row for row in collector.rows if row[0] == raw_label]
            if not matches:
                msg = f"configured source row was not found: {raw_label}"
                raise PublicSourceError(msg)
            occurrence = int(recipe.get("row_occurrence", 0))
            try:
                row = matches[occurrence]
            except IndexError as error:
                msg = f"configured source-row occurrence was not found: {raw_label}"
                raise PublicSourceError(msg) from error
            values = _reported_numbers(row)
            periods = cast("list[str]", recipe["periods"])
            if len(values) < len(periods):
                msg = f"source row has fewer values than configured periods: {raw_label}"
                raise PublicSourceError(msg)
            for index, period_end_text in enumerate(periods):
                quarter = by_period.get(period_end_text)
                if quarter is None:
                    msg = f"source recipe references an unconfigured period: {period_end_text}"
                    raise PublicSourceError(msg)
                raw_value = values[index]
                normalized_value = normalize_reported_value(
                    raw_value,
                    rule=str(recipe["normalization"]),
                )
                metric_id = str(recipe["metric_id"])
                candidate_material = (
                    f"{source.content_sha256}:{metric_id}:{period_end_text}:{raw_value}"
                )
                candidate_id = hashlib.sha256(candidate_material.encode()).hexdigest()[:32]
                unit = str(recipe["canonical_unit"])
                is_duration = str(recipe["period_type"]) == "duration"
                extracted.append(
                    ParsedObservationCandidate(
                        candidate_id=candidate_id,
                        company_id=source.company_id,
                        metric_id=metric_id,
                        metric_version="1.0.0",
                        period_start=(
                            datetime.fromisoformat(str(quarter["period_start"])).date()
                            if is_duration
                            else None
                        ),
                        period_end=datetime.fromisoformat(period_end_text).date(),
                        fiscal_year=int(quarter["fiscal_year"]),
                        fiscal_quarter=int(quarter["fiscal_quarter"]),
                        period_type=str(recipe["period_type"]),
                        raw_label=raw_label,
                        raw_value=raw_value,
                        normalized_value=normalized_value,
                        currency="USD" if unit == "USD" else None,
                        unit=unit,
                        reported_scale=str(recipe["reported_scale"]),
                        reported_decimals=decimal_places(raw_value),
                        observation_state=ObservationState.REPORTED_ACTUAL,
                        methodology=str(recipe["methodology"]),
                        reporting_entity_id=str(company["reporting_entity"]),
                        reporting_scope_id=str(company["reporting_scope"]),
                        evidence_id=f"evidence:{source.key}",
                        evidence_locator=(
                            f"{source.locator}; row '{raw_label}'; "
                            f"period-end column {period_end_text}"
                        ),
                        extraction_method="deterministic_html_table",
                        parser_name=source.parser_name,
                        parser_version=source.parser_version,
                    )
                )
        return tuple(extracted)


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

    @staticmethod
    def _metadata_path(cache_path: Path) -> Path:
        return cache_path.with_suffix(".json")

    def acquire(self, url: str) -> AcquiredDocument:
        """Fetch an official SEC URL under bounded, identified access."""
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host not in {"www.sec.gov", "data.sec.gov"}:
            msg = "SEC acquisition permits only official HTTPS SEC hosts"
            raise ValueError(msg)
        cache_path = self._cache_key(url)
        metadata_path = self._metadata_path(cache_path)
        if cache_path.is_file() and metadata_path.is_file():
            content = cache_path.read_bytes()
            metadata = cast("dict[str, Any]", json.loads(metadata_path.read_text(encoding="utf-8")))
            digest = hashlib.sha256(content).hexdigest()
            if digest == metadata.get("sha256") and len(content) == metadata.get("byte_length"):
                return AcquiredDocument(
                    url=url,
                    content=content,
                    media_type=str(metadata["media_type"]),
                    sha256=digest,
                    cache_path=cache_path,
                    status_code=int(metadata["status_code"]),
                    etag=cast("str | None", metadata.get("etag")),
                    last_modified=cast("str | None", metadata.get("last_modified")),
                )
            cache_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

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
            digest = hashlib.sha256(content).hexdigest()
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(cache_path)
            metadata = {
                "url": url,
                "media_type": response.headers.get("content-type", "application/octet-stream"),
                "sha256": digest,
                "byte_length": len(content),
                "status_code": response.status_code,
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            }
            metadata_path.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            return AcquiredDocument(
                url=url,
                content=content,
                media_type=str(metadata["media_type"]),
                sha256=digest,
                cache_path=cache_path,
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
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
