"""Deterministic FFIEC, FR Y-9C, and NIC regulatory-data adapters."""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlparse

import yaml

from mortgage_servicing_dashboard.domain import parse_decimal

EnumType = TypeVar("EnumType", bound=StrEnum)
_CIK_MAX_DIGITS = 10
_REGULATORY_HOSTS = {"cdr.ffiec.gov", "www.ffiec.gov", "www.federalreserve.gov"}
_SOURCE_DATE_FORMATS = {"%Y%m%d", "%Y-%m-%d"}


class RegulatoryDataError(RuntimeError):
    """Fail-closed regulatory configuration, identity, or parsing error."""


class RegulatorySourceFamily(StrEnum):
    """Supported official regulatory report families."""

    FFIEC_CDR_CALL = "FFIEC_CDR_CALL"
    FR_Y9C = "FR_Y9C"


class RegulatoryReportingScope(StrEnum):
    """Regulatory consolidation boundary attached to a fact."""

    BANK_HOLDING_COMPANY_REGULATORY = "BANK_HOLDING_COMPANY_REGULATORY"
    DEPOSITORY_INSTITUTION_REGULATORY = "DEPOSITORY_INSTITUTION_REGULATORY"


class RegulatoryReporterType(StrEnum):
    """Native regulatory reporter type."""

    BANK_HOLDING_COMPANY = "BANK_HOLDING_COMPANY"
    INSURED_DEPOSITORY_INSTITUTION = "INSURED_DEPOSITORY_INSTITUTION"


@dataclass(frozen=True, slots=True)
class RegulatoryReporter:
    """Effective-dated identity for one native regulatory reporter."""

    rssd_id: str
    company_id: str
    legal_name: str
    reporting_entity_id: str
    reporting_scope_id: str
    reporter_type: RegulatoryReporterType
    reporting_scope: RegulatoryReportingScope
    permitted_source_families: tuple[RegulatorySourceFamily, ...]
    nic_profile_url: str
    valid_from: date
    valid_to: date | None
    ticker: str | None = None
    cik: str | None = None
    parent_rssd_id: str | None = None

    def is_effective(self, report_date: date) -> bool:
        """Return whether this reporter identity applies on a report date."""
        return self.valid_from <= report_date and (
            self.valid_to is None or report_date < self.valid_to
        )


@dataclass(frozen=True, slots=True)
class RegulatorySourceDefinition:
    """Versioned shape and provenance for one delimited regulatory source."""

    family: RegulatorySourceFamily
    delimiter: str
    rssd_column: str
    report_date_column: str
    report_date_format: str
    revision_column: str
    source_url: str


@dataclass(frozen=True, slots=True)
class RegulatoryItemMapping:
    """Effective-dated source-series mapping to a target servicing metric."""

    source_family: RegulatorySourceFamily
    series: str
    schedule: str
    item: str
    metric_id: str
    component: str
    unit: str
    scale: str
    period_type: str
    mapping_revision: str
    valid_from: date
    valid_to: date | None
    definition_url: str

    def is_effective(self, report_date: date) -> bool:
        """Return whether this mapping applies on a report date."""
        return self.valid_from <= report_date and (
            self.valid_to is None or report_date < self.valid_to
        )


@dataclass(frozen=True, slots=True)
class RegulatoryConfig:
    """Typed versioned regulatory reporter, source, and item configuration."""

    version: str
    reporters: tuple[RegulatoryReporter, ...]
    sources: tuple[RegulatorySourceDefinition, ...]
    mappings: tuple[RegulatoryItemMapping, ...]

    def reporter(
        self,
        *,
        rssd_id: str,
        source_family: RegulatorySourceFamily,
        report_date: date,
    ) -> RegulatoryReporter:
        """Resolve exactly one effective reporter without crossing scopes.

        Args:
            rssd_id: Native RSSD identifier from the source row.
            source_family: Regulatory report family being parsed.
            report_date: Report date used for effective-time resolution.

        Returns:
            The uniquely configured native reporter.

        Raises:
            RegulatoryDataError: If the reporter is absent, expired, or belongs to
                a different source family.
        """
        normalized = _normalize_rssd(rssd_id)
        matches = [item for item in self.reporters if item.rssd_id == normalized]
        if len(matches) != 1:
            message = f"RSSD {normalized} is not uniquely configured"
            raise RegulatoryDataError(message)
        reporter = matches[0]
        if not reporter.is_effective(report_date):
            message = f"RSSD {normalized} is not effective on {report_date.isoformat()}"
            raise RegulatoryDataError(message)
        if source_family not in reporter.permitted_source_families:
            message = (
                f"RSSD {normalized} cannot report {source_family.value}; "
                f"configured scope is {reporter.reporting_scope.value}"
            )
            raise RegulatoryDataError(message)
        return reporter

    def source(self, family: RegulatorySourceFamily) -> RegulatorySourceDefinition:
        """Return the unique source definition for a family."""
        matches = [item for item in self.sources if item.family is family]
        if len(matches) != 1:
            message = f"source family {family.value} is not uniquely configured"
            raise RegulatoryDataError(message)
        return matches[0]

    def item_mappings(
        self,
        *,
        family: RegulatorySourceFamily,
        report_date: date,
    ) -> tuple[RegulatoryItemMapping, ...]:
        """Return effective item mappings for a source family and date."""
        return tuple(
            item
            for item in self.mappings
            if item.source_family is family and item.is_effective(report_date)
        )


@dataclass(frozen=True, slots=True)
class RegulatoryFact:
    """One exact source value attached to its native reporter and scope."""

    rssd_id: str
    reporter_name: str
    reporting_entity_id: str
    reporting_scope_id: str
    reporting_scope: RegulatoryReportingScope
    source_family: RegulatorySourceFamily
    report_date: date
    schedule: str
    item: str
    series: str
    metric_id: str
    component: str
    period_type: str
    unit: str
    scale: str
    revision: str
    raw_value: str
    normalized_value: Decimal
    source_url: str
    locator: str
    mapping_revision: str

    @property
    def fact_id(self) -> str:
        """Return a stable identity for this reporter-scoped source fact."""
        material = (
            f"{self.source_family.value}|{self.rssd_id}|{self.report_date.isoformat()}|"
            f"{self.series}|{self.revision}|{self.raw_value}|{self.mapping_revision}"
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RegulatoryMetricValue:
    """Exact aggregate of compatible source components for one metric."""

    rssd_id: str
    reporting_entity_id: str
    reporting_scope: RegulatoryReportingScope
    source_family: RegulatorySourceFamily
    report_date: date
    metric_id: str
    unit: str
    value: Decimal
    input_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NicIdentityRecord:
    """One effective-dated NIC-style ticker, CIK, and RSSD crosswalk row."""

    rssd_id: str
    institution_name: str
    reporter_type: RegulatoryReporterType
    reporting_scope: RegulatoryReportingScope
    valid_from: date
    valid_to: date | None
    nic_profile_url: str
    ticker: str | None = None
    cik: str | None = None
    parent_rssd_id: str | None = None

    def is_effective(self, as_of: date) -> bool:
        """Return whether the identity row applies on the requested date."""
        return self.valid_from <= as_of and (self.valid_to is None or as_of < self.valid_to)


class NicIdentityCrosswalk:
    """Deterministic resolver for NIC-style public identity rows."""

    def __init__(self, records: tuple[NicIdentityRecord, ...]) -> None:
        """Create a crosswalk after checking RSSD validity intervals."""
        if not records:
            message = "NIC crosswalk must contain at least one record"
            raise RegulatoryDataError(message)
        ordered = sorted(records, key=lambda item: (item.rssd_id, item.valid_from))
        for left, right in pairwise(ordered):
            if left.rssd_id != right.rssd_id:
                continue
            if left.valid_to is None or right.valid_from < left.valid_to:
                message = f"NIC crosswalk has overlapping rows for RSSD {left.rssd_id}"
                raise RegulatoryDataError(message)
        self._records = tuple(records)

    @classmethod
    def from_csv(cls, content: bytes) -> NicIdentityCrosswalk:
        """Parse an offline NIC-style identity crosswalk CSV.

        Args:
            content: UTF-8 CSV bytes with the controlled NIC fixture shape.

        Returns:
            A validated effective-dated identity resolver.
        """
        rows = _dict_rows(content, delimiter=",")
        required = {
            "RSSD_ID",
            "INSTITUTION_NAME",
            "REPORTER_TYPE",
            "REPORTING_SCOPE",
            "TICKER",
            "CIK",
            "PARENT_RSSD_ID",
            "VALID_FROM",
            "VALID_TO",
            "NIC_PROFILE_URL",
        }
        if not rows or not required.issubset(rows[0]):
            message = "NIC crosswalk is missing required columns"
            raise RegulatoryDataError(message)
        records = tuple(
            NicIdentityRecord(
                rssd_id=_normalize_rssd(row["RSSD_ID"]),
                institution_name=_nonempty(row["INSTITUTION_NAME"], "INSTITUTION_NAME"),
                reporter_type=_enum_value(
                    RegulatoryReporterType,
                    row["REPORTER_TYPE"],
                    "REPORTER_TYPE",
                ),
                reporting_scope=_enum_value(
                    RegulatoryReportingScope,
                    row["REPORTING_SCOPE"],
                    "REPORTING_SCOPE",
                ),
                ticker=_optional(row["TICKER"], transform=str.upper),
                cik=_optional_cik(row["CIK"]),
                parent_rssd_id=_optional_rssd(row["PARENT_RSSD_ID"]),
                valid_from=_iso_date(row["VALID_FROM"], "VALID_FROM"),
                valid_to=_optional_date(row["VALID_TO"], "VALID_TO"),
                nic_profile_url=_official_url(row["NIC_PROFILE_URL"], host="www.ffiec.gov"),
            )
            for row in rows
        )
        return cls(records)

    @property
    def records(self) -> tuple[NicIdentityRecord, ...]:
        """Return immutable parsed crosswalk rows."""
        return self._records

    def resolve(
        self,
        *,
        as_of: date,
        rssd_id: str | None = None,
        ticker: str | None = None,
        cik: str | None = None,
    ) -> NicIdentityRecord:
        """Resolve one identity using all supplied keys and effective time.

        At least one key is required. Supplying multiple keys means all keys must
        identify the same row; the resolver never falls back from one key to another.
        """
        if rssd_id is None and ticker is None and cik is None:
            message = "NIC resolution requires RSSD, ticker, or CIK"
            raise RegulatoryDataError(message)
        normalized_rssd = _normalize_rssd(rssd_id) if rssd_id is not None else None
        normalized_ticker = ticker.strip().upper() if ticker is not None else None
        normalized_cik = _normalize_cik(cik) if cik is not None else None
        matches = [
            item
            for item in self._records
            if item.is_effective(as_of)
            and (normalized_rssd is None or item.rssd_id == normalized_rssd)
            and (normalized_ticker is None or item.ticker == normalized_ticker)
            and (normalized_cik is None or item.cik == normalized_cik)
        ]
        if len(matches) != 1:
            message = "NIC identity did not resolve to exactly one effective reporter"
            raise RegulatoryDataError(message)
        return matches[0]


class _DelimitedRegulatoryAdapter:
    """Shared parser for controlled wide delimited regulatory files."""

    source_family: RegulatorySourceFamily

    def __init__(self, config: RegulatoryConfig) -> None:
        self._config = config
        self._source = config.source(self.source_family)

    def parse(
        self,
        content: bytes,
        *,
        rssd_id: str,
        report_date: date,
    ) -> tuple[RegulatoryFact, ...]:
        """Parse mapped facts for exactly one native reporter and report date."""
        reporter = self._config.reporter(
            rssd_id=rssd_id,
            source_family=self.source_family,
            report_date=report_date,
        )
        rows = _dict_rows(content, delimiter=self._source.delimiter)
        required = {
            self._source.rssd_column,
            self._source.report_date_column,
            self._source.revision_column,
        }
        if not rows or not required.issubset(rows[0]):
            message = f"{self.source_family.value} file is missing identity columns"
            raise RegulatoryDataError(message)
        matching = [
            row
            for row in rows
            if _normalize_rssd(row[self._source.rssd_column]) == reporter.rssd_id
            and _source_date(
                row[self._source.report_date_column],
                self._source.report_date_format,
            )
            == report_date
        ]
        if len(matching) != 1:
            message = "regulatory source did not contain exactly one requested reporter row"
            raise RegulatoryDataError(message)
        row = matching[0]
        revision = _nonempty(row[self._source.revision_column], self._source.revision_column)
        facts: list[RegulatoryFact] = []
        for mapping in self._config.item_mappings(
            family=self.source_family,
            report_date=report_date,
        ):
            if mapping.series not in row or not row[mapping.series].strip():
                continue
            raw_value = row[mapping.series].strip()
            try:
                normalized_value = parse_decimal(raw_value, scale=mapping.scale)
            except (TypeError, ValueError) as error:
                message = f"regulatory series {mapping.series} is not an exact decimal"
                raise RegulatoryDataError(message) from error
            facts.append(
                RegulatoryFact(
                    rssd_id=reporter.rssd_id,
                    reporter_name=reporter.legal_name,
                    reporting_entity_id=reporter.reporting_entity_id,
                    reporting_scope_id=reporter.reporting_scope_id,
                    reporting_scope=reporter.reporting_scope,
                    source_family=self.source_family,
                    report_date=report_date,
                    schedule=mapping.schedule,
                    item=mapping.item,
                    series=mapping.series,
                    metric_id=mapping.metric_id,
                    component=mapping.component,
                    period_type=mapping.period_type,
                    unit=mapping.unit,
                    scale=mapping.scale,
                    revision=revision,
                    raw_value=raw_value,
                    normalized_value=normalized_value,
                    source_url=self._source.source_url,
                    locator=(
                        f"RSSD {reporter.rssd_id}; report-date {report_date.isoformat()}; "
                        f"{mapping.schedule} {mapping.item}; series {mapping.series}"
                    ),
                    mapping_revision=mapping.mapping_revision,
                )
            )
        if not facts:
            message = "requested regulatory row contains no configured servicing facts"
            raise RegulatoryDataError(message)
        return tuple(facts)


class FfiecCdrBulkAdapter(_DelimitedRegulatoryAdapter):
    """Parse FFIEC CDR Call Report tab-delimited bulk rows keyed by RSSD."""

    source_family = RegulatorySourceFamily.FFIEC_CDR_CALL


class FrY9cBulkAdapter(_DelimitedRegulatoryAdapter):
    """Parse FR Y-9C caret-delimited bulk rows keyed by RSSD."""

    source_family = RegulatorySourceFamily.FR_Y9C


class BankRegulatoryAdapter:
    """Concrete fail-closed facade over the two supported regulatory families."""

    def __init__(self, config: RegulatoryConfig) -> None:
        """Bind both supported parsers to one validated mapping configuration."""
        self._adapters = {
            RegulatorySourceFamily.FFIEC_CDR_CALL: FfiecCdrBulkAdapter(config),
            RegulatorySourceFamily.FR_Y9C: FrY9cBulkAdapter(config),
        }

    def parse(
        self,
        content: bytes,
        *,
        source_family: RegulatorySourceFamily,
        rssd_id: str,
        report_date: date,
    ) -> tuple[RegulatoryFact, ...]:
        """Parse one official-family payload at its native reporter and scope."""
        try:
            adapter = self._adapters[source_family]
        except KeyError as error:
            message = f"unsupported bank regulatory source family: {source_family}"
            raise RegulatoryDataError(message) from error
        return adapter.parse(content, rssd_id=rssd_id, report_date=report_date)


def aggregate_regulatory_metric(
    facts: tuple[RegulatoryFact, ...],
    *,
    metric_id: str,
    required_components: tuple[str, ...],
) -> RegulatoryMetricValue:
    """Aggregate exact components only when every scope dimension matches.

    Args:
        facts: Reporter-scoped parsed facts.
        metric_id: Target metric whose components should be aggregated.
        required_components: Complete expected component vocabulary.

    Returns:
        Exact aggregate with stable input fact identities.

    Raises:
        RegulatoryDataError: If components are missing, duplicated, or cross scope.
    """
    selected = tuple(item for item in facts if item.metric_id == metric_id)
    if not selected or not required_components:
        message = "regulatory aggregate requires facts and explicit components"
        raise RegulatoryDataError(message)
    component_counts = {
        name: sum(item.component == name for item in selected) for name in required_components
    }
    if any(count != 1 for count in component_counts.values()) or len(selected) != len(
        required_components
    ):
        message = "regulatory aggregate components are missing, duplicated, or unexpected"
        raise RegulatoryDataError(message)
    identity = {
        (
            item.rssd_id,
            item.reporting_entity_id,
            item.reporting_scope,
            item.source_family,
            item.report_date,
            item.unit,
        )
        for item in selected
    }
    if len(identity) != 1:
        message = "regulatory aggregate cannot mix reporter, scope, source, date, or unit"
        raise RegulatoryDataError(message)
    first = selected[0]
    return RegulatoryMetricValue(
        rssd_id=first.rssd_id,
        reporting_entity_id=first.reporting_entity_id,
        reporting_scope=first.reporting_scope,
        source_family=first.source_family,
        report_date=first.report_date,
        metric_id=metric_id,
        unit=first.unit,
        value=sum((item.normalized_value for item in selected), Decimal(0)),
        input_fact_ids=tuple(sorted(item.fact_id for item in selected)),
    )


def load_regulatory_config(path: Path) -> RegulatoryConfig:
    """Load and validate a versioned regulatory YAML configuration.

    Args:
        path: Explicit configuration path; no environment fallback is used.

    Returns:
        Typed immutable regulatory configuration.
    """
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        message = f"regulatory configuration could not be loaded: {path.name}"
        raise RegulatoryDataError(message) from error
    root = _as_mapping(payload, "regulatory configuration")
    version = _nonempty(root.get("version"), "version")
    reporter_rows = _mapping_rows(root.get("reporters"), "reporters")
    source_rows = _mapping_rows(root.get("sources"), "sources")
    mapping_rows = _mapping_rows(root.get("mappings"), "mappings")
    reporters = tuple(_reporter_from_mapping(item) for item in reporter_rows)
    sources = tuple(_source_from_mapping(item) for item in source_rows)
    mappings = tuple(_item_from_mapping(item) for item in mapping_rows)
    config = RegulatoryConfig(version, reporters, sources, mappings)
    _validate_regulatory_config(config)
    return config


def _reporter_from_mapping(row: dict[str, Any]) -> RegulatoryReporter:
    families = _string_rows(row.get("permitted_source_families"), "permitted_source_families")
    return RegulatoryReporter(
        rssd_id=_normalize_rssd(row.get("rssd_id")),
        company_id=_nonempty(row.get("company_id"), "company_id"),
        legal_name=_nonempty(row.get("legal_name"), "legal_name"),
        reporting_entity_id=_nonempty(row.get("reporting_entity_id"), "reporting_entity_id"),
        reporting_scope_id=_nonempty(row.get("reporting_scope_id"), "reporting_scope_id"),
        reporter_type=_enum_value(
            RegulatoryReporterType,
            row.get("reporter_type"),
            "reporter_type",
        ),
        reporting_scope=_enum_value(
            RegulatoryReportingScope,
            row.get("reporting_scope"),
            "reporting_scope",
        ),
        permitted_source_families=tuple(
            _enum_value(RegulatorySourceFamily, item, "permitted_source_families")
            for item in families
        ),
        nic_profile_url=_official_url(row.get("nic_profile_url"), host="www.ffiec.gov"),
        valid_from=_iso_date(row.get("valid_from"), "valid_from"),
        valid_to=_optional_date(row.get("valid_to"), "valid_to"),
        ticker=_optional(row.get("ticker"), transform=str.upper),
        cik=_optional_cik(row.get("cik")),
        parent_rssd_id=_optional_rssd(row.get("parent_rssd_id")),
    )


def _source_from_mapping(row: dict[str, Any]) -> RegulatorySourceDefinition:
    delimiter_name = _nonempty(row.get("delimiter"), "delimiter")
    delimiters = {"tab": "\t", "caret": "^"}
    if delimiter_name not in delimiters:
        message = f"unsupported regulatory delimiter: {delimiter_name}"
        raise RegulatoryDataError(message)
    report_date_format = _nonempty(row.get("report_date_format"), "report_date_format")
    if report_date_format not in _SOURCE_DATE_FORMATS:
        message = f"unsupported regulatory report date format: {report_date_format}"
        raise RegulatoryDataError(message)
    return RegulatorySourceDefinition(
        family=_enum_value(RegulatorySourceFamily, row.get("family"), "family"),
        delimiter=delimiters[delimiter_name],
        rssd_column=_nonempty(row.get("rssd_column"), "rssd_column"),
        report_date_column=_nonempty(row.get("report_date_column"), "report_date_column"),
        report_date_format=report_date_format,
        revision_column=_nonempty(row.get("revision_column"), "revision_column"),
        source_url=_official_url(row.get("source_url")),
    )


def _item_from_mapping(row: dict[str, Any]) -> RegulatoryItemMapping:
    return RegulatoryItemMapping(
        source_family=_enum_value(
            RegulatorySourceFamily,
            row.get("source_family"),
            "source_family",
        ),
        series=_nonempty(row.get("series"), "series"),
        schedule=_nonempty(row.get("schedule"), "schedule"),
        item=_nonempty(row.get("item"), "item"),
        metric_id=_nonempty(row.get("metric_id"), "metric_id"),
        component=_nonempty(row.get("component"), "component"),
        unit=_nonempty(row.get("unit"), "unit"),
        scale=_nonempty(row.get("scale"), "scale"),
        period_type=_nonempty(row.get("period_type"), "period_type"),
        mapping_revision=_nonempty(row.get("mapping_revision"), "mapping_revision"),
        valid_from=_iso_date(row.get("valid_from"), "valid_from"),
        valid_to=_optional_date(row.get("valid_to"), "valid_to"),
        definition_url=_official_url(row.get("definition_url")),
    )


def _validate_regulatory_config(config: RegulatoryConfig) -> None:
    if len({item.rssd_id for item in config.reporters}) != len(config.reporters):
        message = "regulatory config has duplicate RSSD reporters"
        raise RegulatoryDataError(message)
    if {item.family for item in config.sources} != set(RegulatorySourceFamily):
        message = "regulatory config must define every supported source family exactly once"
        raise RegulatoryDataError(message)
    mapping_keys = {(item.source_family, item.series, item.valid_from) for item in config.mappings}
    if len(mapping_keys) != len(config.mappings):
        message = "regulatory config has duplicate effective item mappings"
        raise RegulatoryDataError(message)
    for reporter in config.reporters:
        if (
            reporter.reporter_type is RegulatoryReporterType.BANK_HOLDING_COMPANY
            and reporter.reporting_scope
            is not RegulatoryReportingScope.BANK_HOLDING_COMPANY_REGULATORY
        ) or (
            reporter.reporter_type is RegulatoryReporterType.INSURED_DEPOSITORY_INSTITUTION
            and reporter.reporting_scope
            is not RegulatoryReportingScope.DEPOSITORY_INSTITUTION_REGULATORY
        ):
            message = f"reporter type and scope conflict for RSSD {reporter.rssd_id}"
            raise RegulatoryDataError(message)


def _dict_rows(content: bytes, *, delimiter: str) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        message = "regulatory input is not UTF-8"
        raise RegulatoryDataError(message) from error
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if reader.fieldnames is None or any(
        name is None or not name.strip() for name in reader.fieldnames
    ):
        message = "regulatory input has an invalid header"
        raise RegulatoryDataError(message)
    rows: list[dict[str, str]] = []
    try:
        for raw in reader:
            if None in raw:
                message = "regulatory input row has too many fields"
                raise RegulatoryDataError(message)
            rows.append({key: value or "" for key, value in raw.items()})
    except csv.Error as error:
        message = "regulatory delimited input is malformed"
        raise RegulatoryDataError(message) from error
    return rows


def _source_date(value: object, date_format: str) -> date:
    text = _nonempty(value, "report date")
    try:
        return (
            date.fromisoformat(text)
            if date_format == "%Y-%m-%d"
            else date(
                int(text[0:4]),
                int(text[4:6]),
                int(text[6:8]),
            )
        )
    except (ValueError, IndexError) as error:
        message = "regulatory source report date is invalid"
        raise RegulatoryDataError(message) from error


def _as_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        message = f"{label} must be a mapping"
        raise RegulatoryDataError(message)
    return cast("dict[str, Any]", value)


def _mapping_rows(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        message = f"{label} must be a nonempty list"
        raise RegulatoryDataError(message)
    return tuple(_as_mapping(item, label) for item in value)


def _string_rows(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        message = f"{label} must be a nonempty list"
        raise RegulatoryDataError(message)
    return tuple(_nonempty(item, label) for item in value)


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        message = f"{label} must be nonempty text"
        raise RegulatoryDataError(message)
    return value.strip()


def _optional(
    value: object,
    *,
    transform: Callable[[str], str] | None = None,
) -> str | None:
    if value is None or value == "":
        return None
    text = _nonempty(value, "optional value")
    return transform(text) if transform is not None else text


def _normalize_rssd(value: object) -> str:
    text = _nonempty(value, "RSSD").lstrip("0") or "0"
    if not text.isdigit():
        message = "RSSD must contain only decimal digits"
        raise RegulatoryDataError(message)
    return text


def _optional_rssd(value: object) -> str | None:
    return None if value is None or value == "" else _normalize_rssd(value)


def _normalize_cik(value: object) -> str:
    text = _nonempty(value, "CIK")
    if not text.isdigit() or len(text) > _CIK_MAX_DIGITS:
        message = "CIK must contain at most ten decimal digits"
        raise RegulatoryDataError(message)
    return text.zfill(10)


def _optional_cik(value: object) -> str | None:
    return None if value is None or value == "" else _normalize_cik(value)


def _iso_date(value: object, label: str) -> date:
    text = _nonempty(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        message = f"{label} must be an ISO date"
        raise RegulatoryDataError(message) from error


def _optional_date(value: object, label: str) -> date | None:
    return None if value is None or value == "" else _iso_date(value, label)


def _enum_value(
    enum_type: type[EnumType],
    value: object,
    label: str,
) -> EnumType:
    text = _nonempty(value, label)
    try:
        return enum_type(text)
    except ValueError as error:
        message = f"{label} has unsupported value {text}"
        raise RegulatoryDataError(message) from error


def _official_url(value: object, *, host: str | None = None) -> str:
    text = _nonempty(value, "official URL")
    parsed = urlparse(text)
    allowed_hosts = {host} if host is not None else _REGULATORY_HOSTS
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        message = "regulatory source URL must use the configured official HTTPS host"
        raise RegulatoryDataError(message)
    return text
