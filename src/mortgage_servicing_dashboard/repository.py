"""Evidence-rooted Stage A persistence and bounded read services."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import yaml
from sqlalchemy import Engine, Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    AccountingPolicyRegime,
    Company,
    ComparabilityAssessment,
    DerivedObservationInput,
    EarningsEvent,
    EligibleSourceAssessment,
    EntityIdentifier,
    EntityRelationship,
    Filing,
    FilingDocument,
    FiscalCalendarRegime,
    HumanReviewDecision,
    IngestionError,
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
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    AtomicPersistenceResult,
    CommittedCaseOutcome,
    CommittedCaseState,
    ValidatedFiling,
)
from mortgage_servicing_dashboard.financial_discovery import (
    FinancialFieldRegistry,
    FinancialMetricDefinition,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
)

if TYPE_CHECKING:
    from mortgage_servicing_dashboard.metric_engine import MetricCatalog
    from mortgage_servicing_dashboard.metric_engine import (
        MetricDefinition as EngineMetricDefinition,
    )
    from mortgage_servicing_dashboard.phase3 import Phase3Dataset

_MAX_REPOSITORY_RESULTS = 500
_MIN_COMPARISON_COMPANY_COUNT = 2
_MAX_COMPARISON_COMPANY_COUNT = 3
_EDGARTOOLS_METHOD = "SEC_FILING_XBRL_VIA_EDGARTOOLS"
_FINANCIAL_MAPPING_VERSION = "financial-fields-v1"
_LEGACY_UNIVERSE_VERSIONS = frozenset(
    {
        "phase-2-acquisition-2026-08-12",
        _FINANCIAL_MAPPING_VERSION,
    }
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_EDGARTOOLS_COMPANIES: dict[str, tuple[str, str, str, str, str]] = {
    "tfc": (
        "Truist Financial Corporation",
        "TFC",
        "bank",
        "0000092230",
        "tfc_registrant",
    ),
    "pfsi": (
        "PennyMac Financial Services, Inc.",
        "PFSI",
        "nonbank",
        "0001745916",
        "pfsi_registrant",
    ),
}


@dataclass(frozen=True, slots=True)
class EdgarToolsCompanyIdentity:
    """Governed legal identity supplied to generalized edgartools persistence."""

    legal_name: str
    ticker: str
    classification: str
    cik: str
    reporting_entity_id: str
    exchange: str = "NYSE"
    security_type: str = "common_stock"

    def __post_init__(self) -> None:
        """Reject incomplete governed legal identities."""
        if any(
            not value.strip()
            for value in (
                self.legal_name,
                self.ticker,
                self.classification,
                self.cik,
                self.reporting_entity_id,
                self.exchange,
                self.security_type,
            )
        ):
            message = "edgartools company identity fields must not be blank"
            raise ValueError(message)


def _legacy_company_identities() -> dict[str, EdgarToolsCompanyIdentity]:
    return {
        company_id: EdgarToolsCompanyIdentity(*values)
        for company_id, values in _EDGARTOOLS_COMPANIES.items()
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite's timezone-naive round trip for identity comparison."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EdgarToolsPersistenceError(ValueError):
    """Coordinator output cannot be persisted without violating its contract."""


def _fail_edgartools_persistence(message: str) -> NoReturn:
    raise EdgarToolsPersistenceError(message)


class AtomicEdgarToolsRepository:
    """Atomic publication callback for coordinator-validated filing facts.

    The default contract remains the two legacy ``total_assets`` registrants. A
    caller may inject an exact company registry and financial-field registry for a
    larger manifest-bounded cohort. It never invokes legacy seeders.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        companies: Mapping[str, EdgarToolsCompanyIdentity] | None = None,
        registry: FinancialFieldRegistry | None = None,
    ) -> None:
        """Bind the isolated publication engine and optional governed contracts."""
        self._engine = engine
        self._companies = dict(companies or _legacy_company_identities())
        self._registry = registry
        self._universe_version = (
            registry.version if registry is not None else _FINANCIAL_MAPPING_VERSION
        )
        self._last_result: AtomicPersistenceResult | None = None

    @property
    def last_result(self) -> AtomicPersistenceResult | None:
        """Return counts only after a complete transaction has committed."""
        return self._last_result

    def persist_atomically(
        self,
        results: tuple[ValidatedFiling, ...],
    ) -> AtomicPersistenceResult:
        """Persist a validated batch in exactly one application transaction.

        Args:
            results: Complete coordinator-validated batch.

        Raises:
            ValueError: If lineage or governed semantics are incomplete.
            SQLAlchemyError: If the database rejects any row; the whole batch rolls back.
        """
        self._last_result = None
        ordered = tuple(
            sorted(
                results,
                key=lambda item: (
                    item.company_id,
                    item.report_period,
                    item.filing_date,
                    item.amendment,
                    item.accession_number,
                    item.case_id,
                ),
            )
        )
        _validate_edgartools_batch(
            ordered,
            companies=self._companies,
            registry=self._registry,
        )
        if not ordered:
            self._last_result = AtomicPersistenceResult()
            return self._last_result

        # Migration is structural preparation, not publication. It is deferred until
        # this callback so dry runs never create an engine or touch a schema.
        initialize_schema(self._engine)
        counts = {
            "evidence": 0,
            "filings": 0,
            "documents": 0,
            "raw_facts": 0,
            "observations": 0,
            "revisions": 0,
            "linked": 0,
            "quarantined": 0,
        }
        knowledge_base = utc_now()
        outcomes: list[CommittedCaseOutcome] = []
        permitted_scopes: dict[str, set[str]] = {}
        for item in ordered:
            permitted_scopes.setdefault(_metric_version_id(item), set()).add(
                item.reporting_scope_id
            )
        with Session(self._engine) as session, session.begin():
            for item in ordered:
                _ensure_edgartools_structure(
                    session,
                    item,
                    company=self._companies[item.company_id],
                    permitted_scopes=permitted_scopes[_metric_version_id(item)],
                    metric_definition=_financial_metric_definition(item, self._registry),
                    universe_version=self._universe_version,
                )
            session.flush()
            run, run_created = _edgartools_run(session, ordered, started_at=knowledge_base)
            for ordinal, item in enumerate(ordered):
                knowledge_at = knowledge_base + timedelta(microseconds=ordinal)
                outcome = _persist_validated_filing(
                    session,
                    item,
                    run=run,
                    knowledge_at=knowledge_at,
                    counts=counts,
                )
                outcomes.append(
                    CommittedCaseOutcome(
                        case_id=item.case_id,
                        accession_number=item.accession_number,
                        state=outcome,
                    )
                )
            if run_created:
                run.completed_at = knowledge_base + timedelta(microseconds=len(ordered))
                run.status = "COMPLETED_WITH_QUARANTINE" if counts["quarantined"] else "COMPLETED"
                run.terminal_outcomes = {
                    "PUBLISHED": counts["observations"],
                    "LINKED": counts["linked"],
                    "QUARANTINED": counts["quarantined"],
                    "UNCHANGED": sum(
                        outcome.state is CommittedCaseState.UNCHANGED for outcome in outcomes
                    ),
                    "FAILED": 0,
                }
        self._last_result = AtomicPersistenceResult(outcomes=tuple(outcomes), **counts)
        return self._last_result

    def known_accessions(self, company_id: str) -> frozenset[str]:
        """Load persisted SEC accessions for one governed company from this database."""
        company = self._companies.get(company_id)
        if company is None:
            _fail_edgartools_persistence("edgartools accessions are limited to governed companies")
        initialize_schema(self._engine)
        entity_id = company.reporting_entity_id
        with Session(self._engine) as session:
            return frozenset(
                session.scalars(
                    select(Filing.accession).where(Filing.reporting_entity_id == entity_id)
                )
            )


def _validate_edgartools_batch(  # noqa: C901, PLR0912, PLR0915
    results: tuple[ValidatedFiling, ...],
    *,
    companies: Mapping[str, EdgarToolsCompanyIdentity] | None = None,
    registry: FinancialFieldRegistry | None = None,
) -> None:
    governed_companies = dict(companies or _legacy_company_identities())
    case_ids: set[str] = set()
    for item in results:
        company = governed_companies.get(item.company_id)
        if company is None:
            _fail_edgartools_persistence("edgartools publication is limited to governed companies")
        required = (
            item.case_id,
            item.mapping_version,
            item.accession_number,
            item.form,
            item.primary_document,
            item.primary_sequence,
            item.primary_document_type,
            item.primary_description,
            item.source_url,
            item.evidence_sha256,
            item.evidence_location,
            item.field_id,
            item.reporting_entity_id,
            item.reporting_scope_id,
            item.reporting_scope_name,
            item.portfolio_population,
            item.scope_methodology,
            item.raw_display_string,
            item.context_ref,
            item.unit,
            item.qualified_concept,
            item.original_label,
            item.evidence_representation,
            item.evidence_capture_method,
            item.evidence_media_type,
            item.edgartools_version,
            item.source_document or item.primary_document,
            item.source_sequence or item.primary_sequence,
            item.source_document_type or item.primary_document_type,
            item.source_description or item.primary_description,
            item.metric_version,
        )
        if any(not value.strip() for value in required):
            _fail_edgartools_persistence("validated filing lineage contains a blank required field")
        if item.case_id in case_ids:
            _fail_edgartools_persistence("validated filing batch repeats a case identifier")
        case_ids.add(item.case_id)
        expected_mapping_version = (
            _FINANCIAL_MAPPING_VERSION if registry is None else registry.version
        )
        if item.mapping_version != expected_mapping_version:
            _fail_edgartools_persistence(
                "validated filing is outside the governed financial mapping"
            )
        if item.cik != company.cik or item.reporting_entity_id != company.reporting_entity_id:
            _fail_edgartools_persistence(
                "validated filing is outside the governed financial mapping"
            )
        if registry is None:
            if (
                item.field_id != "total_assets"
                or item.reporting_scope_id != f"{item.company_id}_consolidated_company"
            ):
                _fail_edgartools_persistence(
                    "validated filing is outside the governed financial mapping"
                )
        else:
            mapping_matches = tuple(
                mapping
                for mapping in registry.mappings
                if mapping.mapping_id == item.mapping_id
                and mapping.issuer_id == item.company_id
                and mapping.xbrl.cik == item.cik
                and mapping.field_id == item.field_id
                and mapping.xbrl.metric_version == item.metric_version
                and mapping.xbrl.reporting_entity_id == item.reporting_entity_id
                and mapping.xbrl.reporting_scope_id == item.reporting_scope_id
                and mapping.classification is item.classification
                and mapping.xbrl.qualified_concept == item.qualified_concept
                and mapping.xbrl.unit == item.unit
                and mapping.xbrl.period_type is item.period_type
                and mapping.display_name == (item.metric_display_name or item.original_label)
                and mapping.reporting_scope_name == item.reporting_scope_name
                and mapping.portfolio_population == item.portfolio_population
                and mapping.scope_methodology == item.scope_methodology
                and mapping.reporting_scope_category.value == item.reporting_scope_category
                and tuple(
                    (dimension.dimension, dimension.member) for dimension in mapping.xbrl.dimensions
                )
                == item.dimensions
                and mapping.xbrl.applies_to(item.report_period)
            )
            if registry.version != item.mapping_version or len(mapping_matches) != 1:
                _fail_edgartools_persistence(
                    "validated filing is outside the governed financial mapping"
                )
        if not isinstance(item.normalized_value, Decimal) or not item.normalized_value.is_finite():
            _fail_edgartools_persistence("validated filing value must be a finite Decimal")
        if (
            not isinstance(item.source_scale, Decimal)
            or not item.source_scale.is_finite()
            or item.source_scale <= 0
        ):
            _fail_edgartools_persistence("validated filing source scale must be a positive Decimal")
        if (
            _SHA256_PATTERN.fullmatch(item.evidence_sha256) is None
            or item.evidence_byte_length <= 0
        ):
            _fail_edgartools_persistence("validated filing evidence identity is invalid")
        if item.evidence_location != f"content-sha256://{item.evidence_sha256}":
            _fail_edgartools_persistence(
                "validated filing retention location does not match its SHA-256"
            )
        if item.evidence_retrieved_at.tzinfo is None:
            _fail_edgartools_persistence("validated filing retrieval time must be timezone-aware")
        if item.acceptance_timestamp.tzinfo is None:
            _fail_edgartools_persistence("filing acceptance time must be timezone-aware")
        if item.edgartools_version != "5.48.0":
            _fail_edgartools_persistence("validated filing edgartools version is not pinned")
        if item.source_sign not in {None, "-"}:
            _fail_edgartools_persistence("validated filing source sign is invalid")
        if item.source_precision is not None and not item.source_precision.strip():
            _fail_edgartools_persistence("validated filing source precision is blank")
        if item.presentation_sign not in {"NEGATIVE", "ZERO", "POSITIVE"}:
            _fail_edgartools_persistence("validated filing presentation sign is invalid")
        if item.source_object_count < 1 or len(item.source_locators) != item.source_object_count:
            _fail_edgartools_persistence("validated filing source-object lineage is incomplete")
        if not item.primary_sequence.isdigit() or int(item.primary_sequence) < 1:
            _fail_edgartools_persistence("primary document sequence is invalid")
        source_sequence = item.source_sequence or item.primary_sequence
        if not source_sequence.isdigit() or int(source_sequence) < 1:
            _fail_edgartools_persistence("source document sequence is invalid")
        source_document = item.source_document or item.primary_document
        if item.source_is_primary != (source_document == item.primary_document):
            _fail_edgartools_persistence(
                "source and primary document classification is inconsistent"
            )
        if not item.primary_source_url and source_document != item.primary_document:
            _fail_edgartools_persistence("non-primary source requires the primary document URL")
        if not item.source_element_ids:
            _fail_edgartools_persistence("validated filing requires at least one source element")
        if item.amendment != (item.revision_of_accession is not None):
            _fail_edgartools_persistence(
                "amendment lineage must identify exactly one prior accession"
            )
        if item.amendment != item.form.endswith("/A"):
            _fail_edgartools_persistence("filing amendment flag and form suffix disagree")
        evidence_pair = (item.evidence_representation, item.evidence_capture_method)
        live_pair = (
            "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8",
            "edgartools_attachment_text_utf8",
        )
        replay_pair = (
            "BOUNDED_DERIVED_REPLAY_EXCERPT",
            "offline_bounded_xbrl_replay_excerpt",
        )
        if evidence_pair not in {live_pair, replay_pair}:
            _fail_edgartools_persistence(
                "validated filing evidence representation and capture method are not approved"
            )
        if evidence_pair == replay_pair and (
            _SHA256_PATTERN.fullmatch(item.original_evidence_sha256) is None
            or item.original_evidence_byte_length <= 0
            or item.original_evidence_sha256 == item.evidence_sha256
            or item.original_evidence_representation != live_pair[0]
            or item.original_evidence_capture_method != live_pair[1]
            or not item.original_source_locators
        ):
            _fail_edgartools_persistence(
                "bounded replay evidence lacks distinct original-document lineage"
            )
        if item.fiscal_quarter not in {"FY", "Q1", "Q2", "Q3", "Q4"}:
            _fail_edgartools_persistence(
                "validated filing fiscal quarter must be FY or Q1 through Q4"
            )
        if item.fiscal_year < date.min.year:
            _fail_edgartools_persistence("validated filing fiscal year is invalid")
        if item.period_type.value == "instant" and item.period_start is not None:
            _fail_edgartools_persistence("instant filing fact cannot have a period start")
        if item.period_type.value == "duration" and (
            item.period_start is None or item.period_start > item.report_period
        ):
            _fail_edgartools_persistence("duration filing fact requires an ordered period")


def _financial_metric_definition(
    item: ValidatedFiling,
    registry: FinancialFieldRegistry | None,
) -> FinancialMetricDefinition:
    """Resolve issuer-neutral metadata independently of batch or issuer order."""
    definition = (
        registry.metric_definition(item.field_id, item.metric_version)
        if registry is not None
        else None
    )
    if definition is not None:
        return definition
    if registry is not None and registry.metric_definitions:
        _fail_edgartools_persistence(
            "validated filing lacks governed issuer-neutral metric metadata"
        )
    return FinancialMetricDefinition(
        metric_id=item.field_id,
        semantic_version=item.metric_version,
        display_name=_metric_display_name(item.field_id),
        category="core_financial",
        business_meaning=("Consolidated period-end total assets reported by the SEC registrant."),
        grain="reporting entity, reporting scope, fiscal period",
        unit="USD",
    )


def _ensure_edgartools_structure(  # noqa: C901, PLR0912, PLR0913, PLR0915
    session: Session,
    item: ValidatedFiling,
    *,
    company: EdgarToolsCompanyIdentity | None = None,
    permitted_scopes: set[str] | None = None,
    metric_definition: FinancialMetricDefinition,
    universe_version: str,
) -> None:
    identity = company or _legacy_company_identities()[item.company_id]
    legal_name = identity.legal_name
    ticker = identity.ticker
    classification = identity.classification
    cik = identity.cik
    entity_id = identity.reporting_entity_id
    company_row = session.get(Company, item.company_id)
    if company_row is None:
        session.add(
            Company(
                id=item.company_id,
                legal_name=legal_name,
                ticker=ticker,
                classification=classification,
                universe_version=universe_version,
                active=True,
            )
        )
    else:
        if (
            company_row.ticker != ticker
            or company_row.legal_name != legal_name
            or company_row.classification != classification
            or not company_row.active
        ):
            _fail_edgartools_persistence(
                "existing company identity conflicts with the governed registrant"
            )
        if company_row.universe_version != universe_version:
            has_phase5_publication = bool(
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.reporting_entity_id == entity_id,
                        MetricObservation.methodology == _EDGARTOOLS_METHOD,
                    )
                )
            )
            if (
                company_row.universe_version in _LEGACY_UNIVERSE_VERSIONS
                and universe_version != _FINANCIAL_MAPPING_VERSION
                and not has_phase5_publication
            ):
                # An exact legacy identity may be explicitly promoted once during
                # Phase 5 onboarding. Subsequent reruns must match Phase 5 exactly.
                company_row.universe_version = universe_version
            else:
                _fail_edgartools_persistence(
                    "existing company universe version conflicts with the governed registry"
                )
    security_id = f"{item.company_id}:common"
    security = session.get(Security, security_id)
    if security is None:
        session.add(
            Security(
                id=security_id,
                company_id=item.company_id,
                ticker=ticker,
                exchange=identity.exchange,
                security_type=identity.security_type,
            )
        )
    elif (
        security.company_id != item.company_id
        or security.ticker != ticker
        or security.exchange != identity.exchange
        or security.security_type != identity.security_type
    ):
        _fail_edgartools_persistence(
            "existing security identity conflicts with the governed registrant"
        )
    reporting_entity = session.get(ReportingEntity, entity_id)
    if reporting_entity is None:
        session.add(
            ReportingEntity(
                id=entity_id,
                company_id=item.company_id,
                legal_name=legal_name,
                entity_type="SEC_REGISTRANT",
            )
        )
    elif (
        reporting_entity.company_id != item.company_id
        or reporting_entity.legal_name != legal_name
        or reporting_entity.entity_type != "SEC_REGISTRANT"
    ):
        _fail_edgartools_persistence(
            "existing reporting entity conflicts with the governed registrant"
        )
    session.flush()
    identifier_id = f"{entity_id}:cik"
    identifier = session.get(EntityIdentifier, identifier_id)
    if identifier is None:
        session.add(
            EntityIdentifier(
                id=identifier_id,
                reporting_entity_id=entity_id,
                scheme="SEC_CIK",
                value=cik,
                valid_from=date(1900, 1, 1),
                valid_to=None,
            )
        )
    elif (
        identifier.reporting_entity_id != entity_id
        or identifier.scheme != "SEC_CIK"
        or identifier.value != cik
        or identifier.valid_from != date(1900, 1, 1)
        or identifier.valid_to is not None
    ):
        _fail_edgartools_persistence("existing CIK conflicts with the governed registrant")
    reporting_scope = session.get(ReportingScope, item.reporting_scope_id)
    if reporting_scope is None:
        session.add(
            ReportingScope(
                id=item.reporting_scope_id,
                reporting_entity_id=entity_id,
                name=item.reporting_scope_name,
                portfolio_population=item.portfolio_population,
                methodology=item.scope_methodology,
            )
        )
    elif (
        reporting_scope.reporting_entity_id != entity_id
        or reporting_scope.name != item.reporting_scope_name
        or reporting_scope.portfolio_population != item.portfolio_population
        or reporting_scope.methodology != item.scope_methodology
    ):
        _fail_edgartools_persistence("existing scope conflicts with governed semantics")
    fiscal_id = f"{entity_id}:calendar"
    if session.get(FiscalCalendarRegime, fiscal_id) is None:
        session.add(
            FiscalCalendarRegime(
                id=fiscal_id,
                reporting_entity_id=entity_id,
                fiscal_year_end_month=12,
                fiscal_year_end_day=31,
                effective_from=date(1900, 1, 1),
                effective_to=None,
            )
        )
    policy_id = f"{entity_id}:us-gaap"
    if session.get(AccountingPolicyRegime, policy_id) is None:
        session.add(
            AccountingPolicyRegime(
                id=policy_id,
                reporting_entity_id=entity_id,
                policy_name="US_GAAP_ISSUER_REPORTED",
                description="Consolidated issuer-reported US GAAP financial statements.",
                effective_from=date(1900, 1, 1),
                effective_to=None,
            )
        )
    definition = session.get(MetricDefinition, item.field_id)
    if definition is None:
        session.add(
            MetricDefinition(
                id=item.field_id,
                display_name=metric_definition.display_name,
                category=metric_definition.category,
            )
        )
    elif (
        definition.display_name != metric_definition.display_name
        or definition.category != metric_definition.category
    ):
        _fail_edgartools_persistence(
            "existing metric definition conflicts with governed issuer-neutral metadata"
        )
    session.flush()
    metric_version_id = _metric_version_id(item)
    metric_version = session.get(MetricDefinitionVersion, metric_version_id)
    allowed_scopes = sorted(permitted_scopes or {item.reporting_scope_id})
    if metric_version is None:
        session.add(
            MetricDefinitionVersion(
                id=metric_version_id,
                metric_id=item.field_id,
                semantic_version=item.metric_version,
                business_meaning=metric_definition.business_meaning,
                grain=metric_definition.grain,
                unit=metric_definition.unit,
                permitted_scopes=allowed_scopes,
                rules={
                    "mapping_version": universe_version,
                    "publication_source": _EDGARTOOLS_METHOD,
                },
                effective_from=date(1900, 1, 1),
                effective_to=None,
            )
        )
    else:
        if (
            metric_version.metric_id != item.field_id
            or metric_version.semantic_version != item.metric_version
            or metric_version.business_meaning != metric_definition.business_meaning
            or metric_version.grain != metric_definition.grain
            or metric_version.unit != metric_definition.unit
        ):
            _fail_edgartools_persistence(
                "existing metric version conflicts with governed issuer-neutral metadata"
            )
        existing_mapping_version = metric_version.rules.get("mapping_version")
        if existing_mapping_version not in {None, universe_version}:
            _fail_edgartools_persistence(
                "existing metric version mapping contract conflicts with the governed registry"
            )
        if existing_mapping_version is None:
            metric_version.rules = {
                **metric_version.rules,
                "mapping_version": universe_version,
            }
        combined_scopes = sorted(set(metric_version.permitted_scopes) | set(allowed_scopes))
        if combined_scopes != metric_version.permitted_scopes:
            metric_version.permitted_scopes = combined_scopes


def _edgartools_run(
    session: Session,
    results: tuple[ValidatedFiling, ...],
    *,
    started_at: datetime,
) -> tuple[PipelineRun, bool]:
    run_digest = _stable_hash(
        [
            {
                "case_id": item.case_id,
                "accession": item.accession_number,
                "evidence_sha256": item.evidence_sha256,
                "mapping_version": item.mapping_version,
                "mapping_id": item.mapping_id,
            }
            for item in results
        ]
    )
    run_id = f"pipeline:edgartools:{run_digest[:48]}"
    existing = session.get(PipelineRun, run_id)
    if existing is not None:
        return existing, False
    company_ids = sorted({item.company_id for item in results})
    run = PipelineRun(
        id=run_id,
        run_key=f"edgartools-financial-sync:{run_digest}",
        status="PUBLISHING",
        thread_id=f"edgartools:{run_digest[:32]}",
        started_at=started_at,
        completed_at=None,
        error_count=0,
        retry_count=0,
        requested_company_id=company_ids[0] if len(company_ids) == 1 else None,
        requested_periods=sorted({item.report_period.isoformat() for item in results}),
        code_version="edgartools-financial-sync-v1",
        config_version=results[0].mapping_version,
        parser_version="inline-xbrl-selected-fields-v1",
        terminal_outcomes={},
    )
    session.add(run)
    session.flush()
    return run, True


def _metric_version_id(item: ValidatedFiling) -> str:
    """Return the stable metric-version key carried by the reviewed mapping."""
    return f"{item.field_id}:{item.metric_version}"


def _persist_validated_filing(
    session: Session,
    item: ValidatedFiling,
    *,
    run: PipelineRun,
    knowledge_at: datetime,
    counts: dict[str, int],
) -> CommittedCaseState:
    evidence_id = _edgartools_evidence(session, item, run=run, counts=counts)
    filing = _edgartools_filing(session, item, counts=counts)
    _edgartools_document(session, item, filing=filing, evidence_id=evidence_id, counts=counts)
    _edgartools_raw_fact(session, item, filing=filing, evidence_id=evidence_id, counts=counts)
    return _edgartools_observation(
        session,
        item,
        run=run,
        evidence_id=evidence_id,
        knowledge_at=knowledge_at,
        counts=counts,
    )


def _edgartools_evidence(
    session: Session,
    item: ValidatedFiling,
    *,
    run: PipelineRun,
    counts: dict[str, int],
) -> str:
    is_replay = item.evidence_representation == "BOUNDED_DERIVED_REPLAY_EXCERPT"
    source_class = (
        "SEC_XBRL_BOUNDED_REPLAY_EXCERPT" if is_replay else "SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS"
    )
    existing = session.scalar(
        select(SourceEvidence).where(
            SourceEvidence.content_sha256 == item.evidence_sha256,
            SourceEvidence.byte_length == item.evidence_byte_length,
        )
    )
    if existing is not None:
        exact_identity = (
            existing.source_class == source_class
            and existing.original_url == item.source_url
            and existing.representation == item.evidence_representation
            and existing.capture_method == item.evidence_capture_method
            and existing.retention_location == item.evidence_location
            and existing.media_type == item.evidence_media_type
            and existing.source_tool_version == item.edgartools_version
        )
        if not exact_identity:
            _fail_edgartools_persistence(
                "existing evidence hash has incompatible source and media identity"
            )
        return existing.id
    evidence_id = f"evidence:edgartools:{item.evidence_sha256}"
    session.add(
        SourceEvidence(
            id=evidence_id,
            source_class=source_class,
            original_url=item.source_url,
            retrieved_at=item.evidence_retrieved_at,
            published_at=datetime.combine(item.filing_date, time.min, tzinfo=UTC),
            accession_or_identifier=item.accession_number,
            content_sha256=item.evidence_sha256,
            byte_length=item.evidence_byte_length,
            media_type=item.evidence_media_type,
            representation=item.evidence_representation,
            capture_method=item.evidence_capture_method,
            parser_version="inline-xbrl-selected-fields-v1",
            source_tool_version=item.edgartools_version,
            acquisition_run_id=run.id,
            reporting_entity_candidate=item.reporting_entity_id,
            reporting_period_candidate=item.report_period.isoformat(),
            retention_location=item.evidence_location,
            bounded_excerpt=(
                "Tracked bounded derived XBRL replay excerpt; not the original SEC document."
                if is_replay
                else "Retained edgartools filing document; raw body omitted."
            ),
            response_status=None,
            etag=None,
            last_modified=None,
        )
    )
    counts["evidence"] += 1
    return evidence_id


def _filing_id(accession: str) -> str:
    return f"filing:edgartools:{hashlib.sha256(accession.encode()).hexdigest()[:48]}"


def _edgartools_filing(
    session: Session,
    item: ValidatedFiling,
    *,
    counts: dict[str, int],
) -> Filing:
    existing = session.scalar(select(Filing).where(Filing.accession == item.accession_number))
    if existing is not None:
        expected_amendment_id = None
        if item.revision_of_accession is not None:
            expected_prior = session.scalar(
                select(Filing).where(Filing.accession == item.revision_of_accession)
            )
            if expected_prior is None:
                _fail_edgartools_persistence("amendment references an unknown prior accession")
            expected_amendment_id = expected_prior.id
        if (
            existing.reporting_entity_id != item.reporting_entity_id
            or existing.form_type != item.form
            or existing.filed_at.date() != item.filing_date
            or _as_utc(existing.acceptance_timestamp) != _as_utc(item.acceptance_timestamp)
            or existing.period_end != item.report_period
            or existing.amendment_of_id != expected_amendment_id
        ):
            _fail_edgartools_persistence(
                "existing filing identity conflicts with validated metadata"
            )
        return existing
    amendment_of_id = None
    if item.revision_of_accession is not None:
        prior = session.scalar(select(Filing).where(Filing.accession == item.revision_of_accession))
        if prior is None:
            _fail_edgartools_persistence("amendment references an unknown prior accession")
        amendment_of_id = prior.id
    filing = Filing(
        id=_filing_id(item.accession_number),
        reporting_entity_id=item.reporting_entity_id,
        form_type=item.form,
        accession=item.accession_number,
        filed_at=datetime.combine(item.filing_date, time.min, tzinfo=UTC),
        acceptance_timestamp=item.acceptance_timestamp,
        period_end=item.report_period,
        amendment_of_id=amendment_of_id,
    )
    session.add(filing)
    session.flush()
    counts["filings"] += 1
    return filing


def _edgartools_document(
    session: Session,
    item: ValidatedFiling,
    *,
    filing: Filing,
    evidence_id: str,
    counts: dict[str, int],
) -> None:
    source_document = item.source_document or item.primary_document
    source_sequence = item.source_sequence or item.primary_sequence
    source_document_type = item.source_document_type or item.primary_document_type
    source_description = item.source_description or item.primary_description
    primary_source_url = item.primary_source_url or item.source_url

    _ensure_edgartools_document_row(
        session,
        filing=filing,
        accession=item.accession_number,
        filename=item.primary_document,
        source_url=primary_source_url,
        sequence=int(item.primary_sequence),
        document_type=item.primary_document_type,
        description=item.primary_description,
        is_primary=True,
        evidence_id=evidence_id if item.source_is_primary else None,
        counts=counts,
    )
    if source_document != item.primary_document:
        _ensure_edgartools_document_row(
            session,
            filing=filing,
            accession=item.accession_number,
            filename=source_document,
            source_url=item.source_url,
            sequence=int(source_sequence),
            document_type=source_document_type,
            description=source_description,
            is_primary=item.source_is_primary,
            evidence_id=evidence_id,
            counts=counts,
        )


def _ensure_edgartools_document_row(  # noqa: PLR0913
    session: Session,
    *,
    filing: Filing,
    accession: str,
    filename: str,
    source_url: str,
    sequence: int,
    document_type: str,
    description: str,
    is_primary: bool,
    evidence_id: str | None,
    counts: dict[str, int],
) -> None:
    digest = hashlib.sha256(f"{accession}|{filename}".encode()).hexdigest()
    document_id = f"document:edgartools:{digest[:47]}"
    existing = session.get(FilingDocument, document_id)
    if existing is not None:
        if (
            existing.filing_id != filing.id
            or existing.filename != filename
            or existing.source_url != source_url
            or existing.document_type != document_type
            or existing.sequence != sequence
            or existing.description != description
            or existing.source_evidence_id != evidence_id
            or existing.is_primary is not is_primary
        ):
            _fail_edgartools_persistence(
                "filing document identity conflicts with validated metadata"
            )
        return
    session.add(
        FilingDocument(
            id=document_id,
            filing_id=filing.id,
            sequence=sequence,
            document_type=document_type,
            filename=filename,
            source_url=source_url,
            source_evidence_id=evidence_id,
            description=description,
            is_primary=is_primary,
        )
    )
    counts["documents"] += 1


def _edgartools_raw_fact(
    session: Session,
    item: ValidatedFiling,
    *,
    filing: Filing,
    evidence_id: str,
    counts: dict[str, int],
) -> None:
    taxonomy, separator, concept = item.qualified_concept.partition(":")
    if not separator or not taxonomy or not concept:
        _fail_edgartools_persistence("qualified XBRL concept must contain taxonomy and concept")
    digest = _stable_hash(
        {
            "accession": item.accession_number,
            "evidence": item.evidence_sha256,
            "concept": item.qualified_concept,
            "context": item.context_ref,
            "raw": item.raw_display_string,
            "elements": item.source_element_ids,
        }
    )
    fact_id = f"raw-xbrl:edgartools:{digest[:47]}"
    existing = session.get(RawXbrlFact, fact_id)
    if existing is not None:
        if (
            existing.source_sign != item.source_sign
            or existing.source_precision != item.source_precision
            or existing.presentation_sign != item.presentation_sign
        ):
            _fail_edgartools_persistence("existing raw fact sign or precision conflicts")
        return
    session.add(
        RawXbrlFact(
            id=fact_id,
            evidence_id=evidence_id,
            filing_id=filing.id,
            concept=concept,
            taxonomy=taxonomy,
            entity_identifier=item.cik,
            context_ref=item.context_ref,
            raw_value=item.raw_display_string,
            unit_ref=item.unit,
            decimals=None if item.decimals is None else str(item.decimals),
            scale=item.source_scale,
            source_sign=item.source_sign,
            source_precision=item.source_precision,
            presentation_sign=item.presentation_sign,
            period_type=item.period_type.value,
            period_start=item.period_start,
            period_end=item.report_period,
            instant=(item.report_period if item.period_type.value == "instant" else None),
            dimensions=dict(item.dimensions),
            methodology=_EDGARTOOLS_METHOD,
        )
    )
    counts["raw_facts"] += 1


def _edgartools_semantic_digest(item: ValidatedFiling) -> str:
    return _stable_hash(
        {
            "metric_version_id": _metric_version_id(item),
            "reporting_entity_id": item.reporting_entity_id,
            "reporting_scope_id": item.reporting_scope_id,
            "period_start": (None if item.period_start is None else item.period_start.isoformat()),
            "period_end": item.report_period.isoformat(),
            "period_type": item.period_type.value,
            "fiscal_calendar_regime_id": f"{item.reporting_entity_id}:calendar",
            "accounting_policy_regime_id": f"{item.reporting_entity_id}:us-gaap",
            "observation_state": ObservationState.REPORTED_ACTUAL.value,
            "methodology": _EDGARTOOLS_METHOD,
            "currency": "USD",
            "unit": item.unit,
            "scale": "1",
            "dimensions": dict(item.dimensions),
        }
    )


def _observation_accession(observation: MetricObservation) -> str | None:
    accession = observation.parser_metadata.get("accession_number")
    return accession if isinstance(accession, str) else None


def _edgartools_observation(  # noqa: PLR0913
    session: Session,
    item: ValidatedFiling,
    *,
    run: PipelineRun,
    evidence_id: str,
    knowledge_at: datetime,
    counts: dict[str, int],
) -> CommittedCaseState:
    semantic_digest = _edgartools_semantic_digest(item)
    observation_digest = _stable_hash(
        {
            "case_id": item.case_id,
            "accession": item.accession_number,
            "evidence": item.evidence_sha256,
            "semantic": semantic_digest,
        }
    )
    observation_id = f"observation:edgartools:{observation_digest[:48]}"
    if session.get(MetricObservation, observation_id) is not None:
        return CommittedCaseState.UNCHANGED
    active = session.scalars(
        select(MetricObservation)
        .where(
            MetricObservation.semantic_key_digest == semantic_digest,
            MetricObservation.knowledge_to.is_(None),
            MetricObservation.publication_state == PublicationState.PUBLISHED.value,
        )
        .order_by(MetricObservation.revision_number.desc(), MetricObservation.id)
    ).all()
    if len(active) > 1:
        _fail_edgartools_persistence("more than one active observation exists for a semantic key")
    prior = active[0] if active else None
    if prior is not None and not item.amendment:
        if prior.value == item.normalized_value:
            linked = _link_edgartools_evidence(
                session,
                observation=prior,
                item=item,
                evidence_id=evidence_id,
                counts=counts,
            )
            return CommittedCaseState.LINKED if linked else CommittedCaseState.UNCHANGED
        quarantined = _quarantine_edgartools_conflict(
            session,
            item,
            run=run,
            evidence_id=evidence_id,
            prior=prior,
            counts=counts,
        )
        return CommittedCaseState.QUARANTINED if quarantined else CommittedCaseState.UNCHANGED
    if prior is not None and item.revision_of_accession is not None:
        prior_accession = _observation_accession(prior)
        if prior_accession != item.revision_of_accession:
            _fail_edgartools_persistence("amendment does not identify the active prior observation")
        prior.knowledge_to = knowledge_at
        prior.publication_state = PublicationState.SUPERSEDED.value
    elif item.amendment:
        _fail_edgartools_persistence("amendment has no active prior observation to supersede")

    observation = MetricObservation(
        id=observation_id,
        metric_version_id=_metric_version_id(item),
        reporting_entity_id=item.reporting_entity_id,
        reporting_scope_id=item.reporting_scope_id,
        fiscal_calendar_regime_id=f"{item.reporting_entity_id}:calendar",
        accounting_policy_regime_id=f"{item.reporting_entity_id}:us-gaap",
        period_start=item.period_start,
        period_end=item.report_period,
        fiscal_year=item.fiscal_year,
        fiscal_quarter=0 if item.fiscal_quarter == "FY" else int(item.fiscal_quarter[1:]),
        period_type=item.period_type.value,
        value=item.normalized_value,
        currency="USD",
        unit=item.unit,
        scale="1",
        reported_decimals=_reported_decimals(item.decimals),
        reported_precision=item.source_precision or "ABSENT_IN_SOURCE",
        observation_state=ObservationState.REPORTED_ACTUAL.value,
        methodology=_EDGARTOOLS_METHOD,
        dimensions=dict(item.dimensions),
        evidence_locator=item.source_locators[0],
        extraction_method="deterministic_inline_xbrl",
        parser_metadata={
            "case_id": item.case_id,
            "mapping_version": item.mapping_version,
            "mapping_id": item.mapping_id,
            "metric_version": item.metric_version,
            "classification": item.classification.value,
            "reporting_scope_category": item.reporting_scope_category,
            "reporting_scope_name": item.reporting_scope_name,
            "portfolio_population": item.portfolio_population,
            "scope_methodology": item.scope_methodology,
            "accession_number": item.accession_number,
            "form": item.form,
            "fiscal_year": item.fiscal_year,
            "fiscal_quarter": item.fiscal_quarter,
            "amendment": item.amendment,
            "revision_of_accession": item.revision_of_accession,
            "primary_document": item.primary_document,
            "primary_sequence": item.primary_sequence,
            "primary_document_type": item.primary_document_type,
            "primary_description": item.primary_description,
            "primary_source_url": item.primary_source_url or item.source_url,
            "source_document": item.source_document or item.primary_document,
            "source_sequence": item.source_sequence or item.primary_sequence,
            "source_document_type": (item.source_document_type or item.primary_document_type),
            "source_description": item.source_description or item.primary_description,
            "source_is_primary": item.source_is_primary,
            "qualified_concept": item.qualified_concept,
            "context_ref": item.context_ref,
            "source_scale": str(item.source_scale),
            "source_sign": item.source_sign,
            "source_precision": item.source_precision,
            "presentation_sign": item.presentation_sign,
            "source_element_ids": list(item.source_element_ids),
            "source_object_count": item.source_object_count,
            "source_locators": list(item.source_locators),
            "period_type": item.period_type.value,
            "period_start": (None if item.period_start is None else item.period_start.isoformat()),
            "dimensions": dict(item.dimensions),
            "evidence_sha256": item.evidence_sha256,
            "evidence_representation": item.evidence_representation,
            "evidence_capture_method": item.evidence_capture_method,
            "original_evidence_sha256": (item.original_evidence_sha256 or item.evidence_sha256),
            "original_evidence_byte_length": (
                item.original_evidence_byte_length or item.evidence_byte_length
            ),
            "original_evidence_representation": (
                item.original_evidence_representation or item.evidence_representation
            ),
            "original_evidence_capture_method": (
                item.original_evidence_capture_method or item.evidence_capture_method
            ),
            "original_source_locators": list(item.original_source_locators or item.source_locators),
            "evidence_is_bounded_replay_excerpt": (
                item.evidence_representation == "BOUNDED_DERIVED_REPLAY_EXCERPT"
            ),
        },
        validation_summary="Exact approved golden filing fact and SHA-256 lineage validated.",
        publication_state=PublicationState.PUBLISHED.value,
        revision_number=1 if prior is None else prior.revision_number + 1,
        semantic_key_digest=semantic_digest,
        valid_from=item.report_period,
        valid_to=None,
        knowledge_from=knowledge_at,
        knowledge_to=None,
        supersedes_observation_id=None if prior is None else prior.id,
        quality_state=QualityState.VALIDATED.value,
        reported_label=item.original_label,
        reported_value=item.raw_display_string,
        published_at=knowledge_at,
    )
    session.add(observation)
    session.flush()
    _link_edgartools_evidence(
        session,
        observation=observation,
        item=item,
        evidence_id=evidence_id,
        counts=counts,
        count_link=False,
    )
    revision_id = f"revision:edgartools:{observation_digest[:51]}"
    session.add(
        ObservationRevision(
            id=revision_id,
            observation_id=observation.id,
            prior_observation_id=None if prior is None else prior.id,
            reason=(
                "initial edgartools financial publication"
                if prior is None
                else "SEC amendment created an immutable successor revision"
            ),
            created_at=knowledge_at,
        )
    )
    counts["observations"] += 1
    counts["revisions"] += 1
    return CommittedCaseState.PUBLISHED


def _reported_decimals(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _link_edgartools_evidence(  # noqa: PLR0913
    session: Session,
    *,
    observation: MetricObservation,
    item: ValidatedFiling,
    evidence_id: str,
    counts: dict[str, int],
    count_link: bool = True,
) -> bool:
    if session.get(ObservationEvidence, (observation.id, evidence_id)) is not None:
        return False
    session.add(
        ObservationEvidence(
            observation_id=observation.id,
            evidence_id=evidence_id,
            evidence_role="primary" if observation.revision_number == 1 else "revision",
            locator=item.source_locators[0],
            raw_label=item.original_label,
            raw_value=item.raw_display_string,
            disclosed_unit=item.unit,
            disclosed_scale=str(item.source_scale),
            extraction_method="deterministic_inline_xbrl",
            validation_status="VALIDATED",
        )
    )
    if count_link:
        counts["linked"] += 1
    return True


def _quarantine_edgartools_conflict(  # noqa: PLR0913
    session: Session,
    item: ValidatedFiling,
    *,
    run: PipelineRun,
    evidence_id: str,
    prior: MetricObservation,
    counts: dict[str, int],
) -> bool:
    digest = _stable_hash(
        {
            "semantic": prior.semantic_key_digest,
            "active": prior.id,
            "accession": item.accession_number,
            "evidence": item.evidence_sha256,
        }
    )
    candidate_id = f"quarantine:edgartools:{digest[:48]}"
    if session.get(QuarantineCandidate, candidate_id) is not None:
        return False
    session.add(
        QuarantineCandidate(
            id=candidate_id,
            pipeline_run_id=run.id,
            proposed_metric_id=item.field_id,
            raw_source_label=item.original_label,
            raw_value=item.raw_display_string,
            proposed_normalized_value=item.normalized_value,
            unit=item.unit,
            scale=str(item.source_scale),
            period_end=item.report_period,
            reporting_entity_id=item.reporting_entity_id,
            reporting_scope_id=item.reporting_scope_id,
            methodology=_EDGARTOOLS_METHOD,
            evidence_id=evidence_id,
            evidence_locator=item.source_locators[0],
            bounded_excerpt="Conflicting exact fact retained; raw filing body omitted.",
            confidence=Decimal("1.0000"),
            conflicts_and_uncertainties=[
                "OVERLAPPING_FACT_CONFLICT",
                f"active_observation:{prior.id}",
                f"candidate_accession:{item.accession_number}",
            ],
            model_and_prompt_version=None,
            status="OVERLAPPING_FACT_CONFLICT",
        )
    )
    counts["quarantined"] += 1
    return True


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
    dimensions: dict[str, str]
    reporting_entity_id: str
    reporting_scope_id: str
    fiscal_calendar_regime_id: str
    accounting_policy_regime_id: str
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
    derivation_inputs: tuple[dict[str, object], ...] = ()
    evidence_links: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible exact-value representation."""
        payload = asdict(self)
        payload["revision_history"] = list(self.revision_history)
        payload["derivation_inputs"] = list(self.derivation_inputs)
        payload["evidence_links"] = list(self.evidence_links)
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


def _canonical_run_value(value: object) -> object:  # noqa: PLR0911
    """Convert typed dataset/config material into stable JSON primitives."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "__dataclass_fields__"):
        return _canonical_run_value(asdict(cast("Any", value)))
    if isinstance(value, dict):
        return {
            str(key): _canonical_run_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple | list):
        return [_canonical_run_value(item) for item in value]
    return value


def _metric_display_name(metric_id: str) -> str:
    return metric_id.replace("_", " ").title().replace("Msr", "MSR").replace("Upb", "UPB")


def _seed_universe(  # noqa: C901, PLR0912
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
            configured_population = company.get("portfolio_population")
            if configured_population is None:
                configured_population = {
                    "bank": "residential_servicing_for_others_and_bank_owned",
                    "nonbank": "owned_msr_subservicing_and_held_for_sale",
                }.get(str(company["classification"]))
            population = str(configured_population or f"explicit_issuer_scope:{scope_id}")
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
            session.add(
                AccountingPolicyRegime(
                    id=f"{entity_id}:us-gaap",
                    reporting_entity_id=entity_id,
                    policy_name="US_GAAP_ISSUER_REPORTED",
                    description=(
                        "Issuer-reported US GAAP accounting and valuation policies; "
                        "metric-specific methodology remains attached to each observation."
                    ),
                    effective_from=date(1900, 1, 1),
                    effective_to=None,
                )
            )
            inserted += 1

        session.flush()
        if session.get(FiscalCalendarRegime, f"{entity_id}:calendar") is None:
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
        if session.get(AccountingPolicyRegime, f"{entity_id}:us-gaap") is None:
            session.add(
                AccountingPolicyRegime(
                    id=f"{entity_id}:us-gaap",
                    reporting_entity_id=entity_id,
                    policy_name="US_GAAP_ISSUER_REPORTED",
                    description="Issuer-reported US GAAP accounting and valuation policies.",
                    effective_from=date(1900, 1, 1),
                    effective_to=None,
                )
            )

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
            session.flush()
            if session.get(FiscalCalendarRegime, f"{regulatory_entity_id}:calendar") is None:
                session.add(
                    FiscalCalendarRegime(
                        id=f"{regulatory_entity_id}:calendar",
                        reporting_entity_id=regulatory_entity_id,
                        fiscal_year_end_month=12,
                        fiscal_year_end_day=31,
                        effective_from=date(1900, 1, 1),
                        effective_to=None,
                    )
                )
            if (
                session.get(AccountingPolicyRegime, f"{regulatory_entity_id}:regulatory-gaap")
                is None
            ):
                session.add(
                    AccountingPolicyRegime(
                        id=f"{regulatory_entity_id}:regulatory-gaap",
                        reporting_entity_id=regulatory_entity_id,
                        policy_name="REGULATORY_REPORTING_BASIS",
                        description="Native reporter-scoped bank regulatory reporting basis.",
                        effective_from=date(1900, 1, 1),
                        effective_to=None,
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
        definition = session.get(MetricDefinition, metric_id)
        if definition is None:
            session.add(
                MetricDefinition(
                    id=metric_id,
                    display_name=_metric_display_name(metric_id),
                    category=str(metric["category"]),
                )
            )
        existing_version = session.get(MetricDefinitionVersion, version_id)
        if existing_version is not None:
            lifecycle = metric.get("lifecycle")
            if lifecycle is not None and existing_version.rules.get("lifecycle") != lifecycle:
                existing_version.rules = {**existing_version.rules, "lifecycle": lifecycle}
            continue
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
                business_meaning=str(metric.get("business_meaning", metric.get("definition"))),
                grain=str(metric.get("grain", "reporting entity, reporting scope, fiscal period")),
                unit=str(metric["unit"]),
                permitted_scopes=list(
                    metric.get("permitted_reporting_scopes", ["explicit issuer-disclosed scope"])
                ),
                rules=rules,
                effective_from=date.fromisoformat(str(metric.get("effective_from", "2025-07-01"))),
                effective_to=None,
            )
        )
        for index, label in enumerate(metric.get("source_labels_and_aliases", [])):
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
            msg = "idempotent run already belongs to a different runtime thread"
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
    if not configured_metric_ids:
        msg = "eligible-source assessment must configure at least one metric"
        raise ValueError(msg)
    unknown_metrics = sorted(set(configured_metric_ids) - set(catalog_metric_ids))
    if unknown_metrics:
        msg = f"eligible-source assessment references unknown metrics: {unknown_metrics}"
        raise ValueError(msg)
    metric_versions = {
        str(metric["id"]): str(metric.get("semantic_version", "1.0.0")) for metric in metrics
    }
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
                        metric_version_id=f"{metric_id}:{metric_versions[metric_id]}",
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


def _observation_regimes(
    session: Session,
    *,
    reporting_entity_id: str,
    period_end: date,
) -> tuple[str, str]:
    """Resolve the single effective fiscal and accounting regimes fail-closed."""
    calendars = session.scalars(
        select(FiscalCalendarRegime).where(
            FiscalCalendarRegime.reporting_entity_id == reporting_entity_id,
            FiscalCalendarRegime.effective_from <= period_end,
            or_(
                FiscalCalendarRegime.effective_to.is_(None),
                FiscalCalendarRegime.effective_to > period_end,
            ),
        )
    ).all()
    policies = session.scalars(
        select(AccountingPolicyRegime).where(
            AccountingPolicyRegime.reporting_entity_id == reporting_entity_id,
            AccountingPolicyRegime.effective_from <= period_end,
            or_(
                AccountingPolicyRegime.effective_to.is_(None),
                AccountingPolicyRegime.effective_to > period_end,
            ),
        )
    ).all()
    if len(calendars) != 1 or len(policies) != 1:
        msg = (
            "observation requires exactly one effective fiscal and accounting regime: "
            f"{reporting_entity_id}:{period_end.isoformat()}"
        )
        raise ValueError(msg)
    return calendars[0].id, policies[0].id


def _missing_semantic_digest(  # noqa: PLR0913
    *,
    metric_version_id: str,
    entity_id: str,
    scope_id: str,
    period_end: str,
    period_start: str | None = None,
    period_type: str = "duration",
    fiscal_calendar_regime_id: str = "legacy-unspecified",
    accounting_policy_regime_id: str = "legacy-unspecified",
    currency: str | None = None,
    unit: str = "unknown",
    scale: str = "unknown",
    dimensions: dict[str, str] | None = None,
) -> str:
    return _stable_hash(
        {
            "metric_version_id": metric_version_id,
            "reporting_entity_id": entity_id,
            "reporting_scope_id": scope_id,
            "period_start": period_start,
            "period_end": period_end,
            "period_type": period_type,
            "fiscal_calendar_regime_id": fiscal_calendar_regime_id,
            "accounting_policy_regime_id": accounting_policy_regime_id,
            "observation_state": ObservationState.NOT_DISCLOSED.value,
            "methodology": "eligible_source_set_reviewed",
            "currency": currency,
            "unit": unit,
            "scale": scale,
            "dimensions": dimensions or {},
        }
    )


def _select_stage_a_candidates(
    candidates: tuple[ParsedObservationCandidate, ...],
) -> tuple[ParsedObservationCandidate, ...]:
    """Coalesce exact corroboration and fail closed on semantic conflicts."""
    if not candidates:
        return ()
    ordered = tuple(sorted(candidates, key=lambda item: (item.candidate_id, item.evidence_id)))
    signatures = {
        (
            item.semantic_key_digest,
            item.normalized_value,
            item.currency,
            item.unit,
            item.canonical_scale,
        )
        for item in ordered
    }
    if len(signatures) != 1:
        key = (ordered[0].company_id, ordered[0].period_end, ordered[0].metric_id)
        msg = f"conflicting Stage A candidates for configured cell: {key}"
        raise ValueError(msg)
    return ordered


def _seed_observations(  # noqa: C901, PLR0912, PLR0913, PLR0915
    session: Session,
    *,
    companies: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    quarters: list[dict[str, Any]],
    bundles: dict[str, _SourceBundle],
    run: PipelineRun,
    known_at: datetime,
) -> int:
    parsed_groups: dict[tuple[str, str, str], list[ParsedObservationCandidate]] = {}
    for bundle in bundles.values():
        for parsed_candidate in bundle.candidates:
            key = (
                parsed_candidate.company_id,
                parsed_candidate.period_end.isoformat(),
                parsed_candidate.metric_id,
            )
            parsed_groups.setdefault(key, []).append(parsed_candidate)
    parsed_by_key = {
        key: _select_stage_a_candidates(tuple(candidates))
        for key, candidates in parsed_groups.items()
    }
    bundle_by_evidence = {f"evidence:{key}": bundle for key, bundle in bundles.items()}
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
        for quarter in quarters:
            period_end_text = str(quarter["period_end"])
            for metric in metrics:
                metric_id = str(metric["id"])
                observation_id = f"observation:{company_id}:{period_end_text}:{metric_id}:v1"
                if session.get(MetricObservation, observation_id) is not None:
                    continue
                candidate_group = parsed_by_key.get((company_id, period_end_text, metric_id), ())
                candidate = candidate_group[0] if candidate_group else None
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
                    evidence_id = candidate.evidence_id
                    source_bundle = bundle_by_evidence.get(evidence_id)
                    if source_bundle is None:
                        msg = (
                            f"candidate evidence is outside the retained bundle set: {evidence_id}"
                        )
                        raise ValueError(msg)
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
                        "source_sha256": source_bundle.definition.content_sha256,
                        "normalization_exact": True,
                    }
                    validation_summary = validate_candidate(candidate).summary
                observation_period_end = date.fromisoformat(period_end_text)
                fiscal_regime_id, accounting_regime_id = _observation_regimes(
                    session,
                    reporting_entity_id=entity_id,
                    period_end=observation_period_end,
                )
                semantic_digest = _stable_hash(
                    {
                        "metric_version_id": metric_version_id,
                        "reporting_entity_id": entity_id,
                        "reporting_scope_id": scope_id,
                        "period_start": period_start.isoformat() if period_start else None,
                        "period_end": period_end_text,
                        "period_type": period_type,
                        "fiscal_calendar_regime_id": fiscal_regime_id,
                        "accounting_policy_regime_id": accounting_regime_id,
                        "observation_state": state,
                        "methodology": methodology,
                        "currency": currency,
                        "unit": unit,
                        "scale": scale,
                        "dimensions": {},
                    }
                )
                observation = MetricObservation(
                    id=observation_id,
                    metric_version_id=metric_version_id,
                    reporting_entity_id=entity_id,
                    reporting_scope_id=scope_id,
                    fiscal_calendar_regime_id=fiscal_regime_id,
                    accounting_policy_regime_id=accounting_regime_id,
                    period_start=period_start,
                    period_end=observation_period_end,
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
                    dimensions={},
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
                evidence_candidates = candidate_group if candidate is not None else ()
                if not evidence_candidates:
                    session.add(
                        ObservationEvidence(
                            observation_id=observation_id,
                            evidence_id=evidence_id,
                            evidence_role="reviewed_source",
                            locator=locator,
                            raw_label=raw_label,
                            raw_value=raw_value,
                            disclosed_unit=unit,
                            disclosed_scale=scale,
                            extraction_method=extraction_method,
                            validation_status=QualityState.VALIDATED.value,
                        )
                    )
                else:
                    seen_evidence: set[str] = set()
                    for index, evidence_candidate in enumerate(evidence_candidates):
                        if evidence_candidate.evidence_id in seen_evidence:
                            continue
                        seen_evidence.add(evidence_candidate.evidence_id)
                        session.add(
                            ObservationEvidence(
                                observation_id=observation_id,
                                evidence_id=evidence_candidate.evidence_id,
                                evidence_role="primary" if index == 0 else "corroborating",
                                locator=evidence_candidate.evidence_locator,
                                raw_label=evidence_candidate.raw_label,
                                raw_value=evidence_candidate.raw_value,
                                disclosed_unit=evidence_candidate.unit,
                                disclosed_scale=evidence_candidate.reported_scale,
                                extraction_method=evidence_candidate.extraction_method,
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


def _seed_comparability_assessments(
    session: Session,
    *,
    known_at: datetime,
    company_ids: Sequence[str],
) -> None:
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
    grouped: dict[tuple[str, str, date], dict[str, list[tuple[Any, str, str]]]] = {}
    for observation, company_id, metric_id, semantic_version, population in rows:
        grouped.setdefault((metric_id, semantic_version, observation.period_end), {}).setdefault(
            company_id, []
        ).append((observation, semantic_version, population))
    company_order = {company_id: index for index, company_id in enumerate(company_ids)}
    for (metric_id, _semantic_version, _period_end), by_company in grouped.items():
        ordered_company_ids = sorted(
            by_company,
            key=lambda company_id: (company_order.get(company_id, len(company_order)), company_id),
        )
        if len(ordered_company_ids) < _MIN_COMPARISON_COMPANY_COUNT:
            continue

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
                dimensions=tuple(sorted(observation.dimensions.items())),
                period_kind=observation.period_type,
                period_start=observation.period_start,
                period_end=observation.period_end,
                # Published observation values are normalized to canonical
                # units; retain the source/display factor only in evidence.
                scale="1",
                reporting_entity=observation.reporting_entity_id,
                cross_company_comparison=True,
            )

        for left_company_id, right_company_id in combinations(ordered_company_ids, 2):
            left_rows = sorted(by_company[left_company_id], key=lambda row: row[0].id)
            right_rows = sorted(by_company[right_company_id], key=lambda row: row[0].id)
            for left, left_version, left_population in left_rows:
                for right, right_version, right_population in right_rows:
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
                                    if result.status.value
                                    in {"comparable", "comparable_with_caveats"}
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
    all_metrics = cast("list[dict[str, Any]]", catalog["metrics"])
    policy = cast("dict[str, Any]", data["eligible_source_assessment"])
    configured_metric_ids = tuple(str(value) for value in policy["metric_ids"])
    if not configured_metric_ids:
        msg = "Stage A requires a nonempty configured metric subset"
        raise ValueError(msg)
    if len(set(configured_metric_ids)) != len(configured_metric_ids):
        msg = "Stage A configured metric subset contains duplicates"
        raise ValueError(msg)
    metric_by_id = {str(metric["id"]): metric for metric in all_metrics}
    unknown_metrics = sorted(set(configured_metric_ids) - set(metric_by_id))
    if unknown_metrics:
        msg = f"Stage A configured metrics are absent from catalog: {unknown_metrics}"
        raise ValueError(msg)
    metrics = [metric_by_id[metric_id] for metric_id in configured_metric_ids]
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
            total_grid = len(companies) * len(metrics) * len(quarters)
            selected_observation_ids = [
                f"observation:{company['id']}:{quarter['period_end']}:{metric['id']}:v1"
                for company in companies
                for quarter in quarters
                for metric in metrics
            ]
            selected_rows = session.scalars(
                select(MetricObservation).where(MetricObservation.id.in_(selected_observation_ids))
            ).all()
            measured = sum(
                row.observation_state != ObservationState.NOT_DISCLOSED.value
                for row in selected_rows
            )
            not_disclosed = sum(
                row.observation_state == ObservationState.NOT_DISCLOSED.value
                for row in selected_rows
            )
            source_not_checked = total_grid - len(selected_rows)
            if source_not_checked < 0:
                msg = "configured Stage A outcome counts exceed the selected metric grid"
                raise ValueError(msg)
            selected_metric_ids = {str(metric["id"]) for metric in metrics}
            quarantine_count = sum(
                str(recipe["proposed_metric_id"]) in selected_metric_ids
                for bundle in bundles.values()
                for recipe in bundle.definition.quarantine_rows
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
            _seed_comparability_assessments(
                session,
                known_at=known_at,
                company_ids=[str(company["id"]) for company in companies],
            )
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
        thread_id: Optional durable runtime thread that owns the idempotent run.

    Returns:
        Counts of newly inserted primary catalog/evidence/observation records.
    """
    return _write_stage_a(
        engine,
        config_dir=config_dir,
        thread_id=thread_id,
        publish=True,
    )


def _phase3_metric_payload(definition: EngineMetricDefinition) -> dict[str, object]:
    """Adapt one composed metric definition to the persistence catalog shape."""
    return {
        "id": definition.metric_id,
        "semantic_version": definition.semantic_version,
        "lifecycle": definition.lifecycle.value,
        "category": definition.category,
        "definition": definition.definition,
        "unit": definition.unit.value,
        "scale": str(definition.scale),
        "period_types": [item.value for item in definition.period_types],
        "methodologies": [item.value for item in definition.methodologies],
        "dimensions": [
            {"taxonomy": item.taxonomy, "fixed_value": item.fixed_value}
            for item in definition.dimensions
        ],
        "validation_rules": [item.value for item in definition.validation_rules],
        "comparability": {
            "methodology_policy": definition.comparability.methodology_policy.value,
            "dimensions": list(definition.comparability.dimensions),
        },
        "reconciliation_rules": list(definition.reconciliation_rules),
        "quantization": {
            "quantum": str(definition.quantization.quantum),
            "rounding": definition.quantization.rounding.value,
        },
        "derivation": (
            None
            if definition.derivation is None
            else {
                "formula": definition.derivation.formula.value,
                "formula_version": definition.derivation.formula_version,
                "averaging": definition.derivation.averaging.value,
                "annualization": definition.derivation.annualization.value,
                "inputs": [
                    {
                        "role": item.role,
                        "metric_ids": list(item.metric_ids),
                        "unit": item.unit.value,
                        "period_relation": item.period_relation.value,
                        "allowed_scope_pairs": [
                            [pair.input_scope_id, pair.output_scope_id]
                            for pair in item.allowed_scope_pairs
                        ],
                    }
                    for item in definition.derivation.inputs
                ],
            }
        ),
    }


def _phase3_scope_population(scope_id: str, dimensions: Sequence[Any]) -> str:
    """Return the declarative portfolio population carried by the candidate."""
    population = next(
        (
            str(dimension.value)
            for dimension in dimensions
            if str(dimension.name) == "portfolio_population"
        ),
        None,
    )
    return population or f"explicit_issuer_scope:{scope_id}"


def _ensure_phase3_scopes(session: Session, dataset: Phase3Dataset) -> None:
    candidates = tuple(
        (item.candidate, item.dimensions)
        for item in (
            tuple(dataset.reported_candidates) + tuple(getattr(dataset, "support_candidates", ()))
        )
    ) + tuple((item, item.request.dimensions) for item in dataset.derived_candidates)
    for candidate, dimensions in candidates:
        if session.get(ReportingEntity, candidate.reporting_entity_id) is None:
            msg = (
                "Phase 3 reporting entity is not governed by the universe: "
                f"{candidate.reporting_entity_id}"
            )
            raise ValueError(msg)
        if session.get(ReportingScope, candidate.reporting_scope_id) is None:
            session.add(
                ReportingScope(
                    id=candidate.reporting_scope_id,
                    reporting_entity_id=candidate.reporting_entity_id,
                    name=_metric_display_name(candidate.reporting_scope_id),
                    portfolio_population=_phase3_scope_population(
                        candidate.reporting_scope_id,
                        dimensions,
                    ),
                    methodology=(
                        "Explicit issuer-disclosed Phase 3 reporting boundary; no cross-scope "
                        "equivalence is implied."
                    ),
                )
            )
    session.flush()


def _phase3_run(
    session: Session,
    dataset: Phase3Dataset,
    *,
    config_root: Path,
) -> tuple[PipelineRun, bool]:
    catalog_versions = sorted(
        f"{item.metric_id}:{item.semantic_version}" for item in dataset.catalog.definitions
    )
    config_hashes = {
        path.relative_to(config_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(config_root.rglob("*.yaml"))
    }
    run_key = _stable_hash(
        _canonical_run_value(
            {
                "mode": "phase3-recorded-publication",
                "catalog": catalog_versions,
                "config_hashes": config_hashes,
                "evidence": sorted(item.sha256 for item in dataset.evidence),
                "assessments": [
                    (
                        item.company_id,
                        item.metric_id,
                        item.period_end.isoformat(),
                        item.assessment_status,
                        item.result_state,
                    )
                    for item in dataset.assessments
                ],
                "reported": sorted(
                    (
                        item.candidate.candidate_id,
                        str(item.candidate.normalized_value),
                        item.candidate.methodology,
                        tuple((dimension.name, dimension.value) for dimension in item.dimensions),
                    )
                    for item in dataset.reported_candidates
                ),
                "support": sorted(
                    (
                        item.candidate.candidate_id,
                        str(item.candidate.normalized_value),
                        item.candidate.methodology,
                        tuple((dimension.name, dimension.value) for dimension in item.dimensions),
                    )
                    for item in getattr(dataset, "support_candidates", ())
                ),
                "derived": sorted(item.candidate_id for item in dataset.derived_candidates),
                "blocked": sorted(
                    (item.company_id, item.metric_id, item.period_end.isoformat(), item.reason)
                    for item in dataset.blocked_derivations
                ),
                "retained_regulatory_facts": [
                    (
                        item.id,
                        item.evidence_id,
                        item.reporting_entity_id,
                        item.reporting_scope_id,
                        item.source_family,
                        item.schedule,
                        item.item_code,
                        item.report_date.isoformat(),
                        item.raw_value,
                        item.revision_identifier,
                    )
                    for item in session.scalars(
                        select(RawRegulatoryFact).order_by(RawRegulatoryFact.id)
                    )
                ],
                "evidence_typed": dataset.evidence,
                "assessments_typed": dataset.assessments,
                "reported_typed": dataset.reported_candidates,
                "support_typed": getattr(dataset, "support_candidates", ()),
                "derived_typed": dataset.derived_candidates,
                "blocked_typed": dataset.blocked_derivations,
                "missing_typed": dataset.missing_cells,
            }
        )
    )
    run_id = f"pipeline:phase3:{run_key[:32]}"
    existing = session.get(PipelineRun, run_id)
    if existing is not None:
        return existing, False
    run = PipelineRun(
        id=run_id,
        run_key=run_key,
        status="RUNNING",
        thread_id=f"thread:phase3:{run_key[:24]}",
        started_at=dataset.knowledge_at,
        completed_at=None,
        error_count=0,
        retry_count=0,
        requested_company_id=None,
        requested_periods=sorted({item.period_end.isoformat() for item in dataset.assessments}),
        code_version="phase3-profitability-deepening-v1",
        config_version=dataset.catalog.base_version,
        parser_version="phase3-dataset-v1",
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


def _seed_phase3_evidence(
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
) -> tuple[int, dict[str, str]]:
    inserted = 0
    resolved_ids: dict[str, str] = {}
    for item in dataset.evidence:
        existing = session.get(SourceEvidence, item.evidence_id)
        if existing is not None:
            if (existing.content_sha256, existing.byte_length) != (item.sha256, item.byte_length):
                msg = f"immutable Phase 3 evidence identity changed: {item.evidence_id}"
                raise ValueError(msg)
            resolved_ids[item.evidence_id] = existing.id
            continue
        content_match = session.scalar(
            select(SourceEvidence).where(
                SourceEvidence.content_sha256 == item.sha256,
                SourceEvidence.byte_length == item.byte_length,
            )
        )
        if content_match is not None:
            if (
                content_match.representation != item.representation
                or content_match.capture_method != item.capture_method
                or content_match.original_url != item.url
            ):
                msg = (
                    "Phase 3 evidence bytes exist under incompatible immutable metadata: "
                    f"{content_match.id}"
                )
                raise ValueError(msg)
            resolved_ids[item.evidence_id] = content_match.id
            continue
        session.add(
            SourceEvidence(
                id=item.evidence_id,
                source_class=item.source_class,
                original_url=item.url,
                retrieved_at=item.retrieved_at,
                published_at=item.published_at,
                accession_or_identifier=item.accession,
                content_sha256=item.sha256,
                byte_length=item.byte_length,
                media_type=item.media_type,
                representation=item.representation,
                capture_method=item.capture_method,
                parser_version=item.parser_version,
                acquisition_run_id=run.id,
                reporting_entity_candidate=f"{item.company_id}_registrant",
                reporting_period_candidate=(
                    item.period_end.isoformat() if item.period_end is not None else "multiple"
                ),
                retention_location=str(item.retention_location),
                bounded_excerpt=(
                    "Exact retained public document; observation locators are stored separately."
                ),
                response_status=200,
                etag=None,
                last_modified=None,
            )
        )
        resolved_ids[item.evidence_id] = item.evidence_id
        inserted += 1
    session.flush()
    return inserted, resolved_ids


def _phase3_metric_version(catalog: MetricCatalog, metric_id: str) -> str:
    definition = catalog.current_definition(metric_id)
    if definition is None:
        msg = f"Phase 3 cell references a metric absent from the composed catalog: {metric_id}"
        raise ValueError(msg)
    return str(definition.semantic_version)


def _seed_phase3_assessments(
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
    evidence_ids: dict[str, str],
) -> int:
    evidence_by_source = {
        item.source_key: evidence_ids[item.evidence_id] for item in dataset.evidence
    }
    inserted = 0
    for item in dataset.assessments:
        version = _phase3_metric_version(dataset.catalog, item.metric_id)
        definition = dataset.catalog.definition(item.metric_id, version)
        assert definition is not None  # noqa: S101
        required_taxonomies = {dimension.taxonomy for dimension in definition.dimensions}
        assessment_dimensions = {dimension.name: dimension.value for dimension in item.dimensions}
        if set(assessment_dimensions) != required_taxonomies:
            msg = f"Phase 3 assessment dimensions do not match catalog: {item.metric_id}"
            raise ValueError(msg)
        assessment_id = (
            "assessment:phase3:"
            + _stable_hash((item.company_id, item.metric_id, item.period_end.isoformat(), version))[
                :32
            ]
        )
        if session.get(EligibleSourceAssessment, assessment_id) is not None:
            continue
        source_keys = list(item.source_keys)
        checked_evidence_ids = [evidence_by_source[key] for key in source_keys]
        session.add(
            EligibleSourceAssessment(
                id=assessment_id,
                pipeline_run_id=run.id,
                company_id=item.company_id,
                metric_version_id=f"{item.metric_id}:{version}",
                reporting_entity_id=item.reporting_entity_id,
                reporting_scope_id=item.reporting_scope_id,
                period_end=item.period_end,
                assessment_status=item.assessment_status,
                eligible_source_inventory=[
                    {"source_key": key, "evidence_id": evidence_by_source[key]}
                    for key in source_keys
                ],
                checked_evidence_ids=checked_evidence_ids,
                checked_locators=list(item.locators),
                assessment_version="phase3-disclosure-map-v1",
                rationale=item.reason_code or f"Phase 3 result state: {item.result_state}",
                assessed_at=dataset.knowledge_at,
            )
        )
        inserted += 1
    session.flush()
    return inserted


def _phase3_observation_id(candidate_id: str) -> str:
    return f"observation:phase3:{_stable_hash(candidate_id)[:40]}"


def _phase3_reported_semantic_digest(
    candidate: ParsedObservationCandidate,
    dimensions: dict[str, str],
    canonical_scale: str,
    fiscal_calendar_regime_id: str,
    accounting_policy_regime_id: str,
) -> str:
    """Return complete Phase 3 reported semantic identity."""
    return _stable_hash(
        {
            "metric_version_id": f"{candidate.metric_id}:{candidate.metric_version}",
            "reporting_entity_id": candidate.reporting_entity_id,
            "reporting_scope_id": candidate.reporting_scope_id,
            "period_start": candidate.period_start.isoformat() if candidate.period_start else None,
            "period_end": candidate.period_end.isoformat(),
            "period_type": candidate.period_type,
            "fiscal_calendar_regime_id": fiscal_calendar_regime_id,
            "accounting_policy_regime_id": accounting_policy_regime_id,
            "observation_state": candidate.observation_state.value,
            "methodology": candidate.methodology,
            "currency": candidate.currency,
            "unit": candidate.unit,
            "scale": canonical_scale,
            "dimensions": dict(sorted(dimensions.items())),
        }
    )


def _replay_phase3_normalization(wrapped: Any) -> Decimal:
    """Replay a reported wrapper's exact raw token and governed normalization trace."""
    trace = wrapped.normalization_trace
    raw = wrapped.candidate.raw_value
    if trace.dash_policy is not None:
        if raw not in {"\N{EM DASH}", "--"} and "\N{EM DASH}" not in raw:
            msg = "Phase 3 dash-zero policy applied to a non-dash raw token"
            raise ValueError(msg)
        return Decimal(0)
    if trace.rule in {"usd_from_thousands", "count_from_thousands"}:
        value = normalize_reported_value(raw, rule="identity") * Decimal(1000)
    else:
        value = normalize_reported_value(raw, rule=trace.rule)
    if trace.sign_normalization == "ABSOLUTE_REDUCTION_OR_EXPENSE_MAGNITUDE":
        return abs(value)
    if trace.sign_normalization != "PRESERVE_REPORTED_SIGN":
        msg = "Phase 3 candidate has an unknown sign-normalization policy"
        raise ValueError(msg)
    return value


def _publish_phase3_reported(  # noqa: C901, PLR0915
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
    evidence_ids: dict[str, str],
) -> tuple[int, dict[str, str]]:
    from mortgage_servicing_dashboard.metric_engine import (  # noqa: PLC0415
        Completeness,
        DecisionDisposition,
        MetricInput,
        MetricMethodology,
        MetricUnit,
        PeriodType,
        PublicationStatus,
        ValueState,
        validate_metric_input,
    )

    inserted = 0
    observation_by_candidate: dict[str, str] = {}
    wrapped_candidates = tuple(dataset.reported_candidates) + tuple(
        getattr(dataset, "support_candidates", ())
    )
    for wrapped in wrapped_candidates:
        candidate = wrapped.candidate
        if _replay_phase3_normalization(wrapped) != candidate.normalized_value:
            msg = f"Phase 3 raw-token normalization replay failed: {candidate.candidate_id}"
            raise ValueError(msg)
        if dataset.catalog.definition(candidate.metric_id, candidate.metric_version) is None:
            msg = f"Phase 3 candidate is absent from the governed catalog: {candidate.candidate_id}"
            raise ValueError(msg)
        definition = dataset.catalog.definition(candidate.metric_id, candidate.metric_version)
        assert definition is not None  # noqa: S101
        required_taxonomies = {item.taxonomy for item in definition.dimensions}
        candidate_dimensions = {item.name: item.value for item in wrapped.dimensions}
        if set(candidate_dimensions) != required_taxonomies:
            msg = f"Phase 3 candidate dimensions do not match catalog: {candidate.candidate_id}"
            raise ValueError(msg)
        governed_input = MetricInput(
            observation_id=_phase3_observation_id(candidate.candidate_id),
            issuer_id=candidate.company_id,
            metric_id=candidate.metric_id,
            metric_version=candidate.metric_version,
            value=candidate.normalized_value,
            unit=MetricUnit(candidate.unit),
            period_type=PeriodType(candidate.period_type),
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            reporting_entity_id=candidate.reporting_entity_id,
            reporting_scope_id=candidate.reporting_scope_id,
            methodology=MetricMethodology(candidate.methodology),
            publication_status=PublicationStatus.PUBLISHED,
            value_state=ValueState.REPORTED_ACTUAL,
            completeness=Completeness.COMPLETE,
            dimensions=wrapped.dimensions,
            scale="1",
        )
        governed_validation = validate_metric_input(governed_input, dataset.catalog)
        if governed_validation.disposition is not DecisionDisposition.VALIDATED:
            msg = f"Phase 3 candidate violates governed catalog: {candidate.candidate_id}"
            raise ValueError(msg)
        fiscal_regime_id, accounting_regime_id = _observation_regimes(
            session,
            reporting_entity_id=candidate.reporting_entity_id,
            period_end=candidate.period_end,
        )
        semantic_digest = _phase3_reported_semantic_digest(
            candidate,
            candidate_dimensions,
            str(definition.scale),
            fiscal_regime_id,
            accounting_regime_id,
        )
        active = session.scalars(
            select(MetricObservation).where(
                MetricObservation.semantic_key_digest == semantic_digest,
                MetricObservation.knowledge_to.is_(None),
            )
        ).all()
        same_semantics = active
        reusable = next(
            (item for item in same_semantics if item.value == candidate.normalized_value),
            None,
        )
        if reusable is not None:
            observation_by_candidate[candidate.candidate_id] = reusable.id
            resolved_evidence_id = evidence_ids[candidate.evidence_id]
            if session.get(ObservationEvidence, (reusable.id, resolved_evidence_id)) is None:
                session.add(
                    ObservationEvidence(
                        observation_id=reusable.id,
                        evidence_id=resolved_evidence_id,
                        evidence_role="corroborating_phase3",
                        locator=candidate.evidence_locator,
                        raw_label=candidate.raw_label,
                        raw_value=candidate.raw_value,
                        disclosed_unit=candidate.unit,
                        disclosed_scale=candidate.reported_scale,
                        extraction_method=candidate.extraction_method,
                        validation_status=QualityState.VALIDATED.value,
                    )
                )
            continue
        resolved_evidence_id = evidence_ids[candidate.evidence_id]
        legacy_same_fact = session.scalar(
            select(MetricObservation)
            .where(
                MetricObservation.metric_version_id
                == f"{candidate.metric_id}:{candidate.metric_version}",
                MetricObservation.reporting_entity_id == candidate.reporting_entity_id,
                MetricObservation.reporting_scope_id == candidate.reporting_scope_id,
                MetricObservation.period_end == candidate.period_end,
                MetricObservation.value == candidate.normalized_value,
                MetricObservation.reported_value == candidate.raw_value,
                MetricObservation.observation_state == candidate.observation_state.value,
                MetricObservation.knowledge_to.is_(None),
                ~MetricObservation.id.like("observation:phase3:%"),
            )
            .order_by(MetricObservation.revision_number.desc(), MetricObservation.id)
        )
        superseded = same_semantics[0] if same_semantics else legacy_same_fact
        if superseded is not None:
            for prior in {*same_semantics, superseded}:
                prior.knowledge_to = dataset.knowledge_at
        validation = validate_candidate(candidate)
        if not validation.valid:
            msg = f"Phase 3 reported candidate failed validation: {candidate.candidate_id}"
            raise ValueError(msg)
        base_observation_id = _phase3_observation_id(candidate.candidate_id)
        observation_id = base_observation_id
        if session.get(MetricObservation, observation_id) is not None:
            observation_id = (
                f"{base_observation_id}:r"
                f"{(superseded.revision_number + 1 if superseded is not None else 2)}"
            )
        observation_by_candidate[candidate.candidate_id] = observation_id
        if session.get(MetricObservation, observation_id) is not None:
            continue
        observation = MetricObservation(
            id=observation_id,
            metric_version_id=f"{candidate.metric_id}:{candidate.metric_version}",
            reporting_entity_id=candidate.reporting_entity_id,
            reporting_scope_id=candidate.reporting_scope_id,
            fiscal_calendar_regime_id=fiscal_regime_id,
            accounting_policy_regime_id=accounting_regime_id,
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            fiscal_year=candidate.fiscal_year,
            fiscal_quarter=candidate.fiscal_quarter,
            period_type=candidate.period_type,
            value=candidate.normalized_value,
            currency=candidate.currency,
            unit=candidate.unit,
            scale=str(definition.scale),
            reported_decimals=candidate.reported_decimals,
            reported_precision=_reported_precision(candidate),
            observation_state=candidate.observation_state.value,
            methodology=candidate.methodology,
            dimensions=candidate_dimensions,
            evidence_locator=candidate.evidence_locator,
            extraction_method=candidate.extraction_method,
            parser_metadata={
                "candidate_id": candidate.candidate_id,
                "parser_name": candidate.parser_name,
                "parser_version": candidate.parser_version,
                "source_methodology": wrapped.source_methodology,
                "normalization_rule": wrapped.normalization_rule,
                "normalization_trace": asdict(wrapped.normalization_trace),
                "reported_scale": candidate.reported_scale,
                "normalization_exact": True,
            },
            validation_summary=validation.summary,
            publication_state=PublicationState.PUBLISHED.value,
            revision_number=(superseded.revision_number + 1 if superseded is not None else 1),
            semantic_key_digest=semantic_digest,
            valid_from=candidate.period_end,
            valid_to=None,
            knowledge_from=dataset.knowledge_at,
            knowledge_to=None,
            supersedes_observation_id=superseded.id if superseded is not None else None,
            quality_state=QualityState.VALIDATED.value,
            reported_label=candidate.raw_label,
            reported_value=candidate.raw_value,
            published_at=dataset.knowledge_at,
        )
        session.add(observation)
        session.add(
            ObservationEvidence(
                observation_id=observation_id,
                evidence_id=evidence_ids[candidate.evidence_id],
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
                id=f"revision:{observation_id}:1",
                observation_id=observation_id,
                prior_observation_id=superseded.id if superseded is not None else None,
                reason=(
                    f"governed Phase 3 supersession by {run.id}"
                    if superseded is not None
                    else f"initial governed Phase 3 publication by {run.id}"
                ),
                created_at=dataset.knowledge_at,
            )
        )
        inserted += 1
    session.flush()
    return inserted, observation_by_candidate


def _publish_phase3_derived(  # noqa: C901, PLR0912, PLR0915
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
    observation_by_candidate: dict[str, str],
    evidence_ids: dict[str, str],
) -> int:
    from mortgage_servicing_dashboard.metric_engine import (  # noqa: PLC0415
        Completeness,
        DecisionDisposition,
        MetricMethodology,
        PublicationStatus,
        ValueState,
        derive_metric,
    )

    inserted = 0
    available_evidence_ids = {item.evidence_id for item in dataset.evidence}
    for candidate in dataset.derived_candidates:
        definition = dataset.catalog.definition(candidate.metric_id, candidate.metric_version)
        if definition is None or definition.derivation is None:
            msg = (
                f"Phase 3 derivation is absent from the governed catalog: {candidate.candidate_id}"
            )
            raise ValueError(msg)
        observation_id = _phase3_observation_id(candidate.candidate_id)
        observation_by_candidate[candidate.candidate_id] = observation_id
        existing_output = session.get(MetricObservation, observation_id)
        if existing_output is not None and (
            existing_output.publication_state == PublicationState.QUARANTINED.value
            or existing_output.quality_state == QualityState.QUARANTINED.value
        ):
            continue
        input_ids = tuple(
            observation_by_candidate.get(candidate_id, "")
            for candidate_id in candidate.input_candidate_ids
        )
        inputs = tuple(session.get(MetricObservation, item) for item in input_ids if item)
        exact_inputs = (
            len(input_ids) == len(candidate.input_candidate_ids)
            and all(input_ids)
            and len(inputs) == len(input_ids)
            and all(
                item is not None
                and item.publication_state == PublicationState.PUBLISHED.value
                and item.quality_state == QualityState.VALIDATED.value
                and item.value is not None
                for item in inputs
            )
        )
        if not exact_inputs:
            msg = f"Phase 3 ready derivation lacks exact published inputs: {candidate.candidate_id}"
            raise ValueError(msg)
        expected_roles = tuple(item.role for item in definition.derivation.inputs)
        if (
            len(definition.derivation.inputs) != len(inputs)
            or candidate.input_roles != expected_roles
        ):
            msg = (
                "Phase 3 derivation input cardinality differs from catalog: "
                f"{candidate.candidate_id}"
            )
            raise ValueError(msg)
        if any(item not in available_evidence_ids for item in candidate.evidence_ids):
            msg = f"Phase 3 derivation references unretained evidence: {candidate.candidate_id}"
            raise ValueError(msg)
        persisted_request_inputs = []
        for (input_role, original_input), input_observation in zip(
            candidate.request.inputs,
            inputs,
            strict=True,
        ):
            assert input_observation is not None  # noqa: S101
            assert input_observation.value is not None  # noqa: S101
            persisted_request_inputs.append(
                (
                    input_role,
                    replace(
                        original_input,
                        observation_id=input_observation.id,
                        value=input_observation.value,
                        publication_status=PublicationStatus.PUBLISHED,
                        completeness=Completeness.COMPLETE,
                        methodology=MetricMethodology(input_observation.methodology),
                        value_state=(
                            ValueState.DERIVED
                            if input_observation.observation_state == ObservationState.DERIVED.value
                            else ValueState.REPORTED_ACTUAL
                        ),
                        formula_version=(
                            cast(
                                "str | None",
                                input_observation.parser_metadata.get("formula_version"),
                            )
                            if input_observation.observation_state == ObservationState.DERIVED.value
                            else None
                        ),
                    ),
                )
            )
        persisted_request = replace(
            candidate.request,
            derived_observation_id=observation_id,
            inputs=tuple(persisted_request_inputs),
        )
        decision = derive_metric(persisted_request, dataset.catalog)
        if decision.disposition is not DecisionDisposition.VALIDATED or decision.result is None:
            msg = f"Phase 3 persisted-input engine replay failed: {candidate.candidate_id}"
            raise ValueError(msg)
        replay = decision.result
        trace = candidate.trace
        reproduced = trace.unquantized_value.quantize(trace.quantum, rounding=ROUND_HALF_EVEN)
        expected_lineage = tuple(
            (item.input_observation_id, item.input_role, item.input_ordinal, item.input_value)
            for item in replay.lineage
        )
        candidate_lineage = tuple(
            (input_id, role, ordinal, cast("MetricObservation", inputs[ordinal]).value)
            for ordinal, (input_id, role) in enumerate(
                zip(input_ids, candidate.input_roles, strict=True)
            )
        )
        if (
            trace.formula.value != candidate.formula
            or trace.formula_version != candidate.formula_version
            or trace.quantum != definition.quantization.quantum
            or reproduced != candidate.normalized_value
            or replay.value != candidate.normalized_value
            or replay.trace != candidate.trace
            or expected_lineage != candidate_lineage
        ):
            msg = f"Phase 3 derivation trace does not reproduce output: {candidate.candidate_id}"
            raise ValueError(msg)
        if session.get(MetricObservation, observation_id) is not None:
            continue
        period_type = (
            "instant"
            if candidate.period_start is None
            else next(
                (item.value for item in definition.period_types if item.value != "instant"),
                "duration",
            )
        )
        input_evidence_rows: dict[str, list[tuple[str, ObservationEvidence]]] = {}
        for input_observation in inputs:
            assert input_observation is not None  # noqa: S101
            for link in session.scalars(
                select(ObservationEvidence)
                .where(ObservationEvidence.observation_id == input_observation.id)
                .order_by(ObservationEvidence.evidence_id)
            ):
                input_evidence_rows.setdefault(link.evidence_id, []).append(
                    (input_observation.id, link)
                )
        expected_resolved_evidence = {evidence_ids[item] for item in candidate.evidence_ids}
        if set(input_evidence_rows) != expected_resolved_evidence:
            msg = (
                "Phase 3 derived evidence differs from exact input lineage: "
                f"{candidate.candidate_id}"
            )
            raise ValueError(msg)
        lineage_locator = "; ".join(
            f"input {input_id} -> evidence {evidence_id}"
            for evidence_id, rows in sorted(input_evidence_rows.items())
            for input_id, _ in rows
        )
        fiscal_regime_id, accounting_regime_id = _observation_regimes(
            session,
            reporting_entity_id=candidate.reporting_entity_id,
            period_end=candidate.period_end,
        )
        semantic_digest = _stable_hash(
            {
                "metric_version_id": f"{candidate.metric_id}:{candidate.metric_version}",
                "reporting_entity_id": candidate.reporting_entity_id,
                "reporting_scope_id": candidate.reporting_scope_id,
                "period_start": (
                    candidate.period_start.isoformat() if candidate.period_start else None
                ),
                "period_end": candidate.period_end.isoformat(),
                "period_type": period_type,
                "observation_state": ObservationState.DERIVED.value,
                "methodology": candidate.methodology,
                "currency": "USD" if candidate.unit in {"USD", "USD_per_loan"} else None,
                "unit": candidate.unit,
                "scale": "1",
                "dimensions": {item.name: item.value for item in candidate.request.dimensions},
                "fiscal_calendar_regime_id": fiscal_regime_id,
                "accounting_policy_regime_id": accounting_regime_id,
                "formula_version": candidate.formula_version,
                "input_observation_ids": input_ids,
            }
        )
        observation = MetricObservation(
            id=observation_id,
            metric_version_id=f"{candidate.metric_id}:{candidate.metric_version}",
            reporting_entity_id=candidate.reporting_entity_id,
            reporting_scope_id=candidate.reporting_scope_id,
            fiscal_calendar_regime_id=fiscal_regime_id,
            accounting_policy_regime_id=accounting_regime_id,
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            fiscal_year=candidate.period_end.year,
            fiscal_quarter=((candidate.period_end.month - 1) // 3) + 1,
            period_type=period_type,
            value=candidate.normalized_value,
            currency="USD" if candidate.unit in {"USD", "USD_per_loan"} else None,
            unit=candidate.unit,
            scale="1",
            reported_decimals=None,
            reported_precision=f"governed quantization {definition.quantization.quantum}",
            observation_state=ObservationState.DERIVED.value,
            methodology=candidate.methodology,
            dimensions={item.name: item.value for item in candidate.request.dimensions},
            evidence_locator=lineage_locator,
            extraction_method="deterministic_governed_derivation",
            parser_metadata={
                "candidate_id": candidate.candidate_id,
                "formula": candidate.formula,
                "formula_version": candidate.formula_version,
                "averaging": trace.averaging.value,
                "annualization": trace.annualization.value,
                "unquantized_value": str(trace.unquantized_value),
                "observed_days": trace.observed_days,
                "basis_days": str(trace.basis_days) if trace.basis_days is not None else None,
                "input_candidate_ids": list(candidate.input_candidate_ids),
                "input_observation_ids": list(input_ids),
                "evidence_ids": list(candidate.evidence_ids),
                "quantum": str(definition.quantization.quantum),
                "rounding": definition.quantization.rounding.value,
                "calculation_trace_complete": True,
            },
            validation_summary=(
                "deterministic formula reproduced from exact PUBLISHED VALIDATED input revisions"
            ),
            publication_state=PublicationState.PUBLISHED.value,
            revision_number=1,
            semantic_key_digest=semantic_digest,
            valid_from=candidate.period_end,
            valid_to=None,
            knowledge_from=dataset.knowledge_at,
            knowledge_to=None,
            supersedes_observation_id=None,
            quality_state=QualityState.VALIDATED.value,
            reported_label=f"Derived: {candidate.formula}",
            reported_value=str(candidate.normalized_value),
            published_at=dataset.knowledge_at,
        )
        session.add(observation)
        for ordinal, (input_role, input_observation) in enumerate(
            zip(candidate.input_roles, inputs, strict=True)
        ):
            assert input_observation is not None  # noqa: S101
            assert input_observation.value is not None  # noqa: S101
            session.add(
                DerivedObservationInput(
                    derived_observation_id=observation_id,
                    input_observation_id=input_observation.id,
                    input_role=input_role,
                    input_ordinal=ordinal,
                    formula_version=candidate.formula_version,
                    input_value=input_observation.value,
                )
            )
        for evidence_id, lineage_rows in sorted(input_evidence_rows.items()):
            exact_locators = "; ".join(
                f"input {input_id}: {link.locator}" for input_id, link in lineage_rows
            )
            exemplar = lineage_rows[0][1]
            session.add(
                ObservationEvidence(
                    observation_id=observation_id,
                    evidence_id=evidence_id,
                    evidence_role="derived_input",
                    locator=exact_locators,
                    raw_label=exemplar.raw_label,
                    raw_value=exemplar.raw_value,
                    disclosed_unit=exemplar.disclosed_unit,
                    disclosed_scale=exemplar.disclosed_scale,
                    extraction_method="deterministic_governed_derivation",
                    validation_status=QualityState.VALIDATED.value,
                )
            )
        session.add(
            ObservationRevision(
                id=f"revision:{observation_id}:1",
                observation_id=observation_id,
                prior_observation_id=None,
                reason=f"initial governed Phase 3 derivation by {run.id}",
                created_at=dataset.knowledge_at,
            )
        )
        inserted += 1
    session.flush()
    return inserted


def _seed_phase3_missing(
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
) -> int:
    blocked_keys = {
        (item.company_id, item.metric_id, item.period_end) for item in dataset.blocked_derivations
    }
    inserted = 0
    for item in dataset.missing_cells:
        cell_key = (item.company_id, item.metric_id, item.period_end)
        if cell_key in blocked_keys or item.assessment_status != "CHECKED_COMPLETE":
            msg = "a blocked/disclosed Phase 3 cell cannot be persisted as NOT_DISCLOSED"
            raise ValueError(msg)
        version = _phase3_metric_version(dataset.catalog, item.metric_id)
        assessment_id = (
            "assessment:phase3:"
            + _stable_hash((item.company_id, item.metric_id, item.period_end.isoformat(), version))[
                :32
            ]
        )
        assessment = session.get(EligibleSourceAssessment, assessment_id)
        if assessment is None or not assessment.checked_evidence_ids:
            msg = f"Phase 3 missing cell lacks complete retained source lineage: {assessment_id}"
            raise ValueError(msg)
        observation_id = (
            "observation:phase3:missing:"
            + _stable_hash((item.company_id, item.metric_id, item.period_end.isoformat()))[:32]
        )
        if session.get(MetricObservation, observation_id) is not None:
            continue
        definition = dataset.catalog.definition(item.metric_id, version)
        assert definition is not None  # noqa: S101
        period_type = definition.period_types[0].value
        period_start = None
        if period_type != "instant":
            quarter_start_month = ((item.period_end.month - 1) // 3) * 3 + 1
            period_start = date(item.period_end.year, quarter_start_month, 1)
        locator = "; ".join(item.locators) + f"; no '{item.metric_id}' disclosure located"
        fiscal_regime_id, accounting_regime_id = _observation_regimes(
            session,
            reporting_entity_id=assessment.reporting_entity_id,
            period_end=item.period_end,
        )
        session.add(
            MetricObservation(
                id=observation_id,
                metric_version_id=f"{item.metric_id}:{version}",
                reporting_entity_id=assessment.reporting_entity_id,
                reporting_scope_id=assessment.reporting_scope_id,
                fiscal_calendar_regime_id=fiscal_regime_id,
                accounting_policy_regime_id=accounting_regime_id,
                period_start=period_start,
                period_end=item.period_end,
                fiscal_year=item.period_end.year,
                fiscal_quarter=((item.period_end.month - 1) // 3) + 1,
                period_type=period_type,
                value=None,
                currency="USD" if definition.unit.value in {"USD", "USD_per_loan"} else None,
                unit=definition.unit.value,
                scale=str(definition.scale),
                reported_decimals=None,
                reported_precision="not disclosed after complete eligible-source review",
                observation_state=ObservationState.NOT_DISCLOSED.value,
                methodology="eligible_source_set_reviewed_phase3",
                dimensions={dimension.name: dimension.value for dimension in item.dimensions},
                evidence_locator=locator,
                extraction_method="deterministic_source_set_review",
                parser_metadata={
                    "source_assessment_id": assessment.id,
                    "source_assessment_version": assessment.assessment_version,
                    "eligible_source_inventory": assessment.eligible_source_inventory,
                    "reason_code": item.reason_code,
                    "missingness_rule": "all eligible retained sources checked",
                },
                validation_summary="complete retained eligible-source inventory evaluated",
                publication_state=PublicationState.PUBLISHED.value,
                revision_number=1,
                semantic_key_digest=_missing_semantic_digest(
                    metric_version_id=f"{item.metric_id}:{version}",
                    entity_id=assessment.reporting_entity_id,
                    scope_id=assessment.reporting_scope_id,
                    period_end=item.period_end.isoformat(),
                    period_start=period_start.isoformat() if period_start else None,
                    period_type=period_type,
                    fiscal_calendar_regime_id=fiscal_regime_id,
                    accounting_policy_regime_id=accounting_regime_id,
                    currency=("USD" if definition.unit.value in {"USD", "USD_per_loan"} else None),
                    unit=definition.unit.value,
                    scale=str(definition.scale),
                    dimensions={dimension.name: dimension.value for dimension in item.dimensions},
                ),
                valid_from=item.period_end,
                valid_to=None,
                knowledge_from=dataset.knowledge_at,
                knowledge_to=None,
                supersedes_observation_id=None,
                quality_state=QualityState.VALIDATED.value,
                reported_label="No disclosure located",
                reported_value="NOT_DISCLOSED",
                published_at=dataset.knowledge_at,
            )
        )
        for index, evidence_id in enumerate(assessment.checked_evidence_ids):
            session.add(
                ObservationEvidence(
                    observation_id=observation_id,
                    evidence_id=evidence_id,
                    evidence_role="reviewed_source",
                    locator=(item.locators[index] if index < len(item.locators) else locator),
                    raw_label="No disclosure located",
                    raw_value="NOT_DISCLOSED",
                    disclosed_unit=definition.unit.value,
                    disclosed_scale=str(definition.scale),
                    extraction_method="deterministic_source_set_review",
                    validation_status=QualityState.VALIDATED.value,
                )
            )
        session.add(
            ObservationRevision(
                id=f"revision:{observation_id}:1",
                observation_id=observation_id,
                prior_observation_id=None,
                reason=f"complete Phase 3 eligible-source review by {run.id}",
                created_at=dataset.knowledge_at,
            )
        )
        inserted += 1
    session.flush()
    return inserted


def _seed_phase3_blocked(
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
    evidence_ids: dict[str, str],
) -> int:
    assessments = {
        (item.company_id, item.metric_id, item.period_end): item for item in dataset.assessments
    }
    evidence_by_source = {
        item.source_key: evidence_ids[item.evidence_id] for item in dataset.evidence
    }
    inserted = 0
    for item in dataset.blocked_derivations:
        key = (item.company_id, item.metric_id, item.period_end)
        assessment = assessments.get(key)
        if assessment is None or not assessment.source_keys:
            msg = f"blocked Phase 3 derivation lacks source assessment: {key}"
            raise ValueError(msg)
        candidate_id = (
            "quarantine:phase3:blocked:"
            + _stable_hash(
                (
                    (item.company_id, item.metric_id, item.period_end.isoformat()),
                    item.formula_version,
                    item.missing_input_metric_ids,
                    item.reason,
                )
            )[:32]
        )
        if session.get(QuarantineCandidate, candidate_id) is not None:
            continue
        evidence_id = evidence_by_source[assessment.source_keys[0]]
        definition = dataset.catalog.current_definition(item.metric_id)
        if definition is None:
            msg = f"blocked Phase 3 metric is absent from catalog: {item.metric_id}"
            raise ValueError(msg)
        session.add(
            QuarantineCandidate(
                id=candidate_id,
                pipeline_run_id=run.id,
                proposed_metric_id=item.metric_id,
                raw_source_label="Governed derivation blocked",
                raw_value="BLOCKED_DERIVATION",
                proposed_normalized_value=None,
                unit=definition.unit.value,
                scale="1",
                period_end=item.period_end,
                reporting_entity_id=assessment.reporting_entity_id,
                reporting_scope_id=assessment.reporting_scope_id,
                methodology="deterministic_derivation_blocked",
                evidence_id=evidence_id,
                evidence_locator="; ".join(assessment.locators),
                bounded_excerpt="Disclosure exists, but governed derivation inputs are incomplete.",
                confidence=Decimal("1.0000"),
                conflicts_and_uncertainties=[
                    item.reason,
                    f"missing exact published inputs: {', '.join(item.missing_input_metric_ids)}",
                    f"formula version: {item.formula_version or 'not resolved'}",
                ],
                model_and_prompt_version=None,
                status="BLOCKED_LINEAGE",
            )
        )
        inserted += 1
    session.flush()
    return inserted


def _reconcile_phase3_regulatory(  # noqa: C901, PLR0915
    session: Session,
    *,
    dataset: Phase3Dataset,
    run: PipelineRun,
) -> int:
    """Reconcile exact retained regulatory facts without choosing a preferred source."""
    from mortgage_servicing_dashboard.metric_engine import (  # noqa: PLC0415
        Completeness,
        DecisionDisposition,
        MetricDimension,
        MetricInput,
        MetricMethodology,
        MetricUnit,
        PeriodType,
        PublicationStatus,
        ValueState,
        reconcile_cross_source,
    )
    from mortgage_servicing_dashboard.regulatory import (  # noqa: PLC0415
        RegulatorySourceFamily,
        load_regulatory_config,
    )

    config = load_regulatory_config(
        config_directory() / "regulatory" / "regulatory_mappings.v1.yaml"
    )
    raw_facts = session.scalars(select(RawRegulatoryFact).order_by(RawRegulatoryFact.id)).all()
    inserted = 0
    reconciled_keys: set[tuple[str, date, str]] = set()
    scale_factors = {"ones": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1000000)}
    for rule in dataset.catalog.cross_source_rules:
        for sec_scope, regulatory_scope in (
            (pair.sec_scope_id, pair.regulatory_scope_id) for pair in rule.allowed_scope_pairs
        ):
            sec_rows = sorted(
                session.scalars(
                    select(MetricObservation).where(
                        MetricObservation.metric_version_id.like(f"{rule.metric_id}:%"),
                        MetricObservation.reporting_scope_id == sec_scope,
                        MetricObservation.knowledge_to.is_(None),
                    )
                ).all(),
                key=lambda item: (not item.id.startswith("observation:phase3:"), item.id),
            )
            for sec in sec_rows:
                reconciliation_key = (rule.rule_id, sec.period_end, regulatory_scope)
                if reconciliation_key in reconciled_keys or (
                    sec.period_type == "instant" and sec.period_start is not None
                ):
                    continue
                permitted_sec_methods = {item.value for item in rule.sec_methodologies}
                evidence_classes = set(
                    session.scalars(
                        select(SourceEvidence.source_class)
                        .join(
                            ObservationEvidence,
                            ObservationEvidence.evidence_id == SourceEvidence.id,
                        )
                        .where(ObservationEvidence.observation_id == sec.id)
                    )
                )
                eligible_evidence_methods = sorted(
                    permitted_sec_methods.intersection(evidence_classes)
                )
                effective_sec_methodology = (
                    sec.methodology
                    if sec.methodology in permitted_sec_methods
                    else eligible_evidence_methods[0]
                    if len(eligible_evidence_methods) == 1
                    else None
                )
                if effective_sec_methodology is None:
                    continue
                family_rows = [
                    item
                    for item in raw_facts
                    if item.reporting_scope_id == regulatory_scope
                    and item.report_date == sec.period_end
                ]
                for family_text in sorted({item.source_family for item in family_rows}):
                    family = RegulatorySourceFamily(family_text)
                    mappings = tuple(
                        item
                        for item in config.item_mappings(family=family, report_date=sec.period_end)
                        if item.metric_id == rule.metric_id
                    )
                    selected: list[tuple[RawRegulatoryFact, Any]] = []
                    for mapping in mappings:
                        matches = [
                            item
                            for item in family_rows
                            if item.source_family == family.value
                            and item.schedule == mapping.schedule
                            and item.item_code == mapping.item
                        ]
                        if matches:
                            selected.append(
                                (
                                    max(
                                        matches,
                                        key=lambda fact: (
                                            fact.revision_identifier,
                                            fact.id,
                                        ),
                                    ),
                                    mapping,
                                )
                            )
                    if not mappings or len(selected) != len(mappings):
                        continue
                    values = [
                        Decimal(fact.raw_value) * scale_factors[mapping.scale]
                        for fact, mapping in selected
                    ]
                    regulatory_value = sum(values, Decimal(0))
                    metric_version = sec.metric_version_id.rsplit(":", 1)[1]
                    sec_input = MetricInput(
                        observation_id=sec.id,
                        issuer_id=rule.issuer_id,
                        metric_id=rule.metric_id,
                        metric_version=metric_version,
                        value=sec.value,
                        unit=MetricUnit(sec.unit),
                        period_type=PeriodType(sec.period_type),
                        period_start=sec.period_start,
                        period_end=sec.period_end,
                        reporting_entity_id=sec.reporting_entity_id,
                        reporting_scope_id=sec.reporting_scope_id,
                        methodology=MetricMethodology(effective_sec_methodology),
                        publication_status=PublicationStatus.PUBLISHED,
                        value_state=ValueState.REPORTED_ACTUAL,
                        completeness=Completeness.COMPLETE,
                        dimensions=tuple(
                            sorted(
                                MetricDimension(name=name, value=value)
                                for name, value in sec.dimensions.items()
                            )
                        ),
                        # MetricObservation.value is already normalized; the
                        # source/definition display factor is not comparison
                        # scale. Reconciliation inputs therefore use canonical
                        # normalized scale deliberately.
                        scale="1",
                    )
                    first_fact = selected[0][0]
                    regulatory_input = MetricInput(
                        observation_id="regulatory-aggregate:"
                        + _stable_hash(tuple(fact.id for fact, _ in selected))[:40],
                        issuer_id=rule.issuer_id,
                        metric_id=rule.metric_id,
                        metric_version=metric_version,
                        value=regulatory_value,
                        unit=MetricUnit(first_fact.unit),
                        period_type=PeriodType(first_fact.period_type),
                        period_start=sec.period_start,
                        period_end=first_fact.report_date,
                        reporting_entity_id=first_fact.reporting_entity_id,
                        reporting_scope_id=first_fact.reporting_scope_id,
                        methodology=MetricMethodology(first_fact.source_family),
                        publication_status=PublicationStatus.PUBLISHED,
                        value_state=ValueState.REPORTED_ACTUAL,
                        completeness=Completeness.COMPLETE,
                        dimensions=tuple(
                            sorted(
                                MetricDimension(name=name, value=value)
                                for name, value in sec.dimensions.items()
                            )
                        ),
                        scale="1",
                    )
                    decision = reconcile_cross_source(
                        sec_input,
                        regulatory_input,
                        rule_id=rule.rule_id,
                        catalog=dataset.catalog,
                    )
                    reconciled_keys.add(reconciliation_key)
                    audit = {
                        "rule_id": rule.rule_id,
                        "regulatory_input_id": regulatory_input.observation_id,
                        "regulatory_fact_ids": [fact.id for fact, _ in selected],
                        "disposition": decision.disposition.value,
                        "reasons": [item.value for item in decision.reasons],
                        "absolute_difference": (
                            str(decision.absolute_difference)
                            if decision.absolute_difference is not None
                            else None
                        ),
                        "preferred_observation_id": None,
                    }
                    metadata = dict(sec.parser_metadata)
                    prior_reconciliations = metadata.get("cross_source_reconciliations", [])
                    reconciliations = (
                        list(cast("list[object]", prior_reconciliations))
                        if isinstance(prior_reconciliations, list)
                        else []
                    )
                    if audit not in reconciliations:
                        reconciliations.append(audit)
                    metadata["cross_source_reconciliations"] = reconciliations
                    sec.parser_metadata = metadata
                    if decision.disposition is DecisionDisposition.VALIDATED:
                        continue
                    quarantine_id = (
                        "quarantine:cross-source:"
                        + _stable_hash((rule.rule_id, sec.id, regulatory_input.observation_id))[:40]
                    )
                    if session.get(QuarantineCandidate, quarantine_id) is not None:
                        continue
                    evidence = first_fact.evidence_id
                    session.add(
                        QuarantineCandidate(
                            id=quarantine_id,
                            pipeline_run_id=run.id,
                            proposed_metric_id=rule.metric_id,
                            raw_source_label="Cross-source reconciliation: no preferred value",
                            raw_value=(
                                f"SEC={sec.value}; regulatory={regulatory_value}; "
                                f"inputs={','.join(fact.id for fact, _ in selected)}"
                            ),
                            proposed_normalized_value=None,
                            unit=rule.unit.value,
                            scale="1",
                            period_end=sec.period_end,
                            reporting_entity_id=sec.reporting_entity_id,
                            reporting_scope_id=sec.reporting_scope_id,
                            methodology="CROSS_SOURCE_NO_PREFERENCE",
                            evidence_id=evidence,
                            evidence_locator="; ".join(
                                f"{fact.schedule} {fact.item_code}" for fact, _ in selected
                            ),
                            bounded_excerpt="Retained SEC and regulatory values disagree.",
                            confidence=Decimal("1.0000"),
                            conflicts_and_uncertainties=[json.dumps(audit, sort_keys=True)],
                            model_and_prompt_version=None,
                            status="CROSS_SOURCE_NO_PREFERENCE",
                        )
                    )
                    inserted += 1
    session.flush()
    return inserted


def seed_phase3(
    engine: Engine,
    *,
    config_dir: Path | None = None,
) -> dict[str, int]:
    """Idempotently publish the governed Phase 3 local retained dataset.

    Phase 3 is layered over Stage A and never performs network access. Derived
    observations publish only after every exact input revision is already
    ``PUBLISHED`` and ``VALIDATED``; blocked lineage remains auditable and absent
    from public observation reads.

    Args:
        engine: Application database engine.
        config_dir: Optional explicit versioned configuration root.

    Returns:
        Counts of newly inserted Phase 3 records by kind.
    """
    from mortgage_servicing_dashboard.phase3 import load_phase3_dataset  # noqa: PLC0415

    root = config_directory(config_dir)
    required_roots = (root / "phase3", root / "recorded_evidence" / "phase3")
    if not all(path.is_dir() for path in required_roots):
        msg = (
            "Phase 3 retained evidence is not bundled in installed wheels; run from a "
            "repository checkout or pass --config-dir/MSI_CONFIG_DIR for a complete "
            "Phase 3 configuration root"
        )
        raise FileNotFoundError(msg)
    seed_stage_a(engine, config_dir=root)
    dataset = load_phase3_dataset(root)
    phase3_universe, _, _ = load_stage_a_configuration(root)
    phase3_company_ids = [
        str(company["id"]) for company in cast("list[dict[str, Any]]", phase3_universe["companies"])
    ]
    inserted = {
        "metrics": 0,
        "evidence": 0,
        "source_assessments": 0,
        "reported_observations": 0,
        "support_observations": 0,
        "derived_observations": 0,
        "not_disclosed_observations": 0,
        "blocked_derivations": 0,
        "cross_source_quarantines": 0,
        "comparability_assessments": 0,
    }
    with Session(engine) as session:
        metric_payloads = [_phase3_metric_payload(item) for item in dataset.catalog.definitions]
        inserted["metrics"] = _seed_metrics(session, metric_payloads)
        session.flush()
        _ensure_phase3_scopes(session, dataset)
        run, is_new_run = _phase3_run(session, dataset, config_root=root)
        inserted["evidence"], evidence_ids = _seed_phase3_evidence(
            session,
            dataset=dataset,
            run=run,
        )
        inserted["source_assessments"] = _seed_phase3_assessments(
            session,
            dataset=dataset,
            run=run,
            evidence_ids=evidence_ids,
        )
        support_candidates = tuple(getattr(dataset, "support_candidates", ()))
        support_ids = {
            _phase3_observation_id(item.candidate.candidate_id) for item in support_candidates
        }
        support_before = sum(
            session.get(MetricObservation, observation_id) is not None
            for observation_id in support_ids
        )
        reported_inserted, observation_by_candidate = _publish_phase3_reported(
            session,
            dataset=dataset,
            run=run,
            evidence_ids=evidence_ids,
        )
        support_after = sum(
            session.get(MetricObservation, observation_id) is not None
            for observation_id in support_ids
        )
        inserted["support_observations"] = support_after - support_before
        inserted["reported_observations"] = reported_inserted - inserted["support_observations"]
        inserted["derived_observations"] = _publish_phase3_derived(
            session,
            dataset=dataset,
            run=run,
            observation_by_candidate=observation_by_candidate,
            evidence_ids=evidence_ids,
        )
        inserted["cross_source_quarantines"] = _reconcile_phase3_regulatory(
            session,
            dataset=dataset,
            run=run,
        )
        inserted["not_disclosed_observations"] = _seed_phase3_missing(
            session,
            dataset=dataset,
            run=run,
        )
        inserted["blocked_derivations"] = _seed_phase3_blocked(
            session,
            dataset=dataset,
            run=run,
            evidence_ids=evidence_ids,
        )
        comparisons_before = int(
            session.scalar(select(func.count(ComparabilityAssessment.id))) or 0
        )
        _seed_comparability_assessments(
            session,
            known_at=dataset.knowledge_at,
            company_ids=phase3_company_ids,
        )
        comparisons_after = int(session.scalar(select(func.count(ComparabilityAssessment.id))) or 0)
        inserted["comparability_assessments"] = comparisons_after - comparisons_before
        if is_new_run:
            published = int(
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.id.in_(set(observation_by_candidate.values())),
                        MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                        MetricObservation.quality_state == QualityState.VALIDATED.value,
                        MetricObservation.observation_state != ObservationState.NOT_DISCLOSED.value,
                    )
                )
                or 0
            )
            not_disclosed = int(
                session.scalar(
                    select(func.count(MetricObservation.id)).where(
                        MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                        MetricObservation.quality_state == QualityState.VALIDATED.value,
                        MetricObservation.knowledge_from == dataset.knowledge_at,
                        MetricObservation.observation_state == ObservationState.NOT_DISCLOSED.value,
                    )
                )
                or 0
            )
            run.status = (
                "COMPLETED_WITH_BLOCKED_DERIVATIONS" if dataset.blocked_derivations else "COMPLETED"
            )
            run.completed_at = dataset.knowledge_at
            run.terminal_outcomes = {
                "PUBLISHED": published,
                "NOT_DISCLOSED": not_disclosed,
                "SOURCE_NOT_CHECKED": sum(
                    item.assessment_status == "SOURCE_NOT_CHECKED" for item in dataset.assessments
                ),
                "QUARANTINED": (
                    len(dataset.blocked_derivations) + inserted["cross_source_quarantines"]
                ),
                "FAILED": 0,
            }
        session.commit()
    return inserted


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


def persist_derived_observation_inputs(
    engine: Engine,
    *,
    derived_observation_id: str,
    lineage: tuple[Any, ...],
) -> int:
    """Persist exact, ordered inputs for a published derived observation.

    Args:
        engine: Target database engine.
        derived_observation_id: Published observation receiving the lineage.
        lineage: Typed derivation-lineage records produced by deterministic code.

    Returns:
        Number of new lineage links retained.

    Raises:
        KeyError: If the derived or an input observation is absent.
        ValueError: If the target is not an exact published derived observation.
    """
    initialize_schema(engine)
    inserted = 0
    with Session(engine) as session:
        derived = session.get(MetricObservation, derived_observation_id)
        if derived is None:
            raise KeyError(derived_observation_id)
        if (
            derived.observation_state != ObservationState.DERIVED.value
            or derived.publication_state != PublicationState.PUBLISHED.value
            or derived.value is None
        ):
            msg = "derived lineage requires a numeric published DERIVED observation"
            raise ValueError(msg)
        for item in lineage:
            if item.derived_observation_id != derived_observation_id:
                msg = "derived lineage target does not match the requested observation"
                raise ValueError(msg)
            input_observation = session.get(MetricObservation, item.input_observation_id)
            if input_observation is None:
                raise KeyError(item.input_observation_id)
            if (
                input_observation.publication_state != PublicationState.PUBLISHED.value
                or input_observation.quality_state != QualityState.VALIDATED.value
                or input_observation.value is None
                or input_observation.value != item.input_value
            ):
                msg = "derived lineage input is not an exact validated published revision"
                raise ValueError(msg)
            key = (derived_observation_id, item.input_observation_id)
            if session.get(DerivedObservationInput, key) is not None:
                continue
            session.add(
                DerivedObservationInput(
                    derived_observation_id=derived_observation_id,
                    input_observation_id=item.input_observation_id,
                    input_role=item.input_role,
                    input_ordinal=item.input_ordinal,
                    formula_version=item.formula_version,
                    input_value=item.input_value,
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


def _latest_source_assessments(
    session: Session,
    *,
    as_of: datetime,
) -> tuple[EligibleSourceAssessment, ...]:
    """Return the latest assessment for every governed issuer/metric cell.

    Pipeline runs may cover different cohorts. Selecting only the globally latest
    run would discard still-current assessments for every issuer outside that
    run, so recency is resolved independently for each exact assessed cell.
    """
    rows = session.scalars(
        select(EligibleSourceAssessment)
        .join(Company, EligibleSourceAssessment.company_id == Company.id)
        .where(
            Company.active.is_(True),
            EligibleSourceAssessment.assessed_at <= as_of,
        )
        .order_by(EligibleSourceAssessment.assessed_at, EligibleSourceAssessment.id)
    ).all()
    latest: dict[tuple[str, str, str, date], EligibleSourceAssessment] = {}
    for row in rows:
        latest[
            (
                row.company_id,
                row.metric_version_id,
                row.reporting_scope_id,
                row.period_end,
            )
        ] = row
    return tuple(latest[key] for key in sorted(latest))


def _public_pipeline_run_predicate() -> Any:
    """Limit operational aggregates to active issuers or issuer-neutral runs."""
    active_company_ids = select(Company.id).where(Company.active.is_(True))
    inactive_assessments = exists(
        select(EligibleSourceAssessment.id)
        .join(Company, EligibleSourceAssessment.company_id == Company.id)
        .where(
            EligibleSourceAssessment.pipeline_run_id == PipelineRun.id,
            Company.active.is_(False),
        )
    )
    inactive_quarantine = exists(
        select(QuarantineCandidate.id)
        .join(
            ReportingEntity,
            QuarantineCandidate.reporting_entity_id == ReportingEntity.id,
        )
        .join(Company, ReportingEntity.company_id == Company.id)
        .where(
            QuarantineCandidate.pipeline_run_id == PipelineRun.id,
            Company.active.is_(False),
        )
    )
    return or_(
        PipelineRun.requested_company_id.in_(active_company_ids),
        and_(
            PipelineRun.requested_company_id.is_(None),
            ~inactive_assessments,
            ~inactive_quarantine,
        ),
    )


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
        """Return the active governed company universe."""
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

    def comparison_company_ids(
        self,
        *,
        as_of: datetime | date | None = None,
    ) -> tuple[str, ...]:
        """Return active issuer IDs having at least one exact published observation."""
        instant = _as_of_instant(as_of)
        statement = (
            select(Company.id)
            .join(ReportingEntity, ReportingEntity.company_id == Company.id)
            .join(
                MetricObservation,
                MetricObservation.reporting_entity_id == ReportingEntity.id,
            )
            .where(
                Company.active.is_(True),
                MetricObservation.knowledge_from <= instant,
                or_(
                    MetricObservation.knowledge_to.is_(None),
                    MetricObservation.knowledge_to > instant,
                ),
                MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                MetricObservation.quality_state == QualityState.VALIDATED.value,
            )
            .distinct()
            .order_by(Company.id)
        )
        with Session(self._engine) as session:
            return tuple(session.scalars(statement))

    def _validated_comparison_selection(
        self,
        company_ids: Sequence[str],
        *,
        as_of: datetime | date | None,
    ) -> tuple[str, ...]:
        """Validate a bounded ordered issuer selection before observation reads."""
        selected = tuple(company_ids)
        if not _MIN_COMPARISON_COMPANY_COUNT <= len(selected) <= _MAX_COMPARISON_COMPANY_COUNT:
            message = "comparison requires two or three issuer identifiers"
            raise ValueError(message)
        if len(set(selected)) != len(selected):
            message = "comparison issuer identifiers must be distinct"
            raise ValueError(message)
        supported = set(self.comparison_company_ids(as_of=as_of))
        unsupported = [company_id for company_id in selected if company_id not in supported]
        if unsupported:
            message = "comparison issuer is not an active published supported company"
            raise ValueError(message)
        return selected

    def metrics(self) -> list[dict[str, object]]:
        """Return one current metric definition while retaining history in storage."""
        statement = (
            select(MetricDefinition, MetricDefinitionVersion)
            .join(MetricDefinitionVersion, MetricDefinitionVersion.metric_id == MetricDefinition.id)
            .order_by(MetricDefinition.category, MetricDefinition.id)
        )
        with Session(self._engine) as session:
            current: list[dict[str, object]] = []
            for definition, version in session.execute(statement):
                if version.rules.get("lifecycle") == "HISTORICAL":
                    continue
                current.append(
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
                )
            return current

    def evidence(self, evidence_id: str) -> dict[str, object] | None:
        """Return evidence only when it belongs to at least one active issuer."""
        instant = _as_of_instant(None)
        active_observation = exists(
            select(ObservationEvidence.observation_id)
            .join(
                MetricObservation,
                ObservationEvidence.observation_id == MetricObservation.id,
            )
            .join(
                ReportingEntity,
                MetricObservation.reporting_entity_id == ReportingEntity.id,
            )
            .join(Company, ReportingEntity.company_id == Company.id)
            .where(
                ObservationEvidence.evidence_id == SourceEvidence.id,
                Company.active.is_(True),
                MetricObservation.knowledge_from <= instant,
                or_(
                    MetricObservation.knowledge_to.is_(None),
                    MetricObservation.knowledge_to > instant,
                ),
                MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                MetricObservation.quality_state == QualityState.VALIDATED.value,
            )
        )
        active_earnings_event = exists(
            select(EarningsEvent.id)
            .join(Company, EarningsEvent.company_id == Company.id)
            .where(
                EarningsEvent.evidence_id == SourceEvidence.id,
                Company.active.is_(True),
            )
        )
        active_filing = exists(
            select(FilingDocument.id)
            .join(Filing, FilingDocument.filing_id == Filing.id)
            .join(ReportingEntity, Filing.reporting_entity_id == ReportingEntity.id)
            .join(Company, ReportingEntity.company_id == Company.id)
            .where(
                FilingDocument.source_evidence_id == SourceEvidence.id,
                Company.active.is_(True),
            )
        )
        active_quarantine = exists(
            select(QuarantineCandidate.id)
            .join(
                ReportingEntity,
                QuarantineCandidate.reporting_entity_id == ReportingEntity.id,
            )
            .join(Company, ReportingEntity.company_id == Company.id)
            .where(
                QuarantineCandidate.evidence_id == SourceEvidence.id,
                Company.active.is_(True),
            )
        )
        with Session(self._engine) as session:
            item = session.scalar(
                select(SourceEvidence).where(
                    SourceEvidence.id == evidence_id,
                    or_(
                        active_observation,
                        active_earnings_event,
                        active_filing,
                        active_quarantine,
                    ),
                )
            )
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

    def evidence_observation_ids(
        self,
        evidence_id: str,
        *,
        limit: int = 100,
    ) -> list[str]:
        """Return bounded reverse links to current observations for active issuers."""
        if limit < 1 or limit > _MAX_REPOSITORY_RESULTS:
            msg = f"limit must be 1..{_MAX_REPOSITORY_RESULTS}"
            raise ValueError(msg)
        instant = _as_of_instant(None)
        statement = (
            select(ObservationEvidence.observation_id)
            .join(
                MetricObservation,
                ObservationEvidence.observation_id == MetricObservation.id,
            )
            .join(
                ReportingEntity,
                MetricObservation.reporting_entity_id == ReportingEntity.id,
            )
            .join(Company, ReportingEntity.company_id == Company.id)
            .where(
                ObservationEvidence.evidence_id == evidence_id,
                Company.active.is_(True),
                MetricObservation.knowledge_from <= instant,
                or_(
                    MetricObservation.knowledge_to.is_(None),
                    MetricObservation.knowledge_to > instant,
                ),
                MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                MetricObservation.quality_state == QualityState.VALIDATED.value,
            )
            .order_by(ObservationEvidence.observation_id)
            .limit(limit)
        )
        with Session(self._engine) as session:
            return list(session.scalars(statement))

    def earnings_events(self) -> list[dict[str, object]]:
        """Return selected-company public earnings disclosure events."""
        statement = (
            select(EarningsEvent, Company, SourceEvidence)
            .join(Company, EarningsEvent.company_id == Company.id)
            .join(SourceEvidence, EarningsEvent.evidence_id == SourceEvidence.id)
            .where(Company.active.is_(True))
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
        calendar_config = _load_yaml(config_path)
        configured_company_ids = {
            str(item["company_id"])
            for item in cast("list[dict[str, Any]]", calendar_config["companies"])
        }
        payload: list[dict[str, object]] = []
        for company_id in sorted(companies):
            if company_id not in configured_company_ids:
                payload.append(
                    self._filing_only_calendar_row(
                        company_id=company_id,
                        ticker=str(companies[company_id]["ticker"]),
                        as_of=instant,
                        config_version=str(calendar_config["version"]),
                    )
                )
                continue
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

    def _filing_only_calendar_row(
        self,
        *,
        company_id: str,
        ticker: str,
        as_of: datetime,
        config_version: str,
    ) -> dict[str, object]:
        """Expose the latest actual filing without inventing an expected window."""
        statement = (
            select(Filing, FilingDocument)
            .join(ReportingEntity, Filing.reporting_entity_id == ReportingEntity.id)
            .join(FilingDocument, FilingDocument.filing_id == Filing.id)
            .where(
                ReportingEntity.company_id == company_id,
                FilingDocument.is_primary.is_(True),
                Filing.acceptance_timestamp <= as_of,
            )
            .order_by(
                Filing.period_end.desc(),
                Filing.acceptance_timestamp.desc(),
                Filing.accession.desc(),
            )
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.execute(statement).first()
        if row is None:
            return {
                "company_id": company_id,
                "ticker": ticker,
                "as_of": as_of.isoformat(),
                "last_reported_period": None,
                "next_expected_report_window": {
                    "expected_period_end": None,
                    "window_start": None,
                    "window_end": None,
                    "is_inferred": False,
                    "method": "CALENDAR_NOT_CONFIGURED",
                    "config_version": config_version,
                    "inference_basis": [
                        (
                            "No reviewed issuer-specific calendar or retained actual "
                            "filing is available; no date is inferred."
                        )
                    ],
                },
                "freshness_state": "CALENDAR_NOT_CONFIGURED",
                "next_announced_event": None,
            }
        filing, document = row
        accepted = _as_utc(filing.acceptance_timestamp)
        if accepted is None:
            message = "actual filing calendar row requires an acceptance timestamp"
            raise ValueError(message)
        return {
            "company_id": company_id,
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "last_reported_period": {
                "period_end": filing.period_end.isoformat(),
                "event_id": filing.id,
                "accepted_at": accepted.isoformat(),
                "accession": filing.accession,
                "filing_url": document.source_url,
                "exhibit_url": None,
                "is_inferred": False,
            },
            "next_expected_report_window": {
                "expected_period_end": None,
                "window_start": None,
                "window_end": None,
                "is_inferred": False,
                "method": "NOT_AVAILABLE_WITHOUT_REVIEWED_FILING_LAG_HISTORY",
                "config_version": config_version,
                "inference_basis": [
                    (
                        "No reviewed issuer-specific filing-lag history is configured; "
                        "no date is inferred."
                    )
                ],
            },
            "freshness_state": "CALENDAR_NOT_CONFIGURED",
            "next_announced_event": None,
        }

    def freshness(self) -> dict[str, object]:
        """Return evidence, publication, pipeline, and quarantine freshness."""
        active_records = self.observation_snapshot()
        published_count = len(active_records)
        missing_count = sum(
            record.state == ObservationState.NOT_DISCLOSED.value for record in active_records
        )
        knowledge_at = max(
            (datetime.fromisoformat(record.knowledge_from) for record in active_records),
            default=None,
        )
        active_evidence_ids = {
            str(link["evidence_id"]) for record in active_records for link in record.evidence_links
        }
        with Session(self._engine) as session:
            quarantine_count = (
                session.scalar(
                    select(func.count(QuarantineCandidate.id))
                    .join(
                        ReportingEntity,
                        QuarantineCandidate.reporting_entity_id == ReportingEntity.id,
                    )
                    .join(Company, ReportingEntity.company_id == Company.id)
                    .where(
                        Company.active.is_(True),
                        QuarantineCandidate.status.not_in(("REJECTED", "PUBLISHED")),
                    )
                )
                or 0
            )
            run = session.scalars(
                select(PipelineRun)
                .where(_public_pipeline_run_predicate())
                .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
            ).first()
            assessments = _latest_source_assessments(session, as_of=utc_now())
            assessment_count = len(assessments)
            active_evidence_ids.update(
                evidence_id
                for assessment in assessments
                for evidence_id in assessment.checked_evidence_ids
            )
            active_evidence_ids.update(
                session.scalars(
                    select(EarningsEvent.evidence_id)
                    .join(Company, EarningsEvent.company_id == Company.id)
                    .where(Company.active.is_(True))
                )
            )
            active_evidence_ids.update(
                session.scalars(
                    select(QuarantineCandidate.evidence_id)
                    .join(
                        ReportingEntity,
                        QuarantineCandidate.reporting_entity_id == ReportingEntity.id,
                    )
                    .join(Company, ReportingEntity.company_id == Company.id)
                    .where(Company.active.is_(True))
                )
            )
            evidence_rows = (
                session.scalars(
                    select(SourceEvidence).where(SourceEvidence.id.in_(active_evidence_ids))
                ).all()
                if active_evidence_ids
                else []
            )
            evidence_count = len(evidence_rows)
            retrieved_at = max(
                (evidence.retrieved_at for evidence in evidence_rows),
                default=None,
            )
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
                    .join(Company, ReportingEntity.company_id == Company.id)
                    .where(
                        Company.active.is_(True),
                        MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                        MetricObservation.quality_state == QualityState.VALIDATED.value,
                        MetricObservation.knowledge_to.is_(None),
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
            "calendar": self.calendar(),
        }

    def quality_counts(self) -> dict[str, int]:
        """Return active-issuer operational counts for the public quality surface."""
        visible_run_ids = select(PipelineRun.id).where(_public_pipeline_run_predicate())
        with Session(self._engine) as session:
            quarantined = (
                session.scalar(
                    select(func.count(QuarantineCandidate.id))
                    .join(
                        ReportingEntity,
                        QuarantineCandidate.reporting_entity_id == ReportingEntity.id,
                    )
                    .join(Company, ReportingEntity.company_id == Company.id)
                    .where(
                        Company.active.is_(True),
                        QuarantineCandidate.status.not_in(("REJECTED", "PUBLISHED")),
                    )
                )
                or 0
            )
            failed_runs = (
                session.scalar(
                    select(func.count(PipelineRun.id)).where(
                        PipelineRun.id.in_(visible_run_ids),
                        PipelineRun.status == "FAILED",
                    )
                )
                or 0
            )
            ingestion_errors = (
                session.scalar(
                    select(func.count(IngestionError.id))
                    .join(PipelineRun, IngestionError.pipeline_run_id == PipelineRun.id)
                    .where(PipelineRun.id.in_(visible_run_ids))
                )
                or 0
            )
        return {
            "quarantined_candidate_count": int(quarantined),
            "failed_run_count": int(failed_runs),
            "ingestion_error_count": int(ingestion_errors),
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
        selected_evidence = (
            select(
                ObservationEvidence.observation_id.label("observation_id"),
                func.min(ObservationEvidence.evidence_id).label("evidence_id"),
            )
            .group_by(ObservationEvidence.observation_id)
            .subquery()
        )
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
                selected_evidence,
                selected_evidence.c.observation_id == MetricObservation.id,
            )
            .outerjoin(SourceEvidence, selected_evidence.c.evidence_id == SourceEvidence.id)
            .where(
                Company.active.is_(True),
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
            dimensions=observation.dimensions,
            reporting_entity_id=observation.reporting_entity_id,
            reporting_scope_id=observation.reporting_scope_id,
            fiscal_calendar_regime_id=cast("str", observation.fiscal_calendar_regime_id),
            accounting_policy_regime_id=cast("str", observation.accounting_policy_regime_id),
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
        return self._observation_records(
            as_of=as_of,
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
            include_missing=include_missing,
            limit=limit,
            offset=offset,
        )

    def observation_snapshot(
        self,
        *,
        as_of: datetime | date | None = None,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        include_missing: bool = True,
    ) -> list[ObservationRecord]:
        """Return the complete current snapshot for internal aggregate views.

        Public callers remain bounded by :meth:`observations`. Dashboard and
        coverage composition need the complete governed cohort so a later row is
        never silently removed merely because an older baseline exceeded the
        public page size.
        """
        return self._observation_records(
            as_of=as_of,
            company_id=company_id,
            metric_id=metric_id,
            period_end=period_end,
            include_missing=include_missing,
        )

    def _observation_records(  # noqa: PLR0913
        self,
        *,
        as_of: datetime | date | None,
        company_id: str | None,
        metric_id: str | None,
        period_end: date | None,
        include_missing: bool,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ObservationRecord]:
        """Materialize either a bounded public page or a complete internal snapshot."""
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
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        with Session(self._engine) as session:
            records = [self._record(row) for row in session.execute(statement).all()]
        evidence_links = self._evidence_links_by_observation([item.id for item in records])
        return [replace(item, evidence_links=evidence_links.get(item.id, ())) for item in records]

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

    def _derivation_inputs(self, observation_id: str) -> tuple[dict[str, object], ...]:
        statement = (
            select(DerivedObservationInput)
            .where(DerivedObservationInput.derived_observation_id == observation_id)
            .order_by(DerivedObservationInput.input_ordinal)
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return tuple(
            {
                "input_observation_id": row.input_observation_id,
                "input_role": row.input_role,
                "input_ordinal": row.input_ordinal,
                "formula_version": row.formula_version,
                "input_value": str(row.input_value),
            }
            for row in rows
        )

    def _evidence_links(self, observation_id: str) -> tuple[dict[str, object], ...]:
        statement = (
            select(ObservationEvidence, SourceEvidence)
            .join(SourceEvidence, ObservationEvidence.evidence_id == SourceEvidence.id)
            .where(ObservationEvidence.observation_id == observation_id)
            .order_by(ObservationEvidence.evidence_id)
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return tuple(
            {
                "evidence_id": link.evidence_id,
                "role": link.evidence_role,
                "locator": link.locator,
                "raw_label": link.raw_label,
                "raw_value": link.raw_value,
                "disclosed_unit": link.disclosed_unit,
                "disclosed_scale": link.disclosed_scale,
                "extraction_method": link.extraction_method,
                "validation_status": link.validation_status,
                "source_url": evidence.original_url,
                "sha256": evidence.content_sha256,
                "representation": evidence.representation,
            }
            for link, evidence in rows
        )

    def _evidence_links_by_observation(
        self, observation_ids: list[str]
    ) -> dict[str, tuple[dict[str, object], ...]]:
        if not observation_ids:
            return {}
        statement = (
            select(ObservationEvidence.observation_id, ObservationEvidence, SourceEvidence)
            .join(SourceEvidence, ObservationEvidence.evidence_id == SourceEvidence.id)
            .where(ObservationEvidence.observation_id.in_(observation_ids))
            .order_by(ObservationEvidence.observation_id, ObservationEvidence.evidence_id)
        )
        grouped: dict[str, list[dict[str, object]]] = {}
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        for observation_id, link, evidence in rows:
            grouped.setdefault(observation_id, []).append(
                {
                    "evidence_id": link.evidence_id,
                    "role": link.evidence_role,
                    "locator": link.locator,
                    "source_url": evidence.original_url,
                    "sha256": evidence.content_sha256,
                    "representation": evidence.representation,
                }
            )
        return {key: tuple(value) for key, value in grouped.items()}

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
        return replace(
            record,
            revision_history=self._revision_history(observation_id),
            derivation_inputs=self._derivation_inputs(observation_id),
            evidence_links=self._evidence_links(observation_id),
        )

    def latest_period_end(self) -> date | None:
        """Return the latest current published quarter for an active company."""
        with Session(self._engine) as session:
            return session.scalar(
                select(func.max(MetricObservation.period_end))
                .join(
                    ReportingEntity,
                    MetricObservation.reporting_entity_id == ReportingEntity.id,
                )
                .join(Company, ReportingEntity.company_id == Company.id)
                .where(
                    Company.active.is_(True),
                    MetricObservation.publication_state == PublicationState.PUBLISHED.value,
                    MetricObservation.quality_state == QualityState.VALIDATED.value,
                    MetricObservation.knowledge_to.is_(None),
                )
            )

    def coverage(self, *, as_of: datetime | date | None = None) -> list[dict[str, object]]:
        """Summarize reported, proven missing, and unchecked source coverage."""
        records = self.observation_snapshot(as_of=as_of)
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        instant = _as_of_instant(as_of)
        published_cells = {
            (
                record.company_id,
                f"{record.metric_id}:{record.metric_version}",
                record.reporting_scope_id,
                date.fromisoformat(record.period_end),
            )
            for record in records
        }
        with Session(self._engine) as session:
            assessments = _latest_source_assessments(session, as_of=instant)
        for assessment in assessments:
            assessment_cell = (
                assessment.company_id,
                assessment.metric_version_id,
                assessment.reporting_scope_id,
                assessment.period_end,
            )
            if assessment_cell in published_cells:
                continue
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
        company_ids: Sequence[str] = ("tfc", "pfsi"),
    ) -> ComparisonRecord | None:
        """Build a deterministic period-specific comparison for one ordered issuer pair."""
        results = self.compare_pairs(
            metric_id=metric_id,
            period_end=period_end,
            as_of=as_of,
            company_ids=company_ids,
        )
        if results is None:
            return None
        if len(results) != 1:
            message = "compare is pairwise; use compare_pairs for a three-issuer selection"
            raise ValueError(message)
        return results[0]

    def compare_pairs(
        self,
        *,
        metric_id: str,
        period_end: date,
        as_of: datetime | date | None = None,
        company_ids: Sequence[str] = ("tfc", "pfsi"),
    ) -> tuple[ComparisonRecord, ...] | None:
        """Expand an ordered two- or three-issuer selection into bounded pairs."""
        selected = self._validated_comparison_selection(company_ids, as_of=as_of)
        selected_rows: list[ObservationRecord] = []
        for company_id in selected:
            rows = self.observations(
                as_of=as_of,
                company_id=company_id,
                metric_id=metric_id,
                period_end=period_end,
                limit=2,
            )
            if len(rows) > 1:
                message = (
                    "comparison selection is ambiguous across controlled dimensions or versions"
                )
                raise ValueError(message)
            if not rows:
                return None
            selected_rows.append(rows[0])
        return tuple(
            ComparisonRecord(
                metric_id=metric_id,
                period_end=period_end.isoformat(),
                left=left,
                right=right,
                status=(
                    result := assess_comparability(
                        _comparison_input(left, cross_company=True),
                        _comparison_input(right, cross_company=True),
                    )
                ).status.value,
                reasons=result.reasons,
            )
            for left, right in combinations(selected_rows, 2)
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
            existing_decision = session.get(HumanReviewDecision, decision_id)
            if existing_decision is not None and (
                existing_decision.reviewer != reviewer or existing_decision.rationale != rationale
            ):
                msg = "review decision already exists with different reviewer metadata"
                raise ValueError(msg)
            if existing_decision is None:
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


def _comparison_input(
    record: ObservationRecord,
    *,
    cross_company: bool = False,
) -> ComparisonInput:
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
        dimensions=tuple(sorted(record.dimensions.items())),
        period_kind=record.period_type,
        period_start=(date.fromisoformat(record.period_start) if record.period_start else None),
        period_end=date.fromisoformat(record.period_end),
        # ``record.value`` is canonical Decimal data. Strict comparisons use
        # canonical scale and never the issuer's disclosed display factor.
        scale="1" if record.value is not None else record.scale,
        reporting_entity=record.reporting_entity_id,
        fiscal_calendar_regime=record.fiscal_calendar_regime_id,
        accounting_policy_regime=record.accounting_policy_regime_id,
        cross_company_comparison=cross_company,
    )


def _period_days(record: ObservationRecord) -> int | None:
    if record.period_start is None:
        return None
    start = date.fromisoformat(record.period_start)
    return (date.fromisoformat(record.period_end) - start).days + 1
