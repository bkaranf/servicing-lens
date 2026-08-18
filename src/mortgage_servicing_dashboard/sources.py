"""Governed acquisition, immutable retention, and recorded-document parsing."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

from mortgage_servicing_dashboard.domain import (
    ObservationState,
    ParsedObservationCandidate,
    decimal_places,
    normalize_reported_value,
)

_NUMERIC_CELL = re.compile(r"^-?\(?\d[\d,]*(?:\.\d+)?\)?$")
_REPORTED_DASHES = frozenset({"-", "\u2014", "\u2013", "\u2212"})


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
        self.qualified_rows: list[tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._prior_rows: list[tuple[str, ...]] = []

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
                self.qualified_rows.append((tuple(self._prior_rows), nonempty))
                self._prior_rows.append(nonempty)
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
        values.append(candidate)
    return tuple(values)


def _reported_numeric_or_dash_cells(cells: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for cell in cells[1:]:
        candidate = cell.strip().replace("\N{MINUS SIGN}", "-")
        if candidate in _REPORTED_DASHES:
            values.append(cell.strip())
            continue
        if not _NUMERIC_CELL.fullmatch(candidate):
            continue
        values.append(candidate)
    return tuple(values)


def _quarter_map(quarters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(quarter["period_end"]): quarter for quarter in quarters}


def _qualified_row_matches(
    collector: _TableRows,
    *,
    raw_label: str,
    recipe: dict[str, Any],
) -> list[tuple[str, ...]]:
    table_anchor = str(recipe.get("table_anchor", "")).strip()
    column_headers = tuple(str(value) for value in recipe.get("column_headers", []))
    matches: list[tuple[str, ...]] = []
    for prior_rows, row in collector.qualified_rows:
        if row[0] != raw_label:
            continue
        prior_text = " | ".join(" | ".join(item) for item in prior_rows)
        if table_anchor and all(part not in prior_text for part in table_anchor.split(" | ")):
            continue
        if column_headers and any(header not in prior_text for header in column_headers):
            continue
        matches.append(row)
    return matches


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

    @staticmethod
    def _configured_text_value(
        *,
        text: str,
        recipe: dict[str, Any],
        raw_label: str,
    ) -> str | None:
        pattern = recipe.get("text_value_pattern")
        if pattern is None:
            return None
        label_prefix = str(recipe.get("raw_label_prefix", "")).strip()
        if not label_prefix or label_prefix != raw_label or label_prefix not in text:
            msg = f"configured text label prefix was not found: {raw_label}"
            raise PublicSourceError(msg)
        compiled = re.compile(str(pattern), flags=re.IGNORECASE)
        matches = list(compiled.finditer(text))
        occurrence = int(recipe.get("text_occurrence", 0))
        try:
            matched = matches[occurrence]
            value = matched.group("value")
        except (IndexError, KeyError) as error:
            msg = f"configured text value was not uniquely selected: {raw_label}"
            raise PublicSourceError(msg) from error
        if not _NUMERIC_CELL.fullmatch(value):
            msg = f"configured text value is not exact numeric text: {raw_label}"
            raise PublicSourceError(msg)
        return value

    @staticmethod
    def _configured_row_value(
        *,
        row: tuple[str, ...],
        recipe: dict[str, Any],
        period_index: int,
        raw_label: str,
    ) -> str:
        values = _reported_numeric_or_dash_cells(row)
        configured_indices = recipe.get("value_indices")
        if configured_indices is not None:
            indices = cast("list[int]", configured_indices)
            try:
                value_index = indices[period_index]
            except IndexError as error:
                msg = f"configured value index is absent for source row: {raw_label}"
                raise PublicSourceError(msg) from error
        else:
            explicit_index = recipe.get("value_index")
            if explicit_index is not None:
                value_index = int(explicit_index)
            else:
                offset = int(recipe.get("value_offset", 0))
                value_index = period_index + offset
        try:
            return values[value_index]
        except IndexError as error:
            msg = f"source row has fewer selected values than configured periods: {raw_label}"
            raise PublicSourceError(msg) from error

    @staticmethod
    def _apply_sign_normalization(raw_value: str, *, rule: str) -> Decimal:
        normalization_rule, _, sign_rule = rule.partition(":")
        normalized = normalize_reported_value(raw_value, rule=normalization_rule)
        if sign_rule in {
            "positive_reduction_magnitude",
            "reported_negative_to_positive_reduction_magnitude",
        }:
            return abs(normalized)
        if sign_rule in {
            "",
            "preserve",
            "preserve_positive_balance",
            "preserve_positive_increase",
            "preserve_reported_sign",
        }:
            return normalized
        msg = f"unsupported sign normalization rule: {sign_rule}"
        raise PublicSourceError(msg)

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
            text_value = self._configured_text_value(
                text=text,
                recipe=recipe,
                raw_label=raw_label,
            )
            row: tuple[str, ...] | None = None
            if text_value is None:
                matches = _qualified_row_matches(
                    collector,
                    raw_label=raw_label,
                    recipe=recipe,
                )
                if not matches:
                    msg = f"configured source row was not found: {raw_label}"
                    raise PublicSourceError(msg)
                occurrence = int(recipe.get("row_occurrence", 0))
                try:
                    row = matches[occurrence]
                except IndexError as error:
                    msg = f"configured source-row occurrence was not found: {raw_label}"
                    raise PublicSourceError(msg) from error
            periods = cast("list[str]", recipe["periods"])
            for index, period_end_text in enumerate(periods):
                quarter = by_period.get(period_end_text)
                if quarter is None:
                    msg = f"source recipe references an unconfigured period: {period_end_text}"
                    raise PublicSourceError(msg)
                raw_value = (
                    text_value
                    if text_value is not None
                    else self._configured_row_value(
                        row=cast("tuple[str, ...]", row),
                        recipe=recipe,
                        period_index=index,
                        raw_label=raw_label,
                    )
                )
                normalization = str(recipe["normalization"])
                sign_rule = str(recipe.get("sign_normalization", "preserve"))
                if raw_value in _REPORTED_DASHES:
                    dash_policy = recipe.get("dash_policy")
                    dash_normalization = recipe.get("dash_normalization")
                    if (
                        dash_policy != "PUBLISH_ZERO_ONLY_WHEN_ROW_PRESENTS_EM_DASH"
                        and dash_normalization != "exact_reported_zero"
                    ):
                        msg = f"reported dash lacks an explicit measured-zero policy: {raw_label}"
                        raise PublicSourceError(msg)
                    normalized_value = Decimal(0)
                else:
                    normalized_value = self._apply_sign_normalization(
                        raw_value,
                        rule=f"{normalization}:{sign_rule}",
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
