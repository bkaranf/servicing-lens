"""End-to-end Stage A tests using only recorded public fixtures and local stores."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.database import (
    Base,
    Company,
    ComparabilityAssessment,
    HumanReviewDecision,
    MetricObservation,
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
    StageAIngestionServices,
    create_ingestion_graph,
    resume_review,
)
from mortgage_servicing_dashboard.privacy import (
    DataClassification,
    PromptBoundary,
    SensitiveContentError,
    strip_corporate_contact_blocks,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    seed_stage_a,
)
from mortgage_servicing_dashboard.sources import (
    DisabledBankRegulatoryAdapter,
    PublicSourceError,
    SecClient,
)
from mortgage_servicing_dashboard.tools import build_intelligence_tools


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
    assert health.model_calls_enabled is False
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


def test_ingestion_graph_happy_path_and_same_thread_review_resume(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'graph.db').as_posix()}")
    services = StageAIngestionServices(engine=engine, retention_root=tmp_path / "evidence")
    graph = create_ingestion_graph(services=services, checkpointer=InMemorySaver())
    config = RunnableConfig(configurable={"thread_id": "review"})
    state = {
        "thread_id": "review",
        "source_keys": [],
        "visited": [],
        "review_decision": "pending",
        "published_count": 0,
        "audit_events": [],
    }
    interrupted = graph.invoke(state, config=config)
    assert interrupted["__interrupt__"]
    assert len(interrupted["candidate_ids"]) == 36
    assert interrupted["quarantine_candidate_ids"] == [
        "candidate-pfsi-2026q2-expenses-excluding-valuation"
    ]
    rejected = resume_review(graph, thread_id="review", decision="reject")
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
    engine.dispose()


def test_sec_client_success_cache_retries_and_boundaries(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        SecClient(user_agent="bad", cache_directory=tmp_path)
    with pytest.raises(ValueError, match="positive"):
        SecClient(
            user_agent="Research research@example.test",
            cache_directory=tmp_path,
            minimum_interval_seconds=-1,
        )

    calls = 0

    def success(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"public filing", headers={"content-type": "text/html"})

    with SecClient(
        user_agent="Research research@example.test",
        cache_directory=tmp_path / "cache",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(success),
    ) as client:
        with pytest.raises(ValueError, match="official"):
            client.acquire("https://example.test/file")
        first = client.acquire("https://www.sec.gov/Archives/file.htm")
        second = client.acquire("https://www.sec.gov/Archives/file.htm")
    assert calls == 1
    assert first.sha256 == second.sha256
    assert first.media_type == "text/html"
    assert second.media_type == "text/html"

    def failure(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    client = SecClient(
        user_agent="Research research@example.test",
        cache_directory=tmp_path / "failure",
        minimum_interval_seconds=0,
        max_attempts=1,
        transport=httpx.MockTransport(failure),
    )
    with pytest.raises(PublicSourceError, match="bounded retries"):
        client.acquire("https://data.sec.gov/submissions/CIK.json")
    client.close()
    with pytest.raises(PublicSourceError, match="not configured"):
        DisabledBankRegulatoryAdapter().facts(rssd_id="123", period_end="2026-06-30")


def test_privacy_classifications_public_identifiers_and_contacts() -> None:
    boundary = PromptBoundary(max_chars=300, secret_environment={})
    approved = boundary.approve(
        "CIK 0000092230 accession 0000092230-26-000096 public filing",
        classification=DataClassification.PUBLIC,
    )
    assert "0000092230" in approved.text
    regulatory = boundary.approve(
        "Public RSSD summary.", classification=DataClassification.PUBLIC_REGULATORY
    )
    assert regulatory.classification is DataClassification.PUBLIC_REGULATORY
    issuer = boundary.approve(
        "Issuer quarterly performance summary.", classification=DataClassification.ISSUER_PUBLIC
    )
    assert issuer.classification is DataClassification.ISSUER_PUBLIC
    with pytest.raises(SensitiveContentError, match="restricted_private"):
        boundary.approve("private data", classification=DataClassification.RESTRICTED_PRIVATE)
    cleaned = strip_corporate_contact_blocks(
        "Investor Relations: person@example.com\nFinancial results follow."
    )
    assert "example.com" not in cleaned
    assert "Financial results" in cleaned


def test_read_only_intelligence_tools(seeded_engine: Engine) -> None:
    repo = IntelligenceRepository(seeded_engine)
    tools = {tool.name: tool for tool in build_intelligence_tools(repo)}
    assert set(tools) == {
        "list_companies",
        "get_company_profile",
        "list_metric_definitions",
        "get_metric_series",
        "list_observations",
        "compare_metric",
        "get_observation_provenance",
        "get_evidence",
        "get_disclosure_coverage",
        "list_earnings_events",
        "get_pipeline_freshness",
    }
    assert len(tools["list_companies"].invoke({})) == 2
    assert tools["get_company_profile"].invoke({"company_id": "tfc"})["observation_count"] == 20
    assert len(tools["list_metric_definitions"].invoke({})) == 32
    series = tools["get_metric_series"].invoke(
        {"company_id": "tfc", "metric_id": "total_servicing_upb"}
    )
    assert len(series) == 4
    observation_id = series[0]["id"]
    assert (
        len(
            tools["list_observations"].invoke(
                {
                    "company_id": "tfc",
                    "metric_id": "total_servicing_upb",
                    "period_end": "2026-06-30",
                }
            )
        )
        == 1
    )
    assert tools["get_observation_provenance"].invoke({"observation_id": observation_id})[
        "source_url"
    ]
    assert tools["get_observation_provenance"].invoke({"observation_id": "missing"}) == {
        "status": "not_found"
    }
    assert tools["get_evidence"].invoke({"evidence_id": "evidence:tfc_2026_q2_qps"})["original_url"]
    assert tools["get_evidence"].invoke({"evidence_id": "missing"}) == {"status": "not_found"}
    assert (
        tools["compare_metric"].invoke(
            {"metric_id": "total_servicing_upb", "period_end": "2026-06-30"}
        )["status"]
        == "not_comparable"
    )
    with pytest.raises(ValueError, match="Unknown metric"):
        tools["compare_metric"].invoke({"metric_id": "unknown", "period_end": "2026-06-30"})
    assert len(tools["get_disclosure_coverage"].invoke({})) == 8
    assert len(tools["list_earnings_events"].invoke({})) == 2
    assert tools["get_pipeline_freshness"].invoke({})["published_count"] == 36


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
