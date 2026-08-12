"""Deterministic earnings-event inputs and filing-history calendar inference."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import yaml

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK_MAX_DIGITS = 10
_FISCAL_QUARTERS = 4


class EarningsCalendarError(RuntimeError):
    """Fail-closed calendar configuration, event, or inference error."""


class CalendarFreshnessState(StrEnum):
    """Freshness relative to a history-derived expected filing window."""

    NOT_YET_EXPECTED = "NOT_YET_EXPECTED"
    WITHIN_EXPECTED_WINDOW = "WITHIN_EXPECTED_WINDOW"
    AWAITING_EXPECTED_FILING = "AWAITING_EXPECTED_FILING"


@dataclass(frozen=True, slots=True)
class CalendarInferencePolicy:
    """Versioned filing-history-only inference parameters."""

    method: str
    lookback_matching_periods: int
    minimum_matching_periods: int
    padding_days: int
    maximum_lag_days: int


@dataclass(frozen=True, slots=True)
class CalendarCompany:
    """Issuer identity and fiscal quarter convention for calendar inputs."""

    company_id: str
    ticker: str
    cik: str
    fiscal_quarter_ends: tuple[tuple[int, int], ...]
    edgar_submissions_url: str
    investor_relations_events_url: str
    investor_relations_hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EarningsCalendarConfig:
    """Typed versioned calendar inference configuration."""

    version: str
    policy: CalendarInferencePolicy
    companies: tuple[CalendarCompany, ...]

    def company(self, company_id: str) -> CalendarCompany:
        """Resolve exactly one configured company."""
        matches = [item for item in self.companies if item.company_id == company_id]
        if len(matches) != 1:
            message = f"calendar company is not uniquely configured: {company_id}"
            raise EarningsCalendarError(message)
        return matches[0]


@dataclass(frozen=True, slots=True)
class FilingEarningsEvent:
    """Actual filed earnings release evidenced by an EDGAR 8-K/EX-99."""

    event_id: str
    company_id: str
    cik: str
    accession: str
    period_end: date
    accepted_at: datetime
    form: str
    exhibit_type: str
    filing_url: str
    exhibit_url: str
    is_inferred: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class IssuerIrEvent:
    """Actual issuer-announced IR earnings event, distinct from an inference."""

    event_id: str
    company_id: str
    period_end: date
    announced_at: datetime
    event_at: datetime
    title: str
    source_url: str
    is_inferred: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class ReportedPeriod:
    """Latest actually reported fiscal period from an eligible SEC filing."""

    period_end: date
    filing_event_id: str
    accepted_at: datetime
    accession: str
    filing_url: str
    exhibit_url: str
    is_inferred: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class InferredEarningsWindow:
    """Next expected window derived solely from comparable filing lags."""

    expected_period_end: date
    window_start: date
    window_end: date
    inference_basis: tuple[str, ...]
    method: str
    config_version: str
    is_inferred: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class EarningsCalendarResult:
    """Actual last report plus a separately typed inferred next window."""

    company_id: str
    as_of: datetime
    last_reported_period: ReportedPeriod
    inferred_window: InferredEarningsWindow
    freshness_state: CalendarFreshnessState
    next_announced_event: IssuerIrEvent | None

    @property
    def window_start(self) -> date:
        """Expose the inferred window start without flattening its type."""
        return self.inferred_window.window_start

    @property
    def window_end(self) -> date:
        """Expose the inferred window end without flattening its type."""
        return self.inferred_window.window_end

    @property
    def inference_basis(self) -> tuple[str, ...]:
        """Expose exact filing event IDs used by the inference."""
        return self.inferred_window.inference_basis

    @property
    def is_inferred(self) -> Literal[True]:
        """Label the expected window, never the last reported period, as inferred."""
        return True


class EdgarEarningsEventAdapter:
    """Parse normalized official EDGAR filing-index 8-K/EX-99 inputs."""

    def parse(
        self,
        content: bytes,
        *,
        company: CalendarCompany,
    ) -> tuple[FilingEarningsEvent, ...]:
        """Return actual earnings releases from an offline EDGAR JSON input.

        Unrelated forms and non-earnings 8-K filings are ignored. A row explicitly
        marked as an earnings release must contain an official EX-99 document.
        """
        root = _json_mapping(content, "EDGAR earnings input")
        if _normalize_cik(root.get("cik")) != company.cik:
            message = "EDGAR input CIK does not match the configured company"
            raise EarningsCalendarError(message)
        source_url = _sec_url(root.get("source_url"))
        if source_url != company.edgar_submissions_url:
            message = "EDGAR input source URL does not match calendar configuration"
            raise EarningsCalendarError(message)
        rows = _mapping_rows(root.get("filings"), "filings", allow_empty=True)
        events: list[FilingEarningsEvent] = []
        for row in rows:
            if row.get("form") != "8-K" or row.get("purpose") != "EARNINGS_RELEASE":
                continue
            accession = _accession(row.get("accession"))
            documents = _mapping_rows(row.get("documents"), "documents")
            exhibits = [
                item
                for item in documents
                if isinstance(item.get("type"), str)
                and cast("str", item["type"]).startswith("EX-99")
            ]
            if len(exhibits) != 1:
                message = f"earnings filing {accession} must identify exactly one EX-99 exhibit"
                raise EarningsCalendarError(message)
            exhibit = exhibits[0]
            exhibit_type = _text(exhibit.get("type"), "document type")
            events.append(
                FilingEarningsEvent(
                    event_id=f"edgar:{accession}",
                    company_id=company.company_id,
                    cik=company.cik,
                    accession=accession,
                    period_end=_iso_date(row.get("reported_period_end"), "reported_period_end"),
                    accepted_at=_aware_datetime(row.get("accepted_at"), "accepted_at"),
                    form="8-K",
                    exhibit_type=exhibit_type,
                    filing_url=_sec_url(row.get("filing_url")),
                    exhibit_url=_sec_url(exhibit.get("url")),
                )
            )
        _unique_event_ids(tuple(events))
        return tuple(events)


class IssuerIrEventAdapter:
    """Parse normalized official issuer investor-relations event inputs."""

    def parse(
        self,
        content: bytes,
        *,
        company: CalendarCompany,
    ) -> tuple[IssuerIrEvent, ...]:
        """Return issuer-announced earnings events from an offline JSON input."""
        root = _json_mapping(content, "issuer IR input")
        if root.get("company_id") != company.company_id:
            message = "issuer IR input company does not match calendar configuration"
            raise EarningsCalendarError(message)
        source_url = _issuer_url(
            root.get("source_url"),
            allowed_hosts=company.investor_relations_hosts,
        )
        if source_url != company.investor_relations_events_url:
            message = "issuer IR input source URL does not match calendar configuration"
            raise EarningsCalendarError(message)
        rows = _mapping_rows(root.get("events"), "events", allow_empty=True)
        events: list[IssuerIrEvent] = []
        for row in rows:
            if row.get("kind") not in {"EARNINGS_CALL", "EARNINGS_RELEASE"}:
                continue
            announced_at = _aware_datetime(row.get("announced_at"), "announced_at")
            event_at = _aware_datetime(row.get("event_at"), "event_at")
            if announced_at > event_at:
                message = "issuer earnings event cannot be announced after it occurs"
                raise EarningsCalendarError(message)
            events.append(
                IssuerIrEvent(
                    event_id=_text(row.get("event_id"), "event_id"),
                    company_id=company.company_id,
                    period_end=_iso_date(row.get("period_end"), "period_end"),
                    announced_at=announced_at,
                    event_at=event_at,
                    title=_text(row.get("title"), "title"),
                    source_url=_issuer_url(
                        row.get("source_url"),
                        allowed_hosts=company.investor_relations_hosts,
                    ),
                )
            )
        _unique_event_ids(tuple(events))
        return tuple(events)


class EarningsCalendar:
    """Build deterministic calendar state from actual public event inputs."""

    def __init__(self, config: EarningsCalendarConfig) -> None:
        """Create a calendar using one immutable configuration version."""
        self._config = config

    def build(
        self,
        *,
        company_id: str,
        as_of: datetime,
        filing_events: tuple[FilingEarningsEvent, ...],
        issuer_events: tuple[IssuerIrEvent, ...] = (),
    ) -> EarningsCalendarResult:
        """Build actual and inferred state as known at a timezone-aware instant.

        Only SEC filing events influence the inferred window. Issuer IR events are
        exposed as actual announcements but cannot move or narrow that window.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            message = "calendar as_of must be timezone-aware"
            raise EarningsCalendarError(message)
        company = self._config.company(company_id)
        known_filings = tuple(
            event
            for event in filing_events
            if event.company_id == company_id and event.accepted_at <= as_of
        )
        if not known_filings:
            message = "calendar inference requires at least one known SEC earnings filing"
            raise EarningsCalendarError(message)
        period_events = _one_filing_per_period(known_filings)
        latest = max(period_events, key=lambda item: (item.period_end, item.accepted_at))
        expected_period_end = _next_period_end(
            latest.period_end,
            company.fiscal_quarter_ends,
        )
        matching = tuple(
            event
            for event in period_events
            if (event.period_end.month, event.period_end.day)
            == (expected_period_end.month, expected_period_end.day)
            and event.period_end < expected_period_end
        )
        selected = tuple(
            sorted(matching, key=lambda item: item.period_end)[
                -self._config.policy.lookback_matching_periods :
            ]
        )
        if len(selected) < self._config.policy.minimum_matching_periods:
            message = "insufficient matching-quarter filing history for inference"
            raise EarningsCalendarError(message)
        lags = tuple((event.accepted_at.date() - event.period_end).days for event in selected)
        if any(lag < 0 or lag > self._config.policy.maximum_lag_days for lag in lags):
            message = "filing-history lag is outside the configured inference boundary"
            raise EarningsCalendarError(message)
        padding = self._config.policy.padding_days
        inferred_window = InferredEarningsWindow(
            expected_period_end=expected_period_end,
            window_start=expected_period_end + timedelta(days=min(lags) - padding),
            window_end=expected_period_end + timedelta(days=max(lags) + padding),
            inference_basis=tuple(event.event_id for event in selected),
            method=self._config.policy.method,
            config_version=self._config.version,
        )
        if as_of.date() < inferred_window.window_start:
            freshness = CalendarFreshnessState.NOT_YET_EXPECTED
        elif as_of.date() <= inferred_window.window_end:
            freshness = CalendarFreshnessState.WITHIN_EXPECTED_WINDOW
        else:
            freshness = CalendarFreshnessState.AWAITING_EXPECTED_FILING
        next_announced_event = _next_ir_event(
            issuer_events,
            company_id=company_id,
            expected_period_end=expected_period_end,
            as_of=as_of,
        )
        return EarningsCalendarResult(
            company_id=company_id,
            as_of=as_of,
            last_reported_period=ReportedPeriod(
                period_end=latest.period_end,
                filing_event_id=latest.event_id,
                accepted_at=latest.accepted_at,
                accession=latest.accession,
                filing_url=latest.filing_url,
                exhibit_url=latest.exhibit_url,
            ),
            inferred_window=inferred_window,
            freshness_state=freshness,
            next_announced_event=next_announced_event,
        )


def build_earnings_calendar_from_files(
    *,
    config_path: Path,
    company_id: str,
    edgar_input_path: Path,
    as_of: datetime,
    issuer_ir_input_path: Path | None = None,
) -> EarningsCalendarResult:
    """Build one integration-ready calendar result from deterministic files.

    Args:
        config_path: Versioned earnings-calendar YAML path.
        company_id: Stable configured company ID.
        edgar_input_path: Retained normalized EDGAR filing-index JSON bytes.
        as_of: Timezone-aware knowledge cutoff.
        issuer_ir_input_path: Optional retained normalized issuer IR JSON bytes.

    Returns:
        Actual last reported period, separately typed inferred window, and freshness.

    Raises:
        EarningsCalendarError: If any configured or retained input cannot be read or
            deterministically resolved.
    """
    config = load_earnings_calendar_config(config_path)
    company = config.company(company_id)
    try:
        filing_content = edgar_input_path.read_bytes()
        issuer_content = issuer_ir_input_path.read_bytes() if issuer_ir_input_path else None
    except OSError as error:
        message = "earnings calendar input file could not be read"
        raise EarningsCalendarError(message) from error
    filing_events = EdgarEarningsEventAdapter().parse(filing_content, company=company)
    issuer_events = (
        IssuerIrEventAdapter().parse(issuer_content, company=company)
        if issuer_content is not None
        else ()
    )
    return EarningsCalendar(config).build(
        company_id=company_id,
        as_of=as_of,
        filing_events=filing_events,
        issuer_events=issuer_events,
    )


def build_earnings_calendar_from_official_config(
    *,
    config_path: Path,
    company_id: str,
    as_of: datetime,
) -> EarningsCalendarResult:
    """Build calendar state from versioned official SEC filing locators.

    This offline path uses only actual Item 2.02 / EX-99 filing identities and
    acceptance times verified from official SEC archive URLs. It does not treat
    an expected window as issuer guidance and does not consume synthetic fixtures.
    """
    config = load_earnings_calendar_config(config_path)
    company = config.company(company_id)
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        message = f"earnings calendar configuration could not be loaded: {config_path.name}"
        raise EarningsCalendarError(message) from error
    root = _mapping(loaded, "calendar configuration")
    histories = _mapping(root.get("official_filing_history"), "official_filing_history")
    rows = _mapping_rows(histories.get(company_id), f"official_filing_history.{company_id}")
    events: list[FilingEarningsEvent] = []
    for row in rows:
        accession = _accession(row.get("accession"))
        accession_digits = accession.replace("-", "")
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(company.cik)}/{accession_digits}/"
            f"{accession}-index.html"
        )
        events.append(
            FilingEarningsEvent(
                event_id=f"edgar:{accession}",
                company_id=company_id,
                cik=company.cik,
                accession=accession,
                period_end=_iso_date(row.get("period_end"), "period_end"),
                accepted_at=_aware_datetime(row.get("accepted_at"), "accepted_at"),
                form="8-K",
                exhibit_type=_text(row.get("exhibit_type"), "exhibit_type"),
                filing_url=_sec_url(filing_url),
                exhibit_url=_sec_url(row.get("exhibit_url")),
            )
        )
    _unique_event_ids(tuple(events))
    return EarningsCalendar(config).build(
        company_id=company_id,
        as_of=as_of,
        filing_events=tuple(events),
    )


def load_earnings_calendar_config(path: Path) -> EarningsCalendarConfig:
    """Load and validate a versioned earnings-calendar YAML configuration."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        message = f"earnings calendar configuration could not be loaded: {path.name}"
        raise EarningsCalendarError(message) from error
    root = _mapping(payload, "calendar configuration")
    version = _text(root.get("version"), "version")
    policy_row = _mapping(root.get("inference"), "inference")
    policy = CalendarInferencePolicy(
        method=_text(policy_row.get("method"), "method"),
        lookback_matching_periods=_positive_int(
            policy_row.get("lookback_matching_periods"),
            "lookback_matching_periods",
        ),
        minimum_matching_periods=_positive_int(
            policy_row.get("minimum_matching_periods"),
            "minimum_matching_periods",
        ),
        padding_days=_nonnegative_int(policy_row.get("padding_days"), "padding_days"),
        maximum_lag_days=_positive_int(
            policy_row.get("maximum_lag_days"),
            "maximum_lag_days",
        ),
    )
    if policy.minimum_matching_periods > policy.lookback_matching_periods:
        message = "minimum matching periods cannot exceed the lookback"
        raise EarningsCalendarError(message)
    companies = tuple(
        _calendar_company(row) for row in _mapping_rows(root.get("companies"), "companies")
    )
    if len({item.company_id for item in companies}) != len(companies):
        message = "calendar configuration has duplicate company IDs"
        raise EarningsCalendarError(message)
    return EarningsCalendarConfig(version, policy, companies)


def _calendar_company(row: dict[str, Any]) -> CalendarCompany:
    quarter_text = _string_rows(row.get("fiscal_quarter_ends"), "fiscal_quarter_ends")
    quarter_ends = tuple(_month_day(item) for item in quarter_text)
    if len(quarter_ends) != _FISCAL_QUARTERS or len(set(quarter_ends)) != _FISCAL_QUARTERS:
        message = "calendar company must define four unique fiscal quarter ends"
        raise EarningsCalendarError(message)
    hosts = _string_rows(row.get("investor_relations_hosts"), "investor_relations_hosts")
    return CalendarCompany(
        company_id=_text(row.get("company_id"), "company_id"),
        ticker=_text(row.get("ticker"), "ticker").upper(),
        cik=_normalize_cik(row.get("cik")),
        fiscal_quarter_ends=quarter_ends,
        edgar_submissions_url=_sec_url(row.get("edgar_submissions_url")),
        investor_relations_events_url=_issuer_url(
            row.get("investor_relations_events_url"),
            allowed_hosts=hosts,
        ),
        investor_relations_hosts=hosts,
    )


def _one_filing_per_period(
    events: tuple[FilingEarningsEvent, ...],
) -> tuple[FilingEarningsEvent, ...]:
    by_period: dict[date, FilingEarningsEvent] = {}
    for event in sorted(events, key=lambda item: (item.accepted_at, item.event_id)):
        by_period.setdefault(event.period_end, event)
    return tuple(by_period.values())


def _next_period_end(last_period_end: date, quarter_ends: tuple[tuple[int, int], ...]) -> date:
    candidates: list[date] = []
    for year in (last_period_end.year, last_period_end.year + 1):
        for month, day in quarter_ends:
            candidates.append(date(year, month, day))
    return min(item for item in candidates if item > last_period_end)


def _next_ir_event(
    events: tuple[IssuerIrEvent, ...],
    *,
    company_id: str,
    expected_period_end: date,
    as_of: datetime,
) -> IssuerIrEvent | None:
    matches = [
        event
        for event in events
        if event.company_id == company_id
        and event.period_end == expected_period_end
        and event.announced_at <= as_of
        and event.event_at >= as_of
    ]
    return min(matches, key=lambda item: (item.event_at, item.event_id), default=None)


def _unique_event_ids(events: tuple[FilingEarningsEvent | IssuerIrEvent, ...]) -> None:
    ids = [item.event_id for item in events]
    if len(ids) != len(set(ids)):
        message = "earnings event input contains duplicate event IDs"
        raise EarningsCalendarError(message)


def _json_mapping(content: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        message = f"{label} is not valid UTF-8 JSON"
        raise EarningsCalendarError(message) from error
    return _mapping(payload, label)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        message = f"{label} must be a mapping"
        raise EarningsCalendarError(message)
    return cast("dict[str, Any]", value)


def _mapping_rows(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        message = f"{label} must be a {'list' if allow_empty else 'nonempty list'}"
        raise EarningsCalendarError(message)
    return tuple(_mapping(item, label) for item in value)


def _string_rows(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        message = f"{label} must be a nonempty list"
        raise EarningsCalendarError(message)
    return tuple(_text(item, label) for item in value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        message = f"{label} must be nonempty text"
        raise EarningsCalendarError(message)
    return value.strip()


def _accession(value: object) -> str:
    text = _text(value, "accession")
    if _ACCESSION.fullmatch(text) is None:
        message = "EDGAR accession has an invalid format"
        raise EarningsCalendarError(message)
    return text


def _normalize_cik(value: object) -> str:
    text = _text(value, "CIK")
    if not text.isdigit() or len(text) > _CIK_MAX_DIGITS:
        message = "CIK must contain at most ten decimal digits"
        raise EarningsCalendarError(message)
    return text.zfill(10)


def _iso_date(value: object, label: str) -> date:
    text = _text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        message = f"{label} must be an ISO date"
        raise EarningsCalendarError(message) from error


def _aware_datetime(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        message = f"{label} must be an ISO datetime"
        raise EarningsCalendarError(message) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        message = f"{label} must be timezone-aware"
        raise EarningsCalendarError(message)
    return parsed.astimezone(UTC)


def _month_day(value: str) -> tuple[int, int]:
    try:
        parsed = date.fromisoformat(f"2000-{value}")
    except ValueError as error:
        message = "fiscal quarter end must use MM-DD"
        raise EarningsCalendarError(message) from error
    return parsed.month, parsed.day


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        message = f"{label} must be a positive integer"
        raise EarningsCalendarError(message)
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        message = f"{label} must be a nonnegative integer"
        raise EarningsCalendarError(message)
    return value


def _sec_url(value: object) -> str:
    text = _text(value, "SEC URL")
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}:
        message = "earnings filing URL must use an official SEC HTTPS host"
        raise EarningsCalendarError(message)
    return text


def _issuer_url(value: object, *, allowed_hosts: tuple[str, ...]) -> str:
    text = _text(value, "issuer IR URL")
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        message = "issuer event URL must use a configured official IR HTTPS host"
        raise EarningsCalendarError(message)
    return text
