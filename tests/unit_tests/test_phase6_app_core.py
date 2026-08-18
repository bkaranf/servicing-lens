from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.types import Message, Scope

from mortgage_servicing_dashboard.api import FreshnessResponse, _chart_model, create_app
from mortgage_servicing_dashboard.database import (
    Company,
    EligibleSourceAssessment,
    IngestionError,
    MetricObservation,
    ObservationEvidence,
    PipelineRun,
    QuarantineCandidate,
    ReportingEntity,
    SourceEvidence,
    create_database_engine,
    initialize_schema,
)
from mortgage_servicing_dashboard.presentation import CompanyIdentity, normalize_companies
from mortgage_servicing_dashboard.repository import IntelligenceRepository, seed_stage_a


@dataclass(frozen=True, slots=True)
class _AsgiResponse:
    status_code: int
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode()

    def json(self) -> Any:
        return json.loads(self.content)


async def _immediate_call(
    function: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> object:
    return function(*args, **kwargs)


def _get(
    app: FastAPI,
    path: str,
    *,
    params: list[tuple[str, str]] | None = None,
) -> _AsgiResponse:
    """Drive the real ASGI stack without an event loop or internal socketpair."""
    parsed = urlsplit(path)
    encoded_query = urlencode(params or [], doseq=True).encode()
    messages: list[Message] = []
    received = False

    async def receive() -> Message:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(parsed.path),
        "raw_path": parsed.path.encode(),
        "query_string": encoded_query,
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    with (
        patch("fastapi.routing.run_in_threadpool", new=_immediate_call),
        patch("fastapi.dependencies.utils.run_in_threadpool", new=_immediate_call),
    ):
        request = app(scope, receive, send)
        while True:
            try:
                yielded = request.send(None)
            except StopIteration:
                break
            assert yielded is None
    start = next(message for message in messages if message["type"] == "http.response.start")
    body_parts: list[bytes] = []
    for message in messages:
        if message["type"] != "http.response.body":
            continue
        body_part = message.get("body", b"")
        assert isinstance(body_part, bytes)
        body_parts.append(body_part)
    body = b"".join(body_parts)
    return _AsgiResponse(status_code=int(start["status"]), content=body)


def _repository(tmp_path: Path) -> IntelligenceRepository:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'phase6-app.db').as_posix()}")
    seed_stage_a(engine)
    return IntelligenceRepository(engine)


def _install_retained_document(  # noqa: PLR0913
    repository: IntelligenceRepository,
    *,
    evidence_root: Path,
    observation_id: str,
    content: bytes,
    media_type: str,
    locator: str,
) -> tuple[str, Path]:
    observation = repository.observation(observation_id)
    assert observation is not None
    assert observation.evidence_id is not None
    digest = hashlib.sha256(content).hexdigest()
    retained_path = evidence_root / digest[:2] / f"{digest}.bin"
    retained_path.parent.mkdir(parents=True, exist_ok=True)
    retained_path.write_bytes(content)
    with Session(repository.engine) as session:
        evidence = session.get(SourceEvidence, observation.evidence_id)
        stored_observation = session.get(MetricObservation, observation.id)
        link = session.get(ObservationEvidence, (observation.id, observation.evidence_id))
        assert evidence is not None
        assert stored_observation is not None
        assert link is not None
        evidence.content_sha256 = digest
        evidence.byte_length = len(content)
        evidence.media_type = media_type
        evidence.retention_location = f"content-sha256://{digest}"
        stored_observation.evidence_locator = locator
        link.locator = locator
        session.commit()
    return observation.evidence_id, retained_path


def test_factory_is_non_mutating_docs_are_local_disabled_and_routes_are_get_only(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-parent" / "not-created-by-factory.db"
    app = create_app(database_url=f"sqlite:///{database_path.as_posix()}")

    assert not database_path.exists()
    assert all(route.methods == {"GET"} for route in app.routes if isinstance(route, APIRoute))
    health = _get(app, "/api/v1/health")
    assert health.status_code == 503
    assert health.json()["detail"]["code"] == "database_not_found"
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert _get(app, "/docs").status_code == 404
    assert _get(app, "/redoc").status_code == 404


def test_factory_rejects_outdated_schema_without_migration_and_opens_current_read_only(
    tmp_path: Path,
) -> None:
    outdated_path = tmp_path / "outdated.db"
    outdated_engine = create_database_engine(f"sqlite:///{outdated_path.as_posix()}")
    initialize_schema(outdated_engine)
    with outdated_engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = '0001_public_intelligence_schema'")
        )
    outdated_engine.dispose()
    before = hashlib.sha256(outdated_path.read_bytes()).hexdigest()

    outdated_app = create_app(database_url=f"sqlite:///{outdated_path.as_posix()}")
    response = _get(outdated_app, "/api/v1/health")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_schema_not_current"
    assert hashlib.sha256(outdated_path.read_bytes()).hexdigest() == before
    assert not Path(f"{outdated_path}-journal").exists()

    current_repository = _repository(tmp_path)
    current_app = create_app(database_url=str(current_repository.engine.url))
    assert _get(current_app, "/api/v1/health").status_code == 200


def test_empty_and_single_issuer_pages_and_freshness_never_claim_complete(
    tmp_path: Path,
) -> None:
    empty_engine = create_database_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    initialize_schema(empty_engine)
    empty_repository = IntelligenceRepository(empty_engine)
    empty_app = create_app(repository=empty_repository)

    for path in ("/", "/comparison", "/data-quality", "/methodology"):
        assert _get(empty_app, path).status_code == 200
    freshness_response = _get(empty_app, "/api/v1/pipeline/freshness")
    assert freshness_response.status_code == 200
    freshness = FreshnessResponse.model_validate(freshness_response.json())
    assert freshness.dataset is None
    assert freshness.coverage_state == "unassessed"
    assert freshness.calendar_freshness_state == "UNASSESSED"
    assert freshness.calendar_freshness_by_company == {}

    repository = _repository(tmp_path)
    with Session(repository.engine) as session:
        pfsi = session.get(Company, "pfsi")
        assert pfsi is not None
        pfsi.active = False
        session.commit()
    app = create_app(repository=repository)
    for path in (
        "/",
        "/comparison",
        "/companies/tfc",
        "/metrics/total_servicing_upb",
        "/earnings",
        "/data-quality",
        "/methodology",
    ):
        assert _get(app, path).status_code == 200
    comparison = _get(
        app,
        "/api/v1/comparisons",
        params=[("metric_id", "total_servicing_upb"), ("period_end", "2026-06-30")],
    )
    assert comparison.status_code == 422


def test_inactive_company_is_excluded_from_every_public_read_and_aggregate(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    pfsi_record = repository.observations(company_id="pfsi")[0]
    before = repository.freshness()
    with Session(repository.engine) as session:
        stored = session.get(MetricObservation, pfsi_record.id)
        assert stored is not None
        stored.period_end = date(2099, 12, 31)
        session.commit()
    assert repository.latest_period_end() == date(2099, 12, 31)

    with Session(repository.engine) as session:
        pfsi = session.get(Company, "pfsi")
        assert pfsi is not None
        pfsi.active = False
        stored_observation_count = session.scalar(
            select(func.count(MetricObservation.id))
            .join(
                ReportingEntity,
                MetricObservation.reporting_entity_id == ReportingEntity.id,
            )
            .where(ReportingEntity.company_id == "pfsi")
        )
        stored_assessment_count = session.scalar(
            select(func.count(EligibleSourceAssessment.id)).where(
                EligibleSourceAssessment.company_id == "pfsi"
            )
        )
        session.commit()

    assert stored_observation_count is not None
    assert stored_observation_count > 0
    assert stored_assessment_count is not None
    assert stored_assessment_count > 0
    assert all(item["id"] != "pfsi" for item in repository.companies())
    assert repository.observations(company_id="pfsi") == []
    assert all(row.company_id != "pfsi" for row in repository.observation_snapshot())
    assert repository.observation_count(company_id="pfsi") == 0
    assert repository.observation(pfsi_record.id) is None
    assert repository.latest_period_end() == date(2026, 6, 30)
    assert all(item["company_id"] != "pfsi" for item in repository.coverage())
    assert all(item["company_id"] != "pfsi" for item in repository.earnings_events())

    after = repository.freshness()
    assert after["observation_count"] == repository.observation_count()
    assert after["observation_count"] < cast("int", before["observation_count"])
    assert cast("int", after["source_assessment_count"]) < cast(
        "int", before["source_assessment_count"]
    )
    calendar = cast("list[dict[str, object]]", after["calendar"])
    assert all(item["company_id"] != "pfsi" for item in calendar)

    app = create_app(repository=repository)
    assert _get(app, f"/api/v1/observations/{quote(pfsi_record.id, safe='')}").status_code == 404
    response = _get(app, "/api/v1/observations", params=[("company_id", "pfsi")])
    assert response.status_code == 200
    assert response.json() == []


def test_inactive_company_evidence_reverse_links_and_quality_counts_do_not_leak(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    tfc_record = repository.observations(company_id="tfc", include_missing=False)[0]
    pfsi_record = repository.observations(company_id="pfsi", include_missing=False)[0]
    assert tfc_record.evidence_id is not None
    assert pfsi_record.evidence_id is not None
    initial_quality = repository.quality_counts()
    run_id = "pipeline:phase6-inactive-pfsi"
    with Session(repository.engine) as session:
        source_link = session.get(
            ObservationEvidence,
            (tfc_record.id, tfc_record.evidence_id),
        )
        assert source_link is not None
        if (
            session.get(
                ObservationEvidence,
                (pfsi_record.id, tfc_record.evidence_id),
            )
            is None
        ):
            session.add(
                ObservationEvidence(
                    observation_id=pfsi_record.id,
                    evidence_id=tfc_record.evidence_id,
                    evidence_role="corroborating",
                    locator=source_link.locator,
                    raw_label=source_link.raw_label,
                    raw_value=source_link.raw_value,
                    disclosed_unit=source_link.disclosed_unit,
                    disclosed_scale=source_link.disclosed_scale,
                    extraction_method=source_link.extraction_method,
                    validation_status=source_link.validation_status,
                )
            )
        session.add(
            PipelineRun(
                id=run_id,
                run_key=run_id,
                status="FAILED",
                thread_id="phase6-inactive-thread",
                started_at=datetime(2099, 1, 1, tzinfo=UTC),
                completed_at=datetime(2099, 1, 1, 0, 1, tzinfo=UTC),
                error_count=1,
                retry_count=0,
                requested_company_id="pfsi",
                requested_periods=[pfsi_record.period_end],
                code_version="phase6-test",
                config_version="phase6-test",
                parser_version="phase6-test",
                terminal_outcomes={"FAILED": 9},
            )
        )
        session.add(
            IngestionError(
                id="error:phase6-inactive-pfsi",
                pipeline_run_id=run_id,
                stage="test",
                error_code="PHASE6_TEST",
                retryable=False,
                safe_message="bounded test error",
            )
        )
        session.add(
            QuarantineCandidate(
                id="quarantine:phase6-inactive-pfsi",
                pipeline_run_id=run_id,
                proposed_metric_id="phase6_test_metric",
                raw_source_label="Phase 6 test",
                raw_value="1",
                proposed_normalized_value=Decimal(1),
                unit="USD",
                scale="1",
                period_end=date.fromisoformat(pfsi_record.period_end),
                reporting_entity_id=pfsi_record.reporting_entity_id,
                reporting_scope_id=pfsi_record.reporting_scope_id,
                methodology="phase6_test",
                evidence_id=pfsi_record.evidence_id,
                evidence_locator=pfsi_record.evidence_locator,
                bounded_excerpt="bounded test excerpt",
                confidence=Decimal("0.5000"),
                conflicts_and_uncertainties=["test-only candidate"],
                model_and_prompt_version=None,
                status="PENDING",
            )
        )
        session.commit()

    visible_quality = repository.quality_counts()
    assert visible_quality["failed_run_count"] == initial_quality["failed_run_count"] + 1
    assert visible_quality["ingestion_error_count"] == initial_quality["ingestion_error_count"] + 1
    assert repository.freshness()["pipeline_status"] == "FAILED"

    with Session(repository.engine) as session:
        pfsi = session.get(Company, "pfsi")
        assert pfsi is not None
        pfsi.active = False
        session.commit()

    assert repository.evidence(pfsi_record.evidence_id) is None
    assert repository.evidence(tfc_record.evidence_id) is not None
    reverse_ids = repository.evidence_observation_ids(tfc_record.evidence_id)
    assert tfc_record.id in reverse_ids
    assert pfsi_record.id not in reverse_ids
    hidden_quality = repository.quality_counts()
    assert hidden_quality["failed_run_count"] == initial_quality["failed_run_count"]
    assert hidden_quality["ingestion_error_count"] == initial_quality["ingestion_error_count"]
    assert (
        hidden_quality["quarantined_candidate_count"]
        < visible_quality["quarantined_candidate_count"]
    )
    hidden_freshness = repository.freshness()
    assert hidden_freshness["pipeline_status"] != "FAILED"
    assert hidden_freshness["terminal_outcomes"] != {"FAILED": 9}

    app = create_app(repository=repository)
    assert (
        _get(
            app,
            f"/api/v1/evidence/{quote(pfsi_record.evidence_id, safe='')}",
        ).status_code
        == 404
    )
    shared = _get(
        app,
        f"/api/v1/evidence/{quote(tfc_record.evidence_id, safe='')}",
    )
    assert shared.status_code == 200
    assert pfsi_record.id not in shared.json()["linked_observation_ids"]
    with Session(repository.engine) as session:
        assert session.get(PipelineRun, run_id) is not None
        assert session.get(IngestionError, "error:phase6-inactive-pfsi") is not None
        assert session.get(QuarantineCandidate, "quarantine:phase6-inactive-pfsi") is not None


def test_real_asgi_json_preserves_exact_financial_strings(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    record = repository.observations(
        company_id="pfsi",
        metric_id="total_servicing_upb",
        period_end=date(2026, 6, 30),
        include_missing=False,
    )[0]
    response = _get(
        create_app(repository=repository),
        f"/api/v1/observations/{quote(record.id, safe='')}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["value"] == record.value
    assert payload["normalized_value"] == record.value
    assert isinstance(payload["value"], str)
    assert f'"value":"{record.value}"' in response.text
    assert not isinstance(payload["normalized_value"], float)


def test_presentation_preserves_explicit_not_disclosed_evidence_and_distinguishes_absence(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    identity = cast(
        "list[CompanyIdentity]",
        [item for item in repository.companies() if item["id"] == "pfsi"],
    )
    base = repository.observations(
        company_id="pfsi",
        metric_id="total_servicing_upb",
        include_missing=False,
    )[-1]
    explicit_missing = replace(
        base,
        id="observation:phase6:explicit-not-disclosed",
        value=None,
        state="NOT_DISCLOSED",
        reported_value="Not disclosed",
    )
    card = normalize_companies(
        identity,
        [explicit_missing],
        target_periods={"pfsi": explicit_missing.period_end},
    )[0]
    assert card.upb.display == "NOT_DISCLOSED"
    assert card.upb.status == "not_disclosed"
    assert card.upb.source_metric_id == "total_servicing_upb"
    assert len(card.upb.inputs) == 1
    assert card.upb.inputs[0].observation_id == explicit_missing.id
    assert card.upb.inputs[0].evidence_id == explicit_missing.evidence_id

    absent = normalize_companies(
        identity,
        [],
        target_periods={"pfsi": explicit_missing.period_end},
    )[0]
    assert absent.upb.status == "unavailable"
    assert absent.upb.inputs == ()

    unassessed_row = replace(
        explicit_missing,
        id="observation:phase6:source-not-checked",
        state="SOURCE_NOT_CHECKED",
        reported_value="Source not checked",
    )
    unassessed = normalize_companies(
        identity,
        [unassessed_row],
        target_periods={"pfsi": unassessed_row.period_end},
    )[0]
    assert unassessed.upb.status == "unassessed"
    assert unassessed.upb.display == "Unavailable"
    assert unassessed.upb.inputs[0].observation_id == unassessed_row.id


def test_content_addressed_html_xml_locator_tamper_and_path_escape(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    record = repository.observations(
        company_id="pfsi",
        metric_id="total_servicing_upb",
        period_end=date(2026, 6, 30),
        include_missing=False,
    )[0]
    evidence_root = tmp_path / "bounded-evidence"
    app = create_app(repository=repository, evidence_root=evidence_root)

    html_source = (
        f"<html><table><tr><th>{escape(record.reported_label)}</th>"
        f'<td><ix:nonfraction id="fact-1">{escape(record.reported_value)}</ix:nonfraction>'
        "</td></tr></table></html>"
    ).encode()
    evidence_id, _ = _install_retained_document(
        repository,
        evidence_root=evidence_root,
        observation_id=record.id,
        content=html_source,
        media_type="text/html",
        locator="xbrl:test:Fact;context=current;element_id=fact-1;occurrence=1",
    )
    locator_path = (
        f"/evidence/{quote(evidence_id, safe='')}/observations/"
        f"{quote(record.id, safe='')}#cited-source-locator"
    )
    html_response = _get(app, locator_path)
    assert html_response.status_code == 200
    assert "Retained source row containing" in html_response.text
    assert record.reported_value in html_response.text

    xml_source = f'<xbrl><fact id="fact-1">{escape(record.reported_value)}</fact></xbrl>'.encode()
    _, retained_path = _install_retained_document(
        repository,
        evidence_root=evidence_root,
        observation_id=record.id,
        content=xml_source,
        media_type="application/xml",
        locator="//*[@id='fact-1']",
    )
    xml_response = _get(app, locator_path)
    assert xml_response.status_code == 200
    assert record.reported_value in xml_response.text

    retained_path.write_bytes(xml_source.replace(b"fact-1", b"fact-2"))
    tampered = _get(app, locator_path)
    assert tampered.status_code == 200
    assert "failed integrity verification" in tampered.text
    assert "Source transcription unavailable" in tampered.text
    assert "Open full authoritative SEC document" in tampered.text

    with Session(repository.engine) as session:
        evidence = session.get(SourceEvidence, evidence_id)
        assert evidence is not None
        evidence.retention_location = "content-sha256://../../outside"
        session.commit()
    escaped = _get(app, locator_path)
    assert escaped.status_code == 200
    assert "bounded runtime" in escaped.text


def test_svg_table_and_keyboard_evidence_contracts_are_present(tmp_path: Path) -> None:
    app = create_app(repository=_repository(tmp_path))
    response = _get(app, "/", params=[("metric_id", "total_servicing_upb")])
    assert response.status_code == 200
    html = response.text
    assert '<svg class="evidence-chart"' in html
    assert "Exact server-generated Decimal geometry" in html
    assert "ND markers identify explicit NOT_DISCLOSED observations" in html
    assert "NOT_DISCLOSED and absent cells remain gaps, never zero" in html
    assert html.count('scope="col"') >= 8
    assert 'aria-modal="true"' in html
    assert 'aria-live="polite"' in html

    javascript = (
        Path(__file__).parents[2]
        / "src"
        / "mortgage_servicing_dashboard"
        / "static"
        / "dashboard.js"
    ).read_text(encoding="utf-8")
    assert "item.evidence_links" in javascript
    assert "item.derivation_inputs" in javascript
    assert "evidenceTrigger.focus()" in javascript
    assert "evidenceDialog.showModal()" in javascript
    assert all(key in javascript for key in ('event.key === "ArrowRight"', 'event.key === "Home"'))


def test_chart_materializes_union_axis_and_does_not_bridge_an_absent_quarter(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    rows = repository.observations(
        metric_id="total_servicing_upb",
        include_missing=False,
    )
    tfc = next(row for row in rows if row.company_id == "tfc")
    pfsi = next(row for row in rows if row.company_id == "pfsi")
    chart_rows = [
        replace(tfc, id="tfc-q1", period_end="2026-03-31", fiscal_quarter=1),
        replace(tfc, id="tfc-q3", period_end="2026-09-30", fiscal_quarter=3),
        replace(pfsi, id="pfsi-q1", period_end="2026-03-31", fiscal_quarter=1),
        replace(pfsi, id="pfsi-q2", period_end="2026-06-30", fiscal_quarter=2),
        replace(pfsi, id="pfsi-q3", period_end="2026-09-30", fiscal_quarter=3),
    ]

    chart = _chart_model(chart_rows, metric_id="total_servicing_upb", company_id=None)
    series_rows = cast("list[dict[str, Any]]", chart["series"])
    series = {str(item["company_id"]): item for item in series_rows}
    tfc_points = cast("list[dict[str, Any]]", series["tfc"]["points"])
    assert len(tfc_points) == len(cast("list[str]", chart["periods"])) == 3
    assert tfc_points[1]["state"] == "ABSENT"
    assert tfc_points[1]["plotted"] is False
    assert series["tfc"]["segments"] == []
    assert len(cast("list[dict[str, str]]", series["pfsi"]["segments"])) == 2
    table_rows = cast("list[dict[str, Any]]", chart["table_rows"])
    absent_table_rows = [
        row
        for row in table_rows
        if row["company_id"] == "tfc" and row["period_end"] == "2026-06-30"
    ]
    assert len(absent_table_rows) == 1
    assert absent_table_rows[0]["id"] is None
    assert absent_table_rows[0]["display_value"] == "No published observation"
