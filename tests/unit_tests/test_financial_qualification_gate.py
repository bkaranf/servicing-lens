from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mortgage_servicing_dashboard.financial_qualification as qualification
from mortgage_servicing_dashboard.api import create_app
from mortgage_servicing_dashboard.database import create_database_engine
from mortgage_servicing_dashboard.financial_qualification import (
    FinancialQualificationInputs,
    QualificationStatus,
    run_financial_qualification_gate,
    write_financial_qualification_reports,
)
from mortgage_servicing_dashboard.repository import IntelligenceRepository

_ROOT = Path(__file__).parents[2]
_INPUTS = FinancialQualificationInputs.repository_defaults(_ROOT)
_HAS_QUALIFICATION_DATABASE = _INPUTS.database_path.is_file()


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_offline_gate_requires_exact_four_case_agreement_and_lineage() -> None:
    report = run_financial_qualification_gate(_INPUTS)

    assert report.status is QualificationStatus.PASS
    assert report.stop_condition == "DESTRUCTIVE_APPROVAL_REQUIRED"
    assert len(report.golden_cases) == 4
    assert all(case.status is QualificationStatus.PASS for case in report.golden_cases)
    assert all(case.compared_fields >= 98 for case in report.golden_cases)
    assert {case.fiscal_period for case in report.golden_cases} == {"FY 2025", "Q2 2026"}
    assert report.database_sha256 == hashlib.sha256(_INPUTS.database_path.read_bytes()).hexdigest()
    assert {check.check_id for check in report.checks} >= {
        "acquisition_lineage_columns",
        "retrieval_and_acquisition_run_lineage",
        "dry_run_publishes_nothing",
        "idempotent_repeated_sync",
        "exact_four_case_calculation_validation",
        "no_silent_fallback_or_substitution",
        "legacy_diagnostic_bounded",
    }
    assert report.as_payload()["company_facts_validation"] == {
        "disposition": "NOT_RETAINED_NOT_REPERFORMABLE_OFFLINE",
        "gate_effect": "NON_BLOCKING",
        "publication_authority": False,
    }


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_gate_rejects_unbound_rerun_and_inexact_calculation(
    tmp_path: Path,
) -> None:
    rerun_payload = json.loads(_INPUTS.idempotent_rerun_report_path.read_text(encoding="utf-8"))
    rerun_payload["database_sha256"] = "0" * 64
    rerun_path = tmp_path / "rerun.json"
    rerun_path.write_text(json.dumps(rerun_payload), encoding="utf-8")

    calculation_payload = json.loads(
        _INPUTS.calculation_validation_path.read_text(encoding="utf-8")
    )
    calculation_payload["cases"][0]["child_count"] = 12
    calculation_path = tmp_path / "calculation.json"
    calculation_path.write_text(json.dumps(calculation_payload), encoding="utf-8")

    report = run_financial_qualification_gate(
        replace(
            _INPUTS,
            idempotent_rerun_report_path=rerun_path,
            calculation_validation_path=calculation_path,
        )
    )
    checks = {check.check_id: check.status for check in report.checks}

    assert report.status is QualificationStatus.FAIL
    assert report.stop_condition == "FINANCIAL_QUALIFICATION_FAILED"
    assert checks["idempotent_repeated_sync"] is QualificationStatus.FAIL
    assert checks["exact_four_case_calculation_validation"] is QualificationStatus.FAIL


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_gate_rejects_missing_empty_and_unretained_inputs(tmp_path: Path) -> None:
    with pytest.raises(qualification.FinancialQualificationError, match="inputs are missing"):
        run_financial_qualification_gate(
            replace(_INPUTS, calculation_validation_path=tmp_path / "missing.json")
        )

    empty_database = tmp_path / "empty.db"
    empty_database.touch()
    with pytest.raises(qualification.FinancialQualificationError, match="database is empty"):
        run_financial_qualification_gate(replace(_INPUTS, database_path=empty_database))

    with pytest.raises(qualification.FinancialQualificationError, match="evidence root"):
        run_financial_qualification_gate(
            replace(_INPUTS, runtime_evidence_root=tmp_path / "missing-evidence")
        )


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_gate_rejects_mapping_version_mismatch(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        _INPUTS.golden_manifest_path.read_text(encoding="utf-8").replace(
            "mapping_version: financial-fields-v1",
            "mapping_version: unrelated-fields-v9",
        ),
        encoding="utf-8",
    )

    with pytest.raises(qualification.FinancialQualificationError, match="versions differ"):
        run_financial_qualification_gate(replace(_INPUTS, golden_manifest_path=manifest_path))


def _database_copy(tmp_path: Path) -> tuple[Path, FinancialQualificationInputs]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "qualification.db"
    shutil.copy2(_INPUTS.database_path, database_path)
    return database_path, replace(_INPUTS, database_path=database_path)


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_gate_rejects_duplicate_and_missing_persisted_cases(tmp_path: Path) -> None:
    duplicate_database, duplicate_inputs = _database_copy(tmp_path / "duplicate")
    with sqlite3.connect(duplicate_database) as connection:
        connection.execute(
            """
            INSERT INTO observation_revisions
            SELECT id || ':duplicate', observation_id, prior_observation_id, reason, created_at
            FROM observation_revisions LIMIT 1
            """
        )
    with pytest.raises(qualification.FinancialQualificationError, match="repeats golden case"):
        run_financial_qualification_gate(duplicate_inputs)

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    missing_database, missing_inputs = _database_copy(missing_root)
    with sqlite3.connect(missing_database) as connection:
        connection.execute(
            "DELETE FROM observation_revisions WHERE id = "
            "(SELECT id FROM observation_revisions ORDER BY id LIMIT 1)"
        )
    report = run_financial_qualification_gate(missing_inputs)
    assert report.status is QualificationStatus.FAIL
    assert any(case.mismatches == ("persisted_case_missing",) for case in report.golden_cases)


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_gate_rejects_missing_or_malformed_pipeline_run(tmp_path: Path) -> None:
    absent_root = tmp_path / "absent"
    absent_root.mkdir()
    absent_database, absent_inputs = _database_copy(absent_root)
    with sqlite3.connect(absent_database) as connection:
        connection.execute("DELETE FROM pipeline_runs")
    absent_report = run_financial_qualification_gate(absent_inputs)
    assert absent_report.status is QualificationStatus.FAIL

    malformed_root = tmp_path / "malformed"
    malformed_root.mkdir()
    malformed_database, malformed_inputs = _database_copy(malformed_root)
    with sqlite3.connect(malformed_database) as connection:
        connection.execute(
            "UPDATE pipeline_runs SET terminal_outcomes = ?",
            ('{"PUBLISHED": "four"}',),
        )
    with pytest.raises(
        qualification.FinancialQualificationError,
        match="terminal outcomes",
    ):
        run_financial_qualification_gate(malformed_inputs)


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
@pytest.mark.parametrize("mutation", ["method", "discrepancy", "duplicate", "incomplete"])
def test_gate_rejects_malformed_calculation_case_sets(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = json.loads(_INPUTS.calculation_validation_path.read_text(encoding="utf-8"))
    if mutation == "method":
        payload["validation_method"] = "UNAPPROVED_METHOD"
    elif mutation == "discrepancy":
        payload["structural_representation_discrepancies"][0]["selected_assets_impact"] = "UNKNOWN"
    elif mutation == "duplicate":
        payload["cases"].append(dict(payload["cases"][0]))
    else:
        payload["cases"].pop()
    path = tmp_path / f"calculation-{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = run_financial_qualification_gate(replace(_INPUTS, calculation_validation_path=path))
    check = next(
        item for item in report.checks if item.check_id == "exact_four_case_calculation_validation"
    )
    assert check.status is QualificationStatus.FAIL


def test_fail_closed_parsers_reject_malformed_evidence(tmp_path: Path) -> None:
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("mapping: [", encoding="utf-8")
    with pytest.raises(qualification.FinancialQualificationError, match="YAML"):
        qualification._load_yaml_mapping(invalid_yaml)

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(qualification.FinancialQualificationError, match="JSON"):
        qualification._load_json_mapping(invalid_json)

    missing_metric = tmp_path / "missing-metric.csv"
    missing_metric.write_text("other\nvalue\n", encoding="utf-8")
    with pytest.raises(qualification.FinancialQualificationError, match="missing metric_id"):
        qualification._legacy_diagnostic(missing_metric, selected_field_ids=("total_assets",))

    invalid_encoding = tmp_path / "invalid-encoding.csv"
    invalid_encoding.write_bytes(b"metric_id\n\xff")
    with pytest.raises(qualification.FinancialQualificationError, match="cannot be read"):
        qualification._legacy_diagnostic(invalid_encoding, selected_field_ids=("total_assets",))


def test_fail_closed_scalar_validators_reject_ambiguous_values() -> None:
    with pytest.raises(qualification.FinancialQualificationError, match="string-keyed"):
        qualification._string_object_mapping({1: "value"}, location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="string values"):
        qualification._string_string_mapping({"key": 1}, location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="must be a sequence"):
        qualification._sequence_value("not-a-list", location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="nonblank strings"):
        qualification._string_sequence([""], location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="nonblank string"):
        qualification._mapping_string({}, "missing", location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="positive integer"):
        qualification._positive_int_value(0, location="test")
    assert qualification._optional_int(None) is None
    with pytest.raises(qualification.FinancialQualificationError, match="integer or absent"):
        qualification._optional_int("not-an-integer")


def test_fail_closed_decimal_and_timestamp_validation() -> None:
    assert qualification._display_decimal("(1,234)") == Decimal(-1234)
    with pytest.raises(qualification.FinancialQualificationError, match="exact decimal"):
        qualification._display_decimal("not-a-decimal")
    with pytest.raises(qualification.FinancialQualificationError, match="finite"):
        qualification._display_decimal("NaN")
    with pytest.raises(qualification.FinancialQualificationError, match="decimal string"):
        qualification._exact_decimal(None, location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="decimal string"):
        qualification._exact_decimal("not-a-decimal", location="test")
    with pytest.raises(qualification.FinancialQualificationError, match="finite"):
        qualification._exact_decimal("Infinity", location="test")

    aware = datetime(2026, 8, 13, 11, 3, tzinfo=UTC)
    assert qualification._as_utc(None) is None
    assert qualification._as_utc(aware) == aware
    with pytest.raises(qualification.FinancialQualificationError, match="timestamp is invalid"):
        qualification._utc_timestamp_matches(aware, "not-a-timestamp")


def test_manifest_case_lookup_is_exact() -> None:
    with pytest.raises(qualification.FinancialQualificationError, match="one exact case"):
        qualification._manifest_case({"cases": []}, "missing-case")


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
def test_report_writes_are_deterministic(tmp_path: Path) -> None:
    report = run_financial_qualification_gate(_INPUTS)
    json_path = tmp_path / "qualification.json"
    markdown_path = tmp_path / "qualification.md"

    write_financial_qualification_reports(
        report,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    first_json = json_path.read_bytes()
    first_markdown = markdown_path.read_bytes()
    write_financial_qualification_reports(
        report,
        json_path=json_path,
        markdown_path=markdown_path,
    )

    assert json_path.read_bytes() == first_json
    assert markdown_path.read_bytes() == first_markdown
    assert json.loads(first_json)["status"] == "PASS"


@pytest.mark.skipif(
    not _HAS_QUALIFICATION_DATABASE,
    reason="ignored offline Phase 4 qualification database is unavailable",
)
@pytest.mark.enable_socket
def test_dashboard_testclient_renders_annual_and_quarterly_periods() -> None:
    engine = create_database_engine(f"sqlite:///{_INPUTS.database_path.resolve().as_posix()}")
    repository = IntelligenceRepository(engine)
    try:
        with TestClient(create_app(repository=repository)) as client:
            response = client.get("/", params={"metric_id": "total_assets"})
        assert response.status_code == 200
        assert "FY 2025" in response.text
        assert "Q2 2026" in response.text
        assert "Q0 2025" not in response.text
    finally:
        engine.dispose()
