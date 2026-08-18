"""Bounded read API and server-rendered public-servicing intelligence dashboard."""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from mortgage_servicing_dashboard.database import create_database_engine, default_database_url
from mortgage_servicing_dashboard.presentation import (
    CompanyIdentity,
    EarningsIdentity,
    ScaleAssessment,
    fiscal_period_label,
    normalize_companies,
    normalize_earnings,
    serialize_cards,
    serialize_earnings,
)
from mortgage_servicing_dashboard.repository import (
    ComparisonRecord,
    IntelligenceRepository,
    ObservationRecord,
    config_directory,
    seed_phase3,
)


def _repository_from_request(request: Request) -> IntelligenceRepository:
    """Resolve the application repository without leaking it into the query schema."""
    repository = request.app.state.repository
    if repository is None:
        detail = cast("dict[str, str]", request.app.state.database_preflight_error)
        raise HTTPException(status_code=503, detail=detail)
    return cast("IntelligenceRepository", repository)


RepositoryDependency = Annotated[
    IntelligenceRepository,
    Depends(_repository_from_request),
]

_MAX_API_RESULTS = 100
_MAX_OFFSET = 10_000
_MAX_RETAINED_EVIDENCE_BYTES = 64 * 1024 * 1024
_CURRENT_SCHEMA_REVISION = "0005_edgartools_acquisition_lineage"
_STALE_AFTER_DAYS = 120
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_METRIC = "total_servicing_upb"
_DEFAULT_COMPARISON_COMPANY_IDS = ("tfc", "pfsi")
_MIN_COMPARISON_COMPANY_COUNT = 2
_MAX_COMPARISON_COMPANY_COUNT = 3
_EXECUTIVE_METRICS = (
    "total_assets",
    "total_servicing_upb",
    "servicing_for_others_upb",
    "owned_msr_upb",
    "servicing_revenue",
    "servicing_fee_income",
    "servicing_operating_expense",
    "servicing_pretax_income",
    "servicing_adjusted_pretax_income",
    "weighted_average_servicing_fee_bps",
    "delinquency_30_plus_count_rate",
)
_HIGHLIGHT_PRIORITY = (
    "total_servicing_upb",
    "servicing_for_others_upb",
    "owned_msr_upb",
    "servicing_revenue",
    "servicing_fee_income",
    "servicing_operating_expense",
    "servicing_pretax_income",
    "weighted_average_servicing_fee_bps",
    "total_assets",
)


class HealthResponse(BaseModel):
    """Deterministic readiness response."""

    model_config = ConfigDict(extra="forbid")
    status: str
    database: str
    companies: int
    latest_period_end: str | None


class ReadResponse(BaseModel):
    """Strict base for stable public read schemas."""

    model_config = ConfigDict(extra="forbid")


class CompanyResponse(ReadResponse):
    """One selected public-company identity."""

    id: str
    legal_name: str
    ticker: str
    classification: str
    universe_version: str


class MetricResponse(ReadResponse):
    """One immutable metric semantic definition."""

    id: str
    display_name: str
    category: str
    semantic_version: str
    business_meaning: str
    grain: str
    unit: str
    permitted_scopes: list[str]
    rules: dict[str, object]


class EvidenceRecordResponse(ReadResponse):
    """Immutable evidence identity and retention metadata."""

    id: str
    source_class: str
    original_url: str
    retrieved_at: str
    published_at: str | None
    accession_or_identifier: str | None
    content_sha256: str
    byte_length: int
    media_type: str
    representation: str
    capture_method: str
    parser_version: str
    retention_location: str
    bounded_excerpt: str | None
    response_status: int | None
    etag: str | None
    last_modified: str | None


class EvidenceResponse(EvidenceRecordResponse):
    """Evidence detail with a bounded reverse observation index."""

    linked_observation_ids: list[str]


class ObservationResponse(ReadResponse):
    """Exact published observation and its complete flat semantic context."""

    id: str
    company_id: str
    company_name: str
    ticker: str
    company_classification: str
    metric_id: str
    metric_name: str
    metric_category: str
    metric_version: str
    period_start: str | None
    period_end: str
    fiscal_year: int
    fiscal_quarter: int
    period_type: str
    value: str | None
    currency: str | None
    unit: str
    scale: str
    reported_decimals: int | None
    reported_precision: str
    state: str
    quality_state: str
    publication_state: str
    revision_number: int
    semantic_key_digest: str
    methodology: str
    reporting_entity_id: str
    reporting_scope_id: str
    fiscal_calendar_regime_id: str
    accounting_policy_regime_id: str
    portfolio_population: str
    reported_label: str
    reported_value: str
    evidence_locator: str
    extraction_method: str
    validation_summary: str
    parser_metadata: dict[str, object]
    evidence_id: str | None
    source_url: str | None
    source_class: str | None
    accession_or_identifier: str | None
    retrieved_at: str | None
    published_at: str
    bounded_excerpt: str | None
    evidence_sha256: str | None
    evidence_byte_length: int | None
    evidence_representation: str | None
    valid_from: str
    valid_to: str | None
    knowledge_from: str
    knowledge_to: str | None
    revision_history: list[dict[str, object]]
    derivation_inputs: list[dict[str, object]]
    evidence_links: list[dict[str, object]]
    dimensions: dict[str, str]


class ObservationDetailResponse(ObservationResponse):
    """Observation detail aliases plus nested immutable evidence."""

    observation_id: str
    normalized_value: str | None
    validation_status: str
    evidence: EvidenceRecordResponse | None
    evidence_locator_url: str | None


class ComparisonResponse(ReadResponse):
    """Deterministic pairwise comparability result."""

    metric_id: str
    period_end: str
    left: ObservationResponse
    right: ObservationResponse
    status: str
    reasons: list[str]


class CoverageResponse(ReadResponse):
    """Disclosure versus explicit missingness counts."""

    company_id: str
    period_end: str
    reported: int
    missing: int
    source_not_checked: int
    total: int


class EarningsEventResponse(ReadResponse):
    """One public-company disclosure event."""

    id: str
    company_id: str
    ticker: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: str | None
    event_at: str
    evidence_id: str
    source_url: str
    event_kind: str
    source_kind: str
    filing_accession: str | None
    window_start: str | None
    window_end: str | None
    is_inferred: bool
    inference_basis: list[str]


class CalendarResponse(ReadResponse):
    """Actual reporting event plus an explicitly inferred expected window."""

    company_id: str
    ticker: str
    as_of: str
    last_reported_period: dict[str, object] | None
    next_expected_report_window: dict[str, object]
    freshness_state: str
    next_announced_event: dict[str, object] | None


class FreshnessResponse(ReadResponse):
    """Evidence, publication, pipeline and missingness freshness."""

    dataset: str | None
    retrieved_at: str | None
    knowledge_at: str | None
    evidence_count: int
    observation_count: int
    published_count: int
    not_disclosed_count: int
    source_assessment_count: int
    source_not_checked_count: int
    quarantine_count: int
    pipeline_status: str
    terminal_outcomes: dict[str, object]
    reported_count: int
    coverage_state: str
    quarantined_candidate_count: int
    failed_run_count: int
    ingestion_error_count: int
    age_days: int | None
    is_stale: bool
    freshness_state: str
    calendar_freshness_state: str
    calendar_freshness_by_company: dict[str, str]
    calendar: list[dict[str, object]]


def _asset_root() -> Path:
    return Path(__file__).resolve().parent


def _validate_identifier(value: str, *, label: str) -> str:
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail=f"invalid {label}")
    return value


def _validate_page(limit: int, offset: int) -> tuple[int, int]:
    if not 1 <= limit <= _MAX_API_RESULTS:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {_MAX_API_RESULTS}",
        )
    if not 0 <= offset <= _MAX_OFFSET:
        raise HTTPException(
            status_code=422,
            detail=f"offset must be between 0 and {_MAX_OFFSET}",
        )
    return limit, offset


def _comparison_selection(
    repo: IntelligenceRepository,
    values: list[str] | None,
    *,
    as_of: datetime | date | None = None,
) -> tuple[str, ...]:
    """Validate two or three ordered active companies before observation reads."""
    selected = tuple(values) if values is not None else _DEFAULT_COMPARISON_COMPANY_IDS
    if not _MIN_COMPARISON_COMPANY_COUNT <= len(selected) <= _MAX_COMPARISON_COMPANY_COUNT:
        raise HTTPException(
            status_code=422,
            detail="comparison requires two or three company_id query values",
        )
    for company_id in selected:
        _validate_identifier(company_id, label="company identifier")
    if len(set(selected)) != len(selected):
        raise HTTPException(
            status_code=422,
            detail="comparison company identifiers must be distinct",
        )
    supported = set(repo.comparison_company_ids(as_of=as_of))
    if any(company_id not in supported for company_id in selected):
        raise HTTPException(
            status_code=422,
            detail="comparison company is not active, supported, and published",
        )
    return selected


def _default_page_comparison_selection(
    repo: IntelligenceRepository,
) -> tuple[str, ...]:
    """Choose a safe page default without assuming the legacy pair exists."""
    supported = repo.comparison_company_ids()
    ordered = tuple(
        dict.fromkeys(
            (
                *(item for item in _DEFAULT_COMPARISON_COMPANY_IDS if item in supported),
                *supported,
            )
        )
    )
    return ordered[:_MIN_COMPARISON_COMPANY_COUNT]


def _page(items: list[dict[str, object]], *, limit: int, offset: int) -> list[dict[str, object]]:
    bounded_limit, bounded_offset = _validate_page(limit, offset)
    return items[bounded_offset : bounded_offset + bounded_limit]


def _exact_decimal_text(value: Decimal) -> str:
    """Format a computed Decimal for display without passing through float."""
    raw = format(value, "f")
    if "." in raw:
        raw = raw.rstrip("0").rstrip(".")
    sign = ""
    if raw.startswith("-"):
        sign, raw = "-", raw[1:]
    whole, separator, fraction = raw.partition(".")
    grouped = f"{int(whole or '0'):,}"
    return f"{sign}{grouped}{separator}{fraction}"


def _display_value(row: ObservationRecord) -> str:
    if row.value is None:
        return "NOT_DISCLOSED"
    return row.reported_value


def _normalized_value(row: ObservationRecord) -> str:
    if row.value is None:
        return "No numeric value"
    qualifiers = [item for item in (row.currency, row.scale, row.unit) if item]
    return f"{row.value} {' · '.join(qualifiers)}"


def _period_label(row: ObservationRecord) -> str:
    return fiscal_period_label(
        fiscal_year=row.fiscal_year,
        fiscal_quarter=row.fiscal_quarter,
    )


def _semantics_align(left: ObservationRecord, right: ObservationRecord) -> bool:
    return (
        left.company_id == right.company_id
        and left.metric_id == right.metric_id
        and left.metric_version == right.metric_version
        and left.reporting_entity_id == right.reporting_entity_id
        and left.reporting_scope_id == right.reporting_scope_id
        and left.fiscal_calendar_regime_id == right.fiscal_calendar_regime_id
        and left.accounting_policy_regime_id == right.accounting_policy_regime_id
        and left.portfolio_population == right.portfolio_population
        and left.currency == right.currency
        and left.unit == right.unit
        and left.scale == right.scale
        and left.methodology == right.methodology
        and left.period_type == right.period_type
        and left.dimensions == right.dimensions
        and left.state == right.state
        and left.value is not None
        and right.value is not None
    )


def _movement(current: ObservationRecord, previous: ObservationRecord | None) -> dict[str, str]:
    if previous is None:
        return {"status": "unavailable", "label": "No prior quarter", "detail": ""}
    if not _semantics_align(current, previous):
        return {
            "status": "unavailable",
            "label": "QoQ not calculable",
            "detail": "Missing or changed semantics",
        }
    if current.value is None or previous.value is None:  # pragma: no cover - narrowed above
        return {"status": "unavailable", "label": "QoQ not calculable", "detail": ""}
    delta = Decimal(current.value) - Decimal(previous.value)
    prefix = "+" if delta > 0 else ""
    qualifiers = " · ".join(item for item in (current.currency, current.scale) if item)
    return {
        "status": "available",
        "label": f"{prefix}{_exact_decimal_text(delta)} {qualifiers}".strip(),
        "detail": f"versus {_period_label(previous)}; exact normalized difference",
    }


def _row_view(row: ObservationRecord) -> dict[str, object]:
    payload = row.as_dict()
    payload.update(
        {
            "display_value": _display_value(row),
            "normalized_display": _normalized_value(row),
            "period_label": _period_label(row),
            "is_disclosed": row.value is not None,
            "evidence_locator_url": _evidence_locator_url(row),
        }
    )
    return payload


def _highlight_rows(
    rows: list[ObservationRecord],
    *,
    company_id: str,
    period_end: str | None,
) -> list[dict[str, object]]:
    if period_end is None:
        return []
    company_rows = [row for row in rows if row.company_id == company_id]
    current_by_metric = {row.metric_id: row for row in company_rows if row.period_end == period_end}
    result: list[dict[str, object]] = []
    for metric_id in _HIGHLIGHT_PRIORITY:
        current = current_by_metric.get(metric_id)
        if current is None:
            continue
        prior_candidates = sorted(
            (
                row
                for row in company_rows
                if row.metric_id == metric_id and row.period_end < period_end
            ),
            key=lambda item: item.period_end,
        )
        view = _row_view(current)
        view["movement"] = _movement(current, prior_candidates[-1] if prior_candidates else None)
        result.append(view)
    return result


def _chart_model(  # noqa: C901, PLR0915
    rows: list[ObservationRecord],
    *,
    metric_id: str,
    company_id: str | None,
) -> dict[str, object]:
    """Build exact server-side SVG geometry and its equivalent table rows."""
    selected = [
        row
        for row in rows
        if row.metric_id == metric_id and (company_id is None or row.company_id == company_id)
    ]
    periods = sorted({row.period_end for row in selected})
    numeric_values = [Decimal(row.value) for row in selected if row.value is not None]
    companies = tuple(dict.fromkeys(row.company_id for row in selected))
    if not periods:
        return {
            "metric_id": metric_id,
            "status": "empty",
            "periods": periods,
            "series": [],
            "axis_periods": [],
            "table_rows": [],
        }

    minimum = min(numeric_values) if numeric_values else Decimal(0)
    maximum = max(numeric_values) if numeric_values else Decimal(0)
    span = maximum - minimum or Decimal(1)
    chart_left = Decimal(72)
    chart_width = Decimal(576)
    chart_top = Decimal(28)
    chart_height = Decimal(168)

    x_by_period: dict[str, Decimal] = {}
    for index, period in enumerate(periods):
        denominator = max(len(periods) - 1, 1)
        x_by_period[period] = chart_left + (chart_width * Decimal(index) / Decimal(denominator))
    axis_periods = [
        {
            "period": period,
            "x": format(x_by_period[period].quantize(Decimal("0.1")), "f"),
        }
        for period in periods
    ]

    series: list[dict[str, object]] = []
    table_rows: list[dict[str, object]] = []
    for selected_company in companies:
        company_rows = sorted(
            (row for row in selected if row.company_id == selected_company),
            key=lambda item: item.period_end,
        )
        rows_by_period: dict[str, list[ObservationRecord]] = {}
        for row in company_rows:
            rows_by_period.setdefault(row.period_end, []).append(row)
        points: list[dict[str, object]] = []
        segments: list[dict[str, str]] = []
        prior_numeric: tuple[ObservationRecord, dict[str, object]] | None = None
        for period in periods:
            period_rows = rows_by_period.get(period, [])
            x = x_by_period[period].quantize(Decimal("0.1"))
            if not period_rows:
                gap: dict[str, object] = {
                    "id": None,
                    "company_id": selected_company,
                    "ticker": company_rows[0].ticker,
                    "metric_id": metric_id,
                    "metric_name": company_rows[0].metric_name,
                    "period_end": period,
                    "period_label": period,
                    "state": "ABSENT",
                    "display_value": "No published observation",
                    "evidence_locator_url": None,
                    "is_gap": True,
                    "x": format(x, "f"),
                    "y": "210.0",
                    "gap_x1": format(x - Decimal(4), "f"),
                    "gap_x2": format(x + Decimal(4), "f"),
                    "gap_y1": "206.0",
                    "gap_y2": "214.0",
                    "gap_label": "—",
                    "plotted": False,
                }
                points.append(gap)
                table_rows.append(gap)
                prior_numeric = None
                continue
            unambiguous_cell = len(period_rows) == 1
            if not unambiguous_cell:
                prior_numeric = None
            for row in period_rows:
                row_view = {**_row_view(row), "is_gap": row.value is None}
                table_rows.append(row_view)
                if row.value is None:
                    points.append(
                        {
                            **row_view,
                            "x": format(x, "f"),
                            "y": "210.0",
                            "gap_x1": format(x - Decimal(4), "f"),
                            "gap_x2": format(x + Decimal(4), "f"),
                            "gap_y1": "206.0",
                            "gap_y2": "214.0",
                            "gap_label": "ND" if row.state == "NOT_DISCLOSED" else "—",
                            "plotted": False,
                        }
                    )
                    prior_numeric = None
                    continue
                value = Decimal(row.value)
                y = chart_top + ((maximum - value) / span * chart_height)
                point: dict[str, object] = {
                    **row_view,
                    "x": format(x, "f"),
                    "y": format(y.quantize(Decimal("0.1")), "f"),
                    "gap_label": "",
                    "plotted": True,
                }
                points.append(point)
                if (
                    unambiguous_cell
                    and prior_numeric is not None
                    and _semantics_align(prior_numeric[0], row)
                ):
                    segments.append(
                        {
                            "x1": str(prior_numeric[1]["x"]),
                            "y1": str(prior_numeric[1]["y"]),
                            "x2": str(point["x"]),
                            "y2": str(point["y"]),
                        }
                    )
                prior_numeric = (row, point) if unambiguous_cell else None
        series.append(
            {
                "company_id": selected_company,
                "ticker": company_rows[0].ticker,
                "classification": company_rows[0].company_classification,
                "points": points,
                "segments": segments,
            }
        )

    table_rows.sort(
        key=lambda row: (
            str(row["period_end"]),
            str(row["company_id"]),
            str(row["id"] or ""),
        )
    )
    metric_name = selected[0].metric_name if selected else metric_id.replace("_", " ").title()
    return {
        "metric_id": metric_id,
        "metric_name": metric_name,
        "status": "available" if numeric_values else "gaps_only",
        "periods": periods,
        "series": series,
        "axis_periods": axis_periods,
        "table_rows": table_rows,
        "minimum": _exact_decimal_text(minimum) if numeric_values else None,
        "maximum": _exact_decimal_text(maximum) if numeric_values else None,
        "unit": selected[0].unit,
        "scale": selected[0].scale,
    }


def _observation_payload(
    repo: IntelligenceRepository,
    row: ObservationRecord,
) -> dict[str, object]:
    payload = row.as_dict()
    evidence = repo.evidence(row.evidence_id) if row.evidence_id is not None else None
    payload.update(
        {
            "observation_id": row.id,
            "normalized_value": row.value,
            "validation_status": row.quality_state,
            "evidence": evidence,
            "evidence_locator_url": _evidence_locator_url(row),
        }
    )
    return payload


def _evidence_locator_url(row: ObservationRecord) -> str | None:
    """Build the stable no-script target for an observation's exact source locator."""
    if row.evidence_id is None:
        return None
    evidence_id = quote(row.evidence_id, safe="")
    observation_id = quote(row.id, safe="")
    return f"/evidence/{evidence_id}/observations/{observation_id}#cited-source-locator"


def _with_comparison_locators(
    repo: IntelligenceRepository,
    comparison: dict[str, object],
) -> dict[str, object]:
    """Attach the same precise no-script target used by every other value view."""
    for side_name in ("left", "right"):
        side = comparison.get(side_name)
        if not isinstance(side, dict):
            continue
        observation_id = side.get("id")
        if not isinstance(observation_id, str):
            continue
        record = repo.observation(observation_id)
        side["evidence_locator_url"] = _evidence_locator_url(record) if record is not None else None
    return comparison


class _RetainedRowParser(HTMLParser):
    """Extract text cells from retained table rows without executing source markup."""

    def __init__(self, *, target_element_id: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row_element_ids: list[frozenset[str]] = []
        self.target_text: list[str] = []
        self._row: list[str] | None = None
        self._row_ids: set[str] = set()
        self._cell: list[str] | None = None
        self._target_element_id = target_element_id
        self._target_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id") or attributes.get("name")
        if self._target_depth:
            self._target_depth += 1
        elif self._target_element_id is not None and element_id == self._target_element_id:
            self._target_depth = 1
        if tag == "tr":
            self._row = []
            self._row_ids = set()
        if self._row is not None and element_id:
            self._row_ids.add(element_id)
        if tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._target_depth:
            self.target_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            cell = " ".join("".join(self._cell).split())
            if cell:
                self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
                self.row_element_ids.append(frozenset(self._row_ids))
            self._row = None
            self._row_ids = set()
            self._cell = None
        if self._target_depth:
            self._target_depth -= 1


@dataclass(frozen=True, slots=True)
class _RetainedSourceExcerpt:
    """Verified retained-source transcription or an honest unavailable state."""

    cells: tuple[str, ...]
    status: str
    message: str


def _locator_element_id(locator: str) -> str | None:
    for component in locator.split(";"):
        key, separator, value = component.partition("=")
        if separator and key.strip().casefold() in {"element_id", "id"}:
            candidate = value.strip()
            if _IDENTIFIER_PATTERN.fullmatch(candidate):
                return candidate
    match = re.search(r"\[@id=['\"]([^'\"]+)['\"]\]", locator)
    if match is not None and _IDENTIFIER_PATTERN.fullmatch(match.group(1)):
        return match.group(1)
    return None


def _xml_locator_cells(
    source: str,
    *,
    locator: str,
    target_element_id: str | None,
) -> tuple[str, ...]:
    """Resolve a retained XML/inline-XBRL element without external resources."""
    try:
        root = ET.fromstring(source)  # noqa: S314 - verified, bounded local bytes
    except ET.ParseError:
        return ()
    concept = locator.partition(";")[0].removeprefix("xbrl:").casefold()
    context = next(
        (
            value.strip()
            for component in locator.split(";")
            for key, separator, value in (component.partition("="),)
            if separator and key.strip().casefold() == "context"
        ),
        None,
    )
    for element in root.iter():
        element_id = next(
            (
                value
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1].casefold() in {"id", "name"}
            ),
            None,
        )
        local_name = element.tag.rsplit("}", 1)[-1].casefold()
        qualified_name = local_name
        name_attribute = next(
            (
                value.casefold()
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1].casefold() == "name"
            ),
            "",
        )
        context_attribute = next(
            (
                value
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1].casefold() in {"context", "contextref"}
            ),
            None,
        )
        id_match = target_element_id is not None and element_id == target_element_id
        concept_match = bool(concept) and concept in {qualified_name, name_attribute}
        context_match = context is None or context_attribute == context
        if id_match or (concept_match and context_match):
            text_value = " ".join("".join(element.itertext()).replace("\xa0", " ").split())
            return (text_value,) if text_value else ()
    return ()


def _transcribe_retained_source(
    source: str,
    *,
    observation: ObservationRecord,
) -> tuple[str, ...]:
    target_element_id = _locator_element_id(observation.evidence_locator)
    parser = _RetainedRowParser(target_element_id=target_element_id)
    try:
        parser.feed(source)
    except (AssertionError, ValueError):
        return _xml_locator_cells(
            source,
            locator=observation.evidence_locator,
            target_element_id=target_element_id,
        )
    if target_element_id is not None:
        for row, element_ids in zip(parser.rows, parser.row_element_ids, strict=True):
            if target_element_id in element_ids:
                return tuple(row)
    label = " ".join(observation.reported_label.split()).casefold()
    candidates = [row for row in parser.rows if label in " ".join(row).casefold()]
    reported = " ".join((observation.reported_value or "").split()).casefold()
    matching = [row for row in candidates if reported and reported in " ".join(row).casefold()]
    if matching or candidates:
        return tuple((matching or candidates)[-1])
    targeted_text = " ".join("".join(parser.target_text).replace("\xa0", " ").split())
    if targeted_text:
        return (targeted_text,)
    return _xml_locator_cells(
        source,
        locator=observation.evidence_locator,
        target_element_id=target_element_id,
    )


def _retained_source_excerpt(  # noqa: C901, PLR0911, PLR0912
    evidence: dict[str, object],
    observation: ObservationRecord,
    *,
    evidence_root: Path | None = None,
) -> _RetainedSourceExcerpt:
    """Resolve and verify one bounded retained HTML/XML source object."""
    location = evidence.get("retention_location")
    expected_digest = evidence.get("content_sha256")
    expected_length = evidence.get("byte_length")
    if (
        not isinstance(location, str)
        or not isinstance(expected_digest, str)
        or _SHA256_PATTERN.fullmatch(expected_digest) is None
        or not isinstance(expected_length, int)
        or isinstance(expected_length, bool)
        or not 0 < expected_length <= _MAX_RETAINED_EVIDENCE_BYTES
    ):
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "Retained evidence metadata is invalid; no source transcription is available.",
        )

    retained_path: Path
    supported_suffix = False
    config_prefix = "config-recorded://"
    content_prefix = "content-sha256://"
    if location.startswith(config_prefix):
        try:
            root = config_directory().resolve()
        except FileNotFoundError:
            return _RetainedSourceExcerpt(
                (),
                "unavailable",
                "Retained evidence bytes are unavailable in this runtime.",
            )
        retained_path = (root / location.removeprefix(config_prefix)).resolve()
        supported_suffix = retained_path.suffix.casefold() in {".htm", ".html", ".xhtml", ".xml"}
    elif location.startswith(content_prefix):
        location_digest = location.removeprefix(content_prefix)
        if (
            evidence_root is None
            or _SHA256_PATTERN.fullmatch(location_digest) is None
            or not hmac.compare_digest(location_digest, expected_digest)
        ):
            return _RetainedSourceExcerpt(
                (),
                "unavailable",
                "Retained evidence bytes are unavailable in this bounded runtime.",
            )
        root = evidence_root.resolve()
        retained_path = (root / location_digest[:2] / f"{location_digest}.bin").resolve()
    else:
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "The evidence retention scheme is not available for local transcription.",
        )

    if not retained_path.is_relative_to(root):
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "The retained evidence path is outside the bounded runtime root.",
        )
    media_type = str(evidence.get("media_type") or "").casefold()
    if not supported_suffix and "html" not in media_type and "xml" not in media_type:
        return _RetainedSourceExcerpt(
            (),
            "unsupported",
            "Verified retained bytes are not an HTML or XML document; no row is transcribed.",
        )
    try:
        retained_size = retained_path.stat().st_size
    except OSError:
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "Retained evidence bytes are unavailable or incomplete in this runtime.",
        )
    if retained_size != expected_length:
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "Retained evidence bytes are unavailable or incomplete in this runtime.",
        )
    try:
        content = retained_path.read_bytes()
    except OSError:
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "Retained evidence bytes are unavailable or incomplete in this runtime.",
        )
    actual_digest = hashlib.sha256(content).hexdigest()
    if len(content) != expected_length or not hmac.compare_digest(actual_digest, expected_digest):
        return _RetainedSourceExcerpt(
            (),
            "integrity_error",
            "Retained evidence failed integrity verification; no source transcription is shown.",
        )
    try:
        source = content.decode("utf-8", errors="strict")
    except UnicodeError:
        return _RetainedSourceExcerpt(
            (),
            "unavailable",
            "Verified retained evidence is not valid UTF-8 text; no row is transcribed.",
        )
    cells = _transcribe_retained_source(source, observation=observation)
    if not cells:
        return _RetainedSourceExcerpt(
            (),
            "locator_unavailable",
            "Retained bytes were verified, but the cited HTML/XML fragment could not be isolated.",
        )
    return _RetainedSourceExcerpt(cells, "verified", "Retained source bytes verified.")


def _retained_source_row(
    evidence: dict[str, object],
    observation: ObservationRecord,
    *,
    evidence_root: Path | None = None,
) -> list[str]:
    """Compatibility wrapper returning only verified retained source cells."""
    return list(
        _retained_source_excerpt(
            evidence,
            observation,
            evidence_root=evidence_root,
        ).cells
    )


def _evidence_payload(
    repo: IntelligenceRepository,
    evidence_id: str,
) -> dict[str, object] | None:
    payload = repo.evidence(evidence_id)
    if payload is None:
        return None
    linked_ids = repo.evidence_observation_ids(evidence_id, limit=_MAX_API_RESULTS)
    return {**payload, "linked_observation_ids": linked_ids}


def _quality_summary(repo: IntelligenceRepository) -> dict[str, object]:
    freshness = repo.freshness()
    coverage = repo.coverage()
    missing = sum(cast("int", item["missing"]) for item in coverage)
    reported = sum(cast("int", item["reported"]) for item in coverage)
    source_not_checked = sum(cast("int", item["source_not_checked"]) for item in coverage)
    quality_counts = repo.quality_counts()

    retrieved_raw = freshness.get("retrieved_at")
    stale_days: int | None = None
    if isinstance(retrieved_raw, str):
        retrieved = datetime.fromisoformat(retrieved_raw)
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=UTC)
        stale_days = max((datetime.now(tz=UTC) - retrieved).days, 0)
    is_stale = stale_days is None or stale_days > _STALE_AFTER_DAYS
    freshness_state = "unavailable" if stale_days is None else ("stale" if is_stale else "current")
    calendar_rows = cast("list[dict[str, object]]", freshness.get("calendar", []))
    calendar_freshness_by_company = {
        str(item["company_id"]): str(item["freshness_state"])
        for item in calendar_rows
        if "company_id" in item and "freshness_state" in item
    }
    calendar_states = set(calendar_freshness_by_company.values())
    if not calendar_states:
        calendar_freshness_state = "UNASSESSED"
    elif len(calendar_states) == 1:
        calendar_freshness_state = next(iter(calendar_states))
    else:
        calendar_freshness_state = "MIXED"
    assessed_count = int(cast("int", freshness.get("source_assessment_count", 0)))
    covered_cell_count = reported + missing + source_not_checked
    coverage_state = (
        "unassessed"
        if not covered_cell_count or not assessed_count
        else ("partial" if missing or source_not_checked else "complete")
    )
    return {
        **freshness,
        "reported_count": reported,
        "not_disclosed_count": missing,
        "source_not_checked_count": source_not_checked,
        "coverage_state": coverage_state,
        **quality_counts,
        "age_days": stale_days,
        "is_stale": is_stale,
        "freshness_state": freshness_state,
        "calendar_freshness_state": calendar_freshness_state,
        "calendar_freshness_by_company": calendar_freshness_by_company,
    }


def _safe_source_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


def _metric_groups(metrics: Iterable[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for metric in metrics:
        groups.setdefault(str(metric["category"]), []).append(metric)
    return groups


def _bounded_evidence_root(
    *,
    runtime_root: Path | None,
    evidence_root: Path | None,
) -> Path | None:
    """Resolve an injected read-only content store below its declared runtime."""
    runtime = runtime_root.resolve() if runtime_root is not None else None
    if evidence_root is None:
        return runtime / "evidence" / "edgartools" if runtime is not None else None
    evidence = evidence_root.resolve()
    if runtime is not None and not evidence.is_relative_to(runtime):
        msg = "evidence_root must remain within runtime_root"
        raise ValueError(msg)
    return evidence


def _database_preflight_error(
    code: str,
    message: str,
    next_action: str,
) -> dict[str, str]:
    """Build one safe structured database-read failure."""
    return {"code": code, "message": message, "next_action": next_action}


def _preflight_read_database_url(  # noqa: PLR0911
    database_url: str,
) -> tuple[str | None, dict[str, str] | None]:
    """Require an existing current SQLite database and reopen it read-only."""
    try:
        parsed = make_url(database_url)
    except (TypeError, ValueError, SQLAlchemyError):
        return None, _database_preflight_error(
            "database_url_invalid",
            "The configured read database URL is invalid.",
            "Provide a valid URL for an existing current database.",
        )
    if parsed.get_backend_name() != "sqlite":
        return database_url, None
    database = parsed.database
    if database is None or database in {"", ":memory:"} or database.startswith("file:"):
        return None, _database_preflight_error(
            "database_path_required",
            "The read application requires an existing on-disk SQLite database.",
            "Provide sqlite:///path/to/an/existing/current.db.",
        )
    database_path = Path(database).expanduser()
    if not database_path.is_absolute():
        database_path = (Path.cwd() / database_path).resolve()
    else:
        database_path = database_path.resolve()
    if not database_path.is_file():
        return None, _database_preflight_error(
            "database_not_found",
            "The configured SQLite database does not exist and was not created.",
            "Initialize or ingest an explicit database, then retry the read application.",
        )
    encoded_path = quote(database_path.as_posix(), safe="/:")
    try:
        with closing(
            sqlite3.connect(
                f"file:{encoded_path}?mode=ro",
                uri=True,
            )
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            revisions = tuple(
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
            )
    except sqlite3.Error:
        return None, _database_preflight_error(
            "database_schema_not_current",
            "The database schema is missing or is not readable as the current schema.",
            "Migrate the database explicitly outside this read application, then retry.",
        )
    if revisions != (_CURRENT_SCHEMA_REVISION,):
        return None, _database_preflight_error(
            "database_schema_not_current",
            "The database schema is not at the required current revision.",
            "Migrate the database explicitly outside this read application, then retry.",
        )
    return f"sqlite:///file:{encoded_path}?mode=ro&uri=true", None


def create_app(  # noqa: C901, PLR0915
    *,
    database_url: str | None = None,
    repository: IntelligenceRepository | None = None,
    bootstrap_phase3: bool = False,
    runtime_root: Path | None = None,
    evidence_root: Path | None = None,
) -> FastAPI:
    """Create the read-only application with dependency-injectable persistence.

    Args:
        database_url: Optional database URL used when no repository is injected.
        repository: Optional preconfigured bounded read repository.
        bootstrap_phase3: Explicitly seed the governed retained Phase 3 layer for
            a newly constructed local repository. The default factory is read-only.
        runtime_root: Optional bounded runtime root containing ``evidence/edgartools``.
        evidence_root: Optional direct bounded content-addressed evidence root.
    """
    retained_evidence_root = _bounded_evidence_root(
        runtime_root=runtime_root,
        evidence_root=evidence_root,
    )
    active_repository = repository
    database_preflight_error: dict[str, str] | None = None
    if active_repository is None:
        resolved_database_url = database_url
        if resolved_database_url is None:
            resolved_database_url = (
                default_database_url()
                if bootstrap_phase3
                else f"sqlite:///{(Path.cwd() / '.msi' / 'msi.db').resolve().as_posix()}"
            )
        if bootstrap_phase3:
            engine = create_database_engine(resolved_database_url)
            seed_phase3(engine)
            active_repository = IntelligenceRepository(engine)
        else:
            read_database_url, database_preflight_error = _preflight_read_database_url(
                resolved_database_url
            )
            if read_database_url is not None:
                active_repository = IntelligenceRepository(
                    create_database_engine(read_database_url)
                )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="Servicing Lens",
        version="0.1.0",
        description=(
            "Bounded, source-traceable public financial intelligence for governed "
            "U.S. mortgage servicers. Every public operation is read-only."
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.repository = active_repository
    app.state.database_preflight_error = database_preflight_error
    app.state.evidence_root = retained_evidence_root
    templates = Jinja2Templates(directory=_asset_root() / "templates")
    app.mount("/static", StaticFiles(directory=_asset_root() / "static"), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def safe_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> HTMLResponse | JSONResponse:
        raw_detail: object = error.detail
        structured_detail = raw_detail if isinstance(raw_detail, dict) else None
        detail = (
            raw_detail
            if isinstance(raw_detail, str)
            else (
                str(structured_detail.get("message", "The request could not be served."))
                if structured_detail is not None
                else "The request could not be served."
            )
        )
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": structured_detail or detail},
            )
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            status_code=error.status_code,
            context={"status_code": error.status_code, "detail": detail},
        )

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health(repo: RepositoryDependency) -> HealthResponse:
        with repo.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        latest = repo.latest_period_end()
        return HealthResponse(
            status="ready",
            database="reachable",
            companies=len(repo.companies()),
            latest_period_end=latest.isoformat() if latest is not None else None,
        )

    @app.get(
        "/api/v1/companies",
        response_model=list[CompanyResponse],
        tags=["catalog"],
    )
    def companies(
        repo: RepositoryDependency,
        limit: int = _MAX_API_RESULTS,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        return _page(repo.companies(), limit=limit, offset=offset)

    @app.get(
        "/api/v1/companies/{company_id}",
        response_model=CompanyResponse,
        tags=["catalog"],
    )
    def company(company_id: str, repo: RepositoryDependency) -> dict[str, object]:
        _validate_identifier(company_id, label="company identifier")
        result = next((item for item in repo.companies() if item["id"] == company_id), None)
        if result is None:
            raise HTTPException(status_code=404, detail="company not found")
        return result

    @app.get(
        "/api/v1/metrics",
        response_model=list[MetricResponse],
        tags=["catalog"],
    )
    def metrics(
        repo: RepositoryDependency,
        limit: int = _MAX_API_RESULTS,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        return _page(repo.metrics(), limit=limit, offset=offset)

    @app.get(
        "/api/v1/observations",
        response_model=list[ObservationResponse],
        tags=["observations"],
    )
    def observations(  # noqa: PLR0913, PLR0917
        repo: RepositoryDependency,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        as_of: datetime | None = None,
        include_missing: bool = True,  # noqa: FBT001, FBT002
        limit: int = _MAX_API_RESULTS,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        if company_id is not None:
            _validate_identifier(company_id, label="company identifier")
        if metric_id is not None:
            _validate_identifier(metric_id, label="metric identifier")
        bounded_limit, bounded_offset = _validate_page(limit, offset)
        records = repo.observations(
            as_of=as_of,
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
            include_missing=include_missing,
            limit=bounded_limit,
            offset=bounded_offset,
        )
        return [row.as_dict() for row in records]

    @app.get(
        "/api/v1/observations/{observation_id}",
        response_model=ObservationDetailResponse,
        tags=["observations"],
    )
    def observation(observation_id: str, repo: RepositoryDependency) -> dict[str, object]:
        _validate_identifier(observation_id, label="observation identifier")
        row = repo.observation(observation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="observation not found")
        return _observation_payload(repo, row)

    @app.get(
        "/api/v1/comparisons",
        response_model=ComparisonResponse | list[ComparisonResponse],
        tags=["analysis"],
    )
    def comparison(
        repo: RepositoryDependency,
        metric_id: str,
        period_end: date,
        as_of: datetime | None = None,
        company_id: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, object] | list[dict[str, object]]:
        _validate_identifier(metric_id, label="metric identifier")
        selected = _comparison_selection(repo, company_id, as_of=as_of)
        try:
            results = repo.compare_pairs(
                metric_id=metric_id,
                period_end=period_end,
                as_of=as_of,
                company_ids=selected,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if results is None:
            raise HTTPException(status_code=404, detail="comparison inputs not found")
        payloads = [result.as_dict() for result in results]
        return payloads[0] if len(selected) == _MIN_COMPARISON_COMPANY_COUNT else payloads

    @app.get(
        "/api/v1/coverage",
        response_model=list[CoverageResponse],
        tags=["quality"],
    )
    def coverage(
        repo: RepositoryDependency,
        as_of: datetime | None = None,
        limit: int = _MAX_API_RESULTS,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        return _page(repo.coverage(as_of=as_of), limit=limit, offset=offset)

    @app.get(
        "/api/v1/evidence/{evidence_id}",
        response_model=EvidenceResponse,
        tags=["evidence"],
    )
    def evidence(evidence_id: str, repo: RepositoryDependency) -> dict[str, object]:
        _validate_identifier(evidence_id, label="evidence identifier")
        result = _evidence_payload(repo, evidence_id)
        if result is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        return result

    @app.get(
        "/evidence/{evidence_id}/observations/{observation_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def evidence_locator_view(
        request: Request,
        evidence_id: str,
        observation_id: str,
        repo: RepositoryDependency,
    ) -> HTMLResponse:
        """Render a focused, accessible fragment from the retained cited source."""
        _validate_identifier(evidence_id, label="evidence identifier")
        _validate_identifier(observation_id, label="observation identifier")
        observation_record = repo.observation(observation_id)
        if observation_record is None:
            raise HTTPException(status_code=404, detail="observation not found")
        evidence_link = next(
            (
                item
                for item in observation_record.evidence_links
                if item.get("evidence_id") == evidence_id
            ),
            None,
        )
        if evidence_link is None:
            raise HTTPException(status_code=404, detail="evidence linkage not found")
        retained_evidence = repo.evidence(evidence_id)
        if retained_evidence is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        locator_record = replace(
            observation_record,
            evidence_id=evidence_id,
            evidence_locator=str(
                evidence_link.get("locator") or observation_record.evidence_locator
            ),
            reported_label=str(evidence_link.get("raw_label") or observation_record.reported_label),
            reported_value=str(evidence_link.get("raw_value") or observation_record.reported_value),
        )
        retained_excerpt = _retained_source_excerpt(
            retained_evidence,
            locator_record,
            evidence_root=cast("Path | None", request.app.state.evidence_root),
        )
        response = templates.TemplateResponse(
            request=request,
            name="evidence_locator.html",
            context={
                "observation": _row_view(locator_record),
                "evidence": retained_evidence,
                "retained_row": retained_excerpt.cells,
                "retained_status": retained_excerpt.status,
                "retained_message": retained_excerpt.message,
                "safe_source_url": _safe_source_url(
                    retained_evidence.get("original_url"),
                ),
            },
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.get(
        "/api/v1/earnings-events",
        response_model=list[EarningsEventResponse],
        tags=["events"],
    )
    def earnings_events(
        repo: RepositoryDependency,
        limit: int = _MAX_API_RESULTS,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        return _page(repo.earnings_events(), limit=limit, offset=offset)

    @app.get(
        "/api/v1/calendar",
        response_model=list[CalendarResponse],
        tags=["events"],
    )
    def calendar(repo: RepositoryDependency) -> list[dict[str, object]]:
        return repo.calendar()

    @app.get(
        "/api/v1/pipeline/freshness",
        response_model=FreshnessResponse,
        tags=["quality"],
    )
    def freshness(repo: RepositoryDependency) -> dict[str, object]:
        return _quality_summary(repo)

    def render(  # noqa: C901, PLR0913
        request: Request,
        repo: IntelligenceRepository,
        *,
        page: str,
        title: str,
        company_id: str | None = None,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
        comparison_company_ids: tuple[str, ...] = _DEFAULT_COMPARISON_COMPANY_IDS,
    ) -> HTMLResponse:
        companies_payload = repo.companies()
        comparison_supported = set(repo.comparison_company_ids())
        comparison_companies = [
            item for item in companies_payload if str(item["id"]) in comparison_supported
        ]
        if (
            not _MIN_COMPARISON_COMPANY_COUNT
            <= len(comparison_company_ids)
            <= _MAX_COMPARISON_COMPANY_COUNT
            or len(set(comparison_company_ids)) != len(comparison_company_ids)
            or any(item not in comparison_supported for item in comparison_company_ids)
        ):
            comparison_company_ids = _default_page_comparison_selection(repo)
        metrics_payload = repo.metrics()
        known_metrics = {str(item["id"]) for item in metrics_payload}
        if known_metrics and metric_id not in known_metrics:
            raise HTTPException(status_code=404, detail="metric not found")
        all_rows = repo.observation_snapshot(include_missing=True)
        latest_date = repo.latest_period_end()
        latest = latest_date.isoformat() if latest_date is not None else None
        selected_period = period_end.isoformat() if period_end is not None else latest
        periods = sorted({row.period_end for row in all_rows})
        page_rows = [row for row in all_rows if company_id is None or row.company_id == company_id]
        featured = [row for row in page_rows if row.metric_id in _EXECUTIVE_METRICS]

        def page_comparisons(selected_metric_id: str) -> tuple[ComparisonRecord, ...] | None:
            if len(comparison_company_ids) < _MIN_COMPARISON_COMPANY_COUNT:
                return None
            try:
                return repo.compare_pairs(
                    metric_id=selected_metric_id,
                    period_end=date.fromisoformat(cast("str", selected_period)),
                    company_ids=comparison_company_ids,
                )
            except ValueError:
                return None

        comparisons: list[dict[str, object]] = []
        if selected_period is not None:
            selected_comparisons = page_comparisons(metric_id)
            if selected_comparisons is not None:
                comparisons.extend(
                    _with_comparison_locators(repo, comparison.as_dict())
                    for comparison in selected_comparisons
                )
            if metric_id != "servicing_revenue":
                economics_comparisons = page_comparisons("servicing_revenue")
                if economics_comparisons is not None:
                    comparisons.extend(
                        _with_comparison_locators(repo, comparison.as_dict())
                        for comparison in economics_comparisons
                    )
        highlights = {
            company["id"]: _highlight_rows(
                all_rows,
                company_id=str(company["id"]),
                period_end=selected_period,
            )
            for company in companies_payload
        }
        quality = _quality_summary(repo)
        chart = _chart_model(page_rows, metric_id=metric_id, company_id=company_id)
        presentation_companies = cast("list[CompanyIdentity]", companies_payload)
        card_companies = presentation_companies
        if page == "comparison":
            company_by_id = {item["id"]: item for item in presentation_companies}
            card_companies = [
                company_by_id[item] for item in comparison_company_ids if item in company_by_id
            ]
        presentation_periods = (
            {company["id"]: selected_period for company in card_companies}
            if selected_period is not None
            else None
        )
        cards = normalize_companies(
            card_companies,
            all_rows,
            target_periods=presentation_periods,
        )
        earnings_events_payload = cast("list[EarningsIdentity]", repo.earnings_events())
        earnings_briefs = normalize_earnings(
            presentation_companies,
            all_rows,
            earnings_events_payload,
        )
        scale_comparisons = (
            page_comparisons("total_servicing_upb")
            if page == "comparison" and selected_period is not None
            else None
        )
        if scale_comparisons is None:
            scale_assessment = ScaleAssessment(
                status="insufficient_information",
                reasons=(
                    (
                        "Relative scale is limited to explicitly selected comparison issuers"
                        if page != "comparison"
                        else "A governed same-period servicing UPB comparison is unavailable"
                    ),
                ),
            )
        else:
            blocked = [
                comparison for comparison in scale_comparisons if comparison.status != "comparable"
            ]
            scale_assessment = ScaleAssessment(
                status=blocked[0].status if blocked else "comparable",
                reasons=tuple(
                    dict.fromkeys(reason for comparison in blocked for reason in comparison.reasons)
                ),
            )
        serialized_cards = serialize_cards(cards, scale_assessment=scale_assessment)
        active_company = next(
            (item for item in serialized_cards if item["id"] == company_id),
            None,
        )
        active_metric = next(
            (item for item in metrics_payload if item["id"] == metric_id),
            None,
        )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": page,
                "title": title,
                "companies": companies_payload,
                "comparison_companies": comparison_companies,
                "metrics": metrics_payload,
                "metric_groups": _metric_groups(metrics_payload),
                "rows": [_row_view(row) for row in page_rows],
                "featured": [_row_view(row) for row in featured],
                "coverage": repo.coverage(),
                "comparisons": comparisons,
                "latest": latest,
                "periods": periods,
                "selected_period": selected_period,
                "selected_metric": metric_id,
                "selected_company": company_id,
                "selected_comparison_companies": comparison_company_ids,
                "highlights": highlights,
                "chart": chart,
                "quality": quality,
                "safe_source_url": _safe_source_url,
                "servicing_cards": serialized_cards,
                "earnings_briefs": serialize_earnings(earnings_briefs),
                "scale_assessment": asdict(scale_assessment),
                "active_company": active_company,
                "active_metric": active_metric,
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def overview(
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
    ) -> HTMLResponse:
        return render(
            request,
            repo,
            page="overview",
            title="Executive portfolio brief",
            metric_id=metric_id,
            period_end=period_end,
        )

    @app.get("/companies/{company_id}", response_class=HTMLResponse, include_in_schema=False)
    def company_page(
        company_id: str,
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
    ) -> HTMLResponse:
        if company_id not in {str(item["id"]) for item in repo.companies()}:
            raise HTTPException(status_code=404, detail="company not found")
        company_payload = next(item for item in repo.companies() if item["id"] == company_id)
        return render(
            request,
            repo,
            page="company",
            title=f"{company_payload['ticker']} disclosure profile",
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
        )

    @app.get("/metrics/{metric_id}", response_class=HTMLResponse, include_in_schema=False)
    def metric_page(
        metric_id: str,
        request: Request,
        repo: RepositoryDependency,
        period_end: date | None = None,
    ) -> HTMLResponse:
        metric_payload = next(
            (item for item in repo.metrics() if item["id"] == metric_id),
            None,
        )
        if metric_payload is None:
            raise HTTPException(status_code=404, detail="metric not found")
        return render(
            request,
            repo,
            page="metric",
            title=str(metric_payload["display_name"]),
            metric_id=metric_id,
            period_end=period_end,
        )

    @app.get("/comparison", response_class=HTMLResponse, include_in_schema=False)
    def comparison_page(  # noqa: PLR0913, PLR0917
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
        company_id: Annotated[list[str] | None, Query()] = None,
        third_company_id: str | None = None,
    ) -> HTMLResponse:
        requested = list(company_id) if company_id is not None else None
        if third_company_id:
            requested = list(requested or _DEFAULT_COMPARISON_COMPANY_IDS)
            requested.append(third_company_id)
        selected = (
            _default_page_comparison_selection(repo)
            if requested is None
            else _comparison_selection(repo, requested)
        )
        return render(
            request,
            repo,
            page="comparison",
            title="Pairwise comparisons",
            metric_id=metric_id,
            period_end=period_end,
            comparison_company_ids=selected,
        )

    @app.get("/earnings", response_class=HTMLResponse, include_in_schema=False)
    def earnings_page(
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
    ) -> HTMLResponse:
        """Render deterministic briefs from the existing earnings-event pipeline."""
        return render(
            request,
            repo,
            page="earnings",
            title="Earnings brief",
            metric_id=metric_id,
        )

    @app.get("/data-quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
    ) -> HTMLResponse:
        return render(
            request,
            repo,
            page="quality",
            title="Coverage, freshness & limitations",
            metric_id=metric_id,
        )

    @app.get("/methodology", response_class=HTMLResponse, include_in_schema=False)
    def methodology_page(
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
    ) -> HTMLResponse:
        return render(
            request,
            repo,
            page="methodology",
            title="Methods & metric catalog",
            metric_id=metric_id,
        )

    return app
