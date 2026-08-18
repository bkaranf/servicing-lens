"""End-to-end Stage A tests using only recorded public fixtures and local stores."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.database import (
    Base,
    Company,
    ComparabilityAssessment,
    HumanReviewDecision,
    IngestionError,
    MetricObservation,
    ObservationRevision,
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
    default_database_url,
    initialize_schema,
    session_scope,
    utc_now,
)
from mortgage_servicing_dashboard.domain import (
    ComparabilityStatus,
    ComparisonInput,
    ObservationState,
    assess_comparability,
    parse_decimal,
    reconcile_rollforward,
)
from mortgage_servicing_dashboard.ingestion import (
    INGESTION_NODES,
    DeterministicIngestionRuntime,
    IngestionServiceError,
    IngestionState,
    IngestionUpdate,
    StageAIngestionServices,
    StageName,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    seed_stage_a,
)


class _RecordingRuntimeServices:
    """Small deterministic service double used to assert transition order."""

    def __init__(self, *, fail_stage: StageName | None = None) -> None:
        self.calls: list[StageName] = []
        self.fail_stage = fail_stage

    def execute(self, stage: StageName, state: IngestionState) -> IngestionUpdate:
        self.calls.append(stage)
        if stage == self.fail_stage:
            code = "FIXTURE_STAGE_FAILED"
            raise IngestionServiceError(code, "fixture stage failed")
        if stage == "publish_approved_observations":
            return {
                "terminal_status": "COMPLETED",
                "terminal_outcomes": {
                    "PUBLISHED": 0,
                    "NOT_DISCLOSED": 0,
                    "SOURCE_NOT_CHECKED": 0,
                    "QUARANTINED": 0,
                    "FAILED": 0,
                },
            }
        if stage == "refresh_comparability_and_materializations":
            return {"terminal_status": state.get("terminal_status", "RUNNING")}
        if stage == "emit_audit_events":
            status = state.get("terminal_status", "RUNNING")
            return {
                "terminal_status": status,
                "audit_events": [
                    f"ingestion_terminal:{status.lower()}:published=0:not_disclosed=0:source_not_checked=0"
                ],
            }
        return {"terminal_status": "RUNNING"}


class _FailingStageAServices(StageAIngestionServices):
    """Real persisted services with one deterministic stage failure."""

    def __init__(
        self,
        *,
        fail_stage: StageName,
        retryable: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.fail_stage = fail_stage
        self.retryable = retryable

    def execute(self, stage: StageName, state: IngestionState) -> IngestionUpdate:
        if stage == self.fail_stage:
            code = "FIXTURE_STAGE_FAILED"
            raise IngestionServiceError(code, "fixture stage failed", retryable=self.retryable)
        return super().execute(stage, state)


@pytest.fixture
def seeded_engine(tmp_path: Path) -> Any:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'stage-a.db').as_posix()}")
    counts = seed_stage_a(engine)
    assert counts == {
        "companies": 2,
        "metrics": 32,
        "evidence": 2,
        "source_assessments": 256,
        "observations": 36,
    }
    yield engine
    engine.dispose()


def test_decimal_normalization_and_reconciliation() -> None:
    assert parse_decimal("$1,234.50", scale="millions") == Decimal("1234500000.00")
    assert parse_decimal("(1.25)", scale="percent") == Decimal("-0.0125")
    assert parse_decimal("12", scale="basis_points") == Decimal("0.0012")
    with pytest.raises(ValueError, match="unsupported scale"):
        parse_decimal("1", scale="trillions")
    with pytest.raises(ValueError, match="exact decimal"):
        parse_decimal("not-a-number")
    assert reconcile_rollforward(
        beginning=Decimal(10),
        additions=(Decimal(3), Decimal(2)),
        reductions=(Decimal(4),),
        ending=Decimal(11),
        tolerance=Decimal(0),
    )
    assert not reconcile_rollforward(
        beginning=Decimal(10),
        additions=(),
        reductions=(),
        ending=Decimal(12),
        tolerance=Decimal(1),
    )


def _comparison(**overrides: object) -> ComparisonInput:
    values: dict[str, object] = {
        "metric_id": "total_servicing_upb",
        "metric_version": "1.0.0",
        "reporting_scope": "scope",
        "period_days": 92,
        "currency": "USD",
        "unit": "USD",
        "methodology": "reported",
        "observation_state": ObservationState.REPORTED_ACTUAL,
        "portfolio_population": "owned",
    }
    values.update(overrides)
    return ComparisonInput(**values)  # type: ignore[arg-type]


def test_pairwise_comparability_states() -> None:
    assert (
        assess_comparability(_comparison(), _comparison()).status is ComparabilityStatus.COMPARABLE
    )
    missing = assess_comparability(
        _comparison(),
        _comparison(observation_state=ObservationState.NOT_DISCLOSED),
    )
    assert missing.status is ComparabilityStatus.INSUFFICIENT_INFORMATION
    hard = assess_comparability(
        _comparison(),
        _comparison(
            metric_id="other",
            metric_version="2",
            currency="EUR",
            unit="count",
            portfolio_population="other",
            reporting_scope="other",
        ),
    )
    assert hard.status is ComparabilityStatus.NOT_COMPARABLE
    assert len(hard.reasons) == 4
    caveat = assess_comparability(
        _comparison(),
        _comparison(
            period_days=91,
            methodology="derived",
            observation_state=ObservationState.DERIVED,
        ),
    )
    assert caveat.status is ComparabilityStatus.COMPARABLE_WITH_CAVEATS
    assert len(caveat.reasons) == 3


def test_configuration_resolution_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = config_directory()
    assert config_directory(root) == root
    monkeypatch.setenv("MSI_CONFIG_DIR", str(root))
    assert config_directory() == root
    universe, catalog, data = load_stage_a_configuration()
    assert len(universe["companies"]) == 2
    assert len(catalog["metrics"]) == 32
    assert len(data["quarters"]) == 4

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "universe.yaml").write_text("[]", encoding="utf-8")
    (bad / "metrics").mkdir()
    (bad / "metrics" / "catalog.yaml").write_text("{}", encoding="utf-8")
    (bad / "stage_a_data.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError, match="expected a mapping"):
        load_stage_a_configuration(bad)

    monkeypatch.setenv("MSI_CONFIG_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr("mortgage_servicing_dashboard.repository.sys.prefix", str(tmp_path))
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.repository.__file__",
        str(tmp_path / "a" / "b" / "repository.py"),
    )
    with pytest.raises(FileNotFoundError, match="configuration"):
        config_directory()


def test_database_helpers_commit_and_rollback(tmp_path: Path) -> None:
    url = default_database_url(tmp_path)
    engine = create_database_engine(url)
    initialize_schema(engine)
    assert utc_now().tzinfo is not None
    with session_scope(engine) as session:
        session.add(
            Company(
                id="x",
                legal_name="Example",
                ticker="EX",
                classification="bank",
                universe_version="test",
                active=True,
            )
        )
    with Session(engine) as session:
        assert session.get(Company, "x") is not None

    def create_then_fail() -> None:
        with session_scope(engine) as session:
            session.add(
                Company(
                    id="y",
                    legal_name="Example Y",
                    ticker="EY",
                    classification="bank",
                    universe_version="test",
                    active=True,
                )
            )
            message = "rollback"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="rollback"):
        create_then_fail()
    with Session(engine) as session:
        assert session.get(Company, "y") is None
    assert len(Base.metadata.tables) >= 20
    engine.dispose()


def test_seed_is_idempotent_and_repository_queries(seeded_engine: Engine) -> None:
    assert seed_stage_a(seeded_engine) == {
        "companies": 0,
        "metrics": 0,
        "evidence": 0,
        "source_assessments": 0,
        "observations": 0,
    }
    repo = IntelligenceRepository(seeded_engine)
    assert repo.engine is seeded_engine
    assert [item["ticker"] for item in repo.companies()] == ["PFSI", "TFC"]
    assert len(repo.metrics()) == 32
    assert repo.latest_period_end() == date(2026, 6, 30)
    all_rows = repo.observations()
    assert len(all_rows) == 36
    disclosed = repo.observations(include_missing=False)
    assert len(disclosed) == 36
    tfc_total = repo.observations(company_id="tfc", metric_id="total_servicing_upb")
    assert [Decimal(item.value or "0") for item in tfc_total] == [
        Decimal(279_670_000_000),
        Decimal(285_966_000_000),
        Decimal(291_256_000_000),
        Decimal(298_658_000_000),
    ]
    q2 = repo.observations(period_end=date(2026, 6, 30))
    assert len(q2) == 12
    assert repo.observations(as_of=date(2020, 1, 1)) == []
    assert repo.observations(as_of=datetime(2020, 1, 1)) == []  # noqa: DTZ001
    first = disclosed[0]
    detail = repo.observation(first.id)
    assert detail is not None
    assert detail.id == first.id
    assert len(detail.revision_history) == 1
    assert repo.observation("unknown") is None
    assert first.as_dict()["source_url"]

    coverage = repo.coverage()
    assert len(coverage) == 8
    assert sum(int(str(item["reported"])) for item in coverage) == 36
    total = repo.compare(metric_id="total_servicing_upb", period_end=date(2026, 6, 30))
    assert total is not None
    assert total.status == "not_comparable"
    assert total.as_dict()["left"]["ticker"] == "TFC"  # type: ignore[index]
    with Session(seeded_engine) as session:
        assert session.scalar(select(func.count(ComparabilityAssessment.id))) == 4
        retained = session.scalars(
            select(ComparabilityAssessment).where(
                ComparabilityAssessment.left_observation_id
                == "observation:tfc:2026-06-30:total_servicing_upb:v1"
            )
        ).one()
        assert retained.status == "not_comparable"
        assert retained.policy_version == "1.0.0"
        assert retained.permitted_calculations == []
        assert retained.reasons == [
            "portfolio populations differ",
            "reporting scopes differ",
        ]
    missing = repo.compare(metric_id="servicing_revenue", period_end=date(2026, 6, 30))
    assert missing is None
    assert repo.compare(metric_id="unknown", period_end=date(2026, 6, 30)) is None


def test_repository_bitemporal_as_of(seeded_engine: Engine) -> None:
    repo = IntelligenceRepository(seeded_engine)
    observation_id = "observation:tfc:2026-06-30:servicing_revenue:v1"
    with Session(seeded_engine) as session:
        row = session.get(MetricObservation, observation_id)
        assert row is not None
        row.knowledge_to = datetime(2026, 8, 12, 3, tzinfo=UTC)
        session.commit()
    assert repo.observation(observation_id) is None
    rows = repo.observations(
        as_of=datetime(2026, 8, 12, 2, 55, tzinfo=UTC),
        company_id="tfc",
        metric_id="servicing_revenue",
    )
    assert len(rows) == 4


def test_api_routes_and_dashboard_are_read_only(seeded_engine: Engine) -> None:
    repo = IntelligenceRepository(seeded_engine)
    app = create_app(repository=repo)

    def endpoint(path: str) -> Any:
        route = next(
            route for route in app.routes if isinstance(route, APIRoute) and route.path == path
        )
        return route.endpoint

    health = endpoint("/api/v1/health")(repo)
    assert health.status == "ready"
    assert len(endpoint("/api/v1/companies")(repo)) == 2
    assert endpoint("/api/v1/companies/{company_id}")("tfc", repo)["ticker"] == "TFC"
    with pytest.raises(HTTPException):
        endpoint("/api/v1/companies/{company_id}")("missing", repo)
    assert len(endpoint("/api/v1/metrics")(repo)) == 32
    filtered = endpoint("/api/v1/observations")(
        repo,
        company_id="tfc",
        metric_id="total_servicing_upb",
        period_end=None,
        as_of=None,
        include_missing=False,
    )
    assert len(filtered) == 4
    item = filtered[0]
    provenance = endpoint("/api/v1/observations/{observation_id}")(item["id"], repo)
    assert provenance["source_url"]
    with pytest.raises(HTTPException) as missing_observation:
        endpoint("/api/v1/observations/{observation_id}")("unknown", repo)
    assert missing_observation.value.status_code == 404
    comparison = endpoint("/api/v1/comparisons")(
        repo,
        metric_id="total_servicing_upb",
        period_end=date(2026, 6, 30),
        as_of=None,
    )
    assert comparison["status"] == "not_comparable"
    with pytest.raises(HTTPException) as missing_comparison:
        endpoint("/api/v1/comparisons")(
            repo,
            metric_id="unknown",
            period_end=date(2026, 6, 30),
            as_of=None,
        )
    assert missing_comparison.value.status_code == 404
    assert len(endpoint("/api/v1/coverage")(repo, as_of=None)) == 8
    assert endpoint("/api/v1/evidence/{evidence_id}")("evidence:tfc_2026_q2_qps", repo)[
        "content_sha256"
    ]
    with pytest.raises(HTTPException):
        endpoint("/api/v1/evidence/{evidence_id}")("missing", repo)
    assert len(endpoint("/api/v1/earnings-events")(repo)) == 2
    assert endpoint("/api/v1/pipeline/freshness")(repo)["published_count"] == 36

    request = Request(
        {
            "type": "http",
            "app": app,
            "router": app.router,
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )
    pages = (
        ("/", (request, repo)),
        ("/companies/{company_id}", ("tfc", request, repo)),
        ("/companies/{company_id}", ("pfsi", request, repo)),
        ("/metrics/{metric_id}", ("total_servicing_upb", request, repo)),
        ("/comparison", (request, repo)),
        ("/earnings", (request, repo)),
        ("/data-quality", (request, repo)),
        ("/methodology", (request, repo)),
    )
    for path, arguments in pages:
        response = cast("HTMLResponse", endpoint(path)(*arguments))
        assert response.status_code == 200
        assert b"Servicing Lens" in response.body
        assert b'<a class="skip-link" href="#main">' in response.body
        assert b'<dialog id="provenance-dialog"' in response.body
    with pytest.raises(HTTPException):
        endpoint("/companies/{company_id}")("missing", request, repo)
    with pytest.raises(HTTPException):
        endpoint("/metrics/{metric_id}")("missing", request, repo)
    public_methods = {
        method
        for route in app.routes
        for method in getattr(route, "methods", set())
        if getattr(route, "path", "").startswith("/api/")
    }
    assert public_methods <= {"GET", "HEAD"}


def test_deterministic_ingestion_happy_path_and_same_thread_review_resume(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'graph.db').as_posix()}")
    services = StageAIngestionServices(engine=engine, retention_root=tmp_path / "evidence")
    runtime = DeterministicIngestionRuntime(services)
    interrupted = runtime.run(thread_id="review")
    assert interrupted["terminal_status"] == "AWAITING_REVIEW"
    assert len(interrupted["candidate_ids"]) == 36
    assert interrupted["quarantine_candidate_ids"] == [
        "candidate-pfsi-2026q2-expenses-excluding-valuation"
    ]
    with Session(engine) as session:
        run = session.get(PipelineRun, interrupted["run_id"])
        assert run is not None
        assert run.status == "AWAITING_REVIEW"
        candidate = session.get(QuarantineCandidate, interrupted["quarantine_candidate_ids"][0])
        assert candidate is not None
        assert candidate.status == "PENDING"

    # A fresh service/runtime in another process can reconstruct the paused run
    # from its persisted thread without replaying an in-memory workspace.
    reconstructed = DeterministicIngestionRuntime(
        StageAIngestionServices(engine=engine, retention_root=tmp_path / "reconstructed")
    )
    replayed = reconstructed.run(thread_id="review")
    assert replayed["terminal_status"] == "AWAITING_REVIEW"
    assert replayed["run_id"] == interrupted["run_id"]
    assert replayed["visited"] == list(INGESTION_NODES[:12])

    rejected = runtime.resume(
        thread_id="review",
        candidate_id=interrupted["quarantine_candidate_ids"][0],
        decision="reject",
        reviewer="local-reviewer",
        rationale="deterministic test review",
    )
    assert rejected["review_decision"] == "reject"
    assert rejected["published_count"] == 36
    assert rejected["not_disclosed_count"] == 0
    assert rejected["source_not_checked_count"] == 220
    assert rejected["terminal_outcomes"] == {
        "PUBLISHED": 36,
        "NOT_DISCLOSED": 0,
        "SOURCE_NOT_CHECKED": 220,
        "QUARANTINED": 1,
        "FAILED": 0,
    }
    assert rejected["visited"] == list(INGESTION_NODES)
    with Session(engine) as session:
        run = session.get(PipelineRun, interrupted["run_id"])
        assert run is not None
        assert run.status == "COMPLETED"
        decision = session.scalar(select(HumanReviewDecision))
        assert decision is not None
        assert decision.reviewer == "local-reviewer"
        assert decision.rationale == "deterministic test review"
        assert decision.thread_id == "review"
    engine.dispose()


def test_runtime_success_order_is_explicit_and_failure_audits_after_prefix(tmp_path: Path) -> None:
    successful_services = _RecordingRuntimeServices()
    successful = DeterministicIngestionRuntime(successful_services).run(thread_id="ordered")
    assert successful_services.calls == list(INGESTION_NODES)
    assert successful["visited"] == list(INGESTION_NODES)
    assert successful["terminal_status"] == "COMPLETED"

    failing_services = _RecordingRuntimeServices(fail_stage="parse_document")
    failed = DeterministicIngestionRuntime(failing_services).run(thread_id="failed")
    assert failing_services.calls == [
        "discover_sources",
        "acquire_source",
        "hash_and_store",
        "parse_document",
        "emit_audit_events",
    ]
    assert failed["visited"] == failing_services.calls
    assert failed["terminal_status"] == "FAILED"
    assert failed["error_codes"] == ["FIXTURE_STAGE_FAILED"]
    assert failed["audit_events"][-2:] == [
        "stage_failed:parse_document:FIXTURE_STAGE_FAILED",
        "ingestion_terminal:failed:published=0:not_disclosed=0:source_not_checked=0",
    ]

    database_url = f"sqlite:///{(tmp_path / 'failed.db').as_posix()}"
    engine = create_database_engine(database_url)
    persisted_services = _FailingStageAServices(
        engine=engine,
        retention_root=tmp_path / "failed-evidence",
        fail_stage="discover_sources",
        retryable=True,
    )
    persisted = DeterministicIngestionRuntime(persisted_services).run(thread_id="failed-db")
    assert persisted["terminal_status"] == "FAILED"
    with Session(engine) as session:
        run = session.get(PipelineRun, persisted["run_id"])
        assert run is not None
        assert run.status == "FAILED"
        error = session.scalar(
            select(IngestionError).where(IngestionError.pipeline_run_id == run.id)
        )
        assert error is not None
        assert error.stage == "discover_sources"
        assert error.error_code == "FIXTURE_STAGE_FAILED"
        assert error.retryable is True
    engine.dispose()


def test_runtime_rejects_review_when_persisted_run_identity_drifts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'identity.db').as_posix()}")
    services = StageAIngestionServices(engine=engine, retention_root=tmp_path / "evidence")
    runtime = DeterministicIngestionRuntime(services)
    paused = runtime.run(thread_id="identity-bound")
    candidate_id = paused["quarantine_candidate_ids"][0]
    monkeypatch.setattr(
        services,
        "_run_identity",
        lambda _source_keys: ("f" * 64, f"pipeline:{'f' * 32}"),
    )

    with pytest.raises(ValueError, match="no longer matches"):
        runtime.resume(
            thread_id="identity-bound",
            candidate_id=candidate_id,
            decision="approve",
        )

    with Session(engine) as session:
        candidate = session.get(QuarantineCandidate, candidate_id)
        assert candidate is not None
        assert candidate.status == "PENDING"
        assert session.scalar(select(func.count()).select_from(HumanReviewDecision)) == 0
    engine.dispose()


def test_runtime_persists_detected_duplicates_as_reviewable_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'duplicate.db').as_posix()}")
    services = StageAIngestionServices(engine=engine, retention_root=tmp_path / "evidence")
    original_parse = services._parse
    duplicate_id = "candidate-runtime-semantic-duplicate"

    def parse_with_duplicate(state: IngestionState) -> IngestionUpdate:
        update = original_parse(state)
        first = services._workspace.candidates[0]
        duplicate = replace(first, candidate_id=duplicate_id)
        services._workspace = replace(
            services._workspace,
            candidates=(*services._workspace.candidates, duplicate),
        )
        return update

    monkeypatch.setattr(services, "_parse", parse_with_duplicate)
    runtime = DeterministicIngestionRuntime(services)
    paused = runtime.run(thread_id="duplicate")
    assert duplicate_id in paused["quarantine_candidate_ids"]
    configured_id = next(
        candidate_id
        for candidate_id in paused["quarantine_candidate_ids"]
        if candidate_id != duplicate_id
    )
    with Session(engine) as session:
        candidate = session.get(QuarantineCandidate, duplicate_id)
        assert candidate is not None
        assert candidate.status == "PENDING"
        assert candidate.conflicts_and_uncertainties == [
            "duplicate semantic candidate requires deterministic resolution"
        ]

    still_paused = runtime.resume(
        thread_id="duplicate",
        candidate_id=configured_id,
        decision="reject",
    )
    assert still_paused["terminal_status"] == "AWAITING_REVIEW"
    failed = runtime.resume(
        thread_id="duplicate",
        candidate_id=duplicate_id,
        decision="approve",
    )
    assert failed["terminal_status"] == "FAILED"
    assert "UNRESOLVED_RUNTIME_DUPLICATE" in failed["error_codes"]
    with Session(engine) as session:
        duplicate = session.get(QuarantineCandidate, duplicate_id)
        assert duplicate is not None
        assert duplicate.status == "QUARANTINED_AFTER_REVALIDATION"
        assert session.scalar(select(func.count()).select_from(MetricObservation)) == 0
        run = session.get(PipelineRun, failed["run_id"])
        assert run is not None
        assert run.status == "FAILED"
        error = session.scalar(
            select(IngestionError).where(
                IngestionError.pipeline_run_id == failed["run_id"],
                IngestionError.error_code == "UNRESOLVED_RUNTIME_DUPLICATE",
            )
        )
        assert error is not None
    engine.dispose()


def test_runtime_revalidates_before_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'revalidation.db').as_posix()}")
    services = StageAIngestionServices(engine=engine, retention_root=tmp_path / "evidence")
    runtime = DeterministicIngestionRuntime(services)
    paused = runtime.run(thread_id="revalidate")
    candidate_id = paused["quarantine_candidate_ids"][0]
    events: list[str] = []
    original_revalidate = services._revalidate_quarantine_candidate

    def wrapped_revalidate(candidate: QuarantineCandidate) -> bool:
        events.append("revalidate")
        return original_revalidate(candidate)

    monkeypatch.setattr(services, "_revalidate_quarantine_candidate", wrapped_revalidate)
    original_seed = seed_stage_a

    def wrapped_seed(*args: Any, **kwargs: Any) -> dict[str, int]:
        assert events == ["revalidate"]
        events.append("publish")
        return original_seed(*args, **kwargs)

    monkeypatch.setattr(
        "mortgage_servicing_dashboard.ingestion.seed_stage_a",
        wrapped_seed,
    )
    resumed = runtime.resume(
        thread_id="revalidate",
        candidate_id=candidate_id,
        decision="approve",
        reviewer="reviewer@example.test",
        rationale="revalidation ordering",
    )
    assert resumed["terminal_status"] == "COMPLETED"
    assert events == ["revalidate", "publish"]
    engine.dispose()


def test_cli_database_commands(  # noqa: PLR0915
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    assert main([]) == 0
    assert "public-mortgage-servicing-intelligence" in capsys.readouterr().out
    assert main(["init-db", "--database-url", database_url]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inserted"]["observations"] == 36
    assert main(["seed", "--database-url", database_url]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inserted"]["observations"] == 0
    assert main(["discover", "--company", "TFC"]) == 0
    assert len(json.loads(capsys.readouterr().out)["sources"]) == 1
    assert main(["ingest", "--database-url", database_url]) == 0
    assert json.loads(capsys.readouterr().out)["database"] == "ready"
    assert main(["validate", "--database-url", database_url]) == 0
    assert json.loads(capsys.readouterr().out)["observations"] == 36
    assert main(["review", "list", "--database-url", database_url]) == 0
    candidates = json.loads(capsys.readouterr().out)["candidates"]
    assert len(candidates) == 1
    candidate_id = candidates[0]["id"]

    engine = create_database_engine(database_url)
    with Session(engine) as session:
        candidate = session.get(QuarantineCandidate, candidate_id)
        assert candidate is not None
        run = session.get(PipelineRun, candidate.pipeline_run_id)
        assert run is not None
        review_thread = run.thread_id
        observations_before_review = session.scalar(
            select(func.count()).select_from(MetricObservation)
        )
        revisions_before_review = session.scalar(
            select(func.count()).select_from(ObservationRevision)
        )
    engine.dispose()
    assert main(["review", "approve", "--database-url", database_url]) == 2
    assert "candidate-id" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(
            [
                "review",
                "approve",
                "--database-url",
                database_url,
                "--candidate-id",
                candidate_id,
                "--thread-id",
                review_thread,
            ]
        )
        == 0
    )
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["status"] == "QUARANTINED_AFTER_REVALIDATION"
    assert reviewed["decision"] == "approve"
    assert reviewed["thread_id"] == review_thread
    assert reviewed["terminal_status"] == "COMPLETED"
    assert reviewed["terminal_outcomes"]["QUARANTINED"] == 1
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        candidate = session.get(QuarantineCandidate, candidate_id)
        assert candidate is not None
        assert candidate.status == "QUARANTINED_AFTER_REVALIDATION"
        published_candidate = session.scalar(
            select(MetricObservation).where(
                MetricObservation.metric_version_id.like(f"{candidate.proposed_metric_id}:%"),
                MetricObservation.period_end == candidate.period_end,
                MetricObservation.value == candidate.proposed_normalized_value,
            )
        )
        assert published_candidate is None
        assert (
            session.scalar(select(func.count()).select_from(MetricObservation))
            == observations_before_review
        )
        assert (
            session.scalar(select(func.count()).select_from(ObservationRevision))
            == revisions_before_review
        )
    engine.dispose()
    repeated_approve = [
        "review",
        "approve",
        "--database-url",
        database_url,
        "--candidate-id",
        candidate_id,
        "--thread-id",
        review_thread,
    ]
    assert main(repeated_approve) == 0
    capsys.readouterr()
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        approve_decisions = session.scalars(
            select(HumanReviewDecision).where(HumanReviewDecision.decision == "APPROVE")
        ).all()
        assert len(approve_decisions) == 1
        assert (
            session.scalar(select(func.count()).select_from(MetricObservation))
            == observations_before_review
        )
        assert (
            session.scalar(select(func.count()).select_from(ObservationRevision))
            == revisions_before_review
        )
    engine.dispose()
    assert main([*repeated_approve, "--reviewer", "different-reviewer"]) == 4
    assert "review resume failed closed" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(
            [
                "review",
                "reject",
                "--database-url",
                database_url,
                "--candidate-id",
                candidate_id,
                "--thread-id",
                review_thread,
            ]
        )
        == 0
    )
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "REJECTED"
    assert rejected["decision"] == "reject"
    assert rejected["thread_id"] == review_thread
    assert rejected["terminal_status"] == "COMPLETED"
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        decisions = session.scalars(
            select(HumanReviewDecision).order_by(HumanReviewDecision.decision)
        ).all()
        assert [item.decision for item in decisions] == ["APPROVE", "REJECT"]
        assert {item.thread_id for item in decisions} == {review_thread}
    engine.dispose()
    assert (
        main(
            [
                "review",
                "reject",
                "--database-url",
                database_url,
                "--candidate-id",
                candidate_id,
                "--thread-id",
                "wrong-thread",
            ]
        )
        == 4
    )
    assert "original thread" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(
            [
                "review",
                "reject",
                "--database-url",
                database_url,
                "--candidate-id",
                "missing",
                "--thread-id",
                review_thread,
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["error"] == "candidate not found"


def test_cli_serve_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr("mortgage_servicing_dashboard.cli.uvicorn.run", run)
    database_url = f"sqlite:///{(tmp_path / 'serve.db').as_posix()}"
    assert main(["serve", "--database-url", database_url, "--port", "8123"]) == 0
    run.assert_called_once()
