"""Deterministic report contract for the offline financial qualification gate.

The gate is intentionally read-only: it evaluates the frozen selected-field
mapping, independently approved golden manifest, isolated qualification database,
prior dry-run evidence, and hash-bound Phase 4 run evidence. It never acquires SEC
data, changes publication state, or treats the legacy 439-row audit export as a
target dataset.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Self, cast

import yaml
from sqlalchemy import Engine, Select, create_engine, func, inspect, select
from sqlalchemy.orm import Session, aliased

from mortgage_servicing_dashboard.database import (
    Company,
    DerivedObservationInput,
    Filing,
    FilingDocument,
    MetricDefinition,
    MetricDefinitionVersion,
    MetricObservation,
    ObservationEvidence,
    ObservationRevision,
    PipelineRun,
    QuarantineCandidate,
    RawXbrlFact,
    ReportingEntity,
    ReportingScope,
    SourceEvidence,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import GoldenManifest
from mortgage_servicing_dashboard.financial_discovery import FinancialFieldRegistry
from mortgage_servicing_dashboard.presentation import fiscal_period_label

_EXPECTED_CASE_COUNT = 4
_EXPECTED_ISSUER_COUNT = 2
_EXPECTED_EDGARTOOLS_VERSION = "5.48.0"
_EXPECTED_SOURCE_CLASS = "SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS"
_EXPECTED_METHOD = "SEC_FILING_XBRL_VIA_EDGARTOOLS"
_EXPECTED_CAPTURE_METHOD = "edgartools_attachment_text_utf8"
_EXPECTED_REPRESENTATION = "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
_EXPECTED_BASELINE_SHA256 = "112661f7d3414793f747c6cdd9a890f480a2f98768bb8268cae9ad70c2e3f0b2"
_EXPECTED_BASELINE_ROWS = 439
_EXPECTED_DATABASE_NAME = "phase4-gate-qualification.db"
_EXPECTED_ACQUISITION_RUN_ID = (
    "pipeline:edgartools:d7d5b9d3b681a03f727472a8a528f052b967f57b6d545285"
)
_EXPECTED_ACQUISITION_RUN_KEY = (
    "edgartools-financial-sync:d7d5b9d3b681a03f727472a8a528f052b967f57b6d5452855d87c6e9d940073e"
)
_EXPECTED_ACCEPTANCE_TIMESTAMPS = {
    "tfc-2025-annual-total-assets": "2026-02-24T21:42:25Z",
    "tfc-2026q2-quarterly-total-assets": "2026-07-31T20:44:07Z",
    "pfsi-2025-annual-total-assets": "2026-02-20T21:08:00Z",
    "pfsi-2026q2-quarterly-total-assets": "2026-08-04T21:12:17Z",
}
_EXPECTED_ISSUER_SEMANTICS = {
    "tfc": ("Truist Financial Corporation", "bank"),
    "pfsi": ("PennyMac Financial Services, Inc.", "nonbank"),
}
_EXPECTED_CALCULATION_CASES = {
    "tfc-2025-annual-total-assets": (
        13,
        349,
        78,
        "tfc-20251231_cal.xml",
        "ConsolidatedBalanceSheets",
    ),
    "tfc-2026q2-quarterly-total-assets": (
        13,
        204,
        52,
        "tfc-20260630_cal.xml",
        "ConsolidatedBalanceSheets",
    ),
    "pfsi-2025-annual-total-assets": (
        12,
        323,
        59,
        "pfsi-20251231_cal.xml",
        "StatementConsolidatedBalanceSheets",
    ),
    "pfsi-2026q2-quarterly-total-assets": (
        12,
        263,
        52,
        "pfsi-20260630_cal.xml",
        "StatementConsolidatedBalanceSheetsUnaudited",
    ),
}


class FinancialQualificationError(ValueError):
    """An offline qualification input is incomplete, unsafe, or malformed."""


class QualificationStatus(StrEnum):
    """Terminal status for the financial qualification gate and its checks."""

    PASS = "PASS"  # noqa: S105 - controlled gate state, not a credential.
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class FinancialQualificationInputs:
    """Exact offline inputs consumed by the qualification gate."""

    database_path: Path
    golden_manifest_path: Path
    selected_fields_path: Path
    legacy_baseline_path: Path
    dry_run_report_paths: tuple[Path, ...]
    isolated_sync_report_path: Path
    idempotent_rerun_report_path: Path
    independent_validation_path: Path
    calculation_validation_path: Path
    validation_classification_path: Path
    runtime_evidence_root: Path
    cross_check_evidence_root: Path

    @classmethod
    def repository_defaults(cls, repository_root: Path) -> Self:
        """Resolve the governed Phase 4 inputs below one repository root."""
        root = repository_root.resolve()
        artifacts = root / "artifacts" / "edgar-tools-migration"
        return cls(
            database_path=artifacts / _EXPECTED_DATABASE_NAME,
            golden_manifest_path=root
            / "tests"
            / "fixtures"
            / "edgartools"
            / "golden-sources.v1.yaml",
            selected_fields_path=root / "config" / "financial_fields.v1.yaml",
            legacy_baseline_path=root / "config" / "audit" / "legacy-439-baseline.csv",
            dry_run_report_paths=(
                artifacts / "phase3-dry-run-tfc.json",
                artifacts / "phase3-dry-run-pfsi.json",
            ),
            isolated_sync_report_path=artifacts / "phase4-isolated-live-sync.json",
            idempotent_rerun_report_path=(artifacts / "phase4-isolated-idempotent-rerun.json"),
            independent_validation_path=artifacts / "phase0-live-proof.json",
            calculation_validation_path=artifacts / "phase4-calculation-validation.json",
            validation_classification_path=artifacts / "source-route-report.md",
            runtime_evidence_root=artifacts / "phase3-runtime" / "evidence" / "edgartools",
            cross_check_evidence_root=root / "config" / "recorded_evidence" / "phase3",
        )


@dataclass(frozen=True, slots=True)
class QualificationCheck:
    """One deterministic gate assertion with a safe bounded detail."""

    check_id: str
    status: QualificationStatus
    detail: str
    case_id: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        """Return a deterministic JSON-compatible representation."""
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "detail": self.detail,
            "case_id": self.case_id,
        }


@dataclass(frozen=True, slots=True)
class GoldenCaseQualification:
    """Exact agreement and source-lineage disposition for one golden case."""

    case_id: str
    company_id: str
    field_id: str
    classification: str
    fiscal_period: str
    dashboard_value: str
    compared_fields: int
    mismatches: tuple[str, ...]
    source_qualified: bool
    lineage_complete: bool
    independent_cross_check: bool

    @property
    def status(self) -> QualificationStatus:
        """Pass only when every required case condition is exact."""
        passed = (
            not self.mismatches
            and self.source_qualified
            and self.lineage_complete
            and self.independent_cross_check
        )
        return QualificationStatus.PASS if passed else QualificationStatus.FAIL

    def as_payload(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""
        return {
            "case_id": self.case_id,
            "company_id": self.company_id,
            "field_id": self.field_id,
            "classification": self.classification,
            "fiscal_period": self.fiscal_period,
            "dashboard_value": self.dashboard_value,
            "compared_fields": self.compared_fields,
            "mismatches": list(self.mismatches),
            "source_qualified": self.source_qualified,
            "lineage_complete": self.lineage_complete,
            "independent_cross_check": self.independent_cross_check,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class LegacyDiagnostic:
    """Bounded diagnostic result against the immutable legacy audit baseline."""

    baseline_sha256: str
    baseline_rows: int
    selected_field_ids: tuple[str, ...]
    directly_overlapping_field_ids: tuple[str, ...]
    compared_rows: int
    classified_differences: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        """Return diagnostic facts without implying a parity requirement."""
        return {
            "baseline_sha256": self.baseline_sha256,
            "baseline_rows": self.baseline_rows,
            "selected_field_ids": list(self.selected_field_ids),
            "directly_overlapping_field_ids": list(self.directly_overlapping_field_ids),
            "compared_rows": self.compared_rows,
            "classified_differences": list(self.classified_differences),
            "parity_required": False,
        }


@dataclass(frozen=True, slots=True)
class _PersistedGoldenCase:
    case_id: str
    company_id: str
    company_legal_name: str
    ticker: str
    company_classification: str
    metric_id: str
    metric_category: str
    metric_version: str
    reporting_entity_id: str
    reporting_entity_legal_name: str
    reporting_entity_type: str
    reporting_scope_id: str
    scope_name: str
    scope_population: str
    scope_methodology: str
    accession: str
    form: str
    filing_date: str
    acceptance_timestamp: datetime | None
    report_period: str
    amendment_of_id: str | None
    document: str
    document_sequence: int
    document_type: str
    document_description: str | None
    source_url: str
    is_primary: bool | None
    evidence_id: str
    source_class: str
    evidence_sha256: str
    evidence_byte_length: int
    evidence_representation: str
    evidence_capture_method: str
    evidence_media_type: str
    evidence_location: str
    source_tool_version: str | None
    retrieved_at: datetime
    acquisition_run_id: str
    taxonomy: str
    concept: str
    entity_identifier: str
    context_ref: str
    raw_fact_value: str
    fact_unit: str | None
    fact_decimals: str | None
    fact_scale: Decimal | None
    fact_source_sign: str | None
    fact_source_precision: str | None
    fact_presentation_sign: str | None
    fact_period_type: str
    fact_period_end: str | None
    fact_instant: str | None
    fact_dimensions: dict[str, str]
    fact_methodology: str
    observation_id: str
    normalized_value: Decimal | None
    currency: str | None
    observation_unit: str
    observation_scale: str
    reported_decimals: int | None
    reported_precision: str
    observation_state: str
    publication_state: str
    quality_state: str
    methodology: str
    observation_period_type: str
    observation_period_end: str
    fiscal_year: int
    fiscal_quarter: int
    observation_dimensions: dict[str, str]
    reported_label: str
    reported_value: str
    revision_number: int
    supersedes_observation_id: str | None
    knowledge_to: datetime | None
    parser_metadata: dict[str, object]
    evidence_role: str
    evidence_locator: str
    evidence_raw_label: str
    evidence_raw_value: str
    disclosed_unit: str
    disclosed_scale: str
    evidence_extraction_method: str
    validation_status: str
    revision_id: str
    prior_observation_id: str | None
    revision_reason: str


@dataclass(frozen=True, slots=True)
class _PipelineRunSnapshot:
    run_id: str
    run_key: str
    status: str
    error_count: int
    retry_count: int
    code_version: str
    config_version: str
    parser_version: str
    terminal_outcomes: dict[str, int]


@dataclass(frozen=True, slots=True)
class FinancialQualificationReport:
    """Complete deterministic result of the Financial Qualification Gate."""

    mapping_version: str
    manifest_version: str
    database_sha256: str
    golden_cases: tuple[GoldenCaseQualification, ...]
    checks: tuple[QualificationCheck, ...]
    legacy_diagnostic: LegacyDiagnostic

    @property
    def status(self) -> QualificationStatus:
        """Pass only when every golden case and global check passes."""
        passed = all(case.status is QualificationStatus.PASS for case in self.golden_cases)
        passed = passed and all(check.status is QualificationStatus.PASS for check in self.checks)
        return QualificationStatus.PASS if passed else QualificationStatus.FAIL

    @property
    def stop_condition(self) -> str:
        """Return the required next control boundary for the terminal status."""
        if self.status is QualificationStatus.PASS:
            return "DESTRUCTIVE_APPROVAL_REQUIRED"
        return "FINANCIAL_QUALIFICATION_FAILED"

    def as_payload(self) -> dict[str, object]:
        """Return a stable JSON payload containing no acquisition identity secret."""
        passed_cases = sum(case.status is QualificationStatus.PASS for case in self.golden_cases)
        return {
            "gate": "FINANCIAL_QUALIFICATION_GATE",
            "status": self.status.value,
            "stop_condition": self.stop_condition,
            "mapping_version": self.mapping_version,
            "manifest_version": self.manifest_version,
            "database_sha256": self.database_sha256,
            "company_facts_validation": {
                "disposition": "NOT_RETAINED_NOT_REPERFORMABLE_OFFLINE",
                "gate_effect": "NON_BLOCKING",
                "publication_authority": False,
            },
            "golden_exact_agreement": {
                "passed_cases": passed_cases,
                "total_cases": len(self.golden_cases),
                "percent": (
                    "0"
                    if not self.golden_cases
                    else str(passed_cases * 100 // len(self.golden_cases))
                ),
            },
            "golden_cases": [case.as_payload() for case in self.golden_cases],
            "checks": [check.as_payload() for check in self.checks],
            "legacy_diagnostic": self.legacy_diagnostic.as_payload(),
        }


def run_financial_qualification_gate(
    inputs: FinancialQualificationInputs,
) -> FinancialQualificationReport:
    """Evaluate the bounded Financial Qualification Gate without mutating inputs.

    Args:
        inputs: Exact repository artifacts and isolated database to inspect.

    Returns:
        Deterministic pass/fail report with bounded diagnostics.

    Raises:
        FinancialQualificationError: If an input cannot be safely evaluated.
    """
    _require_files(inputs)
    manifest_payload = _load_yaml_mapping(inputs.golden_manifest_path)
    registry = FinancialFieldRegistry.from_yaml(inputs.selected_fields_path)
    manifest = GoldenManifest.from_mapping(manifest_payload)
    if manifest.mapping_version != registry.version:
        message = "golden manifest and selected-field mapping versions differ"
        raise FinancialQualificationError(message)

    database_sha256 = _sha256(inputs.database_path)
    engine = _read_only_sqlite_engine(inputs.database_path)
    try:
        required_columns = _required_lineage_columns(engine)
        persisted = _load_persisted_cases(engine)
        database_counts = _database_counts(engine)
        pipeline_run = _load_pipeline_run(engine)
    finally:
        engine.dispose()

    golden_cases = tuple(
        _qualify_case(
            case,
            persisted.get(case.case_id),
            registry=registry,
            manifest_payload=manifest_payload,
        )
        for case in manifest.cases
    )
    checks = _global_checks(
        inputs,
        manifest_payload=manifest_payload,
        registry=registry,
        required_columns=required_columns,
        persisted=persisted,
        database_counts=database_counts,
        pipeline_run=pipeline_run,
        database_sha256=database_sha256,
    )
    legacy = _legacy_diagnostic(
        inputs.legacy_baseline_path,
        selected_field_ids=tuple(sorted({mapping.field_id for mapping in registry.mappings})),
    )
    return FinancialQualificationReport(
        mapping_version=registry.version,
        manifest_version=manifest.version,
        database_sha256=database_sha256,
        golden_cases=golden_cases,
        checks=checks,
        legacy_diagnostic=legacy,
    )


def _require_files(inputs: FinancialQualificationInputs) -> None:
    paths = (
        inputs.database_path,
        inputs.golden_manifest_path,
        inputs.selected_fields_path,
        inputs.legacy_baseline_path,
        *inputs.dry_run_report_paths,
        inputs.isolated_sync_report_path,
        inputs.idempotent_rerun_report_path,
        inputs.independent_validation_path,
        inputs.calculation_validation_path,
        inputs.validation_classification_path,
    )
    missing = tuple(path.name for path in paths if not path.is_file())
    if missing:
        message = f"financial qualification inputs are missing: {', '.join(missing)}"
        raise FinancialQualificationError(message)
    if inputs.database_path.stat().st_size <= 0:
        message = "financial qualification database is empty"
        raise FinancialQualificationError(message)
    for root in (inputs.runtime_evidence_root, inputs.cross_check_evidence_root):
        if not root.is_dir():
            message = f"financial qualification evidence root is missing: {root.name}"
            raise FinancialQualificationError(message)


def _read_only_sqlite_engine(path: Path) -> Engine:
    resolved = path.resolve().as_posix()
    return create_engine(f"sqlite+pysqlite:///file:{resolved}?mode=ro&uri=true")


def _required_lineage_columns(engine: Engine) -> frozenset[str]:
    inspector = inspect(engine)
    required = {
        "filings.acceptance_timestamp",
        "source_evidence.source_tool_version",
    }
    available = {f"filings.{column['name']}" for column in inspector.get_columns("filings")} | {
        f"source_evidence.{column['name']}" for column in inspector.get_columns("source_evidence")
    }
    return frozenset(required & available)


def _case_query() -> Select[
    tuple[
        Company,
        ReportingEntity,
        ReportingScope,
        Filing,
        FilingDocument,
        SourceEvidence,
        RawXbrlFact,
        MetricDefinition,
        MetricDefinitionVersion,
        MetricObservation,
        ObservationEvidence,
        ObservationRevision,
    ]
]:
    evidence = aliased(SourceEvidence)
    filing = aliased(Filing)
    document = aliased(FilingDocument)
    fact = aliased(RawXbrlFact)
    observation = aliased(MetricObservation)
    observation_evidence = aliased(ObservationEvidence)
    revision = aliased(ObservationRevision)
    metric_version = aliased(MetricDefinitionVersion)
    metric = aliased(MetricDefinition)
    entity = aliased(ReportingEntity)
    scope = aliased(ReportingScope)
    company = aliased(Company)
    return (
        select(
            company,
            entity,
            scope,
            filing,
            document,
            evidence,
            fact,
            metric,
            metric_version,
            observation,
            observation_evidence,
            revision,
        )
        .join(entity, entity.company_id == company.id)
        .join(scope, scope.reporting_entity_id == entity.id)
        .join(filing, filing.reporting_entity_id == entity.id)
        .join(document, document.filing_id == filing.id)
        .join(evidence, evidence.id == document.source_evidence_id)
        .join(fact, (fact.filing_id == filing.id) & (fact.evidence_id == evidence.id))
        .join(observation, observation.reporting_entity_id == entity.id)
        .join(metric_version, metric_version.id == observation.metric_version_id)
        .join(metric, metric.id == metric_version.metric_id)
        .join(
            observation_evidence,
            (observation_evidence.observation_id == observation.id)
            & (observation_evidence.evidence_id == evidence.id),
        )
        .join(revision, revision.observation_id == observation.id)
        .where(
            observation.reporting_scope_id == scope.id,
            observation.period_end == filing.period_end,
        )
    )


def _load_persisted_cases(engine: Engine) -> dict[str, _PersistedGoldenCase]:
    cases: dict[str, _PersistedGoldenCase] = {}
    with Session(engine) as session:
        rows = session.execute(_case_query()).all()
        for (
            company,
            entity,
            scope,
            filing,
            document,
            evidence,
            fact,
            metric,
            metric_version,
            observation,
            observation_evidence,
            revision,
        ) in rows:
            parser_metadata = dict(
                _string_object_mapping(
                    observation.parser_metadata,
                    location="observation.parser_metadata",
                )
            )
            case_id = _object_string(parser_metadata, "case_id", location="parser metadata")
            if case_id in cases:
                message = f"isolated database repeats golden case: {case_id}"
                raise FinancialQualificationError(message)
            cases[case_id] = _PersistedGoldenCase(
                case_id=case_id,
                company_id=company.id,
                company_legal_name=company.legal_name,
                ticker=company.ticker,
                company_classification=company.classification,
                metric_id=metric.id,
                metric_category=metric.category,
                metric_version=metric_version.semantic_version,
                reporting_entity_id=entity.id,
                reporting_entity_legal_name=entity.legal_name,
                reporting_entity_type=entity.entity_type,
                reporting_scope_id=scope.id,
                scope_name=scope.name,
                scope_population=scope.portfolio_population,
                scope_methodology=scope.methodology,
                accession=filing.accession,
                form=filing.form_type,
                filing_date=filing.filed_at.date().isoformat(),
                acceptance_timestamp=filing.acceptance_timestamp,
                report_period=filing.period_end.isoformat(),
                amendment_of_id=filing.amendment_of_id,
                document=document.filename,
                document_sequence=document.sequence,
                document_type=document.document_type,
                document_description=document.description,
                source_url=document.source_url,
                is_primary=document.is_primary,
                evidence_id=evidence.id,
                source_class=evidence.source_class,
                evidence_sha256=evidence.content_sha256,
                evidence_byte_length=evidence.byte_length,
                evidence_representation=evidence.representation,
                evidence_capture_method=evidence.capture_method,
                evidence_media_type=evidence.media_type,
                evidence_location=evidence.retention_location,
                source_tool_version=evidence.source_tool_version,
                retrieved_at=evidence.retrieved_at,
                acquisition_run_id=evidence.acquisition_run_id,
                taxonomy=fact.taxonomy,
                concept=fact.concept,
                entity_identifier=fact.entity_identifier,
                context_ref=fact.context_ref,
                raw_fact_value=fact.raw_value,
                fact_unit=fact.unit_ref,
                fact_decimals=fact.decimals,
                fact_scale=fact.scale,
                fact_source_sign=fact.source_sign,
                fact_source_precision=fact.source_precision,
                fact_presentation_sign=fact.presentation_sign,
                fact_period_type=fact.period_type,
                fact_period_end=None if fact.period_end is None else fact.period_end.isoformat(),
                fact_instant=None if fact.instant is None else fact.instant.isoformat(),
                fact_dimensions=_string_string_mapping(
                    fact.dimensions,
                    location="raw fact dimensions",
                ),
                fact_methodology=fact.methodology,
                observation_id=observation.id,
                normalized_value=observation.value,
                currency=observation.currency,
                observation_unit=observation.unit,
                observation_scale=observation.scale,
                reported_decimals=observation.reported_decimals,
                reported_precision=observation.reported_precision,
                observation_state=observation.observation_state,
                publication_state=observation.publication_state,
                quality_state=observation.quality_state,
                methodology=observation.methodology,
                observation_period_type=observation.period_type,
                observation_period_end=observation.period_end.isoformat(),
                fiscal_year=observation.fiscal_year,
                fiscal_quarter=observation.fiscal_quarter,
                observation_dimensions=_string_string_mapping(
                    observation.dimensions,
                    location="observation dimensions",
                ),
                reported_label=observation.reported_label,
                reported_value=observation.reported_value,
                revision_number=observation.revision_number,
                supersedes_observation_id=observation.supersedes_observation_id,
                knowledge_to=observation.knowledge_to,
                parser_metadata=parser_metadata,
                evidence_role=observation_evidence.evidence_role,
                evidence_locator=observation_evidence.locator,
                evidence_raw_label=observation_evidence.raw_label,
                evidence_raw_value=observation_evidence.raw_value,
                disclosed_unit=observation_evidence.disclosed_unit,
                disclosed_scale=observation_evidence.disclosed_scale,
                evidence_extraction_method=observation_evidence.extraction_method,
                validation_status=observation_evidence.validation_status,
                revision_id=revision.id,
                prior_observation_id=revision.prior_observation_id,
                revision_reason=revision.reason,
            )
    return cases


def _database_counts(engine: Engine) -> dict[str, int]:
    models = {
        "companies": Company,
        "filings": Filing,
        "filing_documents": FilingDocument,
        "source_evidence": SourceEvidence,
        "raw_xbrl_facts": RawXbrlFact,
        "metric_observations": MetricObservation,
        "observation_evidence": ObservationEvidence,
        "observation_revisions": ObservationRevision,
        "derived_observation_inputs": DerivedObservationInput,
        "pipeline_runs": PipelineRun,
        "quarantine_candidates": QuarantineCandidate,
    }
    with Session(engine) as session:
        return {
            name: session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in models.items()
        }


def _load_pipeline_run(engine: Engine) -> _PipelineRunSnapshot | None:
    with Session(engine) as session:
        runs = session.scalars(select(PipelineRun)).all()
    if len(runs) != 1:
        return None
    run = runs[0]
    outcomes = _string_object_mapping(run.terminal_outcomes, location="pipeline outcomes")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) for value in outcomes.values()
    ):
        message = "pipeline terminal outcomes must contain integer counts"
        raise FinancialQualificationError(message)
    return _PipelineRunSnapshot(
        run_id=run.id,
        run_key=run.run_key,
        status=run.status,
        error_count=run.error_count,
        retry_count=run.retry_count,
        code_version=run.code_version,
        config_version=run.config_version,
        parser_version=run.parser_version,
        terminal_outcomes={key: cast("int", value) for key, value in outcomes.items()},
    )


def _qualify_case(
    case: Any,
    persisted: _PersistedGoldenCase | None,
    *,
    registry: FinancialFieldRegistry,
    manifest_payload: Mapping[str, object],
) -> GoldenCaseQualification:
    case_id = case.case_id
    company_id = case.issuer_id
    field_id = case.field_id
    classification = case.classification.value
    fiscal_year = case.fiscal_year
    fiscal_quarter = case.fiscal_quarter
    expected_quarter = 0 if fiscal_quarter == "FY" else int(fiscal_quarter[1:])
    fiscal_period = fiscal_period_label(
        fiscal_year=fiscal_year,
        fiscal_quarter=expected_quarter,
    )
    raw_display = case.raw_display_string
    if persisted is None:
        return GoldenCaseQualification(
            case_id=case_id,
            company_id=company_id,
            field_id=field_id,
            classification=classification,
            fiscal_period=fiscal_period,
            dashboard_value=raw_display,
            compared_fields=0,
            mismatches=("persisted_case_missing",),
            source_qualified=False,
            lineage_complete=False,
            independent_cross_check=False,
        )

    mapping = next(
        (
            item
            for item in registry.mappings
            if item.issuer_id == company_id and item.field_id == field_id
        ),
        None,
    )
    case_payload = _manifest_case(manifest_payload, case_id)
    source = _mapping_value(case_payload, "edgartools_source", location=case_id)
    cross_check = _mapping_value(case_payload, "cross_check_evidence", location=case_id)
    fact_dimensions = dict(case.dimensions)
    source_ids = case.source_element_ids
    metadata_source_ids = tuple(
        sorted(
            _string_sequence(
                persisted.parser_metadata.get("source_element_ids"),
                location=f"{case_id}.source_element_ids",
            )
        )
    )
    source_locators = tuple(
        _string_sequence(
            persisted.parser_metadata.get("source_locators"),
            location=f"{case_id}.source_locators",
        )
    )
    amendment = case.amendment
    revision_accession = case.revision_of_accession
    expected_concept = case.qualified_concept
    expected_taxonomy, _, expected_local_concept = expected_concept.partition(":")
    expected_scale = case.source_scale
    expected_decimals = case.decimals
    expected_value = case.normalized_value
    expected_period = case.report_period.isoformat()
    expected_filing_date = case.filing_date.isoformat()
    expected_sha256 = case.evidence_sha256
    expected_length = case.evidence_byte_length
    expected_representation = case.evidence_representation
    expected_capture = case.evidence_capture_method
    expected_capture = expected_capture or _EXPECTED_CAPTURE_METHOD
    expected_entity = "" if mapping is None else mapping.xbrl.reporting_entity_id
    expected_scope = "" if mapping is None else mapping.xbrl.reporting_scope_id
    expected_source_count = case.source_object_count
    expected_legal_name, expected_company_class = _EXPECTED_ISSUER_SEMANTICS[company_id]
    expected_scope_name = "" if mapping is None else mapping.reporting_scope_name
    expected_scope_perimeter = "" if mapping is None else mapping.portfolio_population
    expected_scope_methodology = "" if mapping is None else mapping.scope_methodology
    expected_acceptance = _EXPECTED_ACCEPTANCE_TIMESTAMPS[case_id]
    expected_checks: tuple[tuple[bool, str], ...] = (
        (persisted.case_id == case_id, "case_id"),
        (persisted.company_id == company_id, "company"),
        (persisted.company_legal_name == expected_legal_name, "company_legal_name"),
        (persisted.ticker == case.ticker, "ticker"),
        (
            persisted.company_classification == expected_company_class,
            "company_classification",
        ),
        (persisted.metric_id == field_id, "field_id"),
        (persisted.metric_category == classification, "classification"),
        (
            persisted.metric_version == ("" if mapping is None else mapping.xbrl.metric_version),
            "metric_version",
        ),
        (persisted.reporting_entity_id == expected_entity, "reporting_entity"),
        (
            persisted.reporting_entity_legal_name == expected_legal_name,
            "reporting_entity_legal_name",
        ),
        (persisted.reporting_entity_type == "SEC_REGISTRANT", "reporting_entity_type"),
        (persisted.reporting_scope_id == expected_scope, "reporting_scope"),
        (persisted.scope_name == expected_scope_name, "scope_name"),
        (persisted.scope_population == expected_scope_perimeter, "scope_perimeter"),
        (persisted.scope_methodology == expected_scope_methodology, "scope_methodology"),
        (persisted.accession == case.accession, "accession"),
        (persisted.form == case.form, "form"),
        (persisted.filing_date == expected_filing_date, "filing_date"),
        (
            _utc_timestamp_matches(persisted.acceptance_timestamp, expected_acceptance),
            "acceptance_timestamp",
        ),
        (persisted.report_period == expected_period, "filing_report_period"),
        ((persisted.amendment_of_id is not None) is amendment, "amendment_behavior"),
        (revision_accession is None or persisted.amendment_of_id is not None, "revision_accession"),
        (persisted.document == case.primary_document, "document"),
        (persisted.document_sequence == int(case.primary_sequence), "document_sequence"),
        (persisted.document_type == case.primary_document_type, "document_type"),
        (persisted.document_description == case.primary_description, "document_description"),
        (persisted.source_url == case.source_url, "source_url"),
        (persisted.is_primary is True, "primary_document"),
        (persisted.source_class == _EXPECTED_SOURCE_CLASS, "source_class"),
        (persisted.evidence_sha256 == expected_sha256, "sha256"),
        (persisted.evidence_byte_length == expected_length, "byte_length"),
        (persisted.evidence_representation == expected_representation, "representation"),
        (persisted.evidence_capture_method == expected_capture, "capture_method"),
        (persisted.evidence_media_type == "text/html; charset=utf-8", "media_type"),
        (
            persisted.evidence_location == f"content-sha256://{expected_sha256}",
            "retention_location",
        ),
        (persisted.source_tool_version == _EXPECTED_EDGARTOOLS_VERSION, "edgartools_version"),
        (_valid_utc_timestamp(persisted.retrieved_at), "retrieval_timestamp"),
        (
            persisted.acquisition_run_id == _EXPECTED_ACQUISITION_RUN_ID,
            "acquisition_run_id",
        ),
        (persisted.taxonomy == expected_taxonomy, "taxonomy"),
        (persisted.concept == expected_local_concept, "concept"),
        (persisted.entity_identifier == case.cik, "entity_identifier"),
        (persisted.context_ref == case.context_ref, "context"),
        (persisted.raw_fact_value == raw_display, "raw_value"),
        (persisted.fact_unit == case.unit, "fact_unit"),
        (persisted.fact_decimals == expected_decimals, "fact_decimals"),
        (persisted.fact_scale == expected_scale, "fact_scale"),
        (persisted.fact_source_sign == case.source_sign, "fact_source_sign"),
        (persisted.fact_source_precision == case.source_precision, "fact_source_precision"),
        (
            persisted.fact_presentation_sign == case.presentation_sign,
            "fact_presentation_sign",
        ),
        (persisted.fact_period_type == "instant", "fact_period_type"),
        (persisted.fact_period_end == expected_period, "fact_period_end"),
        (persisted.fact_instant == expected_period, "fact_instant"),
        (persisted.fact_dimensions == fact_dimensions, "fact_dimensions"),
        (persisted.fact_methodology == _EXPECTED_METHOD, "fact_methodology"),
        (persisted.normalized_value == expected_value, "normalized_decimal"),
        (persisted.currency == "USD", "currency"),
        (persisted.observation_unit == case.unit, "observation_unit"),
        (persisted.observation_scale == "1", "normalized_scale"),
        (persisted.reported_decimals == _optional_int(expected_decimals), "reported_decimals"),
        (persisted.reported_precision == "ABSENT_IN_SOURCE", "reported_precision"),
        (persisted.observation_state == "REPORTED_ACTUAL", "observation_state"),
        (persisted.publication_state == "PUBLISHED", "publication_state"),
        (persisted.quality_state == "VALIDATED", "quality_state"),
        (persisted.methodology == _EXPECTED_METHOD, "methodology"),
        (persisted.observation_period_type == "instant", "observation_period_type"),
        (persisted.observation_period_end == expected_period, "observation_period_end"),
        (persisted.fiscal_year == fiscal_year, "fiscal_year"),
        (persisted.fiscal_quarter == expected_quarter, "fiscal_quarter"),
        (persisted.observation_dimensions == fact_dimensions, "observation_dimensions"),
        (persisted.reported_label == case.original_label, "raw_label"),
        (persisted.reported_value == raw_display, "reported_value"),
        (
            _display_decimal(raw_display) * expected_scale == expected_value,
            "direct_decimal_conversion",
        ),
        (persisted.revision_number == 1, "revision_number"),
        (persisted.supersedes_observation_id is None, "supersession"),
        (persisted.knowledge_to is None, "active_knowledge_interval"),
        (persisted.parser_metadata.get("mapping_version") == registry.version, "mapping_version"),
        (
            persisted.parser_metadata.get("classification") == classification,
            "parser_classification",
        ),
        (persisted.parser_metadata.get("qualified_concept") == expected_concept, "parser_concept"),
        (persisted.parser_metadata.get("context_ref") == persisted.context_ref, "parser_context"),
        (persisted.parser_metadata.get("source_scale") == str(expected_scale), "parser_scale"),
        (metadata_source_ids == tuple(sorted(source_ids)), "source_element_ids"),
        (
            persisted.parser_metadata.get("source_object_count") == expected_source_count,
            "source_object_count",
        ),
        (len(source_locators) == expected_source_count, "source_locator_count"),
        (persisted.evidence_locator in source_locators, "primary_source_locator"),
        (persisted.evidence_role == "primary", "evidence_role"),
        (persisted.evidence_raw_label == persisted.reported_label, "evidence_raw_label"),
        (persisted.evidence_raw_value == raw_display, "evidence_raw_value"),
        (persisted.disclosed_unit == case.unit, "disclosed_unit"),
        (persisted.disclosed_scale == str(expected_scale), "disclosed_scale"),
        (persisted.evidence_extraction_method == "deterministic_inline_xbrl", "extraction_method"),
        (persisted.validation_status == "VALIDATED", "validation_status"),
        (persisted.prior_observation_id is None, "revision_prior"),
        (
            persisted.revision_reason == "initial edgartools financial publication",
            "revision_reason",
        ),
        (
            _mapping_string(source, "sha256", location=case_id) == expected_sha256,
            "manifest_source_hash",
        ),
        (
            _mapping_string(cross_check, "exact_match", location=case_id) == "true",
            "independent_exact_match",
        ),
        (mapping is not None, "selected_mapping"),
        (
            mapping is not None
            and mapping.sign_convention == "INLINE_XBRL_SIGN_ATTRIBUTE_THEN_EXACT_SCALE",
            "sign_convention",
        ),
        (
            mapping is not None
            and mapping.raw_string_to_decimal
            == "INLINE_XBRL_DISPLAY_STRING_TRANSFORM_AND_INTEGER_SCALE_TO_DECIMAL",
            "raw_string_to_decimal_rule",
        ),
    )
    mismatches = tuple(name for matches, name in expected_checks if not matches)
    source_qualified = not any(
        name
        in {
            "source_class",
            "taxonomy",
            "concept",
            "entity_identifier",
            "context",
            "fact_unit",
            "fact_decimals",
            "fact_scale",
            "fact_period_type",
            "fact_period_end",
            "fact_dimensions",
            "normalized_decimal",
            "direct_decimal_conversion",
            "sign_convention",
            "raw_string_to_decimal_rule",
            "mapping_version",
            "reporting_entity",
            "reporting_scope",
            "scope_perimeter",
            "scope_methodology",
            "amendment_behavior",
        }
        for name in mismatches
    )
    lineage_complete = not any(
        name
        in {
            "acceptance_timestamp",
            "edgartools_version",
            "retrieval_timestamp",
            "acquisition_run_id",
            "accession",
            "document",
            "source_url",
            "sha256",
            "byte_length",
            "representation",
            "capture_method",
            "retention_location",
            "source_element_ids",
            "source_locator_count",
            "primary_source_locator",
            "revision_reason",
        }
        for name in mismatches
    )
    independent_cross_check = (
        _mapping_string(cross_check, "exact_match", location=case_id) == "true"
        and _mapping_string(cross_check, "source_role", location=case_id) == "VALIDATION_ONLY"
        and _mapping_string(cross_check, "attribution", location=case_id)
        == "PHASE2_INDEPENDENT_GOLDEN_REVIEW_REPRODUCED_DETERMINISTICALLY"
    )
    return GoldenCaseQualification(
        case_id=case_id,
        company_id=company_id,
        field_id=field_id,
        classification=classification,
        fiscal_period=fiscal_period,
        dashboard_value=persisted.reported_value,
        compared_fields=len(expected_checks),
        mismatches=tuple(dict.fromkeys(mismatches)),
        source_qualified=source_qualified,
        lineage_complete=lineage_complete,
        independent_cross_check=independent_cross_check,
    )


def _global_checks(  # noqa: PLR0913 - each source is a distinct gate input.
    inputs: FinancialQualificationInputs,
    *,
    manifest_payload: Mapping[str, object],
    registry: FinancialFieldRegistry,
    required_columns: frozenset[str],
    persisted: Mapping[str, _PersistedGoldenCase],
    database_counts: Mapping[str, int],
    pipeline_run: _PipelineRunSnapshot | None,
    database_sha256: str,
) -> tuple[QualificationCheck, ...]:
    dry_runs = tuple(_load_json_mapping(path) for path in inputs.dry_run_report_paths)
    isolated = _load_json_mapping(inputs.isolated_sync_report_path)
    rerun = _load_json_mapping(inputs.idempotent_rerun_report_path)
    validation = _load_json_mapping(inputs.independent_validation_path)
    calculation = _load_json_mapping(inputs.calculation_validation_path)
    classification_report = inputs.validation_classification_path.read_text(encoding="utf-8")
    raw_cases = _sequence_value(manifest_payload.get("cases"), location="manifest.cases")
    approved = _string_sequence(
        manifest_payload.get("approved_expectations"),
        location="manifest.approved_expectations",
    )
    case_ids = tuple(
        _mapping_string(
            _string_object_mapping(value, location="manifest case"),
            "case_id",
            location="manifest case",
        )
        for value in raw_cases
    )
    expected_counts = {
        "companies": 2,
        "filings": 4,
        "filing_documents": 4,
        "source_evidence": 4,
        "raw_xbrl_facts": 4,
        "metric_observations": 4,
        "observation_evidence": 4,
        "observation_revisions": 4,
        "derived_observation_inputs": 0,
        "pipeline_runs": 1,
        "quarantine_candidates": 0,
    }
    dry_run_pass = len(dry_runs) == _EXPECTED_ISSUER_COUNT
    for payload in dry_runs:
        calls = _mapping_value(payload, "call_counts", location="dry-run report")
        dry_run_pass = dry_run_pass and (
            _mapping_string(payload, "provider", location="dry-run report") == "PUBLIC_EDGARTOOLS"
            and payload.get("dry_run") is True
            and payload.get("terminal_state") == "VALIDATED"
            and payload.get("validated_count") == _EXPECTED_CASE_COUNT // _EXPECTED_ISSUER_COUNT
            and payload.get("published_count") == 0
            and payload.get("quarantined_count") == 0
            and payload.get("failed_count") == 0
            and payload.get("fallback_calls", payload.get("fallback_call_count", 0)) == 0
            and payload.get("retry_calls", payload.get("retry_count", 0)) == 0
            and calls.get("persistence") == 0
            and calls.get("fallback") == 0
            and calls.get("retry") == 0
        )
    isolated_pass = all(
        (
            isolated.get("provider") == "PUBLIC_EDGARTOOLS",
            isolated.get("database")
            == f"artifacts/edgar-tools-migration/{_EXPECTED_DATABASE_NAME}",
            isolated.get("database_sha256") == database_sha256,
            isolated.get("atomic_batch") is True,
            isolated.get("companies_prepared_before_commit") == _EXPECTED_ISSUER_COUNT,
            isolated.get("published_count") == _EXPECTED_CASE_COUNT,
            isolated.get("quarantined_count") == 0,
            isolated.get("failed_count") == 0,
            isolated.get("observations_after_commit") == _EXPECTED_CASE_COUNT,
            isolated.get("observation_revisions_after_commit") == _EXPECTED_CASE_COUNT,
            isolated.get("legacy_observations_seeded") == 0,
            isolated.get("fallback_calls") == 0,
            isolated.get("retry_calls") == 0,
            isolated.get("acquisition_run_id") == _EXPECTED_ACQUISITION_RUN_ID,
            isolated.get("acquisition_run_key") == _EXPECTED_ACQUISITION_RUN_KEY,
        )
    )
    rerun_pass = all(
        (
            rerun.get("database") == f"artifacts/edgar-tools-migration/{_EXPECTED_DATABASE_NAME}",
            rerun.get("database_sha256") == database_sha256,
            rerun.get("unchanged_count") == _EXPECTED_CASE_COUNT,
            rerun.get("published_count") == 0,
            rerun.get("linked_count") == 0,
            rerun.get("quarantined_count") == 0,
            rerun.get("failed_count") == 0,
            rerun.get("observations_after_rerun") == _EXPECTED_CASE_COUNT,
            rerun.get("observation_revisions_after_rerun") == _EXPECTED_CASE_COUNT,
            rerun.get("fallback_calls") == 0,
            rerun.get("retry_calls") == 0,
            rerun.get("acquisition_run_id") == _EXPECTED_ACQUISITION_RUN_ID,
            rerun.get("acquisition_run_key") == _EXPECTED_ACQUISITION_RUN_KEY,
        )
    )
    retrievals = _mapping_value(isolated, "retrieval_timestamps_utc", location="live sync")
    retrieval_pass = set(retrievals) == set(case_ids) and all(
        case_id in persisted
        and _utc_timestamp_matches(
            persisted[case_id].retrieved_at,
            _mapping_string(retrievals, case_id, location="live sync retrievals"),
        )
        for case_id in case_ids
    )
    company_facts = _mapping_value(isolated, "company_facts", location="live sync")
    company_facts_pass = (
        company_facts.get("disposition") == "NOT_RETAINED_NOT_REPERFORMABLE_OFFLINE"
        and company_facts.get("gate_effect") == "NON_BLOCKING"
        and company_facts.get("publication_authority") is False
        and all("COMPANY_FACTS" not in case.source_class for case in persisted.values())
    )
    acquisition_run_pass = (
        pipeline_run is not None
        and pipeline_run.run_id == _EXPECTED_ACQUISITION_RUN_ID
        and pipeline_run.run_key == _EXPECTED_ACQUISITION_RUN_KEY
        and pipeline_run.status == "COMPLETED"
        and pipeline_run.error_count == 0
        and pipeline_run.retry_count == 0
        and pipeline_run.code_version == "edgartools-financial-sync-v1"
        and pipeline_run.config_version == "financial-fields-v1"
        and pipeline_run.parser_version == "inline-xbrl-selected-fields-v1"
        and pipeline_run.terminal_outcomes
        == {
            "PUBLISHED": 4,
            "LINKED": 0,
            "QUARANTINED": 0,
            "UNCHANGED": 0,
            "FAILED": 0,
        }
        and all(
            case.acquisition_run_id == _EXPECTED_ACQUISITION_RUN_ID for case in persisted.values()
        )
    )
    exact_calculation_pass = _exact_calculation_validation(
        calculation,
        case_ids=case_ids,
        persisted=persisted,
    )
    filings = _sequence_value(validation.get("filings"), location="validation.filings")
    structural_validation_pass = len(filings) == _EXPECTED_ISSUER_COUNT
    for value in filings:
        filing = _string_object_mapping(value, location="validation filing")
        xbrl = _mapping_value(filing, "xbrl", location="validation filing")
        structural_validation_pass = structural_validation_pass and (
            xbrl.get("available") is True
            and _positive_integer(xbrl.get("calculation_rows"))
            and _positive_integer(xbrl.get("negative_calculation_weights"))
            and xbrl.get("viewer_comparison_available") is True
            and xbrl.get("dimensions") == {}
        )
    viewer_classified = all(
        phrase in classification_report
        for phrase in (
            "8 TFC",
            "3 PFSI",
            "None referenced",
            "`us-gaap:Assets`",
            "`EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY`",
        )
    )
    mapping_pass = (
        len(registry.mappings) == _EXPECTED_ISSUER_COUNT
        and {mapping.issuer_id for mapping in registry.mappings} == {"tfc", "pfsi"}
        and all(
            mapping.field_id == "total_assets"
            and mapping.classification.value == "CORE_FINANCIAL"
            and mapping.selection_decision.value == "SELECTED"
            and mapping.source_route.value == _EXPECTED_SOURCE_CLASS
            and mapping.xbrl.period_type.value == "instant"
            and not mapping.xbrl.dimensions
            and mapping.currency == "USD"
            and mapping.amendment_behavior == "RETAIN_ACCESSION_LINEAGE_AND_REVIEW_SUPERSESSION"
            for mapping in registry.mappings
        )
    )
    omission = _mapping_value(
        manifest_payload,
        "omitted_case_classes",
        location="manifest",
    )
    optional = _mapping_value(omission, "optional_servicing", location="omitted cases")
    amendment = _mapping_value(
        omission,
        "amendment_or_restatement",
        location="omitted cases",
    )
    optional_pass = all(
        mapping.classification.value == "CORE_FINANCIAL" for mapping in registry.mappings
    ) and "No optional servicing field was selected" in _mapping_string(
        optional, "reason", location="optional servicing omission"
    )
    amendment_pass = all(
        case.amendment_of_id is None for case in persisted.values()
    ) and "No amendment affecting total_assets surfaced" in _mapping_string(
        amendment, "reason", location="amendment omission"
    )
    expected_lineage_columns = frozenset(
        {
            "filings.acceptance_timestamp",
            "source_evidence.source_tool_version",
        }
    )
    no_silent_substitution = (
        dry_run_pass
        and company_facts_pass
        and isolated.get("fallback_calls") == 0
        and rerun.get("fallback_calls") == 0
        and all(case.source_class == _EXPECTED_SOURCE_CLASS for case in persisted.values())
        and all(case.methodology == _EXPECTED_METHOD for case in persisted.values())
    )
    canonical_evidence_pass, cross_check_evidence_pass = _evidence_integrity(
        inputs,
        manifest_payload,
    )
    legacy = _legacy_diagnostic(
        inputs.legacy_baseline_path,
        selected_field_ids=tuple(sorted({mapping.field_id for mapping in registry.mappings})),
    )
    return (
        _check(
            "selected_fields_documented",
            mapping_pass,
            "Two issuer-specific total_assets CORE_FINANCIAL mappings are explicit.",
        ),
        _check(
            "golden_manifest_independently_approved",
            len(raw_cases) == _EXPECTED_CASE_COUNT
            and set(approved) == set(case_ids)
            and manifest_payload.get("status") == "INDEPENDENTLY_CROSS_CHECKED"
            and manifest_payload.get("publication_authority") is False
            and manifest_payload.get("generated_extractor_output_is_expectation") is False,
            "Four independent expectations are frozen; generated output is not authority.",
        ),
        _check(
            "isolated_database_shape",
            all(database_counts.get(name) == count for name, count in expected_counts.items())
            and set(persisted) == set(case_ids),
            "Only the four selected cases and their immutable lineage are present.",
        ),
        _check(
            "acquisition_lineage_columns",
            required_columns == expected_lineage_columns
            and all(
                case.source_tool_version == _EXPECTED_EDGARTOOLS_VERSION
                and _utc_timestamp_matches(
                    case.acceptance_timestamp,
                    _EXPECTED_ACCEPTANCE_TIMESTAMPS[case.case_id],
                )
                for case in persisted.values()
            ),
            "Every filing retains its exact SEC UTC acceptance time and edgartools 5.48.0.",
        ),
        _check(
            "retrieval_and_acquisition_run_lineage",
            retrieval_pass and acquisition_run_pass,
            "All retrieval times and evidence links resolve to the one exact completed run.",
        ),
        _check(
            "canonical_evidence_integrity",
            canonical_evidence_pass,
            "All four canonical runtime evidence bodies match approved SHA-256 and length.",
        ),
        _check(
            "independent_cross_check_integrity",
            cross_check_evidence_pass,
            "All four distinct validation bodies match approved SHA-256 and length.",
        ),
        _check(
            "dry_run_publishes_nothing",
            dry_run_pass,
            "Both issuer dry runs validated two cases with zero persistence/publication.",
        ),
        _check(
            "isolated_atomic_sync",
            isolated_pass,
            "The hash-bound Phase 4 report records one four-case atomic commit.",
        ),
        _check(
            "idempotent_repeated_sync",
            rerun_pass,
            "The same hash-bound Phase 4 DB records four unchanged cases and no added rows.",
        ),
        _check(
            "no_silent_fallback_or_substitution",
            no_silent_substitution,
            "Fallback, retry, and alternate publication routes remained unused.",
        ),
        _check(
            "exact_four_case_calculation_validation",
            exact_calculation_pass,
            "All Assets deltas are zero; exact children/arcs pass and the TFC Q2 "
            "200/50 versus 204/52 representation difference has selected-field impact NONE.",
        ),
        _check(
            "supporting_structural_diagnostic",
            structural_validation_pass,
            "The earlier diagnostic retains calculation, signed-weight, and Viewer evidence.",
        ),
        _check(
            "viewer_differences_classified",
            viewer_classified,
            "Viewer discrepancies are classified and do not reference us-gaap:Assets.",
        ),
        _check(
            "optional_servicing_scope",
            optional_pass,
            "No optional servicing item was included without individual qualification.",
        ),
        _check(
            "amendment_behavior",
            amendment_pass,
            "No selected-field amendment surfaced; accession/revision handling remains explicit.",
        ),
        _check(
            "no_unresolved_selected_output_defect",
            database_counts.get("quarantine_candidates") == 0
            and len(persisted) == _EXPECTED_CASE_COUNT
            and all(case.validation_status == "VALIDATED" for case in persisted.values()),
            "Selected output has zero quarantine rows and four validated evidence links.",
        ),
        _check(
            "legacy_diagnostic_bounded",
            legacy.baseline_sha256 == _EXPECTED_BASELINE_SHA256
            and legacy.baseline_rows == _EXPECTED_BASELINE_ROWS
            and not legacy.directly_overlapping_field_ids
            and legacy.compared_rows == 0
            and not legacy.classified_differences,
            "The immutable 439-row servicing baseline has no total_assets semantic overlap.",
        ),
    )


def _exact_calculation_validation(
    payload: Mapping[str, object],
    *,
    case_ids: tuple[str, ...],
    persisted: Mapping[str, _PersistedGoldenCase],
) -> bool:
    metadata_matches = not (
        payload.get("validation_method") != "INDEPENDENT_RAW_SEC_CALCULATION_LINKBASE"
        or payload.get("signed_weight_handling") != "RAW_SIGNED_WEIGHTS_PRESERVED"
        or payload.get("publication_authority") is not False
    )
    discrepancies = _sequence_value(
        payload.get("structural_representation_discrepancies"),
        location="structural representation discrepancies",
    )
    discrepancy_matches = False
    if len(discrepancies) == 1:
        discrepancy = _string_object_mapping(
            discrepancies[0],
            location="structural representation discrepancy",
        )
        discrepancy_matches = discrepancy == {
            "case_id": "tfc-2026q2-quarterly-total-assets",
            "edgartools_calculation_rows": 200,
            "edgartools_negative_calculation_weights": 50,
            "raw_linkbase_arc_count": 204,
            "raw_linkbase_negative_weight_count": 52,
            "classification": "EDGARTOOLS_PARSER_OR_VIEWER_DISCREPANCY",
            "selected_parent_concept": "us-gaap:Assets",
            "selected_assets_impact": "NONE",
        }
    if not metadata_matches or not discrepancy_matches:
        return False
    rows = _sequence_value(payload.get("cases"), location="calculation validation cases")
    by_id: dict[str, Mapping[str, object]] = {}
    for value in rows:
        row = _string_object_mapping(value, location="calculation validation case")
        case_id = _mapping_string(row, "case_id", location="calculation validation case")
        if case_id in by_id:
            return False
        by_id[case_id] = row
    if len(rows) != _EXPECTED_CASE_COUNT or set(by_id) != set(case_ids):
        return False
    for case_id, expected in _EXPECTED_CALCULATION_CASES.items():
        if case_id not in persisted or case_id not in by_id:
            return False
        child_count, arc_count, negative_arc_count, calc_document, role = expected
        row = by_id[case_id]
        stored = persisted[case_id]
        missing = _string_sequence(
            row.get("missing_child_concepts"),
            location=f"{case_id}.missing_child_concepts",
        )
        ambiguous = _string_sequence(
            row.get("ambiguous_child_concepts"),
            location=f"{case_id}.ambiguous_child_concepts",
        )
        exact = all(
            (
                row.get("status") == "PASS",
                row.get("accession") == stored.accession,
                row.get("document") == stored.document,
                row.get("context_ref") == stored.context_ref,
                row.get("calculation_document") == calc_document,
                row.get("calculation_role") == role,
                row.get("parent_concept") == "us-gaap:Assets",
                row.get("child_count") == child_count,
                row.get("negative_weight_count") == 0,
                row.get("linkbase_arc_count") == arc_count,
                row.get("linkbase_negative_weight_count") == negative_arc_count,
                not missing,
                not ambiguous,
                _exact_decimal(row.get("delta_decimal"), location=f"{case_id}.delta") == Decimal(0),
                _exact_decimal(row.get("computed_decimal"), location=f"{case_id}.computed")
                == stored.normalized_value,
                _exact_decimal(row.get("reported_decimal"), location=f"{case_id}.reported")
                == stored.normalized_value,
            )
        )
        if not exact:
            return False
    return True


def _evidence_integrity(
    inputs: FinancialQualificationInputs,
    manifest_payload: Mapping[str, object],
) -> tuple[bool, bool]:
    canonical_pass = True
    cross_check_pass = True
    for value in _sequence_value(manifest_payload.get("cases"), location="manifest.cases"):
        case = _string_object_mapping(value, location="manifest case")
        issuer_id = _mapping_string(case, "issuer_id", location="manifest case")
        source = _mapping_value(case, "edgartools_source", location="manifest case")
        source_hash = _mapping_string(source, "sha256", location="manifest source")
        source_length = _positive_int_value(
            source.get("byte_length"),
            location="manifest source byte_length",
        )
        source_path = inputs.runtime_evidence_root / source_hash[:2] / f"{source_hash}.bin"
        canonical_pass = canonical_pass and _file_identity_matches(
            source_path,
            sha256=source_hash,
            byte_length=source_length,
        )
        cross_check = _mapping_value(case, "cross_check_evidence", location="manifest case")
        cross_hash = _mapping_string(
            cross_check,
            "sha256",
            location="cross-check evidence",
        )
        cross_length = _positive_int_value(
            cross_check.get("byte_length"),
            location="cross-check byte_length",
        )
        cross_path = (
            inputs.cross_check_evidence_root
            / issuer_id
            / "sha256"
            / cross_hash[:2]
            / f"{cross_hash}.bin"
        )
        cross_check_pass = cross_check_pass and _file_identity_matches(
            cross_path,
            sha256=cross_hash,
            byte_length=cross_length,
        )
    return canonical_pass, cross_check_pass


def _legacy_diagnostic(
    path: Path,
    *,
    selected_field_ids: tuple[str, ...],
) -> LegacyDiagnostic:
    content = path.read_bytes()
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        message = "legacy audit baseline cannot be read deterministically"
        raise FinancialQualificationError(message) from error
    if reader.fieldnames is None or "metric_id" not in reader.fieldnames:
        message = "legacy audit baseline is missing metric_id"
        raise FinancialQualificationError(message)
    legacy_field_ids = {
        row["metric_id"]
        for row in rows
        if isinstance(row.get("metric_id"), str) and row["metric_id"].strip()
    }
    overlap = tuple(sorted(set(selected_field_ids) & legacy_field_ids))
    compared_rows = sum(row.get("metric_id") in overlap for row in rows)
    return LegacyDiagnostic(
        baseline_sha256=hashlib.sha256(content).hexdigest(),
        baseline_rows=len(rows),
        selected_field_ids=selected_field_ids,
        directly_overlapping_field_ids=overlap,
        compared_rows=compared_rows,
        classified_differences=(),
    )


def _manifest_case(
    manifest_payload: Mapping[str, object],
    case_id: str,
) -> Mapping[str, object]:
    matches = tuple(
        payload
        for payload in (
            _string_object_mapping(value, location="manifest case")
            for value in _sequence_value(
                manifest_payload.get("cases"),
                location="manifest.cases",
            )
        )
        if payload.get("case_id") == case_id
    )
    if len(matches) != 1:
        message = f"golden manifest must contain one exact case: {case_id}"
        raise FinancialQualificationError(message)
    return matches[0]


def _check(
    check_id: str,
    passed: bool,  # noqa: FBT001 - private builder keeps the assertion tuple compact.
    detail: str,
) -> QualificationCheck:
    return QualificationCheck(
        check_id=check_id,
        status=QualificationStatus.PASS if passed else QualificationStatus.FAIL,
        detail=detail,
    )


def _load_yaml_mapping(path: Path) -> Mapping[str, object]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        message = f"qualification YAML is unavailable or invalid: {path.name}"
        raise FinancialQualificationError(message) from error
    return _string_object_mapping(loaded, location=path.name)


def _load_json_mapping(path: Path) -> Mapping[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        message = f"qualification JSON is unavailable or invalid: {path.name}"
        raise FinancialQualificationError(message) from error
    return _string_object_mapping(loaded, location=path.name)


def _mapping_value(
    payload: Mapping[str, object],
    key: str,
    *,
    location: str,
) -> Mapping[str, object]:
    return _string_object_mapping(payload.get(key), location=f"{location}.{key}")


def _string_object_mapping(value: object, *, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        message = f"{location} must be a string-keyed mapping"
        raise FinancialQualificationError(message)
    return cast("Mapping[str, object]", value)


def _string_string_mapping(value: object, *, location: str) -> dict[str, str]:
    payload = _string_object_mapping(value, location=location)
    if not all(isinstance(item, str) for item in payload.values()):
        message = f"{location} must contain string values"
        raise FinancialQualificationError(message)
    return {key: cast("str", item) for key, item in payload.items()}


def _sequence_value(value: object, *, location: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{location} must be a sequence"
        raise FinancialQualificationError(message)
    return value


def _string_sequence(value: object, *, location: str) -> tuple[str, ...]:
    items = _sequence_value(value, location=location)
    if not all(isinstance(item, str) and item.strip() for item in items):
        message = f"{location} must contain nonblank strings"
        raise FinancialQualificationError(message)
    return tuple(cast("str", item) for item in items)


def _mapping_string(
    payload: Mapping[str, object],
    key: str,
    *,
    location: str,
) -> str:
    value = payload.get(key)
    if isinstance(value, bool):
        return str(value).lower()
    if not isinstance(value, str) or not value.strip():
        message = f"{location}.{key} must be a nonblank string"
        raise FinancialQualificationError(message)
    return value


def _object_string(
    payload: Mapping[str, object],
    key: str,
    *,
    location: str,
) -> str:
    return _mapping_string(payload, key, location=location)


def _positive_int_value(value: object, *, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        message = f"{location} must be a positive integer"
        raise FinancialQualificationError(message)
    return value


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as error:
        message = "golden decimals must be an integer or absent"
        raise FinancialQualificationError(message) from error


def _display_decimal(value: str) -> Decimal:
    normalized = value.strip().replace(",", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = f"-{normalized[1:-1]}"
    try:
        parsed = Decimal(normalized)
    except ArithmeticError as error:
        message = "golden display value is not an exact decimal"
        raise FinancialQualificationError(message) from error
    if not parsed.is_finite():
        message = "golden display value must be finite"
        raise FinancialQualificationError(message)
    return parsed


def _exact_decimal(value: object, *, location: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        message = f"{location} must be an exact decimal string"
        raise FinancialQualificationError(message)
    try:
        parsed = Decimal(value)
    except ArithmeticError as error:
        message = f"{location} must be an exact decimal string"
        raise FinancialQualificationError(message) from error
    if not parsed.is_finite():
        message = f"{location} must be finite"
        raise FinancialQualificationError(message)
    return parsed


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    # SQLite drops timezone offsets. These values originate from the adapter's
    # validated SEC UTC timestamp, so restore UTC rather than rejecting lineage.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _valid_utc_timestamp(value: datetime | None) -> bool:
    return _as_utc(value) is not None


def _utc_timestamp_matches(value: datetime | None, expected: str) -> bool:
    normalized = _as_utc(value)
    try:
        expected_timestamp = datetime.fromisoformat(expected)
    except ValueError as error:
        message = "recorded UTC timestamp is invalid"
        raise FinancialQualificationError(message) from error
    return normalized == expected_timestamp.astimezone(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity_matches(path: Path, *, sha256: str, byte_length: int) -> bool:
    return path.is_file() and path.stat().st_size == byte_length and _sha256(path) == sha256


def render_financial_qualification_markdown(report: FinancialQualificationReport) -> str:
    """Render a concise deterministic Markdown qualification report."""
    lines = [
        "# Phase 4 Financial Qualification Gate",
        "",
        f"Status: `{report.status.value}`",
        "",
        f"Stop condition: `{report.stop_condition}`",
        "",
        "## Golden regression",
        "",
    ]
    lines.extend(
        (
            f"- `{case.case_id}`: `{case.status.value}`; {case.compared_fields} exact fields; "
            f"dashboard `{case.dashboard_value}` / `{case.fiscal_period}`."
        )
        for case in report.golden_cases
    )
    lines.extend(("", "## Gate checks", ""))
    lines.extend(
        f"- `{check.check_id}`: `{check.status.value}` - {check.detail}" for check in report.checks
    )
    diagnostic = report.legacy_diagnostic
    lines.extend(
        (
            "",
            "## Legacy diagnostic",
            "",
            (
                f"- Immutable baseline: {diagnostic.baseline_rows} rows; SHA-256 "
                f"`{diagnostic.baseline_sha256}`."
            ),
            (
                f"- Directly overlapping selected fields: "
                f"{len(diagnostic.directly_overlapping_field_ids)}."
            ),
            f"- Rows compared: {diagnostic.compared_rows}. Legacy parity is not required.",
            "",
        )
    )
    return "\n".join(lines)


def write_financial_qualification_reports(
    report: FinancialQualificationReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    """Write deterministic ignored report artifacts after qualification completes."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_financial_qualification_markdown(report),
        encoding="utf-8",
    )
