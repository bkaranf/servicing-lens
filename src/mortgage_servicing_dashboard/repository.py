"""Evidence-rooted Stage A persistence and bounded read services."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import Engine, Select, func, or_, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    Company,
    ComparabilityAssessment,
    EarningsEvent,
    EligibleSourceAssessment,
    EntityIdentifier,
    EntityRelationship,
    Filing,
    FilingDocument,
    FiscalCalendarRegime,
    HumanReviewDecision,
    MetricAlias,
    MetricDefinition,
    MetricDefinitionVersion,
    MetricObservation,
    ObservationEvidence,
    ObservationRevision,
    PipelineRun,
    QuarantineCandidate,
    RawRegulatoryFact,
    RawXbrlFact,
    ReportingEntity,
    ReportingScope,
    Security,
    SourceEvidence,
    initialize_schema,
    utc_now,
)
from mortgage_servicing_dashboard.domain import (
    ComparisonInput,
    ObservationState,
    ParsedObservationCandidate,
    PublicationState,
    QualityState,
    assess_comparability,
    normalize_reported_value,
    validate_candidate,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    LiveSecAcquisition,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
)

_MAX_REPOSITORY_RESULTS = 500


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """Serializable observation with full semantic and evidence context."""

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
    revision_history: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible exact-value representation."""
        payload = asdict(self)
        payload["revision_history"] = list(self.revision_history)
        return payload


@dataclass(frozen=True, slots=True)
class ComparisonRecord:
    """Pairwise comparison suitable for API and dashboard rendering."""

    metric_id: str
    period_end: str
    left: ObservationRecord
    right: ObservationRecord
    status: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return an exact-value JSON-compatible comparison."""
        return {
            "metric_id": self.metric_id,
            "period_end": self.period_end,
            "left": self.left.as_dict(),
            "right": self.right.as_dict(),
            "status": self.status,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class _SourceBundle:
    definition: RecordedSourceDefinition
    document: AcquiredDocument
    candidates: tuple[ParsedObservationCandidate, ...]


def config_directory(explicit: Path | None = None) -> Path:
    """Resolve versioned configuration in a checkout or installed wheel."""
    if explicit is not None:
        return explicit
    configured = os.environ.get("MSI_CONFIG_DIR")
    candidates = [
        Path(configured) if configured else None,
        Path(__file__).resolve().parents[2] / "config",
        Path(sys.prefix) / "share" / "public-mortgage-servicing-intelligence" / "config",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "universe.yaml").is_file():
            return candidate
    msg = "versioned Stage A configuration directory was not found"
    raise FileNotFoundError(msg)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        msg = f"expected a mapping in {path.name}"
        raise TypeError(msg)
    return cast("dict[str, Any]", loaded)


def load_stage_a_configuration(
    explicit: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load universe, metric catalog, and nonnumeric evidence recipes."""
    root = config_directory(explicit)
    return (
        _load_yaml(root / "universe.yaml"),
        _load_yaml(root / "metrics" / "catalog.yaml"),
        _load_yaml(root / "stage_a_data.yaml"),
    )


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _metric_display_name(metric_id: str) -> str:
    return metric_id.replace("_", " ").title().replace("Msr", "MSR").replace("Upb", "UPB")


def _seed_universe(
    session: Session,
    *,
    universe: dict[str, Any],
    companies: list[dict[str, Any]],
) -> int:
    inserted = 0
    for company in companies:
        company_id = str(company["id"])
        entity_id = str(company["reporting_entity"])
        scope_id = str(company["reporting_scope"])
        if session.get(Company, company_id) is None:
            session.add(
                Company(
                    id=company_id,
                    legal_name=str(company["legal_name"]),
                    ticker=str(company["ticker"]),
                    classification=str(company["classification"]),
                    universe_version=str(universe["version"]),
                    active=True,
                )
            )
            session.add(
                Security(
                    id=f"{company_id}:common",
                    company_id=company_id,
                    ticker=str(company["ticker"]),
                    exchange="NYSE",
                    security_type="common_stock",
                )
            )
            session.add(
                ReportingEntity(
                    id=entity_id,
                    company_id=company_id,
                    legal_name=str(company["legal_name"]),
                    entity_type="SEC_REGISTRANT",
                )
            )
            population = (
                "residential_servicing_for_others_and_bank_owned"
                if company_id == "tfc"
                else "owned_msr_subservicing_and_held_for_sale"
            )
            session.add(
                ReportingScope(
                    id=scope_id,
                    reporting_entity_id=entity_id,
                    name=scope_id.replace("_", " ").title(),
                    portfolio_population=population,
                    methodology="Issuer-defined public servicing disclosure scope.",
                )
            )
            session.add(
                EntityIdentifier(
                    id=f"{entity_id}:cik",
                    reporting_entity_id=entity_id,
                    scheme="SEC_CIK",
                    value=str(company["cik"]),
                    valid_from=date(1900, 1, 1),
                    valid_to=None,
                )
            )
            session.add(
                FiscalCalendarRegime(
                    id=f"{entity_id}:calendar",
                    reporting_entity_id=entity_id,
                    fiscal_year_end_month=12,
                    fiscal_year_end_day=31,
                    effective_from=date(1900, 1, 1),
                    effective_to=None,
                )
            )
            inserted += 1

        regulatory_entities = cast(
            "list[dict[str, Any]]", company.get("regulatory_reporting_entities", [])
        )
        for regulatory in regulatory_entities:
            regulatory_entity_id = str(regulatory["id"])
            if session.get(ReportingEntity, regulatory_entity_id) is None:
                session.add(
                    ReportingEntity(
                        id=regulatory_entity_id,
                        company_id=company_id,
                        legal_name=str(regulatory["legal_name"]),
                        entity_type=str(regulatory["entity_type"]),
                    )
                )
            scope = cast("dict[str, Any]", regulatory["scope"])
            regulatory_scope_id = str(scope["id"])
            if session.get(ReportingScope, regulatory_scope_id) is None:
                session.add(
                    ReportingScope(
                        id=regulatory_scope_id,
                        reporting_entity_id=regulatory_entity_id,
                        name=str(scope["name"]),
                        portfolio_population=str(scope["portfolio_population"]),
                        methodology=str(scope["methodology"]),
                    )
                )
            parent_entity_id = str(regulatory.get("parent_entity_id", entity_id))
            relationship_id = f"{parent_entity_id}:{regulatory_entity_id}"
            if session.get(EntityRelationship, relationship_id) is None:
                session.add(
                    EntityRelationship(
                        id=relationship_id,
                        parent_entity_id=parent_entity_id,
                        child_entity_id=regulatory_entity_id,
                        relationship_type=str(regulatory["relationship_type"]),
                        valid_from=date(1900, 1, 1),
                        valid_to=None,
                        known_from=datetime(2026, 8, 12, tzinfo=UTC),
                        known_to=None,
                    )
                )
            identifiers = cast("list[dict[str, Any]]", regulatory["identifiers"])
            for identifier in identifiers:
                scheme = str(identifier["scheme"])
                identifier_id = f"{regulatory_entity_id}:{scheme.lower()}"
                if session.get(EntityIdentifier, identifier_id) is None:
                    session.add(
                        EntityIdentifier(
                            id=identifier_id,
                            reporting_entity_id=regulatory_entity_id,
                            scheme=scheme,
                            value=str(identifier["value"]),
                            valid_from=date(1900, 1, 1),
                            valid_to=None,
                        )
                    )
    return inserted


def _seed_metrics(session: Session, metrics: list[dict[str, Any]]) -> int:
    inserted = 0
    for metric in metrics:
        metric_id = str(metric["id"])
        version_id = f"{metric_id}:{metric['semantic_version']}"
        if session.get(MetricDefinition, metric_id) is not None:
            continue
        session.add(
            MetricDefinition(
                id=metric_id,
                display_name=_metric_display_name(metric_id),
                category=str(metric["category"]),
            )
        )
        rules = {
            key: value
            for key, value in metric.items()
            if key
            not in {
                "id",
                "category",
                "semantic_version",
                "business_meaning",
                "grain",
                "unit",
                "permitted_reporting_scopes",
            }
        }
        session.add(
            MetricDefinitionVersion(
                id=version_id,
                metric_id=metric_id,
                semantic_version=str(metric["semantic_version"]),
                business_meaning=str(metric["business_meaning"]),
                grain=str(metric["grain"]),
                unit=str(metric["unit"]),
                permitted_scopes=list(metric["permitted_reporting_scopes"]),
                rules=rules,
                effective_from=date(2025, 7, 1),
                effective_to=None,
            )
        )
        for index, label in enumerate(metric["source_labels_and_aliases"]):
            session.add(
                MetricAlias(
                    id=f"{version_id}:alias:{index}",
                    metric_version_id=version_id,
                    reporting_entity_id=None,
                    source_label=str(label),
                )
            )
        inserted += 1
    return inserted


def _load_source_bundles(
    *,
    config_root: Path,
    data: dict[str, Any],
    companies: list[dict[str, Any]],
) -> dict[str, _SourceBundle]:
    company_by_id = {str(item["id"]): item for item in companies}
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    source_payloads = cast("dict[str, dict[str, Any]]", data["sources"])
    acquirer = RecordedEvidenceAcquirer()
    parser = StageARecordedDocumentParser()
    bundles: dict[str, _SourceBundle] = {}
    for key, payload in source_payloads.items():
        definition = RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=config_root,
        )
        document = acquirer.acquire(definition)
        candidates = parser.parse(
            source=definition,
            content=document.content,
            company=company_by_id[definition.company_id],
            quarters=quarters,
        )
        for candidate in candidates:
            result = validate_candidate(candidate)
            if not result.valid:
                msg = f"candidate validation failed closed: {candidate.candidate_id}:{result.code}"
                raise ValueError(msg)
        bundles[key] = _SourceBundle(definition, document, candidates)
    return bundles


def _seed_pipeline_run(
    session: Session,
    *,
    data: dict[str, Any],
    bundles: dict[str, _SourceBundle],
    known_at: datetime,
    thread_id: str | None,
) -> PipelineRun:
    dataset_version = str(data["dataset_version"])
    run_key = _stable_hash(
        {
            "dataset_version": dataset_version,
            "evidence": sorted(bundle.document.sha256 for bundle in bundles.values()),
            "parser_versions": sorted(
                bundle.definition.parser_version for bundle in bundles.values()
            ),
            "source_assessment": data["eligible_source_assessment"],
        }
    )
    run_id = f"pipeline:{run_key[:32]}"
    existing = session.get(PipelineRun, run_id)
    if existing is not None:
        if thread_id is not None and existing.thread_id != thread_id:
            msg = "idempotent run already belongs to a different checkpoint thread"
            raise ValueError(msg)
        return existing
    quarantine_count = sum(len(bundle.definition.quarantine_rows) for bundle in bundles.values())
    run = PipelineRun(
        id=run_id,
        run_key=run_key,
        status="AWAITING_REVIEW" if quarantine_count else "READY_TO_PUBLISH",
        thread_id=thread_id or f"thread:{run_key[:24]}",
        started_at=known_at,
        completed_at=None,
        error_count=0,
        retry_count=0,
        requested_company_id=None,
        requested_periods=[str(item["period_end"]) for item in data["quarters"]],
        code_version="stage-a-evidence-pipeline-v2",
        config_version=dataset_version,
        parser_version="2.0.0",
        terminal_outcomes={
            "PUBLISHED": 0,
            "NOT_DISCLOSED": 0,
            "SOURCE_NOT_CHECKED": 0,
            "QUARANTINED": quarantine_count,
            "FAILED": 0,
        },
    )
    session.add(run)
    session.flush()
    return run


def _seed_evidence(
    session: Session,
    *,
    bundles: dict[str, _SourceBundle],
    run: PipelineRun,
    known_at: datetime,
) -> int:
    inserted = 0
    for key, bundle in bundles.items():
        source = bundle.definition
        evidence_id = f"evidence:{key}"
        if session.get(SourceEvidence, evidence_id) is None:
            relative = source.fixture_path.name
            session.add(
                SourceEvidence(
                    id=evidence_id,
                    source_class=source.source_class,
                    original_url=source.url,
                    retrieved_at=known_at,
                    published_at=source.published_at,
                    accession_or_identifier=source.accession,
                    content_sha256=bundle.document.sha256,
                    byte_length=bundle.document.byte_length,
                    media_type=source.media_type,
                    representation=source.representation,
                    capture_method=source.capture_method,
                    parser_version=source.parser_version,
                    acquisition_run_id=run.id,
                    reporting_entity_candidate=f"{source.company_id}_registrant",
                    reporting_period_candidate=source.period_end,
                    retention_location=f"config-recorded://recorded_evidence/{relative}",
                    bounded_excerpt=(
                        "Retained official document parsed by exact row label; "
                        "reported row text remains in observation evidence."
                    ),
                    response_status=200,
                    etag=None,
                    last_modified=None,
                )
            )
            company_entity = f"{source.company_id}_registrant"
            filing = session.scalar(select(Filing).where(Filing.accession == source.accession))
            if filing is None:
                filing = Filing(
                    id=f"filing:{key}",
                    reporting_entity_id=company_entity,
                    form_type="8-K EXHIBIT",
                    accession=source.accession,
                    filed_at=source.published_at,
                    period_end=date.fromisoformat(source.period_end),
                    amendment_of_id=None,
                )
                session.add(filing)
                session.flush()
            if session.get(FilingDocument, f"document:{key}") is None:
                session.add(
                    FilingDocument(
                        id=f"document:{key}",
                        filing_id=filing.id,
                        sequence=1,
                        document_type="earnings_exhibit",
                        filename=source.url.rsplit("/", maxsplit=1)[-1],
                        source_url=source.url,
                    )
                )
            event_quarter = ((int(source.period_end[5:7]) - 1) // 3) + 1
            existing_event = session.scalar(
                select(EarningsEvent).where(
                    EarningsEvent.company_id == source.company_id,
                    EarningsEvent.filing_accession == source.accession,
                )
            )
            if existing_event is None:
                session.add(
                    EarningsEvent(
                        id=f"earnings:{key}",
                        company_id=source.company_id,
                        fiscal_year=int(source.period_end[:4]),
                        fiscal_quarter=event_quarter,
                        period_end=date.fromisoformat(source.period_end),
                        event_at=source.published_at,
                        evidence_id=evidence_id,
                        event_kind="FILED_ACTUAL",
                        source_kind="SEC_8_K_EX_99",
                        filing_accession=source.accession,
                        window_start=None,
                        window_end=None,
                        is_inferred=False,
                        inference_basis=[],
                    )
                )
            inserted += 1
    session.flush()
    return inserted


def _seed_source_assessments(  # noqa: C901, PLR0913
    session: Session,
    *,
    data: dict[str, Any],
    companies: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    quarters: list[dict[str, Any]],
    bundles: dict[str, _SourceBundle],
    run: PipelineRun,
    known_at: datetime,
) -> int:
    """Expand the reviewable assessment matrix to retained cell-level records."""
    policy = cast("dict[str, Any]", data["eligible_source_assessment"])
    assessment_version = str(policy["assessment_version"])
    configured_metric_ids = [str(value) for value in policy["metric_ids"]]
    catalog_metric_ids = [str(metric["id"]) for metric in metrics]
    if set(configured_metric_ids) != set(catalog_metric_ids):
        msg = "eligible-source assessment must enumerate the complete metric catalog"
        raise ValueError(msg)
    company_policies = cast("dict[str, dict[str, Any]]", policy["companies"])
    if set(company_policies) != {str(company["id"]) for company in companies}:
        msg = "eligible-source assessment must enumerate the selected company universe"
        raise ValueError(msg)
    inserted = 0
    disclosed_cells = {
        (candidate.company_id, candidate.period_end.isoformat(), candidate.metric_id)
        for bundle in bundles.values()
        for candidate in bundle.candidates
    }
    for company in companies:
        company_id = str(company["id"])
        entity_id = str(company["reporting_entity"])
        scope_id = str(company["reporting_scope"])
        company_policy = company_policies[company_id]
        missingness_status = str(company_policy["assessment_status"])
        if missingness_status not in {"CHECKED_COMPLETE", "SOURCE_NOT_CHECKED"}:
            msg = f"unsupported eligible-source assessment status: {missingness_status}"
            raise ValueError(msg)
        inventories = cast(
            "dict[str, list[dict[str, object]]]",
            company_policy["period_filing_inventory"],
        )
        configured_periods = {str(quarter["period_end"]) for quarter in quarters}
        if set(inventories) != configured_periods:
            msg = f"eligible-source filing inventory is incomplete for {company_id}"
            raise ValueError(msg)
        checked_evidence_ids = [str(value) for value in company_policy["checked_evidence_ids"]]
        checked_locators = [str(value) for value in company_policy["checked_locators"]]
        for period_end_text, inventory in inventories.items():
            if not inventory:
                msg = (
                    f"eligible-source filing inventory is empty for {company_id}:{period_end_text}"
                )
                raise ValueError(msg)
            if missingness_status == "CHECKED_COMPLETE" and any(
                item.get("review_status") != "CHECKED"
                or not item.get("retained_evidence_id")
                or not item.get("locator")
                for item in inventory
            ):
                msg = "CHECKED_COMPLETE requires every eligible source to be retained and located"
                raise ValueError(msg)
            for metric_id in configured_metric_ids:
                assessment_status = (
                    "DISCLOSURE_FOUND"
                    if (company_id, period_end_text, metric_id) in disclosed_cells
                    else missingness_status
                )
                assessment_id = (
                    f"assessment:{company_id}:{period_end_text}:{metric_id}:{assessment_version}"
                )
                if session.get(EligibleSourceAssessment, assessment_id) is not None:
                    continue
                session.add(
                    EligibleSourceAssessment(
                        id=assessment_id,
                        pipeline_run_id=run.id,
                        company_id=company_id,
                        metric_version_id=f"{metric_id}:1.0.0",
                        reporting_entity_id=entity_id,
                        reporting_scope_id=scope_id,
                        period_end=date.fromisoformat(period_end_text),
                        assessment_status=assessment_status,
                        eligible_source_inventory=inventory,
                        checked_evidence_ids=checked_evidence_ids,
                        checked_locators=checked_locators,
                        assessment_version=assessment_version,
                        rationale=str(company_policy["rationale"]),
                        assessed_at=known_at,
                    )
                )
                inserted += 1
    session.flush()
    return inserted


def _reported_precision(candidate: ParsedObservationCandidate) -> str:
    if candidate.reported_scale == "millions":
        return "nearest USD million"
    if candidate.reported_scale == "billions":
        return "nearest USD billion"
    if candidate.reported_scale == "percent":
        return f"{candidate.reported_decimals} decimal places in percent"
    return f"{candidate.reported_decimals} reported decimal places"


def _missing_semantic_digest(
    *,
    metric_version_id: str,
    entity_id: str,
    scope_id: str,
    period_end: str,
) -> str:
    return _stable_hash(
        {
            "metric_version_id": metric_version_id,
            "reporting_entity_id": entity_id,
            "reporting_scope_id": scope_id,
            "period_end": period_end,
            "observation_state": ObservationState.NOT_DISCLOSED.value,
            "methodology": "eligible_source_set_reviewed",
        }
    )


def _seed_observations(  # noqa: PLR0913, PLR0915
    session: Session,
    *,
    companies: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    quarters: list[dict[str, Any]],
    bundles: dict[str, _SourceBundle],
    run: PipelineRun,
    known_at: datetime,
) -> int:
    parsed_by_key = {
        (candidate.company_id, candidate.period_end.isoformat(), candidate.metric_id): candidate
        for bundle in bundles.values()
        for candidate in bundle.candidates
    }
    evidence_by_company = {
        bundle.definition.company_id: f"evidence:{key}" for key, bundle in bundles.items()
    }
    source_by_company = {
        bundle.definition.company_id: bundle.definition for bundle in bundles.values()
    }
    assessments = session.scalars(
        select(EligibleSourceAssessment).where(EligibleSourceAssessment.pipeline_run_id == run.id)
    ).all()
    assessment_by_key = {
        (
            item.company_id,
            item.period_end.isoformat(),
            item.metric_version_id,
        ): item
        for item in assessments
    }
    inserted = 0
    for company in companies:
        company_id = str(company["id"])
        entity_id = str(company["reporting_entity"])
        scope_id = str(company["reporting_scope"])
        evidence_id = evidence_by_company[company_id]
        source = source_by_company[company_id]
        for quarter in quarters:
            period_end_text = str(quarter["period_end"])
            for metric in metrics:
                metric_id = str(metric["id"])
                observation_id = f"observation:{company_id}:{period_end_text}:{metric_id}:v1"
                if session.get(MetricObservation, observation_id) is not None:
                    continue
                candidate = parsed_by_key.get((company_id, period_end_text, metric_id))
                metric_version_id = f"{metric_id}:{metric['semantic_version']}"
                if candidate is None:
                    assessment = assessment_by_key.get(
                        (company_id, period_end_text, metric_version_id)
                    )
                    if assessment is None or assessment.assessment_status != "CHECKED_COMPLETE":
                        continue
                    if not assessment.checked_evidence_ids or not assessment.checked_locators:
                        msg = "CHECKED_COMPLETE source assessment lacks retained evidence lineage"
                        raise ValueError(msg)
                    evidence_id = assessment.checked_evidence_ids[0]
                    value = None
                    currency = "USD" if str(metric["unit"]) in {"USD", "USD_per_loan"} else None
                    unit = str(metric["unit"])
                    scale = str(metric.get("scale", "ones"))
                    reported_decimals = None
                    precision = "not disclosed after the eligible source set was reviewed"
                    state = ObservationState.NOT_DISCLOSED.value
                    methodology = "eligible_source_set_reviewed"
                    locator = (
                        f"{'; '.join(assessment.checked_locators)}; "
                        f"no '{metric_id}' disclosure located"
                    )
                    extraction_method = "deterministic_source_set_review"
                    raw_label = "No disclosure located"
                    raw_value = "NOT_DISCLOSED"
                    semantic_digest = _missing_semantic_digest(
                        metric_version_id=metric_version_id,
                        entity_id=entity_id,
                        scope_id=scope_id,
                        period_end=period_end_text,
                    )
                    period_start = date.fromisoformat(str(quarter["period_start"]))
                    period_type = str(metric["period_semantics"])
                    parser_metadata: dict[str, object] = {
                        "eligible_source_inventory": assessment.eligible_source_inventory,
                        "source_assessment_id": assessment.id,
                        "source_assessment_version": assessment.assessment_version,
                        "missingness_rule": "all eligible retained sources checked",
                    }
                    validation_summary = (
                        "complete retained eligible-source inventory evaluated; no disclosure found"
                    )
                else:
                    value = candidate.normalized_value
                    currency = candidate.currency
                    unit = candidate.unit
                    scale = candidate.reported_scale
                    reported_decimals = candidate.reported_decimals
                    precision = _reported_precision(candidate)
                    state = candidate.observation_state.value
                    methodology = candidate.methodology
                    locator = candidate.evidence_locator
                    extraction_method = candidate.extraction_method
                    raw_label = candidate.raw_label
                    raw_value = candidate.raw_value
                    semantic_digest = candidate.semantic_key_digest
                    period_start = candidate.period_start or date.fromisoformat(
                        str(quarter["period_start"])
                    )
                    period_type = candidate.period_type
                    parser_metadata = {
                        "parser_name": candidate.parser_name,
                        "parser_version": candidate.parser_version,
                        "source_sha256": source.content_sha256,
                        "normalization_exact": True,
                    }
                    validation_summary = validate_candidate(candidate).summary
                observation = MetricObservation(
                    id=observation_id,
                    metric_version_id=metric_version_id,
                    reporting_entity_id=entity_id,
                    reporting_scope_id=scope_id,
                    period_start=period_start,
                    period_end=date.fromisoformat(period_end_text),
                    fiscal_year=int(quarter["fiscal_year"]),
                    fiscal_quarter=int(quarter["fiscal_quarter"]),
                    period_type=period_type,
                    value=value,
                    currency=currency,
                    unit=unit,
                    scale=scale,
                    reported_decimals=reported_decimals,
                    reported_precision=precision,
                    observation_state=state,
                    methodology=methodology,
                    evidence_locator=locator,
                    extraction_method=extraction_method,
                    parser_metadata=parser_metadata,
                    validation_summary=validation_summary,
                    publication_state=PublicationState.PUBLISHED.value,
                    revision_number=1,
                    semantic_key_digest=semantic_digest,
                    valid_from=date.fromisoformat(period_end_text),
                    valid_to=None,
                    knowledge_from=known_at,
                    knowledge_to=None,
                    supersedes_observation_id=None,
                    quality_state=QualityState.VALIDATED.value,
                    reported_label=raw_label,
                    reported_value=raw_value,
                    published_at=known_at,
                )
                session.add(observation)
                session.add(
                    ObservationEvidence(
                        observation_id=observation_id,
                        evidence_id=evidence_id,
                        evidence_role="reviewed_source" if candidate is None else "primary",
                        locator=locator,
                        raw_label=raw_label,
                        raw_value=raw_value,
                        disclosed_unit=unit,
                        disclosed_scale=scale,
                        extraction_method=extraction_method,
                        validation_status=QualityState.VALIDATED.value,
                    )
                )
                session.add(
                    ObservationRevision(
                        id=f"revision:{observation_id}:1",
                        observation_id=observation_id,
                        prior_observation_id=None,
                        reason=f"initial deterministic publication by {run.id}",
                        created_at=known_at,
                    )
                )
                inserted += 1
    session.flush()
    return inserted


def _seed_quarantine(
    session: Session,
    *,
    bundles: dict[str, _SourceBundle],
    companies: list[dict[str, Any]],
    run: PipelineRun,
) -> None:
    company_by_id = {str(item["id"]): item for item in companies}
    parser = StageARecordedDocumentParser()
    for bundle in bundles.values():
        source = bundle.definition
        company = company_by_id[source.company_id]
        for recipe in source.quarantine_rows:
            candidate_id = str(recipe["candidate_id"])
            if session.get(QuarantineCandidate, candidate_id) is not None:
                continue
            values = parser.extract_row_values(
                content=bundle.document.content,
                raw_label=str(recipe["raw_label"]),
                occurrence=int(recipe.get("row_occurrence", 0)),
            )
            raw_value = values[int(recipe.get("value_index", 0))]
            normalized = normalize_reported_value(raw_value, rule=str(recipe["normalization"]))
            period_end = date.fromisoformat(str(recipe["period_end"]))
            session.add(
                QuarantineCandidate(
                    id=candidate_id,
                    pipeline_run_id=run.id,
                    proposed_metric_id=str(recipe["proposed_metric_id"]),
                    raw_source_label=str(recipe["raw_label"]),
                    raw_value=raw_value,
                    proposed_normalized_value=normalized,
                    unit=str(recipe["canonical_unit"]),
                    scale=str(recipe["reported_scale"]),
                    period_end=period_end,
                    reporting_entity_id=str(company["reporting_entity"]),
                    reporting_scope_id=str(company["reporting_scope"]),
                    methodology=str(recipe["proposed_methodology"]),
                    evidence_id=f"evidence:{source.key}",
                    evidence_locator=(
                        f"{source.locator}; row '{recipe['raw_label']}'; "
                        f"period-end column {period_end.isoformat()}"
                    ),
                    bounded_excerpt="Composite row retained in official recorded document.",
                    confidence=Decimal("0.5500"),
                    conflicts_and_uncertainties=[str(recipe["conflict"])],
                    model_and_prompt_version=None,
                    status="PENDING",
                )
            )


def _seed_comparability_assessments(session: Session, *, known_at: datetime) -> None:
    """Retain pairwise Stage A assessments against exact observation revisions."""
    rows = session.execute(
        select(
            MetricObservation,
            ReportingEntity.company_id,
            MetricDefinitionVersion.metric_id,
            MetricDefinitionVersion.semantic_version,
            ReportingScope.portfolio_population,
        )
        .join(ReportingEntity, MetricObservation.reporting_entity_id == ReportingEntity.id)
        .join(
            MetricDefinitionVersion,
            MetricObservation.metric_version_id == MetricDefinitionVersion.id,
        )
        .join(ReportingScope, MetricObservation.reporting_scope_id == ReportingScope.id)
        .where(MetricObservation.publication_state == PublicationState.PUBLISHED.value)
    ).all()
    grouped: dict[tuple[str, date], dict[str, tuple[Any, str, str]]] = {}
    for observation, company_id, metric_id, semantic_version, population in rows:
        grouped.setdefault((metric_id, observation.period_end), {})[company_id] = (
            observation,
            semantic_version,
            population,
        )
    for (metric_id, _period_end), by_company in grouped.items():
        if set(by_company) != {"tfc", "pfsi"}:
            continue
        left, left_version, left_population = by_company["tfc"]
        right, right_version, right_population = by_company["pfsi"]

        def assessment_input(
            observation: MetricObservation,
            version: str,
            population: str,
            metric: str = metric_id,
        ) -> ComparisonInput:
            period_days = (
                (observation.period_end - observation.period_start).days + 1
                if observation.period_start is not None
                else None
            )
            return ComparisonInput(
                metric_id=metric,
                metric_version=version,
                reporting_scope=observation.reporting_scope_id,
                period_days=period_days,
                currency=observation.currency,
                unit=observation.unit,
                methodology=observation.methodology,
                observation_state=ObservationState(observation.observation_state),
                portfolio_population=population,
            )

        result = assess_comparability(
            assessment_input(left, left_version, left_population),
            assessment_input(right, right_version, right_population),
        )
        assessment_id = (
            "comparison:"
            + _stable_hash(
                {
                    "left": left.id,
                    "right": right.id,
                    "policy_version": "1.0.0",
                    "requested_operation": "cross_company_comparison",
                }
            )[:32]
        )
        if session.get(ComparabilityAssessment, assessment_id) is None:
            session.add(
                ComparabilityAssessment(
                    id=assessment_id,
                    left_observation_id=left.id,
                    right_observation_id=right.id,
                    policy_version="1.0.0",
                    requested_operation="cross_company_comparison",
                    status=result.status.value,
                    reasons=list(result.reasons),
                    permitted_calculations=(
                        ["difference", "percentage_change"]
                        if result.status.value in {"comparable", "comparable_with_caveats"}
                        else []
                    ),
                    assessed_at=known_at,
                )
            )


def _write_stage_a(
    engine: Engine,
    *,
    config_dir: Path | None = None,
    thread_id: str | None = None,
    publish: bool,
) -> dict[str, int]:
    initialize_schema(engine)
    root = config_directory(config_dir)
    universe, catalog, data = load_stage_a_configuration(root)
    companies = cast("list[dict[str, Any]]", universe["companies"])
    metrics = cast("list[dict[str, Any]]", catalog["metrics"])
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    bundles = _load_source_bundles(config_root=root, data=data, companies=companies)
    known_at = _instant(str(data["knowledge_at"]))
    inserted = {
        "companies": 0,
        "metrics": 0,
        "evidence": 0,
        "source_assessments": 0,
        "observations": 0,
    }
    with Session(engine) as session:
        inserted["companies"] = _seed_universe(
            session,
            universe=universe,
            companies=companies,
        )
        inserted["metrics"] = _seed_metrics(session, metrics)
        session.flush()
        run = _seed_pipeline_run(
            session,
            data=data,
            bundles=bundles,
            known_at=known_at,
            thread_id=thread_id,
        )
        inserted["evidence"] = _seed_evidence(
            session,
            bundles=bundles,
            run=run,
            known_at=known_at,
        )
        inserted["source_assessments"] = _seed_source_assessments(
            session,
            data=data,
            companies=companies,
            metrics=metrics,
            quarters=quarters,
            bundles=bundles,
            run=run,
            known_at=known_at,
        )
        _seed_quarantine(session, bundles=bundles, companies=companies, run=run)
        if publish:
            inserted["observations"] = _seed_observations(
                session,
                companies=companies,
                metrics=metrics,
                quarters=quarters,
                bundles=bundles,
                run=run,
                known_at=known_at,
            )
            measured = sum(len(bundle.candidates) for bundle in bundles.values())
            total_grid = len(companies) * len(metrics) * len(quarters)
            not_disclosed = int(
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.observation_state == ObservationState.NOT_DISCLOSED.value
                    )
                )
                or 0
            )
            source_not_checked = total_grid - measured - not_disclosed
            quarantine_count = sum(
                len(bundle.definition.quarantine_rows) for bundle in bundles.values()
            )
            run.status = "COMPLETED_WITH_GAPS" if source_not_checked else "COMPLETED"
            run.completed_at = known_at
            run.terminal_outcomes = {
                "PUBLISHED": measured,
                "NOT_DISCLOSED": not_disclosed,
                "SOURCE_NOT_CHECKED": source_not_checked,
                "QUARANTINED": quarantine_count,
                "FAILED": 0,
            }
            _seed_comparability_assessments(session, known_at=known_at)
        session.commit()
    return inserted


def prepare_stage_a(
    engine: Engine,
    *,
    config_dir: Path | None = None,
    thread_id: str | None = None,
) -> dict[str, int]:
    """Persist catalogs, verified evidence, and quarantine before publication."""
    return _write_stage_a(
        engine,
        config_dir=config_dir,
        thread_id=thread_id,
        publish=False,
    )


def seed_stage_a(
    engine: Engine,
    *,
    config_dir: Path | None = None,
    thread_id: str | None = None,
) -> dict[str, int]:
    """Idempotently parse retained evidence and publish the Stage A data set.

    Args:
        engine: Application database engine.
        config_dir: Optional explicit versioned configuration root.
        thread_id: Optional durable graph thread that owns the idempotent run.

    Returns:
        Counts of newly inserted primary catalog/evidence/observation records.
    """
    return _write_stage_a(
        engine,
        config_dir=config_dir,
        thread_id=thread_id,
        publish=True,
    )


def _live_pipeline_run(
    session: Session,
    *,
    acquisitions: tuple[LiveSecAcquisition, ...],
    config_version: str,
    known_at: datetime,
) -> tuple[PipelineRun, bool]:
    run_key = _stable_hash(
        {
            "mode": "live-sec",
            "config_version": config_version,
            "evidence": sorted(item.acquired_document.sha256 for item in acquisitions),
            "parsers": sorted(item.runtime_definition.parser_version for item in acquisitions),
        }
    )
    run_id = f"pipeline:live-sec:{run_key[:23]}"
    existing = session.get(PipelineRun, run_id)
    if existing is not None:
        return existing, False
    run = PipelineRun(
        id=run_id,
        run_key=run_key,
        status="RUNNING",
        thread_id=f"thread:live-sec:{run_key[:20]}",
        started_at=known_at,
        completed_at=None,
        error_count=0,
        retry_count=0,
        requested_company_id=(
            acquisitions[0].company_id
            if len({item.company_id for item in acquisitions}) == 1
            else None
        ),
        requested_periods=sorted({item.runtime_definition.period_end for item in acquisitions}),
        code_version="phase-2-live-sec-v1",
        config_version=config_version,
        parser_version="2.0.0",
        terminal_outcomes={
            "PUBLISHED": 0,
            "NOT_DISCLOSED": 0,
            "SOURCE_NOT_CHECKED": 0,
            "QUARANTINED": 0,
            "FAILED": 0,
        },
    )
    session.add(run)
    session.flush()
    return run, True


def _persist_live_sec_evidence(
    session: Session,
    *,
    acquisition: LiveSecAcquisition,
    run: PipelineRun,
) -> tuple[str, bool]:
    source = acquisition.runtime_definition
    document = acquisition.acquired_document
    evidence_id = f"evidence:{acquisition.source_key}"
    existing_content = session.scalar(
        select(SourceEvidence).where(
            SourceEvidence.content_sha256 == document.sha256,
            SourceEvidence.byte_length == document.byte_length,
        )
    )
    if existing_content is not None:
        if (
            existing_content.representation != "ORIGINAL_HTTP_RESPONSE"
            or existing_content.capture_method != "sec_http_get"
        ):
            msg = "live SEC bytes already exist under incompatible evidence metadata"
            raise ValueError(msg)
        return existing_content.id, False
    if session.get(SourceEvidence, evidence_id) is not None:
        msg = "live SEC evidence identity exists with different immutable bytes"
        raise ValueError(msg)
    session.add(
        SourceEvidence(
            id=evidence_id,
            source_class=source.source_class,
            original_url=source.url,
            retrieved_at=document.retrieved_at,
            published_at=source.published_at,
            accession_or_identifier=source.accession,
            content_sha256=document.sha256,
            byte_length=document.byte_length,
            media_type=document.media_type,
            representation="ORIGINAL_HTTP_RESPONSE",
            capture_method="sec_http_get",
            parser_version=source.parser_version,
            acquisition_run_id=run.id,
            reporting_entity_candidate=f"{source.company_id}_registrant",
            reporting_period_candidate=source.period_end,
            retention_location=f"content-sha256://{document.sha256}",
            bounded_excerpt=(
                "Exact original SEC HTTP response retained immutably; "
                "bounded row text is recorded on observation lineage."
            ),
            response_status=document.status_code,
            etag=document.etag,
            last_modified=document.last_modified,
        )
    )
    filing = session.scalar(select(Filing).where(Filing.accession == source.accession))
    if filing is None:
        metadata = acquisition.discovered_filing
        filing_id = f"filing:sec:{source.accession.replace('-', '')}"
        filed_at = metadata.acceptance_at or datetime.combine(
            metadata.filing_date,
            time.min,
            tzinfo=UTC,
        )
        filing = Filing(
            id=filing_id,
            reporting_entity_id=f"{source.company_id}_registrant",
            form_type=metadata.form,
            accession=source.accession,
            filed_at=filed_at,
            period_end=metadata.report_date or date.fromisoformat(source.period_end),
            amendment_of_id=None,
        )
        session.add(filing)
        session.flush()
    document_id = f"document:sec:{document.sha256[:24]}"
    if session.get(FilingDocument, document_id) is None:
        session.add(
            FilingDocument(
                id=document_id,
                filing_id=filing.id,
                sequence=1,
                document_type=acquisition.discovered_filing.form,
                filename=source.url.rsplit("/", maxsplit=1)[-1],
                source_url=source.url,
            )
        )
    existing_event = session.scalar(
        select(EarningsEvent).where(
            EarningsEvent.company_id == source.company_id,
            EarningsEvent.filing_accession == source.accession,
        )
    )
    if existing_event is None:
        event_period_end = date.fromisoformat(source.period_end)
        session.add(
            EarningsEvent(
                id=f"earnings:sec:{document.sha256[:24]}",
                company_id=source.company_id,
                fiscal_year=event_period_end.year,
                fiscal_quarter=((event_period_end.month - 1) // 3) + 1,
                period_end=event_period_end,
                event_at=acquisition.discovered_filing.acceptance_at or source.published_at,
                evidence_id=evidence_id,
                event_kind="FILED_ACTUAL",
                source_kind="SEC_8_K_EX_99",
                filing_accession=source.accession,
                window_start=None,
                window_end=None,
                is_inferred=False,
                inference_basis=[],
            )
        )
    session.flush()
    return evidence_id, True


def _live_observation_id(
    session: Session,
    candidate: ParsedObservationCandidate,
) -> tuple[str, int]:
    prefix = (
        f"observation:{candidate.company_id}:{candidate.period_end.isoformat()}:"
        f"{candidate.metric_id}:v"
    )
    revisions = [
        int(value.rsplit(":v", maxsplit=1)[-1])
        for value in session.scalars(
            select(MetricObservation.id).where(MetricObservation.id.like(f"{prefix}%"))
        )
        if value.rsplit(":v", maxsplit=1)[-1].isdigit()
    ]
    revision_number = max(revisions, default=0) + 1
    return f"{prefix}{revision_number}", revision_number


def _link_live_candidate(
    session: Session,
    *,
    candidate: ParsedObservationCandidate,
    evidence_id: str,
    run: PipelineRun,
    known_at: datetime,
) -> str:
    active = session.scalars(
        select(MetricObservation).where(
            MetricObservation.metric_version_id
            == f"{candidate.metric_id}:{candidate.metric_version}",
            MetricObservation.reporting_entity_id == candidate.reporting_entity_id,
            MetricObservation.reporting_scope_id == candidate.reporting_scope_id,
            MetricObservation.period_end == candidate.period_end,
            MetricObservation.knowledge_to.is_(None),
        )
    ).all()
    matching = next(
        (
            item
            for item in active
            if item.semantic_key_digest == candidate.semantic_key_digest
            and item.value == candidate.normalized_value
            and item.observation_state == candidate.observation_state.value
        ),
        None,
    )
    if matching is not None:
        if session.get(ObservationEvidence, (matching.id, evidence_id)) is None:
            session.add(
                ObservationEvidence(
                    observation_id=matching.id,
                    evidence_id=evidence_id,
                    evidence_role="corroborating_original_response",
                    locator=candidate.evidence_locator,
                    raw_label=candidate.raw_label,
                    raw_value=candidate.raw_value,
                    disclosed_unit=candidate.unit,
                    disclosed_scale=candidate.reported_scale,
                    extraction_method=candidate.extraction_method,
                    validation_status=QualityState.VALIDATED.value,
                )
            )
        return "LINKED"
    conflicting = [
        item for item in active if item.observation_state != ObservationState.NOT_DISCLOSED.value
    ]
    if conflicting:
        quarantine_id = (
            f"quarantine:live-sec:{candidate.company_id}:"
            f"{candidate.metric_id}:{candidate.period_end.isoformat()}:"
            f"{candidate.semantic_key_digest[:12]}"
        )
        if session.get(QuarantineCandidate, quarantine_id) is None:
            session.add(
                QuarantineCandidate(
                    id=quarantine_id,
                    pipeline_run_id=run.id,
                    proposed_metric_id=candidate.metric_id,
                    raw_source_label=candidate.raw_label,
                    raw_value=candidate.raw_value,
                    proposed_normalized_value=candidate.normalized_value,
                    unit=candidate.unit,
                    scale=candidate.reported_scale,
                    period_end=candidate.period_end,
                    reporting_entity_id=candidate.reporting_entity_id,
                    reporting_scope_id=candidate.reporting_scope_id,
                    methodology=candidate.methodology,
                    evidence_id=evidence_id,
                    evidence_locator=candidate.evidence_locator,
                    bounded_excerpt="Live original response conflicts with an active observation.",
                    confidence=Decimal("1.0000"),
                    conflicts_and_uncertainties=[
                        "deterministic live extraction disagrees with retained active observation",
                        *[f"active observation {item.id}={item.value}" for item in conflicting],
                    ],
                    model_and_prompt_version=None,
                    status="PENDING",
                )
            )
        return "QUARANTINED"

    for item in active:
        item.knowledge_to = known_at
    observation_id, revision_number = _live_observation_id(session, candidate)
    prior_id = max(active, key=lambda item: item.revision_number).id if active else None
    session.add(
        MetricObservation(
            id=observation_id,
            metric_version_id=f"{candidate.metric_id}:{candidate.metric_version}",
            reporting_entity_id=candidate.reporting_entity_id,
            reporting_scope_id=candidate.reporting_scope_id,
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            fiscal_year=candidate.fiscal_year,
            fiscal_quarter=candidate.fiscal_quarter,
            period_type=candidate.period_type,
            value=candidate.normalized_value,
            currency=candidate.currency,
            unit=candidate.unit,
            scale=candidate.reported_scale,
            reported_decimals=candidate.reported_decimals,
            reported_precision=_reported_precision(candidate),
            observation_state=candidate.observation_state.value,
            methodology=candidate.methodology,
            evidence_locator=candidate.evidence_locator,
            extraction_method=candidate.extraction_method,
            parser_metadata={
                "parser_name": candidate.parser_name,
                "parser_version": candidate.parser_version,
                "source_mode": "live-sec-original-http-response",
            },
            validation_summary=validate_candidate(candidate).summary,
            publication_state=PublicationState.PUBLISHED.value,
            revision_number=revision_number,
            semantic_key_digest=candidate.semantic_key_digest,
            valid_from=candidate.period_end,
            valid_to=None,
            knowledge_from=known_at,
            knowledge_to=None,
            supersedes_observation_id=prior_id,
            quality_state=QualityState.VALIDATED.value,
            reported_label=candidate.raw_label,
            reported_value=candidate.raw_value,
            published_at=known_at,
        )
    )
    session.add(
        ObservationEvidence(
            observation_id=observation_id,
            evidence_id=evidence_id,
            evidence_role="primary",
            locator=candidate.evidence_locator,
            raw_label=candidate.raw_label,
            raw_value=candidate.raw_value,
            disclosed_unit=candidate.unit,
            disclosed_scale=candidate.reported_scale,
            extraction_method=candidate.extraction_method,
            validation_status=QualityState.VALIDATED.value,
        )
    )
    session.add(
        ObservationRevision(
            id=f"revision:{observation_id}:{revision_number}",
            observation_id=observation_id,
            prior_observation_id=prior_id,
            reason=f"deterministic live SEC publication by {run.id}",
            created_at=known_at,
        )
    )
    return "PUBLISHED"


def ingest_live_sec_acquisitions(
    engine: Engine,
    acquisitions: tuple[LiveSecAcquisition, ...],
    *,
    config_dir: Path | None = None,
) -> dict[str, int]:
    """Persist and deterministically parse opt-in live SEC acquisitions.

    Existing equivalent observations gain corroborating original-response lineage.
    A disagreement with an active published value is quarantined rather than
    silently preferred. Repeated runs over identical bytes are idempotent.
    """
    if not acquisitions:
        msg = "at least one live SEC acquisition is required"
        raise ValueError(msg)
    initialize_schema(engine)
    root = config_directory(config_dir)
    universe, catalog, data = load_stage_a_configuration(root)
    companies = cast("list[dict[str, Any]]", universe["companies"])
    company_by_id = {str(item["id"]): item for item in companies}
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    known_at = max(item.retained_at for item in acquisitions)
    parser = StageARecordedDocumentParser()
    counts = {"evidence": 0, "published": 0, "linked": 0, "quarantined": 0}
    with Session(engine) as session:
        _seed_universe(session, universe=universe, companies=companies)
        _seed_metrics(session, cast("list[dict[str, Any]]", catalog["metrics"]))
        run, created = _live_pipeline_run(
            session,
            acquisitions=acquisitions,
            config_version=str(data["dataset_version"]),
            known_at=known_at,
        )
        if not created:
            return counts
        for acquisition in acquisitions:
            source = acquisition.runtime_definition
            if source.company_id not in company_by_id:
                msg = f"live SEC company is outside the configured universe: {source.company_id}"
                raise ValueError(msg)
            evidence_id, evidence_inserted = _persist_live_sec_evidence(
                session,
                acquisition=acquisition,
                run=run,
            )
            counts["evidence"] += int(evidence_inserted)
            candidates = parser.parse(
                source=source,
                content=acquisition.acquired_document.content,
                company=company_by_id[source.company_id],
                quarters=quarters,
            )
            for candidate in candidates:
                validation = validate_candidate(candidate)
                if not validation.valid:
                    msg = (
                        "live SEC candidate validation failed closed: "
                        f"{candidate.candidate_id}:{validation.code}"
                    )
                    raise ValueError(msg)
                outcome = _link_live_candidate(
                    session,
                    candidate=candidate,
                    evidence_id=evidence_id,
                    run=run,
                    known_at=known_at,
                )
                counts[outcome.lower()] += 1
        run.status = "AWAITING_REVIEW" if counts["quarantined"] else "COMPLETED"
        run.completed_at = known_at
        run.terminal_outcomes = {
            "PUBLISHED": counts["published"] + counts["linked"],
            "NOT_DISCLOSED": 0,
            "SOURCE_NOT_CHECKED": 0,
            "QUARANTINED": counts["quarantined"],
            "FAILED": 0,
        }
        session.commit()
    return counts


def persist_xbrl_facts(
    engine: Engine,
    facts: tuple[Any, ...],
    *,
    filing_id: str | None = None,
) -> int:
    """Retain replayable exact XBRL facts after evidence acquisition.

    The typed adapter owns validation. This boundary refuses missing evidence and
    stores raw text plus every context semantic needed to replay mapping later.
    """
    initialize_schema(engine)
    inserted = 0
    with Session(engine) as session:
        if filing_id is not None and session.get(Filing, filing_id) is None:
            raise KeyError(filing_id)
        for fact in facts:
            if session.get(SourceEvidence, fact.evidence_id) is None:
                raise KeyError(fact.evidence_id)
            fact_id = f"raw-xbrl:{_stable_hash((fact.evidence_id, fact.locator))[:64]}"
            if session.get(RawXbrlFact, fact_id) is not None:
                continue
            session.add(
                RawXbrlFact(
                    id=fact_id,
                    evidence_id=fact.evidence_id,
                    filing_id=filing_id,
                    concept=fact.concept,
                    taxonomy=fact.taxonomy,
                    entity_identifier=fact.entity_identifier,
                    context_ref=fact.context_id,
                    raw_value=fact.raw_value,
                    unit_ref=fact.unit,
                    decimals=(str(fact.decimals) if fact.decimals is not None else None),
                    scale=fact.scale,
                    period_type=fact.period_type.value,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    instant=(fact.period_end if fact.period_type.value == "instant" else None),
                    dimensions={item.dimension: item.member for item in fact.dimensions},
                    methodology=(
                        "SEC_COMPANY_FACTS_XBRL"
                        if fact.source.value == "SEC_COMPANY_FACTS"
                        else "SEC_FILING_XBRL"
                    ),
                )
            )
            inserted += 1
        session.commit()
    return inserted


def persist_regulatory_facts(
    engine: Engine,
    facts: tuple[Any, ...],
    *,
    evidence_id: str,
) -> int:
    """Retain native reporter-scoped regulatory facts without scope blending."""
    initialize_schema(engine)
    inserted = 0
    with Session(engine) as session:
        if session.get(SourceEvidence, evidence_id) is None:
            raise KeyError(evidence_id)
        for fact in facts:
            fact_id = f"raw-regulatory:{fact.fact_id[:64]}"
            if session.get(RawRegulatoryFact, fact_id) is not None:
                continue
            if session.get(ReportingEntity, fact.reporting_entity_id) is None:
                raise KeyError(fact.reporting_entity_id)
            if session.get(ReportingScope, fact.reporting_scope_id) is None:
                raise KeyError(fact.reporting_scope_id)
            session.add(
                RawRegulatoryFact(
                    id=fact_id,
                    evidence_id=evidence_id,
                    reporting_entity_id=fact.reporting_entity_id,
                    reporting_scope_id=fact.reporting_scope_id,
                    source_family=fact.source_family.value,
                    rssd_id=fact.rssd_id,
                    schedule=fact.schedule,
                    item_code=fact.item,
                    report_date=fact.report_date,
                    period_type=fact.period_type,
                    unit=fact.unit,
                    scale=fact.scale,
                    raw_value=fact.raw_value,
                    revision_identifier=fact.revision,
                )
            )
            inserted += 1
        session.commit()
    return inserted


def _as_of_instant(as_of: datetime | date | None) -> datetime:
    if as_of is None:
        return utc_now()
    if isinstance(as_of, datetime):
        return as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    return datetime.combine(as_of, time.max, tzinfo=UTC)


class IntelligenceRepository:
    """Bounded read service over published exact observations."""

    def __init__(self, engine: Engine) -> None:
        """Bind typed reads to an initialized engine."""
        self._engine = engine

    @property
    def engine(self) -> Engine:
        """Expose the configured engine for health and migration checks."""
        return self._engine

    def companies(self) -> list[dict[str, object]]:
        """Return the selected two-company universe."""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(Company).where(Company.active.is_(True)).order_by(Company.id)
            )
            return [
                {
                    "id": row.id,
                    "legal_name": row.legal_name,
                    "ticker": row.ticker,
                    "classification": row.classification,
                    "universe_version": row.universe_version,
                }
                for row in rows
            ]

    def metrics(self) -> list[dict[str, object]]:
        """Return the versioned metric catalog."""
        statement = (
            select(MetricDefinition, MetricDefinitionVersion)
            .join(MetricDefinitionVersion, MetricDefinitionVersion.metric_id == MetricDefinition.id)
            .order_by(MetricDefinition.category, MetricDefinition.id)
        )
        with Session(self._engine) as session:
            return [
                {
                    "id": definition.id,
                    "display_name": definition.display_name,
                    "category": definition.category,
                    "semantic_version": version.semantic_version,
                    "business_meaning": version.business_meaning,
                    "grain": version.grain,
                    "unit": version.unit,
                    "permitted_scopes": version.permitted_scopes,
                    "rules": version.rules,
                }
                for definition, version in session.execute(statement)
            ]

    def evidence(self, evidence_id: str) -> dict[str, object] | None:
        """Return one immutable evidence identity and retention record."""
        with Session(self._engine) as session:
            item = session.get(SourceEvidence, evidence_id)
            if item is None:
                return None
            return {
                "id": item.id,
                "source_class": item.source_class,
                "original_url": item.original_url,
                "retrieved_at": item.retrieved_at.isoformat(),
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "accession_or_identifier": item.accession_or_identifier,
                "content_sha256": item.content_sha256,
                "byte_length": item.byte_length,
                "media_type": item.media_type,
                "representation": item.representation,
                "capture_method": item.capture_method,
                "parser_version": item.parser_version,
                "retention_location": item.retention_location,
                "bounded_excerpt": item.bounded_excerpt,
                "response_status": item.response_status,
                "etag": item.etag,
                "last_modified": item.last_modified,
            }

    def earnings_events(self) -> list[dict[str, object]]:
        """Return selected-company public earnings disclosure events."""
        statement = (
            select(EarningsEvent, Company, SourceEvidence)
            .join(Company, EarningsEvent.company_id == Company.id)
            .join(SourceEvidence, EarningsEvent.evidence_id == SourceEvidence.id)
            .order_by(EarningsEvent.event_at, Company.id)
        )
        with Session(self._engine) as session:
            return [
                {
                    "id": event.id,
                    "company_id": company.id,
                    "ticker": company.ticker,
                    "fiscal_year": event.fiscal_year,
                    "fiscal_quarter": event.fiscal_quarter,
                    "period_end": event.period_end.isoformat() if event.period_end else None,
                    "event_at": event.event_at.isoformat(),
                    "evidence_id": evidence.id,
                    "source_url": evidence.original_url,
                    "event_kind": event.event_kind,
                    "source_kind": event.source_kind,
                    "filing_accession": event.filing_accession,
                    "window_start": (
                        event.window_start.isoformat() if event.window_start else None
                    ),
                    "window_end": event.window_end.isoformat() if event.window_end else None,
                    "is_inferred": event.is_inferred,
                    "inference_basis": event.inference_basis,
                }
                for event, company, evidence in session.execute(statement)
            ]

    def calendar(
        self,
        *,
        as_of: datetime | date | None = None,
        config_dir: Path | None = None,
    ) -> list[dict[str, object]]:
        """Return actual last reports and conspicuously inferred filing windows."""
        from mortgage_servicing_dashboard.calendar import (  # noqa: PLC0415
            build_earnings_calendar_from_official_config,
        )

        instant = _as_of_instant(as_of)
        config_path = config_directory(config_dir) / "calendar" / "earnings_calendar.v1.yaml"
        companies = {str(item["id"]): item for item in self.companies()}
        payload: list[dict[str, object]] = []
        for company_id in sorted(companies):
            result = build_earnings_calendar_from_official_config(
                config_path=config_path,
                company_id=company_id,
                as_of=instant,
            )
            reported = result.last_reported_period
            window = result.inferred_window
            payload.append(
                {
                    "company_id": company_id,
                    "ticker": companies[company_id]["ticker"],
                    "as_of": instant.isoformat(),
                    "last_reported_period": {
                        "period_end": reported.period_end.isoformat(),
                        "event_id": reported.filing_event_id,
                        "accepted_at": reported.accepted_at.isoformat(),
                        "accession": reported.accession,
                        "filing_url": reported.filing_url,
                        "exhibit_url": reported.exhibit_url,
                        "is_inferred": reported.is_inferred,
                    },
                    "next_expected_report_window": {
                        "expected_period_end": window.expected_period_end.isoformat(),
                        "window_start": window.window_start.isoformat(),
                        "window_end": window.window_end.isoformat(),
                        "is_inferred": window.is_inferred,
                        "method": window.method,
                        "config_version": window.config_version,
                        "inference_basis": list(window.inference_basis),
                    },
                    "freshness_state": result.freshness_state.value,
                    "next_announced_event": None,
                }
            )
        return payload

    def freshness(self) -> dict[str, object]:
        """Return evidence, publication, pipeline, and quarantine freshness."""
        with Session(self._engine) as session:
            retrieved_at = session.scalar(select(func.max(SourceEvidence.retrieved_at)))
            knowledge_at = session.scalar(select(func.max(MetricObservation.knowledge_from)))
            evidence_count = session.scalar(select(func.count(SourceEvidence.id))) or 0
            published_count = (
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.publication_state == PublicationState.PUBLISHED.value
                    )
                )
                or 0
            )
            missing_count = (
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.observation_state == ObservationState.NOT_DISCLOSED.value,
                        MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                    )
                )
                or 0
            )
            quarantine_count = (
                session.scalar(
                    select(func.count(QuarantineCandidate.id)).where(
                        QuarantineCandidate.status.not_in(("REJECTED", "PUBLISHED"))
                    )
                )
                or 0
            )
            run = session.scalars(
                select(PipelineRun).order_by(PipelineRun.started_at.desc())
            ).first()
            assessment_count = 0
            source_not_checked_count = 0
            if run is not None:
                assessments = session.scalars(
                    select(EligibleSourceAssessment).where(
                        EligibleSourceAssessment.pipeline_run_id == run.id
                    )
                ).all()
                assessment_count = len(assessments)
                published_cells = set(
                    session.execute(
                        select(
                            ReportingEntity.company_id,
                            MetricObservation.metric_version_id,
                            MetricObservation.reporting_scope_id,
                            MetricObservation.period_end,
                        )
                        .join(
                            ReportingEntity,
                            MetricObservation.reporting_entity_id == ReportingEntity.id,
                        )
                        .where(
                            MetricObservation.publication_state == PublicationState.PUBLISHED.value
                        )
                    ).all()
                )
                source_not_checked_count = sum(
                    1
                    for item in assessments
                    if item.assessment_status == "SOURCE_NOT_CHECKED"
                    and (
                        item.company_id,
                        item.metric_version_id,
                        item.reporting_scope_id,
                        item.period_end,
                    )
                    not in published_cells
                )
        return {
            "dataset": run.config_version if run is not None else None,
            "retrieved_at": retrieved_at.isoformat() if retrieved_at is not None else None,
            "knowledge_at": knowledge_at.isoformat() if knowledge_at is not None else None,
            "evidence_count": evidence_count,
            "observation_count": published_count,
            "published_count": published_count,
            "not_disclosed_count": missing_count,
            "source_assessment_count": assessment_count,
            "source_not_checked_count": source_not_checked_count,
            "quarantine_count": quarantine_count,
            "pipeline_status": run.status if run is not None else "NOT_RUN",
            "terminal_outcomes": run.terminal_outcomes if run is not None else {},
            "model_calls_enabled": False,
            "calendar": self.calendar(),
        }

    def _observation_statement(
        self,
        *,
        as_of: datetime | date | None,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
    ) -> Select[tuple[Any, ...]]:
        instant = _as_of_instant(as_of)
        statement = (
            select(
                MetricObservation,
                Company,
                MetricDefinition,
                MetricDefinitionVersion,
                ReportingScope,
                SourceEvidence,
            )
            .join(ReportingEntity, MetricObservation.reporting_entity_id == ReportingEntity.id)
            .join(Company, ReportingEntity.company_id == Company.id)
            .join(
                MetricDefinitionVersion,
                MetricObservation.metric_version_id == MetricDefinitionVersion.id,
            )
            .join(MetricDefinition, MetricDefinitionVersion.metric_id == MetricDefinition.id)
            .join(ReportingScope, MetricObservation.reporting_scope_id == ReportingScope.id)
            .outerjoin(
                ObservationEvidence,
                ObservationEvidence.observation_id == MetricObservation.id,
            )
            .outerjoin(SourceEvidence, ObservationEvidence.evidence_id == SourceEvidence.id)
            .where(
                MetricObservation.knowledge_from <= instant,
                or_(
                    MetricObservation.knowledge_to.is_(None),
                    MetricObservation.knowledge_to > instant,
                ),
                MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                MetricObservation.quality_state == QualityState.VALIDATED.value,
            )
        )
        if company_id is not None:
            statement = statement.where(Company.id == company_id)
        if metric_id is not None:
            statement = statement.where(MetricDefinition.id == metric_id)
        if period_end is not None:
            statement = statement.where(MetricObservation.period_end == period_end)
        return statement.order_by(MetricObservation.period_end, Company.id, MetricDefinition.id)

    @staticmethod
    def _record(row: Any) -> ObservationRecord:
        observation, company, definition, version, scope, evidence = row
        return ObservationRecord(
            id=observation.id,
            company_id=company.id,
            company_name=company.legal_name,
            ticker=company.ticker,
            company_classification=company.classification,
            metric_id=definition.id,
            metric_name=definition.display_name,
            metric_category=definition.category,
            metric_version=version.semantic_version,
            period_start=observation.period_start.isoformat() if observation.period_start else None,
            period_end=observation.period_end.isoformat(),
            fiscal_year=observation.fiscal_year,
            fiscal_quarter=observation.fiscal_quarter,
            period_type=observation.period_type,
            value=str(observation.value) if observation.value is not None else None,
            currency=observation.currency,
            unit=observation.unit,
            scale=observation.scale,
            reported_decimals=observation.reported_decimals,
            reported_precision=observation.reported_precision,
            state=observation.observation_state,
            quality_state=observation.quality_state,
            publication_state=observation.publication_state,
            revision_number=observation.revision_number,
            semantic_key_digest=observation.semantic_key_digest,
            methodology=observation.methodology,
            reporting_entity_id=observation.reporting_entity_id,
            reporting_scope_id=observation.reporting_scope_id,
            portfolio_population=scope.portfolio_population,
            reported_label=observation.reported_label,
            reported_value=observation.reported_value,
            evidence_locator=observation.evidence_locator,
            extraction_method=observation.extraction_method,
            validation_summary=observation.validation_summary,
            parser_metadata=observation.parser_metadata,
            evidence_id=evidence.id if evidence is not None else None,
            source_url=evidence.original_url if evidence is not None else None,
            source_class=evidence.source_class if evidence is not None else None,
            accession_or_identifier=evidence.accession_or_identifier if evidence else None,
            retrieved_at=evidence.retrieved_at.isoformat() if evidence else None,
            published_at=observation.published_at.isoformat(),
            bounded_excerpt=evidence.bounded_excerpt if evidence else None,
            evidence_sha256=evidence.content_sha256 if evidence else None,
            evidence_byte_length=evidence.byte_length if evidence else None,
            evidence_representation=evidence.representation if evidence else None,
            valid_from=observation.valid_from.isoformat(),
            valid_to=observation.valid_to.isoformat() if observation.valid_to else None,
            knowledge_from=observation.knowledge_from.isoformat(),
            knowledge_to=observation.knowledge_to.isoformat() if observation.knowledge_to else None,
        )

    def observations(  # noqa: PLR0913
        self,
        *,
        as_of: datetime | date | None = None,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        include_missing: bool = True,
        limit: int = _MAX_REPOSITORY_RESULTS,
        offset: int = 0,
    ) -> list[ObservationRecord]:
        """Query a bounded page of published observations as known at a time."""
        if limit < 1 or limit > _MAX_REPOSITORY_RESULTS or offset < 0:
            msg = f"limit must be 1..{_MAX_REPOSITORY_RESULTS} and offset must be nonnegative"
            raise ValueError(msg)
        statement = self._observation_statement(
            as_of=as_of,
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
        )
        if not include_missing:
            statement = statement.where(
                MetricObservation.observation_state != ObservationState.NOT_DISCLOSED.value
            )
        statement = statement.offset(offset).limit(limit)
        with Session(self._engine) as session:
            return [self._record(row) for row in session.execute(statement).all()]

    def observation_page(self, **filters: Any) -> dict[str, object]:
        """Return items with explicit bounded pagination metadata."""
        limit = int(filters.pop("limit", 100))
        offset = int(filters.pop("offset", 0))
        items = self.observations(limit=limit, offset=offset, **filters)
        count = self.observation_count(**filters)
        return {
            "items": [item.as_dict() for item in items],
            "count": count,
            "limit": limit,
            "offset": offset,
        }

    def observation_count(
        self,
        *,
        as_of: datetime | date | None = None,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        include_missing: bool = True,
    ) -> int:
        """Count all matching published observations without materializing the result set."""
        statement = self._observation_statement(
            as_of=as_of,
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
        )
        if not include_missing:
            statement = statement.where(
                MetricObservation.observation_state != ObservationState.NOT_DISCLOSED.value
            )
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        with Session(self._engine) as session:
            return int(session.scalar(count_statement) or 0)

    def _revision_history(self, observation_id: str) -> tuple[dict[str, object], ...]:
        statement = (
            select(ObservationRevision)
            .where(ObservationRevision.observation_id == observation_id)
            .order_by(ObservationRevision.created_at, ObservationRevision.id)
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return tuple(
            {
                "revision_id": row.id,
                "prior_observation_id": row.prior_observation_id,
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        )

    def observation(self, observation_id: str) -> ObservationRecord | None:
        """Return one published observation with revision history."""
        statement = self._observation_statement(as_of=None).where(
            MetricObservation.id == observation_id
        )
        with Session(self._engine) as session:
            row = session.execute(statement).first()
        if row is None:
            return None
        record = self._record(row)
        return replace(record, revision_history=self._revision_history(observation_id))

    def latest_period_end(self) -> date | None:
        """Return the latest published quarter."""
        with Session(self._engine) as session:
            return session.scalar(
                select(func.max(MetricObservation.period_end)).where(
                    MetricObservation.publication_state == PublicationState.PUBLISHED.value
                )
            )

    def coverage(self, *, as_of: datetime | date | None = None) -> list[dict[str, object]]:
        """Summarize reported, proven missing, and unchecked source coverage."""
        records = self.observations(as_of=as_of)
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        instant = _as_of_instant(as_of)
        with Session(self._engine) as session:
            run = session.scalars(
                select(PipelineRun).order_by(PipelineRun.started_at.desc())
            ).first()
            assessments = (
                session.scalars(
                    select(EligibleSourceAssessment).where(
                        EligibleSourceAssessment.pipeline_run_id == run.id,
                        EligibleSourceAssessment.assessed_at <= instant,
                    )
                ).all()
                if run is not None
                else []
            )
        for assessment in assessments:
            counts = grouped.setdefault(
                (assessment.company_id, assessment.period_end.isoformat()),
                {"reported": 0, "missing": 0, "source_not_checked": 0},
            )
            if assessment.assessment_status == "SOURCE_NOT_CHECKED":
                counts["source_not_checked"] += 1
        for record in records:
            counts = grouped.setdefault(
                (record.company_id, record.period_end),
                {"reported": 0, "missing": 0, "source_not_checked": 0},
            )
            bucket = (
                "missing" if record.state == ObservationState.NOT_DISCLOSED.value else "reported"
            )
            counts[bucket] += 1
        return [
            {
                "company_id": company_id,
                "period_end": period,
                "reported": counts["reported"],
                "missing": counts["missing"],
                "source_not_checked": counts["source_not_checked"],
                "total": (counts["reported"] + counts["missing"] + counts["source_not_checked"]),
            }
            for (company_id, period), counts in sorted(grouped.items())
        ]

    def compare(
        self,
        *,
        metric_id: str,
        period_end: date,
        as_of: datetime | date | None = None,
    ) -> ComparisonRecord | None:
        """Build a deterministic period-specific two-company comparison."""
        rows = self.observations(as_of=as_of, metric_id=metric_id, period_end=period_end)
        by_company = {row.company_id: row for row in rows}
        if set(by_company) != {"tfc", "pfsi"}:
            return None
        left = by_company["tfc"]
        right = by_company["pfsi"]
        result = assess_comparability(_comparison_input(left), _comparison_input(right))
        return ComparisonRecord(
            metric_id=metric_id,
            period_end=period_end.isoformat(),
            left=left,
            right=right,
            status=result.status.value,
            reasons=result.reasons,
        )

    def record_review_decision(
        self,
        *,
        candidate_id: str,
        decision: str,
        reviewer: str,
        rationale: str,
        thread_id: str,
    ) -> dict[str, str]:
        """Record an audited same-thread review without direct publication."""
        normalized_decision = decision.upper()
        if normalized_decision not in {"APPROVE", "REJECT"}:
            msg = "review decision must be APPROVE or REJECT"
            raise ValueError(msg)
        with Session(self._engine) as session:
            candidate = session.get(QuarantineCandidate, candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            run = session.get(PipelineRun, candidate.pipeline_run_id)
            if run is None or run.thread_id != thread_id:
                msg = "review must resume the candidate's original thread"
                raise ValueError(msg)
            decision_id = _stable_hash(
                {
                    "candidate_id": candidate_id,
                    "decision": normalized_decision,
                    "thread_id": thread_id,
                }
            )[:32]
            if session.get(HumanReviewDecision, decision_id) is None:
                session.add(
                    HumanReviewDecision(
                        id=decision_id,
                        candidate_id=candidate_id,
                        decision=normalized_decision,
                        reviewer=reviewer,
                        rationale=rationale,
                        thread_id=thread_id,
                        resulting_observation_id=None,
                    )
                )
            candidate.status = (
                "APPROVED_PENDING_REVALIDATION" if normalized_decision == "APPROVE" else "REJECTED"
            )
            session.commit()
            return {"candidate_id": candidate.id, "status": candidate.status}


def _comparison_input(record: ObservationRecord) -> ComparisonInput:
    return ComparisonInput(
        metric_id=record.metric_id,
        metric_version=record.metric_version,
        reporting_scope=record.reporting_scope_id,
        period_days=_period_days(record),
        currency=record.currency,
        unit=record.unit,
        methodology=record.methodology,
        observation_state=ObservationState(record.state),
        portfolio_population=record.portfolio_population,
    )


def _period_days(record: ObservationRecord) -> int | None:
    if record.period_start is None:
        return None
    start = date.fromisoformat(record.period_start)
    return (date.fromisoformat(record.period_end) - start).days + 1
