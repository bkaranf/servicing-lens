"""Governed acquisition, immutable retention, and recorded-document parsing."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Self, cast

import httpx

from mortgage_servicing_dashboard.config import validate_sec_user_agent
from mortgage_servicing_dashboard.domain import (
    ObservationState,
    ParsedObservationCandidate,
    decimal_places,
    normalize_reported_value,
)

_NUMERIC_CELL = re.compile(r"^-?\(?\d[\d,]*(?:\.\d+)?\)?$")
_SEC_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SEC_DOCUMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_OFFICIAL_SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
_RETRYABLE_SEC_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_SEC_ATTEMPTS = 5
_MAX_SEC_RESPONSE_BYTES = 25_000_000
_MAX_DISCOVERED_FILINGS = 256
_MAX_CIK_DIGITS = 10
_HTTP_NOT_MODIFIED = 304


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    retrieved_at: datetime = dataclass_field(default_factory=_utc_now)

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
    retained_at: datetime = dataclass_field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class SecFilingMetadata:
    """Bounded filing identity parsed from an official SEC submissions index."""

    company_id: str
    cik: str
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    acceptance_at: datetime | None
    primary_document: str
    primary_document_url: str
    items: tuple[str, ...]
    is_xbrl: bool
    is_inline_xbrl: bool

    def as_payload(self) -> dict[str, object]:
        """Return allow-listed filing metadata suitable for CLI output."""
        return {
            "company_id": self.company_id,
            "cik": self.cik,
            "accession": self.accession,
            "form": self.form,
            "filing_date": self.filing_date.isoformat(),
            "report_date": self.report_date.isoformat() if self.report_date else None,
            "acceptance_at": self.acceptance_at.isoformat() if self.acceptance_at else None,
            "primary_document": self.primary_document,
            "primary_document_url": self.primary_document_url,
            "items": list(self.items),
            "is_xbrl": self.is_xbrl,
            "is_inline_xbrl": self.is_inline_xbrl,
        }


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


@dataclass(frozen=True, slots=True)
class LiveSecAcquisition:
    """One governed live SEC response prepared for deterministic publication."""

    configured_source_key: str
    source_key: str
    company_id: str
    cik: str
    accession: str
    discovered_filing: SecFilingMetadata
    acquired_document: AcquiredDocument
    retained_document: RetainedDocument
    runtime_definition: RecordedSourceDefinition
    acquired_at: datetime
    retained_at: datetime
    cache_path: Path
    retention_path: Path

    def as_payload(self) -> dict[str, object]:
        """Return bounded acquisition identity without raw bytes or local paths."""
        return {
            "configured_source_key": self.configured_source_key,
            "source_key": self.source_key,
            "company_id": self.company_id,
            "cik": self.cik,
            "accession": self.accession,
            "sha256": self.acquired_document.sha256,
            "byte_length": self.acquired_document.byte_length,
            "media_type": self.acquired_document.media_type,
            "representation": self.runtime_definition.representation,
            "response_status": self.acquired_document.status_code,
            "etag": self.acquired_document.etag,
            "last_modified": self.acquired_document.last_modified,
            "acquired_at": self.acquired_at.isoformat(),
            "retained_at": self.retained_at.isoformat(),
        }


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


def normalize_sec_cik(value: str) -> str:
    """Return the ten-digit SEC submissions CIK representation.

    Args:
        value: Configured numeric CIK, with or without leading zeroes.

    Returns:
        A ten-digit CIK suitable for the submissions endpoint.

    Raises:
        ValueError: If the value is not a one-to-ten digit public identifier.
    """
    normalized = value.strip()
    if (
        not normalized.isascii()
        or not normalized.isdigit()
        or not 1 <= len(normalized) <= _MAX_CIK_DIGITS
    ):
        msg = "SEC CIK must contain one to ten ASCII digits"
        raise ValueError(msg)
    return normalized.zfill(10)


def sec_submissions_url(cik: str) -> str:
    """Build the official submissions-index URL for a configured CIK."""
    return f"https://data.sec.gov/submissions/CIK{normalize_sec_cik(cik)}.json"


def _require_official_sec_url(url: str) -> httpx.URL:
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host not in _OFFICIAL_SEC_HOSTS:
        msg = "SEC acquisition permits only official HTTPS SEC hosts"
        raise ValueError(msg)
    if parsed.username or parsed.password:
        msg = "SEC acquisition URL must not contain user information"
        raise ValueError(msg)
    return parsed


def _parse_optional_date(value: object, *, field_name: str) -> date | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        msg = f"SEC submissions {field_name} is not an ISO date"
        raise PublicSourceError(msg) from error


def _parse_optional_instant(value: object) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        msg = "SEC submissions acceptanceDateTime is invalid"
        raise PublicSourceError(msg) from error
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _submission_column(
    recent: Mapping[str, object],
    name: str,
    *,
    expected_length: int,
    required: bool,
) -> Sequence[object]:
    value = recent.get(name)
    if value is None and not required:
        return ("",) * expected_length
    if not isinstance(value, list) or len(value) != expected_length:
        msg = f"SEC submissions column is missing or misaligned: {name}"
        raise PublicSourceError(msg)
    return cast("list[object]", value)


@dataclass(frozen=True, slots=True)
class _SecRecentColumns:
    accessions: Sequence[object]
    filing_dates: Sequence[object]
    report_dates: Sequence[object]
    acceptance_instants: Sequence[object]
    forms: Sequence[object]
    primary_documents: Sequence[object]
    items: Sequence[object]
    is_xbrl: Sequence[object]
    is_inline_xbrl: Sequence[object]


def _recent_columns(document: AcquiredDocument, expected_cik: str) -> _SecRecentColumns:
    if document.url != sec_submissions_url(expected_cik):
        msg = "SEC submissions response URL does not match the configured CIK"
        raise PublicSourceError(msg)
    try:
        payload = json.loads(document.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "SEC submissions response is not valid UTF-8 JSON"
        raise PublicSourceError(msg) from error
    if not isinstance(payload, dict):
        msg = "SEC submissions response must be a JSON object"
        raise PublicSourceError(msg)
    try:
        response_cik = normalize_sec_cik(str(payload.get("cik", "")))
    except ValueError as error:
        msg = "SEC submissions response contains an invalid CIK"
        raise PublicSourceError(msg) from error
    if response_cik != expected_cik:
        msg = "SEC submissions response CIK does not match the configured company"
        raise PublicSourceError(msg)
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        msg = "SEC submissions response lacks the recent filing index"
        raise PublicSourceError(msg)
    recent_mapping = cast("Mapping[str, object]", recent)
    accession_values = recent_mapping.get("accessionNumber")
    if not isinstance(accession_values, list):
        msg = "SEC submissions response lacks accession numbers"
        raise PublicSourceError(msg)
    count = len(accession_values)

    def column(name: str, *, required: bool = False) -> Sequence[object]:
        return _submission_column(
            recent_mapping,
            name,
            expected_length=count,
            required=required,
        )

    return _SecRecentColumns(
        accessions=cast("list[object]", accession_values),
        filing_dates=column("filingDate", required=True),
        report_dates=column("reportDate", required=True),
        acceptance_instants=column("acceptanceDateTime"),
        forms=column("form", required=True),
        primary_documents=column("primaryDocument", required=True),
        items=column("items"),
        is_xbrl=column("isXBRL"),
        is_inline_xbrl=column("isInlineXBRL"),
    )


def _parse_sec_filing_row(
    *,
    columns: _SecRecentColumns,
    index: int,
    company_id: str,
    cik: str,
) -> SecFilingMetadata:
    accession = str(columns.accessions[index]).strip()
    if not _SEC_ACCESSION.fullmatch(accession):
        msg = "SEC submissions response contains an invalid accession"
        raise PublicSourceError(msg)
    filing_date = _parse_optional_date(columns.filing_dates[index], field_name="filingDate")
    if filing_date is None:
        msg = "SEC submissions filingDate must not be blank"
        raise PublicSourceError(msg)
    primary_document = str(columns.primary_documents[index]).strip()
    if not _SEC_DOCUMENT_NAME.fullmatch(primary_document):
        msg = "SEC submissions primary document name is invalid"
        raise PublicSourceError(msg)
    raw_items = str(columns.items[index]).strip()
    items = tuple(item.strip() for item in raw_items.split(",") if item.strip())[:32]
    primary_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession.replace('-', '')}/{primary_document}"
    )
    return SecFilingMetadata(
        company_id=company_id,
        cik=cik,
        accession=accession,
        form=str(columns.forms[index]).strip(),
        filing_date=filing_date,
        report_date=_parse_optional_date(columns.report_dates[index], field_name="reportDate"),
        acceptance_at=_parse_optional_instant(columns.acceptance_instants[index]),
        primary_document=primary_document,
        primary_document_url=primary_url,
        items=items,
        is_xbrl=str(columns.is_xbrl[index]).strip().lower() in {"1", "true"},
        is_inline_xbrl=(str(columns.is_inline_xbrl[index]).strip().lower() in {"1", "true"}),
    )


def parse_sec_submissions(  # noqa: PLR0913
    *,
    document: AcquiredDocument,
    company_id: str,
    cik: str,
    forms: Collection[str] | None = None,
    filed_on_or_after: date | None = None,
    max_filings: int = 100,
) -> tuple[SecFilingMetadata, ...]:
    """Parse a bounded filing list from one official submissions response.

    Args:
        document: Exact response bytes from the official submissions endpoint.
        company_id: Stable configured company identifier.
        cik: Configured SEC CIK expected in the response.
        forms: Optional exact form allow-list applied before bounding results.
        filed_on_or_after: Optional inclusive filing-date lower bound.
        max_filings: Maximum metadata records returned from this response.

    Returns:
        Filing metadata in the SEC response's newest-first order.

    Raises:
        PublicSourceError: If the response identity or aligned columns are invalid.
        ValueError: If the caller requests an invalid bound.
    """
    if not 1 <= max_filings <= _MAX_DISCOVERED_FILINGS:
        msg = f"SEC discovery max_filings must be within 1..{_MAX_DISCOVERED_FILINGS}"
        raise ValueError(msg)
    expected_cik = normalize_sec_cik(cik)
    columns = _recent_columns(document, expected_cik)
    allowed_forms = frozenset(forms) if forms is not None else None
    discovered: list[SecFilingMetadata] = []
    for index in range(len(columns.accessions)):
        raw_form = str(columns.forms[index]).strip()
        if allowed_forms is not None and raw_form not in allowed_forms:
            continue
        raw_filing_date = _parse_optional_date(
            columns.filing_dates[index],
            field_name="filingDate",
        )
        if raw_filing_date is None:
            msg = "SEC submissions filingDate must not be blank"
            raise PublicSourceError(msg)
        if filed_on_or_after is not None and raw_filing_date < filed_on_or_after:
            continue
        filing = _parse_sec_filing_row(
            columns=columns,
            index=index,
            company_id=company_id,
            cik=expected_cik,
        )
        discovered.append(filing)
        if len(discovered) == max_filings:
            break
    return tuple(discovered)


def live_sec_source_key(*, configured_key: str, accession: str, sha256: str) -> str:
    """Build an immutable live source identity from accession and response bytes."""
    if not _SEC_ACCESSION.fullmatch(accession):
        msg = "live SEC source key requires a valid accession"
        raise ValueError(msg)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        msg = "live SEC source key requires a lowercase SHA-256 digest"
        raise ValueError(msg)
    return f"{configured_key}--{accession.replace('-', '')}--{sha256[:12]}"


def prepare_live_sec_acquisition(
    *,
    source: RecordedSourceDefinition,
    cik: str,
    discovered_filing: SecFilingMetadata,
    acquired_document: AcquiredDocument,
    retained_document: RetainedDocument,
) -> LiveSecAcquisition:
    """Bind exact retained response bytes to a deterministic runtime recipe.

    Args:
        source: Configured nonnumeric parsing recipe.
        cik: CIK configured for the source's company.
        discovered_filing: Filing identity found in that CIK's submissions index.
        acquired_document: Exact HTTP response returned for the configured source URL.
        retained_document: Verified content-addressed retention result.

    Returns:
        A content-specific acquisition safe to hand to deterministic publication.

    Raises:
        PublicSourceError: If discovery, source identity, or retained bytes disagree.
    """
    normalized_cik = normalize_sec_cik(cik)
    if (
        discovered_filing.company_id != source.company_id
        or discovered_filing.cik != normalized_cik
        or discovered_filing.accession != source.accession
    ):
        msg = "live SEC filing identity does not match the configured source"
        raise PublicSourceError(msg)
    _require_official_sec_url(source.url)
    if acquired_document.url != source.url:
        msg = "live SEC response URL does not match the configured source"
        raise PublicSourceError(msg)
    if (
        acquired_document.sha256 != retained_document.sha256
        or acquired_document.byte_length != retained_document.byte_length
        or retained_document.path.read_bytes() != acquired_document.content
    ):
        msg = "live SEC retained response failed exact replay verification"
        raise PublicSourceError(msg)
    source_key = live_sec_source_key(
        configured_key=source.key,
        accession=source.accession,
        sha256=acquired_document.sha256,
    )
    runtime_definition = replace(
        source,
        key=source_key,
        media_type=acquired_document.media_type,
        representation="ORIGINAL_HTTP_RESPONSE",
        capture_method="sec_http_get",
        fixture_path=retained_document.path,
        byte_length=acquired_document.byte_length,
        content_sha256=acquired_document.sha256,
    )
    return LiveSecAcquisition(
        configured_source_key=source.key,
        source_key=source_key,
        company_id=source.company_id,
        cik=normalized_cik,
        accession=source.accession,
        discovered_filing=discovered_filing,
        acquired_document=acquired_document,
        retained_document=retained_document,
        runtime_definition=runtime_definition,
        acquired_at=acquired_document.retrieved_at,
        retained_at=retained_document.retained_at,
        cache_path=acquired_document.cache_path,
        retention_path=retained_document.path,
    )


class SecClient:
    """SEC-compliant client with identity, throttling, retries, and local cache."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        user_agent: str,
        cache_directory: Path,
        minimum_interval_seconds: float = 0.2,
        max_attempts: int = 3,
        max_response_bytes: int = _MAX_SEC_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        """Configure a single-threaded, bounded, cache-first SEC client."""
        identity = validate_sec_user_agent(user_agent)
        if (
            minimum_interval_seconds < 0
            or not 1 <= max_attempts <= _MAX_SEC_ATTEMPTS
            or not 1 <= max_response_bytes <= _MAX_SEC_RESPONSE_BYTES
        ):
            msg = "SEC throttle, response, and retry settings must be positive and bounded"
            raise ValueError(msg)
        cache_directory.mkdir(parents=True, exist_ok=True)
        self._cache_directory = cache_directory.resolve()
        self._minimum_interval = minimum_interval_seconds
        self._max_attempts = max_attempts
        self._max_response_bytes = max_response_bytes
        self._last_request_at = 0.0
        self._sleep = sleep
        self._monotonic = monotonic
        secure_random = random.SystemRandom()
        self._jitter = jitter or (lambda maximum: secure_random.uniform(0.0, maximum))
        self._client = httpx.Client(
            headers={"User-Agent": identity, "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
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

    @staticmethod
    def _discard_cache(cache_path: Path, metadata_path: Path) -> None:
        cache_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)

    def _cached_document(
        self,
        *,
        url: str,
        cache_path: Path,
        metadata_path: Path,
    ) -> AcquiredDocument | None:
        if not cache_path.is_file() or not metadata_path.is_file():
            self._discard_cache(cache_path, metadata_path)
            return None
        try:
            content = cache_path.read_bytes()
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._discard_cache(cache_path, metadata_path)
            return None
        if not isinstance(loaded, dict):
            self._discard_cache(cache_path, metadata_path)
            return None
        metadata = cast("dict[str, object]", loaded)
        digest = hashlib.sha256(content).hexdigest()
        try:
            retrieved_at = _parse_optional_instant(metadata.get("retrieved_at", ""))
            media_type = str(metadata["media_type"])
            status_code = int(cast("int", metadata["status_code"]))
        except (KeyError, PublicSourceError, TypeError, ValueError):
            self._discard_cache(cache_path, metadata_path)
            return None
        valid = (
            metadata.get("url") == url
            and digest == metadata.get("sha256")
            and len(content) == metadata.get("byte_length")
            and retrieved_at is not None
        )
        if not valid or retrieved_at is None:
            self._discard_cache(cache_path, metadata_path)
            return None
        return AcquiredDocument(
            url=url,
            content=content,
            media_type=media_type,
            sha256=digest,
            cache_path=cache_path,
            status_code=status_code,
            etag=cast("str | None", metadata.get("etag")),
            last_modified=cast("str | None", metadata.get("last_modified")),
            retrieved_at=retrieved_at,
        )

    def _throttle(self) -> None:
        elapsed = self._monotonic() - self._last_request_at
        if elapsed < self._minimum_interval:
            self._sleep(self._minimum_interval - elapsed)
        self._last_request_at = self._monotonic()

    def _retry_delay(self, attempt: int) -> None:
        base = min(0.5 * (2**attempt), 4.0)
        self._sleep(base + self._jitter(min(base * 0.25, 0.5)))

    def _write_cache(
        self,
        *,
        document: AcquiredDocument,
        metadata_path: Path,
    ) -> None:
        cache_path = document.cache_path
        suffix = f".{os.getpid()}.{time.time_ns()}.tmp"
        content_temporary = cache_path.with_name(f".{cache_path.name}{suffix}")
        metadata_temporary = metadata_path.with_name(f".{metadata_path.name}{suffix}")
        metadata = {
            "url": document.url,
            "media_type": document.media_type,
            "sha256": document.sha256,
            "byte_length": document.byte_length,
            "status_code": document.status_code,
            "etag": document.etag,
            "last_modified": document.last_modified,
            "retrieved_at": document.retrieved_at.isoformat(),
        }
        try:
            content_temporary.write_bytes(document.content)
            metadata_temporary.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            content_temporary.replace(cache_path)
            metadata_temporary.replace(metadata_path)
        except OSError as error:
            msg = "SEC response cache could not be written"
            raise PublicSourceError(msg) from error
        finally:
            content_temporary.unlink(missing_ok=True)
            metadata_temporary.unlink(missing_ok=True)

    @staticmethod
    def _conditional_headers(cached: AcquiredDocument | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if cached is None:
            return headers
        if cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        return headers

    def _request_with_retry(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        cached: AcquiredDocument | None,
    ) -> httpx.Response | AcquiredDocument:
        last_error: httpx.RequestError | None = None
        for attempt in range(self._max_attempts):
            self._throttle()
            try:
                response = self._client.get(url, headers=headers)
            except httpx.RequestError as error:
                last_error = error
                if attempt + 1 < self._max_attempts:
                    self._retry_delay(attempt)
                    continue
                break
            if response.status_code == _HTTP_NOT_MODIFIED:
                if cached is None:
                    msg = "SEC returned not-modified without a verified cached response"
                    raise PublicSourceError(msg)
                return cached
            if response.is_redirect:
                msg = "SEC acquisition rejected an HTTP redirect"
                raise PublicSourceError(msg)
            if response.status_code not in _RETRYABLE_SEC_STATUS_CODES:
                return response
            if attempt + 1 < self._max_attempts:
                self._retry_delay(attempt)
        msg = "SEC acquisition failed after bounded retries"
        raise PublicSourceError(msg) from last_error

    def _document_from_response(
        self,
        *,
        url: str,
        cache_path: Path,
        response: httpx.Response,
    ) -> AcquiredDocument:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            msg = "SEC acquisition returned a permanent HTTP error"
            raise PublicSourceError(msg) from error
        declared_length = response.headers.get("content-length")
        try:
            declared_bytes = int(declared_length) if declared_length is not None else None
        except ValueError as error:
            msg = "SEC response Content-Length is invalid"
            raise PublicSourceError(msg) from error
        if declared_bytes is not None and declared_bytes > self._max_response_bytes:
            msg = "SEC response exceeds the configured byte bound"
            raise PublicSourceError(msg)
        content = response.content
        if not content or len(content) > self._max_response_bytes:
            msg = "SEC response is empty or exceeds the configured byte bound"
            raise PublicSourceError(msg)
        media_type = response.headers.get("content-type", "application/octet-stream")
        return AcquiredDocument(
            url=url,
            content=content,
            media_type=media_type.split(";", maxsplit=1)[0].strip().lower(),
            sha256=hashlib.sha256(content).hexdigest(),
            cache_path=cache_path,
            status_code=response.status_code,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            retrieved_at=_utc_now(),
        )

    def acquire(self, url: str, *, refresh: bool = False) -> AcquiredDocument:
        """Fetch an official SEC URL under bounded, identified access.

        Args:
            url: Official HTTPS SEC resource.
            refresh: Revalidate a cached response using available HTTP validators.

        Returns:
            Exact response bytes plus safe response and cache metadata.

        Raises:
            PublicSourceError: If acquisition fails, redirects, or exceeds its bounds.
            ValueError: If the URL is outside the official SEC boundary.
        """
        _require_official_sec_url(url)
        cache_path = self._cache_key(url)
        metadata_path = self._metadata_path(cache_path)
        cached = self._cached_document(
            url=url,
            cache_path=cache_path,
            metadata_path=metadata_path,
        )
        if cached is not None and not refresh:
            return cached
        response_or_cache = self._request_with_retry(
            url=url,
            headers=self._conditional_headers(cached),
            cached=cached,
        )
        if isinstance(response_or_cache, AcquiredDocument):
            return response_or_cache
        document = self._document_from_response(
            url=url,
            cache_path=cache_path,
            response=response_or_cache,
        )
        self._write_cache(document=document, metadata_path=metadata_path)
        return document
