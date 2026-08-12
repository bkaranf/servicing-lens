"""Backend contracts for immutable evidence, exact parsing, and migrations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    Base,
    EligibleSourceAssessment,
    MetricObservation,
    QuarantineCandidate,
    create_database_engine,
    initialize_schema,
)
from mortgage_servicing_dashboard.domain import ObservationState, ParsedObservationCandidate
from mortgage_servicing_dashboard.ingestion import (
    IngestionServiceError,
    StageAIngestionServices,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    seed_stage_a,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    PublicSourceError,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
    TransientPublicSourceError,
    _qualified_row_matches,
    _TableRows,
)


def _configured_sources() -> tuple[
    Path,
    dict[str, Any],
    dict[str, Any],
    dict[str, RecordedSourceDefinition],
]:
    root = config_directory()
    universe, _, data = load_stage_a_configuration(root)
    source_payloads = cast("dict[str, dict[str, Any]]", data["sources"])
    definitions = {
        key: RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=root,
        )
        for key, payload in source_payloads.items()
    }
    return root, universe, data, definitions


def test_recorded_parser_matches_independent_exact_expectations() -> None:
    root, universe, data, definitions = _configured_sources()
    expected = json.loads(
        (
            root.parent / "tests" / "fixtures" / "stage_a" / "expected_recorded_parser_outputs.json"
        ).read_text(encoding="utf-8")
    )
    companies = {str(item["id"]): item for item in universe["companies"]}
    candidates: list[ParsedObservationCandidate] = []
    for key, definition in definitions.items():
        document = RecordedEvidenceAcquirer().acquire(definition)
        expected_source = expected["sources"][key]
        assert document.byte_length == expected_source["byte_length"]
        assert document.sha256 == expected_source["sha256"]
        assert hashlib.sha256(document.content).hexdigest() == definition.content_sha256
        candidates.extend(
            StageARecordedDocumentParser().parse(
                source=definition,
                content=document.content,
                company=companies[definition.company_id],
                quarters=data["quarters"],
            )
        )

    assert len(candidates) == 36
    for row in expected["rows"]:
        actual = [
            item
            for item in candidates
            if item.company_id == row["company_id"] and item.metric_id == row["metric_id"]
        ]
        assert [item.period_end.isoformat() for item in actual] == row["periods"]
        assert [item.raw_value for item in actual] == row["raw_values"]
        assert [str(item.normalized_value) for item in actual] == row["normalized_values"]
        assert {item.observation_state.value for item in actual} == {row["state"]}

    direct_tfc = [
        item
        for item in candidates
        if item.company_id == "tfc"
        and item.metric_id in {"total_servicing_upb", "weighted_average_servicing_fee_bps"}
    ]
    assert direct_tfc
    assert {item.observation_state for item in direct_tfc} == {ObservationState.REPORTED_ACTUAL}


def test_manifest_has_no_authoritative_numeric_observation_payload() -> None:
    _, _, data, _ = _configured_sources()
    forbidden_keys = {"observations", "values", "raw_values", "normalized_values"}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(data)


def test_recorded_evidence_integrity_fails_closed(tmp_path: Path) -> None:
    _, _, _, definitions = _configured_sources()
    source = definitions["tfc_2026_q2_qps"]
    corrupted_path = tmp_path / "corrupted.html"
    corrupted_path.write_bytes(source.fixture_path.read_bytes() + b"corruption")
    corrupted = replace(source, fixture_path=corrupted_path)
    with pytest.raises(PublicSourceError, match="integrity mismatch"):
        RecordedEvidenceAcquirer().acquire(corrupted)


def test_recorded_parser_supports_explicit_columns_and_reduction_signs() -> None:
    parser = StageARecordedDocumentParser()
    row = ("Realization of expected cash flows", "(12.5)", "9.0", "(7.5)")
    recipe: dict[str, Any] = {"value_indices": [2, 0]}
    first = parser._configured_row_value(
        row=row,
        recipe=recipe,
        period_index=0,
        raw_label=row[0],
    )
    second = parser._configured_row_value(
        row=row,
        recipe=recipe,
        period_index=1,
        raw_label=row[0],
    )
    assert (first, second) == ("(7.5)", "(12.5)")
    assert parser._apply_sign_normalization(
        first,
        rule="usd_from_millions:positive_reduction_magnitude",
    ) == Decimal("7500000.0")
    assert parser._apply_sign_normalization(
        second,
        rule="usd_from_millions:positive_reduction_magnitude",
    ) == Decimal("12500000.0")
    with pytest.raises(PublicSourceError, match="sign normalization"):
        parser._apply_sign_normalization(
            first,
            rule="usd_from_millions:guess_sign",
        )


def test_recorded_parser_qualifies_duplicate_labels_by_anchor_and_headers() -> None:
    collector = _TableRows()
    collector.feed(
        "<table><tr><th>Wrong table</th></tr><tr><th>2024</th></tr>"
        "<tr><td>Ending balance</td><td>1</td></tr></table>"
        "<table><tr><th>MSR roll-forward</th></tr><tr><th>2026</th></tr>"
        "<tr><td>Ending balance</td><td>2</td></tr></table>"
    )
    matches = _qualified_row_matches(
        collector,
        raw_label="Ending balance",
        recipe={"table_anchor": "MSR roll-forward", "column_headers": ["2026"]},
    )
    assert matches == [("Ending balance", "2")]


def test_recorded_parser_preserves_reported_dash_column_alignment() -> None:
    parser = StageARecordedDocumentParser()
    row = ("Sales", "1", "—", "3")
    assert (
        parser._configured_row_value(
            row=row,
            recipe={"value_index": 1},
            period_index=0,
            raw_label="Sales",
        )
        == "—"
    )


def test_recorded_parser_extracts_bounded_narrative_value() -> None:
    parser = StageARecordedDocumentParser()
    recipe: dict[str, Any] = {
        "raw_label_prefix": "Servicing fees recognized",
        "text_value_pattern": (
            r"Servicing fees recognized were \$(?P<value>[\d,]+) million for the three months"
        ),
    }
    assert (
        parser._configured_text_value(
            text="Servicing fees recognized were $155 million for the three months ended.",
            recipe=recipe,
            raw_label="Servicing fees recognized",
        )
        == "155"
    )
    with pytest.raises(PublicSourceError, match="not uniquely selected"):
        parser._configured_text_value(
            text="Servicing fees recognized were unavailable.",
            recipe=recipe,
            raw_label="Servicing fees recognized",
        )


def _migration_config(engine: Any) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", str(engine.url))
    return config


def test_explicit_migration_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    initialize_schema(engine)
    expected_tables = set(Base.metadata.tables)
    assert expected_tables <= set(inspect(engine).get_table_names())
    migration_source = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0001_public_intelligence_schema.py"
    ).read_text(encoding="utf-8")
    assert "op.create_table" in migration_source
    assert "metadata.create_all" not in migration_source
    assert "metadata.drop_all" not in migration_source

    config = _migration_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert expected_tables.isdisjoint(inspect(engine).get_table_names())
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    assert expected_tables <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_repository_exposes_exact_semantics_evidence_and_bounded_page(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'repository.db').as_posix()}")
    seed_stage_a(engine)
    repository = IntelligenceRepository(engine)
    page = repository.observation_page(limit=2, offset=1)
    assert page["count"] == 36
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert len(cast("list[object]", page["items"])) == 2
    with pytest.raises(ValueError, match="limit"):
        repository.observations(limit=501)

    tfc_total = repository.observations(
        company_id="tfc",
        metric_id="total_servicing_upb",
        include_missing=False,
    )[-1]
    assert tfc_total.value == "298658000000.0000000000"
    assert tfc_total.state == "REPORTED_ACTUAL"
    assert tfc_total.reported_precision == "nearest USD million"
    assert tfc_total.extraction_method == "deterministic_html_table"
    assert tfc_total.validation_summary
    assert tfc_total.evidence_sha256 == (
        "7353334b2f40cb48d0ed6dc6756378e93260d2e2b6541ea37d800790057a7883"
    )
    detail = repository.observation(tfc_total.id)
    assert detail is not None
    assert len(detail.revision_history) == 1

    delinquency = repository.observations(
        company_id="pfsi",
        metric_id="delinquency_60_plus_count_rate",
        include_missing=False,
    )
    assert len(delinquency) == 1
    assert delinquency[0].value == "0.0410000000"
    assert (
        repository.observations(
            company_id="pfsi",
            metric_id="delinquency_30_plus_count_rate",
            include_missing=False,
        )
        == []
    )
    freshness = repository.freshness()
    assert freshness["source_assessment_count"] == 256
    assert freshness["source_not_checked_count"] == 220
    assert freshness["terminal_outcomes"] == {
        "PUBLISHED": 36,
        "NOT_DISCLOSED": 0,
        "SOURCE_NOT_CHECKED": 220,
        "QUARANTINED": 1,
        "FAILED": 0,
    }
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count(MetricObservation.id)).where(
                    MetricObservation.observation_state == "NOT_DISCLOSED"
                )
            )
            == 0
        )
        assessment = session.get(
            EligibleSourceAssessment,
            "assessment:tfc:2025-09-30:msr_fair_value:1.0.0",
        )
        assert assessment is not None
        assert assessment.assessment_status == "SOURCE_NOT_CHECKED"
        assert assessment.checked_locators
        assert any(
            item["review_status"] == "NOT_RETAINED_NOT_CHECKED"
            for item in assessment.eligible_source_inventory
        )
        disclosed_assessment = session.get(
            EligibleSourceAssessment,
            "assessment:tfc:2025-09-30:total_servicing_upb:1.0.0",
        )
        assert disclosed_assessment is not None
        assert disclosed_assessment.assessment_status == "DISCLOSURE_FOUND"
    engine.dispose()


def test_float_values_are_rejected_before_orm_persistence() -> None:
    with pytest.raises(TypeError, match="binary floats"):
        MetricObservation(value=1.25)
    with pytest.raises(TypeError, match="binary floats"):
        QuarantineCandidate(proposed_normalized_value=1.25)


class _TransientTwiceAcquirer:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = RecordedEvidenceAcquirer()

    def acquire(self, source: RecordedSourceDefinition) -> AcquiredDocument:
        self.calls += 1
        if self.calls <= 2:
            message = "temporary source failure"
            raise TransientPublicSourceError(message)
        return self.delegate.acquire(source)


def test_acquisition_retry_is_bounded_and_integrity_errors_do_not_retry(tmp_path: Path) -> None:
    transient = _TransientTwiceAcquirer()
    services = StageAIngestionServices(
        engine=create_database_engine("sqlite:///:memory:"),
        retention_root=tmp_path / "retry",
        acquirer=transient,
    )
    state: dict[str, Any] = {"thread_id": "retry-thread", "source_keys": []}
    state.update(services.execute("discover_sources", cast("Any", state)))
    state.update(services.execute("acquire_source", cast("Any", state)))
    assert state["retry_counts"] == {"acquire_source": 2}

    class BrokenAcquirer:
        calls = 0

        def acquire(self, source: RecordedSourceDefinition) -> AcquiredDocument:
            del source
            self.calls += 1
            message = "hash mismatch"
            raise PublicSourceError(message)

    broken = BrokenAcquirer()
    failing = StageAIngestionServices(
        engine=create_database_engine("sqlite:///:memory:"),
        retention_root=tmp_path / "fail",
        acquirer=broken,
    )
    failed_state: dict[str, Any] = {"thread_id": "fail-thread", "source_keys": []}
    failed_state.update(failing.execute("discover_sources", cast("Any", failed_state)))
    with pytest.raises(IngestionServiceError) as error:
        failing.execute("acquire_source", cast("Any", failed_state))
    assert error.value.code == "EVIDENCE_INTEGRITY_FAILED"
    assert broken.calls == 1
