from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy import Engine, update
from starlette.exceptions import HTTPException as StarletteHTTPException

from mortgage_servicing_dashboard.api import (
    CalendarResponse,
    CoverageResponse,
    EvidenceResponse,
    FreshnessResponse,
    ObservationDetailResponse,
    ObservationResponse,
    create_app,
)
from mortgage_servicing_dashboard.database import (
    SourceEvidence,
    create_database_engine,
    initialize_schema,
)
from mortgage_servicing_dashboard.presentation import fiscal_period_label
from mortgage_servicing_dashboard.repository import IntelligenceRepository, seed_stage_a


def test_fiscal_period_label_distinguishes_annual_from_quarterly() -> None:
    assert fiscal_period_label(fiscal_year=2025, fiscal_quarter=0) == "FY 2025"
    assert fiscal_period_label(fiscal_year=2026, fiscal_quarter=2) == "Q2 2026"


@pytest.fixture
def repository(tmp_path: Path) -> IntelligenceRepository:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'ui-contract.db').as_posix()}")
    seed_stage_a(engine)
    return IntelligenceRepository(engine)


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_float(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_float(nested)


def _endpoint(app: Any, path: str) -> Any:
    return next(
        route.endpoint for route in app.routes if isinstance(route, APIRoute) and route.path == path
    )


def _request(app: Any, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "router": app.router,
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _finish_immediate_coroutine(coroutine: Any) -> Any:
    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    return completed.value.value


def _html(response: Any) -> str:
    return bytes(response.body).decode()


class _RenderedContractParser(HTMLParser):
    _VOID_TAGS: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
    }

    def __init__(self) -> None:
        super().__init__()
        self._regions: list[str | None] = []
        self._region: str | None = None
        self.observation_ids: dict[str, set[str]] = defaultdict(set)
        self.landmarks: set[str] = set()
        self.external_assets: list[str] = []
        self.dialog_count = 0
        self.caption_count = 0
        self.tab_count = 0
        self.provenance_links: list[str] = []

    def handle_starttag(  # noqa: C901
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag not in self._VOID_TAGS:
            self._regions.append(self._region)
        identifier = attributes.get("id")
        if identifier in {"chart-panel", "table-panel"}:
            self._region = identifier
        if tag in {"header", "nav", "main", "footer"}:
            self.landmarks.add(tag)
        if tag == "dialog":
            self.dialog_count += 1
        if tag == "caption":
            self.caption_count += 1
        if attributes.get("role") == "tab":
            self.tab_count += 1
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "provenance-trigger" in classes:
            self.provenance_links.append(attributes.get("href") or "")
        observation_id = attributes.get("data-observation-id")
        if observation_id and self._region:
            self.observation_ids[self._region].add(observation_id)
        if tag in {"link", "script"}:
            asset = attributes.get("href") or attributes.get("src")
            if (
                asset
                and asset.startswith(("http://", "https://", "//"))
                and not asset.startswith("http://testserver/")
            ):
                self.external_assets.append(asset)

    def handle_endtag(self, _tag: str) -> None:
        if _tag not in self._VOID_TAGS and self._regions:
            self._region = self._regions.pop()


def test_read_api_is_bounded_exact_strict_and_read_only(  # noqa: PLR0915
    repository: IntelligenceRepository,
) -> None:
    app = create_app(repository=repository)
    route_methods = {
        route.path: set(route.methods or set())
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    }
    assert route_methods == {
        "/api/v1/health": {"GET"},
        "/api/v1/companies": {"GET"},
        "/api/v1/companies/{company_id}": {"GET"},
        "/api/v1/metrics": {"GET"},
        "/api/v1/observations": {"GET"},
        "/api/v1/observations/{observation_id}": {"GET"},
        "/api/v1/comparisons": {"GET"},
        "/api/v1/coverage": {"GET"},
        "/api/v1/evidence/{evidence_id}": {"GET"},
        "/api/v1/earnings-events": {"GET"},
        "/api/v1/calendar": {"GET"},
        "/api/v1/pipeline/freshness": {"GET"},
    }

    observations = _endpoint(app, "/api/v1/observations")(
        repository,
        company_id=None,
        metric_id=None,
        period_end=None,
        as_of=None,
        include_missing=True,
        limit=2,
        offset=0,
    )
    TypeAdapter(list[ObservationResponse]).validate_python(observations)
    assert len(observations) == 2
    _assert_no_float(observations)
    coverage = _endpoint(app, "/api/v1/coverage")(
        repository,
        as_of=None,
        limit=100,
        offset=0,
    )
    TypeAdapter(list[CoverageResponse]).validate_python(coverage)
    assert sum(item["source_not_checked"] for item in coverage) == 220
    freshness = _endpoint(app, "/api/v1/pipeline/freshness")(repository)
    FreshnessResponse.model_validate(freshness)
    assert freshness["source_not_checked_count"] == 220
    assert freshness["coverage_state"] == "partial"
    assert freshness["calendar_freshness_state"] == "NOT_YET_EXPECTED"
    calendar = _endpoint(app, "/api/v1/calendar")(repository)
    TypeAdapter(list[CalendarResponse]).validate_python(calendar)
    assert {item["company_id"] for item in calendar} == {"tfc", "pfsi"}
    assert all(item["next_expected_report_window"]["is_inferred"] is True for item in calendar)
    with pytest.raises(HTTPException) as invalid_limit:
        _endpoint(app, "/api/v1/observations")(
            repository,
            limit=101,
            offset=0,
        )
    assert invalid_limit.value.status_code == 422
    with pytest.raises(HTTPException) as invalid_offset:
        _endpoint(app, "/api/v1/observations")(
            repository,
            limit=2,
            offset=10001,
        )
    assert invalid_offset.value.status_code == 422

    observation_id = observations[0]["id"]
    detail = _endpoint(app, "/api/v1/observations/{observation_id}")(
        observation_id,
        repository,
    )
    ObservationDetailResponse.model_validate(detail)
    _assert_no_float(detail)
    assert {
        "observation_id",
        "metric_version",
        "reporting_entity_id",
        "reporting_scope_id",
        "period_start",
        "period_end",
        "unit",
        "scale",
        "reported_precision",
        "reported_label",
        "reported_value",
        "normalized_value",
        "methodology",
        "state",
        "evidence_id",
        "evidence_locator",
        "extraction_method",
        "validation_status",
        "revision_history",
        "evidence_locator_url",
    } <= detail.keys()
    assert isinstance(detail["value"], str) or detail["value"] is None
    assert detail["value"] == detail["normalized_value"]
    assert detail["evidence"]["content_sha256"] == detail["evidence_sha256"]
    evidence = _endpoint(app, "/api/v1/evidence/{evidence_id}")(
        detail["evidence_id"],
        repository,
    )
    EvidenceResponse.model_validate(evidence)
    assert observation_id in evidence["linked_observation_ids"]
    assert detail["evidence_locator_url"].endswith("#cited-source-locator")

    locator_view = _endpoint(
        app,
        "/evidence/{evidence_id}/observations/{observation_id}",
    )(
        _request(app, detail["evidence_locator_url"]),
        detail["evidence_id"],
        observation_id,
        repository,
    )
    locator_html = unescape(_html(locator_view))
    assert locator_view.status_code == 200
    assert "frame-ancestors 'none'" in locator_view.headers["content-security-policy"]
    assert 'id="cited-source-locator"' in locator_html
    assert detail["evidence_locator"] in locator_html
    assert detail["reported_label"] in locator_html
    assert detail["reported_value"] in locator_html
    assert "Retained source row containing" in locator_html

    with pytest.raises(HTTPException) as mismatched_linkage:
        _endpoint(
            app,
            "/evidence/{evidence_id}/observations/{observation_id}",
        )(
            _request(app, "/evidence/not-linked"),
            "evidence:not_linked_to_observation",
            observation_id,
            repository,
        )
    assert mismatched_linkage.value.status_code == 404

    with pytest.raises(HTTPException) as invalid_identifier:
        _endpoint(app, "/api/v1/observations/{observation_id}")(" ", repository)
    assert invalid_identifier.value.status_code == 422
    assert invalid_identifier.value.detail == "invalid observation identifier"


def test_chart_and_accessible_table_share_one_exact_observation_set(
    repository: IntelligenceRepository,
) -> None:
    app = create_app(repository=repository)
    response = _endpoint(app, "/")(
        _request(app, "/"),
        repository,
        metric_id="total_servicing_upb",
        period_end=None,
    )
    assert response.status_code == 200
    html = _html(response)
    parser = _RenderedContractParser()
    parser.feed(html)
    assert parser.observation_ids["chart-panel"]
    assert parser.observation_ids["chart-panel"] == parser.observation_ids["table-panel"]
    assert parser.landmarks == {"header", "nav", "main", "footer"}
    assert parser.dialog_count == 1
    assert parser.caption_count >= 2
    assert parser.tab_count == 2
    assert not parser.external_assets
    assert parser.provenance_links
    assert all(
        link.startswith("/evidence/") and link.endswith("#cited-source-locator")
        for link in parser.provenance_links
    )
    assert "exact decimal arithmetic and retained evidence" in html

    selected = repository.observations(
        metric_id="total_servicing_upb",
        include_missing=True,
    )
    for observation in selected:
        assert observation.reported_value in html
        assert observation.id in parser.observation_ids["chart-panel"]


def test_empty_stale_partial_missing_and_error_states_are_real_views(
    repository: IntelligenceRepository,
    tmp_path: Path,
) -> None:
    app = create_app(repository=repository)
    quality = _endpoint(app, "/data-quality")(
        _request(app, "/data-quality"),
        repository,
        metric_id="total_servicing_upb",
    )
    assert quality.status_code == 200
    quality_html = _html(quality)
    assert "PARTIAL" in quality_html
    assert "SOURCE_NOT_CHECKED" in quality_html
    assert "QUARANTINED" in quality_html

    stale_at = datetime(2025, 1, 1, tzinfo=UTC)
    with repository.engine.begin() as connection:
        connection.execute(update(SourceEvidence).values(retrieved_at=stale_at))
    stale = _endpoint(app, "/")(
        _request(app, "/"),
        repository,
        metric_id="total_servicing_upb",
        period_end=None,
    )
    assert stale.status_code == 200
    stale_html = _html(stale)
    assert "Recorded evidence is stale" in stale_html
    assert 'role="alert"' in stale_html

    empty_engine: Engine = create_database_engine(
        f"sqlite:///{(tmp_path / 'empty-ui.db').as_posix()}"
    )
    initialize_schema(empty_engine)
    empty_app = create_app(repository=IntelligenceRepository(empty_engine))
    empty = _endpoint(empty_app, "/")(
        _request(empty_app, "/"),
        IntelligenceRepository(empty_engine),
        metric_id="total_servicing_upb",
        period_end=None,
    )
    assert empty.status_code == 200
    empty_html = _html(empty)
    assert "No published observations are available" in empty_html
    assert 'role="status"' in empty_html

    missing_error = HTTPException(status_code=404, detail="company not found")
    handler = app.exception_handlers[StarletteHTTPException]
    missing_page = _finish_immediate_coroutine(
        handler(_request(app, "/companies/not-a-company"), missing_error)
    )
    assert isinstance(missing_page, HTMLResponse)
    assert missing_page.status_code == 404
    error_html = bytes(missing_page.body).decode()
    assert "No data was changed" in error_html
    assert 'role="alert"' in error_html
