"""Bounded read API and server-rendered Stage A intelligence dashboard."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from mortgage_servicing_dashboard.database import (
    IngestionError,
    ObservationEvidence,
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
    default_database_url,
)
from mortgage_servicing_dashboard.presentation import (
    CompanyIdentity,
    EarningsIdentity,
    ScaleAssessment,
    normalize_companies,
    normalize_earnings,
    serialize_cards,
    serialize_earnings,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    ObservationRecord,
    config_directory,
    seed_stage_a,
)


def _repository_from_request(request: Request) -> IntelligenceRepository:
    """Resolve the application repository without leaking it into the query schema."""
    return cast("IntelligenceRepository", request.app.state.repository)


RepositoryDependency = Annotated[
    IntelligenceRepository,
    Depends(_repository_from_request),
]

_MAX_API_RESULTS = 100
_MAX_OFFSET = 10_000
_STALE_AFTER_DAYS = 120
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DEFAULT_METRIC = "total_servicing_upb"
_EXECUTIVE_METRICS = (
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
_HIGHLIGHT_PRIORITY = {
    "tfc": (
        "total_servicing_upb",
        "servicing_for_others_upb",
        "servicing_revenue",
        "weighted_average_servicing_fee_bps",
    ),
    "pfsi": (
        "total_servicing_upb",
        "owned_msr_upb",
        "servicing_fee_income",
        "servicing_operating_expense",
        "servicing_pretax_income",
    ),
}


class HealthResponse(BaseModel):
    """Deterministic readiness response."""

    model_config = ConfigDict(extra="forbid")
    status: str
    database: str
    companies: int
    latest_period_end: str | None
    model_calls_enabled: bool


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
    event_at: str
    evidence_id: str
    source_url: str


class FreshnessResponse(ReadResponse):
    """Evidence, publication, pipeline and missingness freshness."""

    dataset: str
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
    model_calls_enabled: bool
    reported_count: int
    coverage_state: str
    quarantined_candidate_count: int
    failed_run_count: int
    ingestion_error_count: int
    age_days: int | None
    is_stale: bool
    freshness_state: str


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
    return f"Q{row.fiscal_quarter} {row.fiscal_year}"


def _semantics_align(left: ObservationRecord, right: ObservationRecord) -> bool:
    return (
        left.metric_id == right.metric_id
        and left.metric_version == right.metric_version
        and left.reporting_entity_id == right.reporting_entity_id
        and left.reporting_scope_id == right.reporting_scope_id
        and left.currency == right.currency
        and left.unit == right.unit
        and left.scale == right.scale
        and left.methodology == right.methodology
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
    for metric_id in _HIGHLIGHT_PRIORITY[company_id]:
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


def _chart_model(
    rows: list[ObservationRecord],
    *,
    metric_id: str,
    company_id: str | None,
) -> dict[str, object]:
    selected = [
        row
        for row in rows
        if row.metric_id == metric_id and (company_id is None or row.company_id == company_id)
    ]
    periods = sorted({row.period_end for row in selected})
    numeric_values = [Decimal(row.value) for row in selected if row.value is not None]
    companies = sorted({row.company_id for row in selected})
    if not numeric_values or not periods:
        return {
            "metric_id": metric_id,
            "status": "empty",
            "periods": periods,
            "series": [],
            "table_rows": [_row_view(row) for row in selected],
        }

    minimum = min(numeric_values)
    maximum = max(numeric_values)
    span = maximum - minimum
    if span == 0:
        span = Decimal(1)
    chart_left = Decimal(72)
    chart_width = Decimal(576)
    chart_top = Decimal(28)
    chart_height = Decimal(168)

    x_by_period: dict[str, Decimal] = {}
    for index, period in enumerate(periods):
        denominator = max(len(periods) - 1, 1)
        x_by_period[period] = chart_left + (chart_width * Decimal(index) / Decimal(denominator))

    series: list[dict[str, object]] = []
    for selected_company in companies:
        company_rows = sorted(
            (row for row in selected if row.company_id == selected_company),
            key=lambda item: item.period_end,
        )
        points: list[dict[str, object]] = []
        segments: list[dict[str, str]] = []
        prior_numeric: tuple[ObservationRecord, dict[str, object]] | None = None
        for row in company_rows:
            x = x_by_period[row.period_end].quantize(Decimal("0.1"))
            if row.value is None:
                points.append(
                    {
                        **_row_view(row),
                        "x": format(x, "f"),
                        "y": "218.0",
                        "plotted": False,
                    }
                )
                prior_numeric = None
                continue
            value = Decimal(row.value)
            y = chart_top + ((maximum - value) / span * chart_height)
            point: dict[str, object] = {
                **_row_view(row),
                "x": format(x, "f"),
                "y": format(y.quantize(Decimal("0.1")), "f"),
                "plotted": True,
            }
            points.append(point)
            if prior_numeric is not None and _semantics_align(prior_numeric[0], row):
                segments.append(
                    {
                        "x1": str(prior_numeric[1]["x"]),
                        "y1": str(prior_numeric[1]["y"]),
                        "x2": str(point["x"]),
                        "y2": str(point["y"]),
                    }
                )
            prior_numeric = (row, point)
        series.append(
            {
                "company_id": selected_company,
                "ticker": company_rows[0].ticker,
                "classification": company_rows[0].company_classification,
                "points": points,
                "segments": segments,
            }
        )

    table_rows = sorted(selected, key=lambda row: (row.period_end, row.company_id))
    metric_name = selected[0].metric_name if selected else metric_id.replace("_", " ").title()
    return {
        "metric_id": metric_id,
        "metric_name": metric_name,
        "status": "available",
        "periods": periods,
        "series": series,
        "table_rows": [_row_view(row) for row in table_rows],
        "minimum": _exact_decimal_text(minimum),
        "maximum": _exact_decimal_text(maximum),
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        _attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            cell = " ".join("".join(self._cell).split())
            if cell:
                self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _retained_source_row(
    evidence: dict[str, object],
    observation: ObservationRecord,
) -> list[str]:
    """Read one cited row from the immutable configured source, fail-closed on paths."""
    location = evidence.get("retention_location")
    prefix = "config-recorded://"
    if not isinstance(location, str) or not location.startswith(prefix):
        return []
    root = config_directory().resolve()
    retained_path = (root / location.removeprefix(prefix)).resolve()
    if not retained_path.is_relative_to(root) or retained_path.suffix.lower() != ".html":
        return []
    try:
        source = retained_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    parser = _RetainedRowParser()
    parser.feed(source)
    label = " ".join(observation.reported_label.split()).casefold()
    candidates = [row for row in parser.rows if label in " ".join(row).casefold()]
    if not candidates:
        return []
    reported = " ".join((observation.reported_value or "").split()).casefold()
    matching = [row for row in candidates if reported and reported in " ".join(row).casefold()]
    return (matching or candidates)[-1]


def _evidence_payload(
    repo: IntelligenceRepository,
    evidence_id: str,
) -> dict[str, object] | None:
    payload = repo.evidence(evidence_id)
    if payload is None:
        return None
    statement = (
        select(ObservationEvidence.observation_id)
        .where(ObservationEvidence.evidence_id == evidence_id)
        .order_by(ObservationEvidence.observation_id)
        .limit(_MAX_API_RESULTS)
    )
    with Session(repo.engine) as session:
        linked_ids = list(session.scalars(statement))
    return {**payload, "linked_observation_ids": linked_ids}


def _quality_summary(repo: IntelligenceRepository) -> dict[str, object]:
    freshness = repo.freshness()
    coverage = repo.coverage()
    missing = sum(cast("int", item["missing"]) for item in coverage)
    reported = sum(cast("int", item["reported"]) for item in coverage)
    source_not_checked = sum(cast("int", item["source_not_checked"]) for item in coverage)
    with Session(repo.engine) as session:
        quarantined = (
            session.scalar(
                select(func.count(QuarantineCandidate.id)).where(
                    QuarantineCandidate.status.not_in(("REJECTED", "PUBLISHED"))
                )
            )
            or 0
        )
        failed_runs = (
            session.scalar(select(func.count(PipelineRun.id)).where(PipelineRun.status == "FAILED"))
            or 0
        )
        ingestion_errors = session.scalar(select(func.count(IngestionError.id))) or 0

    retrieved_raw = freshness.get("retrieved_at")
    stale_days: int | None = None
    if isinstance(retrieved_raw, str):
        retrieved = datetime.fromisoformat(retrieved_raw)
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=UTC)
        stale_days = max((datetime.now(tz=UTC) - retrieved).days, 0)
    is_stale = stale_days is None or stale_days > _STALE_AFTER_DAYS
    freshness_state = "unavailable" if stale_days is None else ("stale" if is_stale else "current")
    return {
        **freshness,
        "reported_count": reported,
        "not_disclosed_count": missing,
        "source_not_checked_count": source_not_checked,
        "coverage_state": "partial" if missing or source_not_checked else "complete",
        "quarantined_candidate_count": quarantined,
        "failed_run_count": failed_runs,
        "ingestion_error_count": ingestion_errors,
        "age_days": stale_days,
        "is_stale": is_stale,
        "freshness_state": freshness_state,
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


def create_app(  # noqa: C901, PLR0915
    *,
    database_url: str | None = None,
    repository: IntelligenceRepository | None = None,
) -> FastAPI:
    """Create the read-only application with dependency-injectable persistence."""
    active_repository = repository
    if active_repository is None:
        engine = create_database_engine(database_url or default_database_url())
        seed_stage_a(engine)
        active_repository = IntelligenceRepository(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="Servicing Lens",
        version="0.1.0",
        description=(
            "Bounded, source-traceable public financial intelligence for two selected "
            "U.S. mortgage servicers. Every public operation is read-only."
        ),
        lifespan=lifespan,
    )
    app.state.repository = active_repository
    templates = Jinja2Templates(directory=_asset_root() / "templates")
    app.mount("/static", StaticFiles(directory=_asset_root() / "static"), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def safe_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> HTMLResponse | JSONResponse:
        detail = (
            error.detail if isinstance(error.detail, str) else "The request could not be served."
        )
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=error.status_code, content={"detail": detail})
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
            model_calls_enabled=False,
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
        response_model=ComparisonResponse,
        tags=["analysis"],
    )
    def comparison(
        repo: RepositoryDependency,
        metric_id: str,
        period_end: date,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        _validate_identifier(metric_id, label="metric identifier")
        result = repo.compare(metric_id=metric_id, period_end=period_end, as_of=as_of)
        if result is None:
            raise HTTPException(status_code=404, detail="comparison inputs not found")
        return result.as_dict()

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
        if observation_record.evidence_id != evidence_id:
            raise HTTPException(status_code=404, detail="evidence linkage not found")
        retained_evidence = repo.evidence(evidence_id)
        if retained_evidence is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        response = templates.TemplateResponse(
            request=request,
            name="evidence_locator.html",
            context={
                "observation": _row_view(observation_record),
                "evidence": retained_evidence,
                "retained_row": _retained_source_row(
                    retained_evidence,
                    observation_record,
                ),
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
        "/api/v1/pipeline/freshness",
        response_model=FreshnessResponse,
        tags=["quality"],
    )
    def freshness(repo: RepositoryDependency) -> dict[str, object]:
        return _quality_summary(repo)

    def render(  # noqa: PLR0913
        request: Request,
        repo: IntelligenceRepository,
        *,
        page: str,
        title: str,
        company_id: str | None = None,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
    ) -> HTMLResponse:
        companies_payload = repo.companies()
        metrics_payload = repo.metrics()
        known_metrics = {str(item["id"]) for item in metrics_payload}
        if known_metrics and metric_id not in known_metrics:
            raise HTTPException(status_code=404, detail="metric not found")
        all_rows = repo.observations(include_missing=True)
        latest_date = repo.latest_period_end()
        latest = latest_date.isoformat() if latest_date is not None else None
        selected_period = period_end.isoformat() if period_end is not None else latest
        periods = sorted({row.period_end for row in all_rows})
        page_rows = [row for row in all_rows if company_id is None or row.company_id == company_id]
        featured = [row for row in page_rows if row.metric_id in _EXECUTIVE_METRICS]
        comparisons = []
        if selected_period is not None:
            selected_comparison = repo.compare(
                metric_id=metric_id,
                period_end=date.fromisoformat(selected_period),
            )
            if selected_comparison is not None:
                comparisons.append(
                    _with_comparison_locators(repo, selected_comparison.as_dict()),
                )
            if metric_id != "servicing_revenue":
                economics_comparison = repo.compare(
                    metric_id="servicing_revenue",
                    period_end=date.fromisoformat(selected_period),
                )
                if economics_comparison is not None:
                    comparisons.append(
                        _with_comparison_locators(repo, economics_comparison.as_dict()),
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
        presentation_periods = (
            {company["id"]: selected_period for company in presentation_companies}
            if selected_period is not None
            else None
        )
        cards = normalize_companies(
            presentation_companies,
            all_rows,
            target_periods=presentation_periods,
        )
        earnings_events_payload = cast("list[EarningsIdentity]", repo.earnings_events())
        earnings_briefs = normalize_earnings(
            presentation_companies,
            all_rows,
            earnings_events_payload,
        )
        scale_comparison = (
            repo.compare(
                metric_id="total_servicing_upb",
                period_end=date.fromisoformat(selected_period),
            )
            if selected_period is not None
            else None
        )
        scale_assessment = ScaleAssessment(
            status=scale_comparison.status if scale_comparison else "insufficient_information",
            reasons=(
                scale_comparison.reasons
                if scale_comparison
                else ("A governed same-period servicing UPB comparison is unavailable",)
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
                "highlights": highlights,
                "chart": chart,
                "quality": quality,
                "safe_source_url": _safe_source_url,
                "model_calls_enabled": False,
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
    def comparison_page(
        request: Request,
        repo: RepositoryDependency,
        metric_id: str = _DEFAULT_METRIC,
        period_end: date | None = None,
    ) -> HTMLResponse:
        return render(
            request,
            repo,
            page="comparison",
            title="Pairwise comparison",
            metric_id=metric_id,
            period_end=period_end,
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
