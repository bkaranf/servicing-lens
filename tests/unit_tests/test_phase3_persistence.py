"""Phase 3 persistence, lineage, CLI, and read-API integration contracts."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.api import ObservationResponse, create_app
from mortgage_servicing_dashboard.cli import build_parser, doctor_payload
from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import (
    ComparabilityAssessment,
    DerivedObservationInput,
    EligibleSourceAssessment,
    MetricObservation,
    ObservationEvidence,
    PipelineRun,
    QuarantineCandidate,
    RawRegulatoryFact,
    SourceEvidence,
    create_database_engine,
    default_database_url,
    session_scope,
)
from mortgage_servicing_dashboard.domain import (
    ComparabilityStatus,
    ComparisonInput,
    ObservationState,
    assess_comparability,
)
from mortgage_servicing_dashboard.regulatory import FrY9cBulkAdapter, load_regulatory_config
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    persist_regulatory_facts,
    seed_phase3,
    seed_stage_a,
)

_ROOT = Path(__file__).resolve().parents[2]


def test_phase3_seed_is_idempotent_and_publicly_visible(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Persist the full composed dataset once and expose it through bounded reads."""
    pytest.importorskip("mortgage_servicing_dashboard.phase3")
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'phase3.db').as_posix()}")
    first = seed_phase3(engine, config_dir=_ROOT / "config")
    second = seed_phase3(engine, config_dir=_ROOT / "config")

    assert first["evidence"] == 21
    assert first["source_assessments"] == 424
    assert first["reported_observations"] == 120
    assert first["support_observations"] == 40
    assert first["derived_observations"] == 43
    assert first["not_disclosed_observations"] == 222
    assert first["blocked_derivations"] == 0
    assert first["cross_source_quarantines"] == 0
    assert second == dict.fromkeys(second, 0)

    with Session(engine) as session:
        run = session.scalar(select(PipelineRun).where(PipelineRun.id.like("pipeline:phase3:%")))
        assert run is not None
        assert run.status in {"COMPLETED", "COMPLETED_WITH_BLOCKED_DERIVATIONS"}
        assert int(session.scalar(select(func.count(SourceEvidence.id))) or 0) >= first["evidence"]
        assert (
            int(session.scalar(select(func.count(EligibleSourceAssessment.id))) or 0)
            >= first["source_assessments"]
        )
        assert int(session.scalar(select(func.count(ComparabilityAssessment.id))) or 0) > 0
        assessments = session.scalars(select(ComparabilityAssessment)).all()
        assert any(
            "controlled metric dimensions differ" in assessment.reasons
            for assessment in assessments
        )
        assert session.scalar(select(RawRegulatoryFact)) is None
        assert (
            session.scalar(
                select(MetricObservation).where(MetricObservation.methodology == "FR_Y9C")
            )
            is None
        )
        active = session.scalars(
            select(MetricObservation).where(MetricObservation.knowledge_to.is_(None))
        ).all()
        semantic_keys = [
            (
                item.metric_version_id,
                item.reporting_entity_id,
                item.reporting_scope_id,
                item.period_start,
                item.period_end,
                item.period_type,
                item.methodology,
                item.currency,
                item.unit,
                item.scale,
                tuple(sorted(item.dimensions.items())),
            )
            for item in active
        ]
        assert len(semantic_keys) == len(set(semantic_keys))
        legacy_rows = [
            item
            for item in session.scalars(select(MetricObservation)).all()
            if item.metric_version_id == "servicing_revenue:1.0.0"
            and item.reporting_entity_id == "tfc_registrant"
            and not item.id.startswith("observation:phase3:")
        ]
        assert len(legacy_rows) == 4
        prior_as_of = min(item.knowledge_from for item in legacy_rows)

    repository = IntelligenceRepository(engine)
    assert any(item["semantic_version"] == "2.0.0" for item in repository.metrics())
    phase3_rows = [
        item
        for item in repository.observations(limit=500)
        if item.parser_metadata.get("candidate_id")
    ]
    assert phase3_rows
    from mortgage_servicing_dashboard.metric_engine import MetricMethodology  # noqa: PLC0415

    reported_phase3_rows = [item for item in phase3_rows if item.state != "DERIVED"]
    assert all(MetricMethodology(item.methodology) for item in reported_phase3_rows)
    assert all(item.parser_metadata.get("source_methodology") for item in reported_phase3_rows)
    assert all(item.parser_metadata.get("normalization_trace") for item in reported_phase3_rows)
    assert any(
        item.reported_value.startswith("(")
        and cast("dict[str, object]", item.parser_metadata["normalization_trace"])[
            "sign_normalization"
        ]
        == "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE"
        for item in reported_phase3_rows
    )
    assert any(
        item.reported_value == "—"
        and cast("dict[str, object]", item.parser_metadata["normalization_trace"])["dash_policy"]
        == "PUBLISH_ZERO_ONLY_WHEN_ROW_PRESENTS_EM_DASH"
        for item in reported_phase3_rows
    )
    assert any(
        item.company_id == "tfc"
        and cast("dict[str, object]", item.parser_metadata["normalization_trace"])[
            "sign_normalization"
        ]
        == "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE"
        for item in reported_phase3_rows
    )
    assert (
        len(
            repository.observations(
                company_id="tfc",
                metric_id="servicing_revenue",
                as_of=prior_as_of,
            )
        )
        == 4
    )
    app = create_app(repository=repository)
    assert app.state.repository is repository
    response = ObservationResponse.model_validate(phase3_rows[0].as_dict())
    assert response.dimensions is not None
    assert response.fiscal_calendar_regime_id
    assert response.accounting_policy_regime_id
    engine.dispose()


def test_phase3_lineage_and_blocked_cells_remain_honest(tmp_path: Path) -> None:
    """Require exact lineage and prohibit blocked derivations from missingness."""
    pytest.importorskip("mortgage_servicing_dashboard.phase3")
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'lineage.db').as_posix()}")
    seed_phase3(engine, config_dir=_ROOT / "config")
    from mortgage_servicing_dashboard.metric_engine import MetricMethodology  # noqa: PLC0415

    with Session(engine) as session:
        derived = session.scalars(
            select(MetricObservation).where(
                MetricObservation.observation_state == "DERIVED",
                MetricObservation.publication_state == "PUBLISHED",
            )
        ).all()
        assert derived
        for observation in derived:
            lineage = session.scalars(
                select(DerivedObservationInput).where(
                    DerivedObservationInput.derived_observation_id == observation.id
                )
            ).all()
            assert lineage
            assert all(item.input_value is not None for item in lineage)
            for item in lineage:
                input_observation = session.get(MetricObservation, item.input_observation_id)
                assert input_observation is not None
                assert MetricMethodology(input_observation.methodology)
            assert observation.parser_metadata["calculation_trace_complete"] is True
            assert observation.dimensions

            detail = IntelligenceRepository(engine).observation(observation.id)
            assert detail is not None
            expected_evidence = set(cast("list[str]", observation.parser_metadata["evidence_ids"]))
            assert {item["evidence_id"] for item in detail.evidence_links} == expected_evidence
            assert all(item["locator"] and item["source_url"] for item in detail.evidence_links)

        blocked = session.scalars(
            select(QuarantineCandidate).where(QuarantineCandidate.status == "BLOCKED_LINEAGE")
        ).all()
        assert blocked == []
        for candidate in blocked:
            false_missing = session.scalar(
                select(MetricObservation).where(
                    MetricObservation.metric_version_id.like(f"{candidate.proposed_metric_id}:%"),
                    MetricObservation.reporting_entity_id == candidate.reporting_entity_id,
                    MetricObservation.reporting_scope_id == candidate.reporting_scope_id,
                    MetricObservation.period_end == candidate.period_end,
                    MetricObservation.observation_state == "NOT_DISCLOSED",
                )
            )
            assert false_missing is None
    engine.dispose()


def test_phase3_blocked_derivation_is_quarantined_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist a genuine blocked lineage result without inventing missingness."""
    import mortgage_servicing_dashboard.phase3 as phase3_module  # noqa: PLC0415
    from mortgage_servicing_dashboard.phase3 import (  # noqa: PLC0415
        Phase3BlockedDerivation,
    )

    dataset = phase3_module.load_phase3_dataset(_ROOT / "config")
    target = next(item for item in dataset.missing_cells if item.source_keys)
    blocked = Phase3BlockedDerivation(
        company_id=target.company_id,
        metric_id=target.metric_id,
        period_end=target.period_end,
        formula_version="coverage-blocked-v1",
        missing_input_metric_ids=("governed_missing_input",),
        reason="exact published input revision is unavailable",
    )
    blocked_dataset = replace(
        dataset,
        blocked_derivations=(blocked,),
        missing_cells=tuple(item for item in dataset.missing_cells if item != target),
    )
    monkeypatch.setattr(phase3_module, "load_phase3_dataset", lambda _: blocked_dataset)

    engine = create_database_engine(f"sqlite:///{(tmp_path / 'blocked.db').as_posix()}")
    first = seed_phase3(engine, config_dir=_ROOT / "config")
    second = seed_phase3(engine, config_dir=_ROOT / "config")
    assert first["blocked_derivations"] == 1
    assert first["not_disclosed_observations"] == len(blocked_dataset.missing_cells)
    assert second == dict.fromkeys(second, 0)

    with Session(engine) as session:
        candidate = session.scalar(
            select(QuarantineCandidate).where(
                QuarantineCandidate.status == "BLOCKED_LINEAGE",
                QuarantineCandidate.proposed_metric_id == target.metric_id,
                QuarantineCandidate.period_end == target.period_end,
            )
        )
        assert candidate is not None
        assert candidate.pipeline_run_id
        assert candidate.evidence_id
        assert candidate.evidence_locator == "; ".join(target.locators)
        assert candidate.proposed_normalized_value is None
        assert candidate.conflicts_and_uncertainties == [
            blocked.reason,
            "missing exact published inputs: governed_missing_input",
            "formula version: coverage-blocked-v1",
        ]
        run = session.get(PipelineRun, candidate.pipeline_run_id)
        assert run is not None
        assert run.status == "COMPLETED_WITH_BLOCKED_DERIVATIONS"
        assert run.terminal_outcomes["QUARANTINED"] == 1
        false_missing = session.scalar(
            select(MetricObservation).where(
                MetricObservation.metric_version_id.like(f"{target.metric_id}:%"),
                MetricObservation.reporting_entity_id == target.reporting_entity_id,
                MetricObservation.reporting_scope_id == target.reporting_scope_id,
                MetricObservation.period_end == target.period_end,
                MetricObservation.observation_state == "NOT_DISCLOSED",
            )
        )
        assert false_missing is None
    engine.dispose()


def test_phase3_cli_routes_are_explicit() -> None:
    """Keep both dedicated and ingest-flag routes discoverable."""
    assert build_parser().parse_args(["seed-phase3"]).command == "seed-phase3"
    ingest = build_parser().parse_args(["ingest", "--phase3"])
    assert ingest.command == "ingest"
    assert ingest.phase3 is True
    doctor = doctor_payload(AppSettings())
    assert doctor["stage"] == "phase_3_metric_deepening"
    assert doctor["capabilities"]["phase"] == "phase_3_metric_deepening"


def test_phase3_dimensions_schema_and_api_contract() -> None:
    """Retain controlled dimensions on observation rows and read responses."""
    from mortgage_servicing_dashboard.database import MetricObservation  # noqa: PLC0415

    assert MetricObservation.__table__.c.dimensions.nullable is False
    assert MetricObservation.__table__.c.fiscal_calendar_regime_id.nullable is False
    assert MetricObservation.__table__.c.accounting_policy_regime_id.nullable is False
    assert any(
        constraint.name == "uq_observation_semantic_digest_knowledge"
        for constraint in cast("Any", MetricObservation.__table__).constraints
    )
    migration = (_ROOT / "alembic" / "versions" / "0003_phase3_derived_lineage.py").read_text(
        encoding="utf-8"
    )
    assert '"dimensions"' in migration
    assert 'op.drop_column("metric_observations", "dimensions")' in migration
    assert "_backfill_observation_regimes()" in migration


def test_phase3_migration_backfills_populated_0002_observations(tmp_path: Path) -> None:
    """Upgrade populated 0002 data into complete, non-null semantic identities."""
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'populated-0002.db').as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002_phase2_structured_acquisition")
        connection.execute(
            text(
                "INSERT INTO companies VALUES "
                "('legacy','Legacy Servicer','LGY','servicer','legacy-v1',1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO reporting_entities VALUES "
                "('legacy_registrant','legacy','Legacy Servicer','SEC_REGISTRANT')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO reporting_scopes VALUES "
                "('legacy_scope','legacy_registrant','Legacy scope','legacy_population',"
                "'issuer disclosed')"
            )
        )
        connection.execute(
            text("INSERT INTO metric_definitions VALUES ('legacy_metric','Legacy metric','test')")
        )
        connection.execute(
            text(
                "INSERT INTO metric_definition_versions VALUES "
                "('legacy_metric:1.0.0','legacy_metric','1.0.0','legacy meaning',"
                "'entity scope period','USD','[]','{}','1900-01-01',NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO metric_observations "
                "(id,metric_version_id,reporting_entity_id,reporting_scope_id,period_start,"
                "period_end,fiscal_year,fiscal_quarter,period_type,value,currency,unit,scale,"
                "reported_decimals,reported_precision,observation_state,methodology,"
                "evidence_locator,extraction_method,parser_metadata,validation_summary,"
                "publication_state,revision_number,semantic_key_digest,valid_from,valid_to,"
                "knowledge_from,knowledge_to,supersedes_observation_id,quality_state,"
                "reported_label,reported_value,published_at) VALUES "
                "('legacy-observation','legacy_metric:1.0.0','legacy_registrant',"
                "'legacy_scope','2026-04-01','2026-06-30',2026,2,'duration',100,'USD','USD',"
                "'1',0,'exact','REPORTED_ACTUAL','ISSUER_REPORTED','legacy locator',"
                "'legacy_parser','{}','validated','PUBLISHED',1,:digest,'2026-06-30',NULL,"
                "'2026-08-01 00:00:00',NULL,NULL,'VALIDATED','Legacy','100',"
                "'2026-08-01 00:00:00')"
            ),
            {"digest": "0" * 64},
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    columns = {item["name"]: item for item in inspect(engine).get_columns("metric_observations")}
    assert columns["fiscal_calendar_regime_id"]["nullable"] is False
    assert columns["accounting_policy_regime_id"]["nullable"] is False
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT fiscal_calendar_regime_id, accounting_policy_regime_id, "
                "semantic_key_digest FROM metric_observations WHERE id='legacy-observation'"
            )
        ).one()
    assert row[0] == "legacy_registrant:calendar"
    assert row[1] == "legacy_registrant:us-gaap"
    assert row[2] != "0" * 64
    assert len(row[2]) == 64
    first = seed_phase3(engine, config_dir=_ROOT / "config")
    second = seed_phase3(engine, config_dir=_ROOT / "config")
    assert first["source_assessments"] == 424
    assert second == dict.fromkeys(second, 0)
    engine.dispose()


def test_phase3_normalization_replay_is_fail_closed() -> None:
    """Exercise exact scale, sign, dash, and invalid-trace replay branches."""
    from mortgage_servicing_dashboard.repository import (  # noqa: PLC0415
        _replay_phase3_normalization,
    )

    def wrapped(raw: str, rule: str, sign: str, dash: str | None = None) -> object:
        return SimpleNamespace(
            candidate=SimpleNamespace(raw_value=raw),
            normalization_trace=SimpleNamespace(
                rule=rule,
                sign_normalization=sign,
                dash_policy=dash,
            ),
        )

    assert _replay_phase3_normalization(
        wrapped("12", "usd_from_thousands", "PRESERVE_REPORTED_SIGN")
    ) == Decimal(12000)
    assert _replay_phase3_normalization(
        wrapped("(7.5)", "usd_from_millions", "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE")
    ) == Decimal(7500000)
    assert _replay_phase3_normalization(
        wrapped("—", "identity", "PRESERVE_REPORTED_SIGN", "DASH_ZERO")
    ) == Decimal(0)
    with pytest.raises(ValueError, match="non-dash"):
        _replay_phase3_normalization(
            wrapped("1", "identity", "PRESERVE_REPORTED_SIGN", "DASH_ZERO")
        )
    with pytest.raises(ValueError, match="unknown sign"):
        _replay_phase3_normalization(wrapped("1", "identity", "UNCONTROLLED"))


def test_numeric_orm_boundaries_accept_exact_types_and_reject_binary_float() -> None:
    """Cover exact-Decimal guards on observations, lineage, and quarantine."""
    observation_validator = MetricObservation.reject_float_value
    lineage_validator = DerivedObservationInput.reject_float_value
    quarantine_validator = QuarantineCandidate.reject_float_value
    assert observation_validator(cast("Any", None), "value", None) is None
    assert observation_validator(cast("Any", None), "value", Decimal("1.25")) == Decimal("1.25")
    assert observation_validator(cast("Any", None), "value", "2.5") == Decimal("2.5")
    assert lineage_validator(cast("Any", None), "input_value", 3) == Decimal(3)
    assert quarantine_validator(cast("Any", None), "value", None) is None
    assert quarantine_validator(cast("Any", None), "value", "4.5") == Decimal("4.5")
    for validator in (observation_validator, lineage_validator, quarantine_validator):
        with pytest.raises(TypeError, match="binary floats"):
            validator(cast("Any", None), "value", 1.5)
        with pytest.raises(TypeError):
            validator(cast("Any", None), "value", object())


def test_database_helpers_commit_rollback_and_create_local_url(tmp_path: Path) -> None:
    """Exercise deterministic URL creation and transaction rollback semantics."""
    local_dir = tmp_path / "nested" / "data"
    assert default_database_url(local_dir).endswith("nested/data/msi.db")
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'scope.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE probe (value INTEGER NOT NULL)"))
    with session_scope(engine) as session:
        session.execute(text("INSERT INTO probe VALUES (1)"))

    def insert_then_fail() -> None:
        message = "rollback"
        with session_scope(engine) as session:
            session.execute(text("INSERT INTO probe VALUES (2)"))
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="rollback"):
        insert_then_fail()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM probe")) == 1
    engine.dispose()


def test_phase3_private_identity_helpers_fail_closed() -> None:
    """Cover canonical run serialization and unknown metric rejection."""
    from mortgage_servicing_dashboard.metric_engine import load_metric_catalog  # noqa: PLC0415
    from mortgage_servicing_dashboard.repository import (  # noqa: PLC0415
        _canonical_run_value,
        _phase3_metric_version,
    )

    canonical = _canonical_run_value(
        {
            "decimal": Decimal("1.25"),
            "date": date(2026, 6, 30),
            "path": Path("config/metrics/catalog.yaml"),
            "tuple": (1, 2),
        }
    )
    assert canonical == {
        "date": "2026-06-30",
        "decimal": "1.25",
        "path": "config/metrics/catalog.yaml",
        "tuple": [1, 2],
    }
    catalog = load_metric_catalog(_ROOT / "config" / "metrics" / "catalog.yaml")
    with pytest.raises(ValueError, match="absent"):
        _phase3_metric_version(catalog, "metric_that_does_not_exist")


def test_reported_weighted_fee_rate_permits_instant_period() -> None:
    """Catalog permits the governed reported-rate and derived-duration variants."""
    from mortgage_servicing_dashboard.metric_engine import (  # noqa: PLC0415
        PeriodType,
        load_metric_catalog,
    )

    catalog = load_metric_catalog(
        _ROOT / "config" / "metrics" / "catalog.yaml",
        extension_paths=(_ROOT / "config" / "metrics" / "phase3_deepening.v1.yaml",),
    )
    definition = catalog.definition("weighted_average_servicing_fee_bps", "2.0.0")
    assert definition is not None
    assert definition.period_types == (PeriodType.INSTANT, PeriodType.DURATION)


def test_dimension_mismatch_is_not_comparable() -> None:
    """A controlled dimension mismatch blocks deterministic comparison."""
    left = ComparisonInput(
        metric_id="delinquency_60_plus_count_rate",
        metric_version="2.0.0",
        reporting_scope="owned_msr",
        period_days=None,
        currency=None,
        unit="ratio",
        methodology="DELINQUENCY_COUNT_REPORTED",
        observation_state=ObservationState.REPORTED_ACTUAL,
        portfolio_population="owned_msr",
        dimensions=(("delinquency_measure_basis", "count"),),
    )
    right = replace(left, dimensions=(("delinquency_measure_basis", "upb"),))
    result = assess_comparability(left, right)
    assert result.status is ComparabilityStatus.NOT_COMPARABLE
    assert result.reasons == ("controlled metric dimensions differ",)


def test_phase3_semantic_digest_distinguishes_metric_and_dimensions() -> None:
    """Metric identity and controlled dimensions cannot collide."""
    phase3 = pytest.importorskip("mortgage_servicing_dashboard.phase3")
    from mortgage_servicing_dashboard.repository import (  # noqa: PLC0415
        _phase3_reported_semantic_digest,
    )

    dataset = phase3.load_phase3_dataset(_ROOT / "config")
    wrapped = dataset.reported_candidates[0]
    candidate = wrapped.candidate
    dimensions = {item.name: item.value for item in wrapped.dimensions}
    regimes = ("tfc_registrant:calendar", "tfc_registrant:us-gaap")
    original = _phase3_reported_semantic_digest(candidate, dimensions, "1", *regimes)
    assert original != _phase3_reported_semantic_digest(
        replace(candidate, metric_id=f"{candidate.metric_id}_other"),
        dimensions,
        "1",
        *regimes,
    )
    assert original != _phase3_reported_semantic_digest(
        candidate,
        {**dimensions, "test_dimension": "different"},
        "1",
        *regimes,
    )


def test_phase3_reuses_byte_identical_compatible_evidence(tmp_path: Path) -> None:
    """Remap links when immutable original bytes already have a database identity."""
    phase3 = pytest.importorskip("mortgage_servicing_dashboard.phase3")
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'reuse.db').as_posix()}")
    seed_stage_a(engine, config_dir=_ROOT / "config")
    dataset = phase3.load_phase3_dataset(_ROOT / "config")
    with Session(engine) as session:
        existing_hashes = set(session.scalars(select(SourceEvidence.content_sha256)))
        evidence = next(item for item in dataset.evidence if item.sha256 not in existing_hashes)
        alias_id = "evidence:preexisting-compatible-original"
        session.add(
            SourceEvidence(
                id=alias_id,
                source_class=evidence.source_class,
                original_url=evidence.url,
                retrieved_at=evidence.retrieved_at,
                published_at=evidence.published_at,
                accession_or_identifier=evidence.accession,
                content_sha256=evidence.sha256,
                byte_length=evidence.byte_length,
                media_type=evidence.media_type,
                representation=evidence.representation,
                capture_method=evidence.capture_method,
                parser_version=evidence.parser_version,
                acquisition_run_id="preexisting-compatible-run",
                reporting_entity_candidate=f"{evidence.company_id}_registrant",
                reporting_period_candidate=evidence.period_end.isoformat(),
                retention_location=str(evidence.retention_location),
                bounded_excerpt="Preexisting immutable original bytes.",
                response_status=200,
                etag=None,
                last_modified=None,
            )
        )
        session.commit()

    seed_phase3(engine, config_dir=_ROOT / "config")
    with Session(engine) as session:
        assert session.get(SourceEvidence, alias_id) is not None
        assert session.get(SourceEvidence, evidence.evidence_id) is None
        linked = session.scalar(
            select(func.count(ObservationEvidence.observation_id)).where(
                ObservationEvidence.evidence_id == alias_id
            )
        )
        assert int(linked or 0) > 0
    engine.dispose()


def test_phase3_regulatory_mismatch_is_audited_without_preference(tmp_path: Path) -> None:
    """A Y-9C mismatch quarantines the comparison without preferring either source."""
    pytest.importorskip("mortgage_servicing_dashboard.phase3")
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'reconciliation.db').as_posix()}")
    seed_stage_a(engine, config_dir=_ROOT / "config")
    fixture = (
        _ROOT / "tests" / "fixtures" / "phase2" / "regulatory" / "fr_y9c_2026q2.txt"
    ).read_bytes()
    evidence_id = "evidence:synthetic-tfc-y9c-phase3-reconciliation"
    digest = hashlib.sha256(fixture).hexdigest()
    with Session(engine) as session:
        session.add(
            SourceEvidence(
                id=evidence_id,
                source_class="SYNTHETIC_FR_Y9C",
                original_url="https://example.invalid/synthetic-y9c-test-only",
                retrieved_at=datetime(2026, 8, 12, tzinfo=UTC),
                published_at=None,
                accession_or_identifier="SYNTHETIC_TEST_DATA",
                content_sha256=digest,
                byte_length=len(fixture),
                media_type="text/plain",
                representation="SYNTHETIC_TEST_DATA",
                capture_method="test_fixture",
                parser_version="1.0.0",
                acquisition_run_id="fixture-only",
                reporting_entity_candidate="tfc_bhc_regulatory_reporter",
                reporting_period_candidate="2026-06-30",
                retention_location=f"test-fixture-sha256://{digest}",
                bounded_excerpt="Synthetic adapter fixture; not public evidence.",
                response_status=None,
                etag=None,
                last_modified=None,
            )
        )
        session.commit()
    regulatory_config = load_regulatory_config(
        _ROOT / "config" / "regulatory" / "regulatory_mappings.v1.yaml"
    )
    facts = FrY9cBulkAdapter(regulatory_config).parse(
        fixture,
        rssd_id="1074156",
        report_date=date(2026, 6, 30),
    )
    assert persist_regulatory_facts(engine, facts, evidence_id=evidence_id) == 5

    result = seed_phase3(engine, config_dir=_ROOT / "config")
    assert result["cross_source_quarantines"] == 1
    with Session(engine) as session:
        assert int(session.scalar(select(func.count(RawRegulatoryFact.id))) or 0) == 5
        quarantine = session.scalar(
            select(QuarantineCandidate).where(
                QuarantineCandidate.status == "CROSS_SOURCE_NO_PREFERENCE"
            )
        )
        assert quarantine is not None
        assert quarantine.proposed_normalized_value is None
        public = session.scalars(
            select(MetricObservation).where(
                MetricObservation.metric_version_id.like("servicing_for_others_upb:%"),
                MetricObservation.reporting_entity_id == "tfc_registrant",
                MetricObservation.period_end == date(2026, 6, 30),
                MetricObservation.publication_state == "PUBLISHED",
                MetricObservation.quality_state == "VALIDATED",
                MetricObservation.knowledge_to.is_(None),
            )
        ).all()
        assert len(public) == 1
        history = session.scalars(
            select(MetricObservation).where(
                MetricObservation.metric_version_id.like("servicing_for_others_upb:%"),
                MetricObservation.reporting_entity_id == "tfc_registrant",
                MetricObservation.period_end == date(2026, 6, 30),
            )
        ).all()
        assert len(history) == 2
        prior = next(item for item in history if item.knowledge_to is not None)
        assert public[0].supersedes_observation_id == prior.id
        assert session.scalar(
            select(func.count(ObservationEvidence.evidence_id)).where(
                ObservationEvidence.observation_id.in_([prior.id, public[0].id])
            )
        )
        reconciled_public = [
            item for item in public if "cross_source_reconciliations" in item.parser_metadata
        ]
        assert len(reconciled_public) == 1
        reconciliation_audit = reconciled_public[0].parser_metadata["cross_source_reconciliations"]
        assert isinstance(reconciliation_audit, list)
        assert reconciliation_audit[0]["preferred_observation_id"] is None
        assert (
            session.scalar(
                select(MetricObservation).where(MetricObservation.methodology == "FR_Y9C")
            )
            is None
        )
    engine.dispose()
