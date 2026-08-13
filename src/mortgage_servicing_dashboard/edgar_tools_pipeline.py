"""Manifest-bounded synchronization through the public-edgartools adapter.

This coordinator deliberately has no alternate acquisition route.  It resolves one
company, lists filings once, retains the exact approved primary documents, and passes
those retained bytes to the deterministic inline-XBRL parser.  Persistence is an
injected atomic boundary and is never called by a dry run or a partially failed run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, TypeAlias, cast

from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    Attachment,
    AttachmentAcquisition,
    Company,
    Filing,
    RetainedContent,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import EdgarToolsAdapterError
from mortgage_servicing_dashboard.financial_discovery import (
    FinancialClassification,
    FinancialDiscoveryError,
    FinancialFieldRegistry,
    RawFilingFactLocator,
    discover_retained_document_fields,
)
from mortgage_servicing_dashboard.xbrl import XbrlDataError, XbrlPeriodType

_OVERLAP_DAYS = 7
_MAX_APPROVED_CASES = 4
_ELIGIBLE_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")
_APPROVED_REVIEW_STATES = frozenset({"INDEPENDENTLY_CROSS_CHECKED", "REVIEWER_APPROVED"})
_QUARTER_MARKERS = frozenset({"Q1", "Q2", "Q3", "Q4"})


class EdgarToolsSyncState(StrEnum):
    """Terminal and per-filing states without a missing-disclosure inference."""

    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    LINKED = "LINKED"
    UNCHANGED = "UNCHANGED"
    MISMATCH = "MISMATCH"
    AMBIGUOUS = "AMBIGUOUS"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"

    # Retained compatibility names for callers migrating from the hosted coordinator.
    PARSER_UNQUALIFIED = "PARSER_UNQUALIFIED"
    AUTHENTICATION_BLOCKED = "AUTHENTICATION_BLOCKED"
    TIER_BLOCKED = "TIER_BLOCKED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_NOT_AVAILABLE_VIA_PROVIDER = "SOURCE_NOT_AVAILABLE_VIA_PROVIDER"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class EdgarToolsCompany:
    """Governed issuer identity; CIK is the stable acquisition key."""

    company_id: str
    ticker: str
    cik: str


class EdgarToolsAdapterProtocol(Protocol):
    """Small subset of :class:`EdgarToolsAdapter` used by this coordinator."""

    def company(self, cik_or_ticker: str) -> Company:
        """Resolve an exact company identity."""
        ...

    def filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: date | tuple[date, date] | None = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        """List explicitly filtered filings."""
        ...

    def attachments(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        """List filing attachments without acquiring content."""
        ...

    def acquire_attachment(
        self,
        accession: str,
        document: str,
        *,
        expected_cik: str | None = None,
        retain: bool = True,
    ) -> AttachmentAcquisition:
        """Acquire and retain one exact attachment."""
        ...


@dataclass(frozen=True, slots=True)
class ValidatedFiling:
    """Exact validated result supplied to the injected atomic persistence seam."""

    case_id: str
    mapping_version: str
    company_id: str
    cik: str
    accession_number: str
    form: str
    filing_date: date
    report_period: date
    fiscal_year: int
    fiscal_quarter: str
    amendment: bool
    revision_of_accession: str | None
    primary_document: str
    source_url: str
    evidence_sha256: str
    evidence_byte_length: int
    evidence_location: str
    evidence_retrieved_at: datetime
    evidence_representation: str
    evidence_capture_method: str
    evidence_media_type: str
    field_id: str
    classification: FinancialClassification
    reporting_entity_id: str
    reporting_scope_id: str
    qualified_concept: str
    original_label: str
    raw_display_string: str
    normalized_value: Decimal
    context_ref: str
    unit: str
    decimals: int | str | None
    source_scale: Decimal
    source_element_ids: tuple[str, ...]
    source_object_count: int
    source_locators: tuple[str, ...]


class CommittedCaseState(StrEnum):
    """Repository disposition for one validated case after a successful commit."""

    PUBLISHED = "PUBLISHED"
    LINKED = "LINKED"
    QUARANTINED = "QUARANTINED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class CommittedCaseOutcome:
    """Typed committed disposition for one validated manifest case."""

    case_id: str
    accession_number: str
    state: CommittedCaseState


@dataclass(frozen=True, slots=True)
class AtomicPersistenceResult:
    """Counts and per-case outcomes from one committed persistence transaction."""

    outcomes: tuple[CommittedCaseOutcome, ...] = ()
    evidence: int = 0
    filings: int = 0
    documents: int = 0
    raw_facts: int = 0
    observations: int = 0
    revisions: int = 0
    linked: int = 0
    quarantined: int = 0

    def as_payload(self) -> dict[str, int]:
        """Return the established bounded count payload without financial values."""
        return {
            "evidence": self.evidence,
            "filings": self.filings,
            "documents": self.documents,
            "raw_facts": self.raw_facts,
            "observations": self.observations,
            "revisions": self.revisions,
            "linked": self.linked,
            "quarantined": self.quarantined,
        }


class AtomicPersistence(Protocol):
    """Persistence boundary that commits all validated revisions atomically."""

    def persist_atomically(
        self,
        results: tuple[ValidatedFiling, ...],
    ) -> AtomicPersistenceResult:
        """Commit all results or none of them."""
        ...


PersistenceCallback: TypeAlias = Callable[
    [tuple[ValidatedFiling, ...]],
    AtomicPersistenceResult,
]


@dataclass(frozen=True, slots=True)
class PipelineCallCounts:
    """Bounded acquisition, parser, and persistence call counts."""

    company: int = 0
    filings: int = 0
    attachments: int = 0
    acquire_attachment: int = 0
    discover_retained_document_fields: int = 0
    persistence: int = 0
    fallback: int = 0
    retry: int = 0
    company_facts: int = 0
    filing_xbrl: int = 0
    filing_structure: int = 0

    def as_payload(self) -> dict[str, int]:
        """Return explicit counts, including prohibited-path zeros."""
        return {
            "company": self.company,
            "filings": self.filings,
            "attachments": self.attachments,
            "acquire_attachment": self.acquire_attachment,
            "discover_retained_document_fields": self.discover_retained_document_fields,
            "persistence": self.persistence,
            "fallback": self.fallback,
            "retry": self.retry,
            "company_facts": self.company_facts,
            "filing_xbrl": self.filing_xbrl,
            "filing_structure": self.filing_structure,
        }


@dataclass(frozen=True, slots=True)
class FilingSyncResult:
    """Safe bounded outcome for one approved filing case."""

    accession_number: str
    form: str
    filing_date: str
    already_known: bool
    state: EdgarToolsSyncState
    retained_evidence_ids: tuple[str, ...]
    safe_detail: str
    report_period: str | None = None
    amendment: bool = False
    case_id: str = ""
    field_id: str = ""
    classification: FinancialClassification | None = None
    evidence_sha256: str | None = None

    def as_payload(self) -> dict[str, object]:
        """Return identities and classifications, never raw bodies or values."""
        return {
            "case_id": self.case_id,
            "accession_number": self.accession_number,
            "form": self.form,
            "filing_date": self.filing_date,
            "report_period": self.report_period,
            "amendment": self.amendment,
            "already_known": self.already_known,
            "field_id": self.field_id,
            "classification": None if self.classification is None else self.classification.value,
            "state": self.state.value,
            "retained_evidence_ids": list(self.retained_evidence_ids),
            "evidence_sha256": self.evidence_sha256,
            "detail": self.safe_detail,
        }


@dataclass(frozen=True, slots=True)
class EdgarToolsSyncSummary:
    """Safe company summary for one bounded manifest synchronization."""

    company_id: str
    ticker: str
    cik: str
    dry_run: bool
    overlap_start: str | None
    discovered_count: int
    eligible_count: int
    filing_results: tuple[FilingSyncResult, ...]
    retained_metadata_evidence_ids: tuple[str, ...]
    terminal_state: EdgarToolsSyncState
    call_counts: PipelineCallCounts = PipelineCallCounts()
    core_count: int = 0
    optional_count: int = 0
    validated_count: int = 0
    published_count: int = 0
    linked_count: int = 0
    quarantined_count: int = 0
    failed_count: int = 0
    approved_accessions: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, object]:
        """Return a deterministic summary with no SEC identity string or source values."""
        state_counts = {
            state.value: sum(result.state is state for result in self.filing_results)
            for state in EdgarToolsSyncState
            if any(result.state is state for result in self.filing_results)
        }
        return {
            "company_id": self.company_id,
            "ticker": self.ticker,
            "cik": self.cik,
            "dry_run": self.dry_run,
            "overlap_start": self.overlap_start,
            "discovered_count": self.discovered_count,
            "eligible_count": self.eligible_count,
            "validated_count": self.validated_count,
            "published_count": self.published_count,
            "linked_count": self.linked_count,
            "quarantined_count": self.quarantined_count,
            "failed_count": self.failed_count,
            "core_count": self.core_count,
            "optional_count": self.optional_count,
            "approved_accessions": list(self.approved_accessions),
            "evidence_hashes": list(self.evidence_hashes),
            "fallback_call_count": self.call_counts.fallback,
            "retry_count": self.call_counts.retry,
            "call_counts": self.call_counts.as_payload(),
            "state_counts": state_counts,
            "terminal_state": self.terminal_state.value,
            "retained_metadata_evidence_ids": list(self.retained_metadata_evidence_ids),
            "filings": [result.as_payload() for result in self.filing_results],
        }


@dataclass(frozen=True, slots=True)
class PreparedEdgarToolsSync:
    """Fully acquired and validated company work awaiting an atomic batch commit."""

    summary: EdgarToolsSyncSummary
    validated_filings: tuple[ValidatedFiling, ...]


@dataclass(slots=True)
class _MutableCallCounts:
    company: int = 0
    filings: int = 0
    attachments: int = 0
    acquire_attachment: int = 0
    discover_retained_document_fields: int = 0
    persistence: int = 0

    def freeze(self) -> PipelineCallCounts:
        return PipelineCallCounts(
            company=self.company,
            filings=self.filings,
            attachments=self.attachments,
            acquire_attachment=self.acquire_attachment,
            discover_retained_document_fields=self.discover_retained_document_fields,
            persistence=self.persistence,
        )


@dataclass(frozen=True, slots=True)
class _GoldenCase:
    case_id: str
    issuer_id: str
    ticker: str
    cik: str
    field_id: str
    classification: FinancialClassification
    accession: str
    form: str
    filing_date: date
    report_period: date
    fiscal_year: int
    fiscal_quarter: str
    amendment: bool
    revision_of_accession: str | None
    primary_document: str
    source_url: str
    evidence_sha256: str
    evidence_byte_length: int
    evidence_representation: str
    evidence_capture_method: str | None
    qualified_concept: str
    original_label: str
    raw_display_string: str
    normalized_value: Decimal
    context_ref: str
    period_type: XbrlPeriodType
    dimensions: tuple[tuple[str, str], ...]
    unit: str
    decimals: str | None
    source_scale: Decimal
    source_element_ids: tuple[str, ...]
    source_object_count: int
    semantic_fact_count: int


@dataclass(frozen=True, slots=True)
class GoldenManifest:
    """Validated four-case golden-source contract."""

    version: str
    mapping_version: str
    cases: tuple[_GoldenCase, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> GoldenManifest:
        """Validate and freeze an independently approved four-case manifest."""
        version = _required_string(payload, "manifest_version", location="manifest")
        mapping_version = _required_string(payload, "mapping_version", location="manifest")
        status = _required_string(payload, "status", location="manifest")
        if status not in _APPROVED_REVIEW_STATES:
            message = "golden manifest is not independently approved"
            raise ValueError(message)
        raw_cases = _sequence(payload.get("cases"), location="manifest.cases")
        approved = tuple(
            _string_item(value, location="manifest.approved_expectations")
            for value in _sequence(
                payload.get("approved_expectations"),
                location="manifest.approved_expectations",
            )
        )
        if len(raw_cases) != _MAX_APPROVED_CASES or len(approved) != _MAX_APPROVED_CASES:
            message = "golden manifest must contain exactly four approved cases"
            raise ValueError(message)
        cases = tuple(
            _golden_case(_mapping(value, location=f"manifest.cases[{index}]"))
            for index, value in enumerate(raw_cases)
        )
        case_ids = tuple(case.case_id for case in cases)
        if len(set(case_ids)) != len(case_ids) or set(approved) != set(case_ids):
            message = "approved expectations must identify the four unique cases"
            raise ValueError(message)
        if len({case.accession for case in cases}) != len(cases):
            message = "golden manifest accessions must be unique"
            raise ValueError(message)
        return cls(version=version, mapping_version=mapping_version, cases=cases)


class EdgarToolsSyncPipeline:
    """Exact four-case coordinator over one injected public-edgartools adapter."""

    def __init__(
        self,
        *,
        adapter: EdgarToolsAdapterProtocol,
        registry: FinancialFieldRegistry,
        golden_manifest: Mapping[str, object] | GoldenManifest,
        persistence: AtomicPersistence | PersistenceCallback | None = None,
    ) -> None:
        """Bind acquisition, mapping, approval, and optional atomic persistence seams."""
        manifest = (
            golden_manifest
            if isinstance(golden_manifest, GoldenManifest)
            else GoldenManifest.from_mapping(golden_manifest)
        )
        if manifest.mapping_version != registry.version:
            message = "golden manifest and financial registry versions differ"
            raise ValueError(message)
        _validate_manifest_mappings(manifest, registry)
        self._adapter = adapter
        self._registry = registry
        self._manifest = manifest
        self._persistence = persistence

    def sync_company(
        self,
        company: EdgarToolsCompany,
        *,
        since: date | None = None,
        dry_run: bool = False,
        known_accessions: frozenset[str] = frozenset(),
    ) -> EdgarToolsSyncSummary:
        """Prepare one company and, unless dry-run, commit that complete batch."""
        prepared = self.prepare_company(
            company,
            since=since,
            dry_run=dry_run,
            known_accessions=known_accessions,
        )
        if dry_run:
            return prepared.summary
        return self.persist_prepared_batch((prepared,))[0]

    def prepare_company(
        self,
        company: EdgarToolsCompany,
        *,
        since: date | None = None,
        dry_run: bool = False,
        known_accessions: frozenset[str] = frozenset(),
    ) -> PreparedEdgarToolsSync:
        """Acquire and validate one company without invoking persistence."""
        calls = _MutableCallCounts()
        overlap_start = None if since is None else since - timedelta(days=_OVERLAP_DAYS)
        cases = tuple(case for case in self._manifest.cases if case.issuer_id == company.company_id)
        if not cases or any(
            case.cik != company.cik or case.ticker != company.ticker for case in cases
        ):
            return PreparedEdgarToolsSync(
                summary=_summary(
                    company,
                    dry_run=dry_run,
                    overlap_start=overlap_start,
                    calls=calls,
                    terminal_state=EdgarToolsSyncState.FAILED,
                    failed_count=1,
                ),
                validated_filings=(),
            )

        try:
            calls.company += 1
            resolved = self._adapter.company(company.cik)
            if resolved.cik != company.cik or company.ticker not in resolved.tickers:
                return PreparedEdgarToolsSync(
                    summary=_summary(
                        company,
                        dry_run=dry_run,
                        overlap_start=overlap_start,
                        calls=calls,
                        terminal_state=EdgarToolsSyncState.FAILED,
                        failed_count=1,
                    ),
                    validated_filings=(),
                )
            filing_dates = tuple(case.filing_date for case in cases)
            calls.filings += 1
            discovered = self._adapter.filings(
                company.cik,
                forms=_ELIGIBLE_FORMS,
                filing_date=(min(filing_dates), max(filing_dates)),
                include_amendments=True,
            )
        except EdgarToolsAdapterError:
            return PreparedEdgarToolsSync(
                summary=_summary(
                    company,
                    dry_run=dry_run,
                    overlap_start=overlap_start,
                    calls=calls,
                    terminal_state=EdgarToolsSyncState.FAILED,
                    failed_count=1,
                ),
                validated_filings=(),
            )

        planned = tuple(
            case
            for case in cases
            if overlap_start is None
            or case.filing_date >= overlap_start
            or case.accession not in known_accessions
        )
        by_accession: dict[str, list[Filing]] = {}
        approved_accessions = frozenset(case.accession for case in cases)
        for filing in discovered:
            if filing.accession_number in approved_accessions:
                by_accession.setdefault(filing.accession_number, []).append(filing)

        results: list[FilingSyncResult] = []
        validated: list[ValidatedFiling] = []
        for case in planned:
            matches = by_accession.get(case.accession, [])
            if len(matches) != 1:
                state = (
                    EdgarToolsSyncState.MISMATCH if not matches else EdgarToolsSyncState.AMBIGUOUS
                )
                results.append(
                    _case_result(
                        case,
                        already_known=case.accession in known_accessions,
                        state=state,
                        detail=(
                            "approved filing was not discovered"
                            if not matches
                            else "approved accession was returned more than once"
                        ),
                    )
                )
                continue
            result, qualified = self._sync_filing(
                case,
                matches[0],
                already_known=case.accession in known_accessions,
                calls=calls,
            )
            results.append(result)
            if qualified is not None:
                validated.append(qualified)

        quarantined_count = sum(
            result.state in {EdgarToolsSyncState.MISMATCH, EdgarToolsSyncState.AMBIGUOUS}
            for result in results
        )
        failed_count = sum(result.state is EdgarToolsSyncState.FAILED for result in results)
        terminal_state = _terminal_state(
            results,
            persistence_missing=False,
            published_count=0,
        )
        evidence_hashes = tuple(
            result.evidence_sha256 for result in results if result.evidence_sha256 is not None
        )
        classifications = tuple(case.classification for case in planned)
        return PreparedEdgarToolsSync(
            summary=EdgarToolsSyncSummary(
                company_id=company.company_id,
                ticker=company.ticker,
                cik=company.cik,
                dry_run=dry_run,
                overlap_start=None if overlap_start is None else overlap_start.isoformat(),
                discovered_count=len(discovered),
                eligible_count=len(planned),
                filing_results=tuple(results),
                retained_metadata_evidence_ids=evidence_hashes,
                terminal_state=terminal_state,
                call_counts=calls.freeze(),
                core_count=sum(
                    item is FinancialClassification.CORE_FINANCIAL for item in classifications
                ),
                optional_count=sum(
                    item is FinancialClassification.OPTIONAL_SERVICING for item in classifications
                ),
                validated_count=len(validated),
                quarantined_count=quarantined_count,
                failed_count=failed_count,
                approved_accessions=tuple(case.accession for case in planned),
                evidence_hashes=evidence_hashes,
            ),
            validated_filings=tuple(validated),
        )

    def persist_prepared_batch(
        self,
        prepared: tuple[PreparedEdgarToolsSync, ...],
    ) -> tuple[EdgarToolsSyncSummary, ...]:
        """Commit multiple fully prepared companies in one atomic transaction.

        A preparation failure for any company prevents the persistence callback from
        being invoked for every company in the batch.
        """
        summaries = tuple(item.summary for item in prepared)
        if not prepared:
            return ()
        if any(summary.dry_run for summary in summaries):
            message = "dry-run preparation cannot be persisted"
            raise ValueError(message)
        blocking_states = {
            EdgarToolsSyncState.FAILED,
            EdgarToolsSyncState.QUARANTINED,
            EdgarToolsSyncState.MISMATCH,
            EdgarToolsSyncState.AMBIGUOUS,
        }
        if any(summary.terminal_state in blocking_states for summary in summaries):
            return summaries
        validated = tuple(
            filing
            for company_preparation in prepared
            for filing in company_preparation.validated_filings
        )
        if not validated:
            return summaries
        if self._persistence is None:
            return tuple(
                replace(
                    summary,
                    terminal_state=EdgarToolsSyncState.FAILED,
                    failed_count=summary.failed_count + 1,
                )
                if any(
                    result.state is EdgarToolsSyncState.VALIDATED
                    for result in summary.filing_results
                )
                else summary
                for summary in summaries
            )

        committed = self._persist(validated)
        outcomes = _validated_commit_outcomes(validated, committed)
        return tuple(_apply_commit_outcomes(summary, outcomes) for summary in summaries)

    def _sync_filing(  # noqa: PLR0911 - every fail-closed gate returns immediately.
        self,
        case: _GoldenCase,
        filing: Filing,
        *,
        already_known: bool,
        calls: _MutableCallCounts,
    ) -> tuple[FilingSyncResult, ValidatedFiling | None]:
        identity_mismatches = _filing_mismatches(case, filing)
        if identity_mismatches:
            return (
                _case_result(
                    case,
                    already_known=already_known,
                    state=EdgarToolsSyncState.MISMATCH,
                    detail="filing identity mismatch: " + ",".join(identity_mismatches),
                ),
                None,
            )
        try:
            calls.attachments += 1
            attachments = self._adapter.attachments(
                case.accession,
                expected_cik=case.cik,
            )
            primary = _select_primary(case, attachments)
            if isinstance(primary, tuple):
                state, detail = primary
                return _case_result(
                    case,
                    already_known=already_known,
                    state=state,
                    detail=detail,
                ), None
            calls.acquire_attachment += 1
            acquisition = self._adapter.acquire_attachment(
                case.accession,
                case.primary_document,
                expected_cik=case.cik,
                retain=True,
            )
            integrity_mismatches = _acquisition_mismatches(case, primary, acquisition)
            if integrity_mismatches:
                return _case_result(
                    case,
                    already_known=already_known,
                    state=EdgarToolsSyncState.MISMATCH,
                    detail="retained evidence mismatch: " + ",".join(integrity_mismatches),
                    evidence_sha256=acquisition.content.sha256,
                ), None
            retained = cast("RetainedContent", acquisition.retained)
            calls.discover_retained_document_fields += 1
            discoveries = discover_retained_document_fields(
                acquisition.content.content,
                issuer_id=case.issuer_id,
                cik=case.cik,
                evidence_id=retained.content_sha256,
                accession_number=case.accession,
                source_document=case.primary_document,
                source_url=case.source_url,
                form=case.form,
                filed=case.filing_date,
                registry=self._registry,
            )
        except EdgarToolsAdapterError:
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.FAILED,
                detail="edgartools adapter error",
            ), None
        except (FinancialDiscoveryError, XbrlDataError):
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.FAILED,
                detail="deterministic document parser error",
                evidence_sha256=acquisition.content.sha256,
            ), None

        matching = tuple(
            discovery for discovery in discoveries if discovery.mapping.field_id == case.field_id
        )
        if len(matching) != 1:
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.MISMATCH,
                detail="selected field discovery count mismatch",
                evidence_sha256=retained.content_sha256,
            ), None
        discovery = matching[0]
        period_candidates = tuple(
            candidate
            for candidate in discovery.candidates
            if candidate.period_end == case.report_period
        )
        if len(period_candidates) > 1:
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.AMBIGUOUS,
                detail="selected field has ambiguous candidates",
                evidence_sha256=retained.content_sha256,
            ), None
        if not period_candidates:
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.MISMATCH,
                detail="selected field has no available candidate",
                evidence_sha256=retained.content_sha256,
            ), None
        candidate = period_candidates[0]
        candidate_mismatches = _candidate_mismatches(case, candidate)
        if candidate_mismatches:
            return _case_result(
                case,
                already_known=already_known,
                state=EdgarToolsSyncState.MISMATCH,
                detail="golden candidate mismatch: " + ",".join(candidate_mismatches),
                evidence_sha256=retained.content_sha256,
            ), None
        mapping = discovery.mapping
        qualified = ValidatedFiling(
            case_id=case.case_id,
            mapping_version=self._manifest.mapping_version,
            company_id=case.issuer_id,
            cik=case.cik,
            accession_number=case.accession,
            form=case.form,
            filing_date=case.filing_date,
            report_period=case.report_period,
            fiscal_year=case.fiscal_year,
            fiscal_quarter=case.fiscal_quarter,
            amendment=case.amendment,
            revision_of_accession=case.revision_of_accession,
            primary_document=case.primary_document,
            source_url=case.source_url,
            evidence_sha256=retained.content_sha256,
            evidence_byte_length=retained.byte_length,
            evidence_location=retained.retention_location,
            evidence_retrieved_at=acquisition.content.retrieved_at,
            evidence_representation=retained.representation.value,
            evidence_capture_method=retained.capture_method,
            evidence_media_type=retained.media_type,
            field_id=case.field_id,
            classification=case.classification,
            reporting_entity_id=mapping.xbrl.reporting_entity_id,
            reporting_scope_id=mapping.xbrl.reporting_scope_id,
            qualified_concept=candidate.qualified_concept,
            original_label=case.original_label,
            raw_display_string=candidate.raw_value,
            normalized_value=candidate.normalized_value,
            context_ref=candidate.context_ref,
            unit=candidate.unit,
            decimals=candidate.decimals,
            source_scale=case.source_scale,
            source_element_ids=candidate.source_element_ids,
            source_object_count=candidate.source_object_count,
            source_locators=candidate.source_locators,
        )
        return _case_result(
            case,
            already_known=already_known,
            state=EdgarToolsSyncState.VALIDATED,
            detail="approved retained-document fact validated",
            evidence_sha256=retained.content_sha256,
        ), qualified

    def _persist(self, results: tuple[ValidatedFiling, ...]) -> AtomicPersistenceResult:
        persistence = self._persistence
        if persistence is None:
            message = "atomic persistence is not configured"
            raise RuntimeError(message)
        if callable(persistence):
            return persistence(results)
        return persistence.persist_atomically(results)


def _summary(  # noqa: PLR0913 - explicit summary inputs avoid hidden mutable state.
    company: EdgarToolsCompany,
    *,
    dry_run: bool,
    overlap_start: date | None,
    calls: _MutableCallCounts,
    terminal_state: EdgarToolsSyncState,
    failed_count: int,
) -> EdgarToolsSyncSummary:
    return EdgarToolsSyncSummary(
        company_id=company.company_id,
        ticker=company.ticker,
        cik=company.cik,
        dry_run=dry_run,
        overlap_start=None if overlap_start is None else overlap_start.isoformat(),
        discovered_count=0,
        eligible_count=0,
        filing_results=(),
        retained_metadata_evidence_ids=(),
        terminal_state=terminal_state,
        call_counts=calls.freeze(),
        failed_count=failed_count,
    )


def _terminal_state(  # noqa: PLR0911 - state precedence is clearest as early returns.
    results: Sequence[FilingSyncResult],
    *,
    persistence_missing: bool,
    published_count: int,
) -> EdgarToolsSyncState:
    states = {result.state for result in results}
    if persistence_missing or EdgarToolsSyncState.FAILED in states:
        return EdgarToolsSyncState.FAILED
    if states & {
        EdgarToolsSyncState.MISMATCH,
        EdgarToolsSyncState.AMBIGUOUS,
        EdgarToolsSyncState.QUARANTINED,
    }:
        return EdgarToolsSyncState.QUARANTINED
    if published_count:
        return EdgarToolsSyncState.PUBLISHED
    if EdgarToolsSyncState.LINKED in states:
        return EdgarToolsSyncState.LINKED
    if EdgarToolsSyncState.UNCHANGED in states:
        return EdgarToolsSyncState.UNCHANGED
    if EdgarToolsSyncState.VALIDATED in states:
        return EdgarToolsSyncState.VALIDATED
    return EdgarToolsSyncState.DISCOVERED


def _validated_commit_outcomes(
    validated: tuple[ValidatedFiling, ...],
    committed: AtomicPersistenceResult,
) -> Mapping[str, CommittedCaseOutcome]:
    expected = {item.case_id: item.accession_number for item in validated}
    actual: dict[str, CommittedCaseOutcome] = {}
    for outcome in committed.outcomes:
        if outcome.case_id in actual:
            message = "atomic persistence returned a duplicate case outcome"
            raise RuntimeError(message)
        actual[outcome.case_id] = outcome
    if set(actual) != set(expected) or any(
        actual[case_id].accession_number != accession for case_id, accession in expected.items()
    ):
        message = "atomic persistence outcomes do not match the validated batch"
        raise RuntimeError(message)
    disposition_counts = {
        state: sum(outcome.state is state for outcome in actual.values())
        for state in CommittedCaseState
    }
    if (
        committed.observations != disposition_counts[CommittedCaseState.PUBLISHED]
        or committed.linked != disposition_counts[CommittedCaseState.LINKED]
        or committed.quarantined != disposition_counts[CommittedCaseState.QUARANTINED]
    ):
        message = "atomic persistence counts do not match its per-case outcomes"
        raise RuntimeError(message)
    return actual


def _apply_commit_outcomes(
    summary: EdgarToolsSyncSummary,
    outcomes: Mapping[str, CommittedCaseOutcome],
) -> EdgarToolsSyncSummary:
    state_map = {
        CommittedCaseState.PUBLISHED: EdgarToolsSyncState.PUBLISHED,
        CommittedCaseState.LINKED: EdgarToolsSyncState.LINKED,
        CommittedCaseState.QUARANTINED: EdgarToolsSyncState.QUARANTINED,
        CommittedCaseState.UNCHANGED: EdgarToolsSyncState.UNCHANGED,
    }
    filing_results = tuple(
        replace(result, state=state_map[outcomes[result.case_id].state])
        if result.state is EdgarToolsSyncState.VALIDATED and result.case_id in outcomes
        else result
        for result in summary.filing_results
    )
    published_count = sum(
        result.state is EdgarToolsSyncState.PUBLISHED for result in filing_results
    )
    linked_count = sum(result.state is EdgarToolsSyncState.LINKED for result in filing_results)
    quarantined_count = sum(
        result.state
        in {
            EdgarToolsSyncState.MISMATCH,
            EdgarToolsSyncState.AMBIGUOUS,
            EdgarToolsSyncState.QUARANTINED,
        }
        for result in filing_results
    )
    call_counts = replace(
        summary.call_counts,
        persistence=summary.call_counts.persistence + 1,
    )
    return replace(
        summary,
        filing_results=filing_results,
        terminal_state=_terminal_state(
            filing_results,
            persistence_missing=False,
            published_count=published_count,
        ),
        call_counts=call_counts,
        published_count=published_count,
        linked_count=linked_count,
        quarantined_count=quarantined_count,
    )


def _case_result(
    case: _GoldenCase,
    *,
    already_known: bool,
    state: EdgarToolsSyncState,
    detail: str,
    evidence_sha256: str | None = None,
) -> FilingSyncResult:
    evidence_ids = () if evidence_sha256 is None else (evidence_sha256,)
    return FilingSyncResult(
        accession_number=case.accession,
        form=case.form,
        filing_date=case.filing_date.isoformat(),
        already_known=already_known,
        state=state,
        retained_evidence_ids=evidence_ids,
        safe_detail=detail,
        report_period=case.report_period.isoformat(),
        amendment=case.amendment,
        case_id=case.case_id,
        field_id=case.field_id,
        classification=case.classification,
        evidence_sha256=evidence_sha256,
    )


def _filing_mismatches(case: _GoldenCase, filing: Filing) -> tuple[str, ...]:
    checks = (
        (filing.cik == case.cik, "cik"),
        (filing.accession_number == case.accession, "accession"),
        (filing.form == case.form, "form"),
        (filing.filing_date == case.filing_date, "filing_date"),
        (filing.report_period == case.report_period, "report_period"),
        (filing.primary_document == case.primary_document, "primary_document"),
        (filing.amendment is case.amendment, "amendment"),
    )
    return tuple(name for matches, name in checks if not matches)


def _select_primary(
    case: _GoldenCase,
    attachments: Sequence[Attachment],
) -> Attachment | tuple[EdgarToolsSyncState, str]:
    primaries = tuple(attachment for attachment in attachments if attachment.is_primary)
    if not primaries:
        return EdgarToolsSyncState.MISMATCH, "primary attachment was not present"
    if len(primaries) != 1:
        return EdgarToolsSyncState.AMBIGUOUS, "multiple primary attachments were returned"
    primary = primaries[0]
    checks = (
        primary.cik == case.cik,
        primary.accession_number == case.accession,
        primary.document == case.primary_document,
        primary.source_url == case.source_url,
    )
    if not all(checks):
        return EdgarToolsSyncState.MISMATCH, "primary attachment identity did not match"
    return primary


def _acquisition_mismatches(
    case: _GoldenCase,
    primary: Attachment,
    acquisition: AttachmentAcquisition,
) -> tuple[str, ...]:
    retained = acquisition.retained
    content = acquisition.content
    computed_hash = hashlib.sha256(content.content).hexdigest()
    checks = (
        (acquisition.attachment == primary, "attachment"),
        (content.cik == case.cik, "cik"),
        (content.accession_number == case.accession, "accession"),
        (content.document == case.primary_document, "document"),
        (content.source_url == case.source_url, "source_url"),
        (content.sha256 == computed_hash, "content_hash"),
        (content.sha256 == case.evidence_sha256, "approved_hash"),
        (content.byte_length == case.evidence_byte_length, "byte_length"),
        (retained is not None, "retention"),
        (
            retained is not None and retained.content_sha256 == content.sha256,
            "retained_hash",
        ),
        (
            retained is not None and retained.byte_length == content.byte_length,
            "retained_length",
        ),
        (
            retained is not None and retained.representation.value == case.evidence_representation,
            "representation",
        ),
        (
            case.evidence_capture_method is None
            or (retained is not None and retained.capture_method == case.evidence_capture_method),
            "capture_method",
        ),
    )
    return tuple(name for matches, name in checks if not matches)


def _candidate_mismatches(
    case: _GoldenCase,
    candidate: RawFilingFactLocator,
) -> tuple[str, ...]:
    raw_decimal = _raw_display_decimal(candidate.raw_value)
    effective_scale = (
        case.source_scale
        if raw_decimal == 0 and candidate.normalized_value == 0
        else candidate.normalized_value / raw_decimal
    )
    dimensions = tuple((item.dimension, item.member) for item in candidate.dimensions)
    checks = (
        (candidate.accession_number == case.accession, "accession"),
        (candidate.source_document == case.primary_document, "document"),
        (candidate.source_url == case.source_url, "source_url"),
        (candidate.qualified_concept == case.qualified_concept, "concept"),
        (candidate.raw_value == case.raw_display_string, "raw_display"),
        (candidate.normalized_value == case.normalized_value, "decimal"),
        (candidate.context_ref == case.context_ref, "context"),
        (candidate.period_type is case.period_type, "period_type"),
        (candidate.period_end == case.report_period, "period_end"),
        (dimensions == case.dimensions, "dimensions"),
        (candidate.unit == case.unit, "unit"),
        (str(candidate.decimals) == case.decimals, "decimals"),
        (effective_scale == case.source_scale, "scale"),
        (tuple(sorted(candidate.source_element_ids)) == case.source_element_ids, "source_ids"),
        (candidate.source_object_count == case.source_object_count, "source_count"),
        (case.semantic_fact_count == 1, "semantic_count"),
        (candidate.entity_identifier.zfill(10) == case.cik, "entity"),
    )
    return tuple(name for matches, name in checks if not matches)


def _raw_display_decimal(value: str) -> Decimal:
    normalized = value.strip().replace(",", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = "-" + normalized[1:-1]
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        message = "approved raw display is not an exact decimal"
        raise FinancialDiscoveryError(message) from error


def _validate_manifest_mappings(
    manifest: GoldenManifest,
    registry: FinancialFieldRegistry,
) -> None:
    for case in manifest.cases:
        mappings = tuple(
            mapping
            for mapping in registry.mappings
            if mapping.issuer_id == case.issuer_id
            and mapping.xbrl.cik == case.cik
            and mapping.field_id == case.field_id
        )
        if len(mappings) != 1:
            message = "each golden case requires one selected-field mapping"
            raise ValueError(message)
        mapping = mappings[0]
        if (
            case.form not in mapping.eligible_forms
            or case.classification is not mapping.classification
            or case.qualified_concept != mapping.xbrl.qualified_concept
            or case.original_label != mapping.display_name
        ):
            message = "golden case and selected-field mapping semantics differ"
            raise ValueError(message)


def _golden_case(payload: Mapping[str, object]) -> _GoldenCase:
    location = "golden case"
    review_status = _required_string(payload, "review_status", location=location)
    if review_status not in _APPROVED_REVIEW_STATES:
        message = "every golden case must be independently approved"
        raise ValueError(message)
    source = _mapping(payload.get("edgartools_source"), location="golden case source")
    fact = _mapping(payload.get("approved_fact"), location="golden case approved_fact")
    form = _required_string(payload, "form", location=location)
    amendment = _boolean(payload, "amendment", location=location)
    if form not in _ELIGIBLE_FORMS or form.endswith("/A") is not amendment:
        message = "golden case form and amendment marker differ"
        raise ValueError(message)
    fiscal_year = _positive_int(payload, "fiscal_year", location=location)
    fiscal_quarter = _required_string(payload, "fiscal_quarter", location=location)
    annual_form = form.startswith("10-K")
    if (annual_form and fiscal_quarter != "FY") or (
        not annual_form and fiscal_quarter not in _QUARTER_MARKERS
    ):
        message = "golden case fiscal quarter marker does not match its form"
        raise ValueError(message)
    dimensions = tuple(
        _dimension(value, location="golden case approved_fact.dimensions")
        for value in _sequence(
            fact.get("dimensions"),
            location="golden case approved_fact.dimensions",
        )
    )
    revision = payload.get("revision_of_accession", payload.get("amends_accession"))
    return _GoldenCase(
        case_id=_required_string(payload, "case_id", location=location),
        issuer_id=_required_string(payload, "issuer_id", location=location),
        ticker=_required_string(payload, "ticker", location=location),
        cik=_required_string(payload, "cik", location=location),
        field_id=_required_string(payload, "field_id", location=location),
        classification=FinancialClassification(
            _required_string(payload, "classification", location=location)
        ),
        accession=_required_string(payload, "accession", location=location),
        form=form,
        filing_date=_required_date(payload, "filing_date", location=location),
        report_period=_required_date(payload, "period_end", location=location),
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        amendment=amendment,
        revision_of_accession=(
            None if revision is None else _string_item(revision, location="revision accession")
        ),
        primary_document=_required_string(payload, "primary_document", location=location),
        source_url=_required_string(payload, "source_url", location=location),
        evidence_sha256=_required_string(source, "sha256", location="golden case source"),
        evidence_byte_length=_positive_int(
            source,
            "byte_length",
            location="golden case source",
        ),
        evidence_representation=_required_string(
            source,
            "representation",
            location="golden case source",
        ),
        evidence_capture_method=_optional_string(source.get("capture_method")),
        qualified_concept=_required_string(
            fact,
            "qualified_concept",
            location="golden case approved_fact",
        ),
        original_label=_required_string(
            fact,
            "original_label",
            location="golden case approved_fact",
        ),
        raw_display_string=_required_string(
            fact,
            "raw_display_string",
            location="golden case approved_fact",
        ),
        normalized_value=_required_decimal(
            fact,
            "normalized_decimal_string",
            location="golden case approved_fact",
        ),
        context_ref=_required_string(
            fact,
            "context_ref",
            location="golden case approved_fact",
        ),
        period_type=XbrlPeriodType(
            _required_string(fact, "period_type", location="golden case approved_fact")
        ),
        dimensions=dimensions,
        unit=_required_string(fact, "unit", location="golden case approved_fact"),
        decimals=_optional_scalar_string(fact.get("decimals")),
        source_scale=_required_decimal(fact, "scale", location="golden case approved_fact"),
        source_element_ids=tuple(
            sorted(
                _string_item(value, location="golden case source_element_ids")
                for value in _sequence(
                    fact.get("source_element_ids"),
                    location="golden case source_element_ids",
                )
            )
        ),
        source_object_count=_positive_int(
            fact,
            "source_object_count",
            location="golden case approved_fact",
        ),
        semantic_fact_count=_positive_int(
            fact,
            "semantic_fact_count",
            location="golden case approved_fact",
        ),
    )


def _dimension(value: object, *, location: str) -> tuple[str, str]:
    payload = _mapping(value, location=location)
    return (
        _required_string(payload, "dimension", location=location),
        _required_string(payload, "member", location=location),
    )


def _mapping(value: object, *, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        message = f"{location} must be a string-keyed mapping"
        raise ValueError(message)
    return cast("Mapping[str, object]", value)


def _sequence(value: object, *, location: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{location} must be a sequence"
        raise TypeError(message)
    return cast("Sequence[object]", value)


def _required_string(payload: Mapping[str, object], key: str, *, location: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"{location}.{key} must be a nonblank string"
        raise ValueError(message)
    return value


def _string_item(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        message = f"{location} must contain nonblank strings"
        raise ValueError(message)
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string_item(value, location="optional string")


def _optional_scalar_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str | int):
        message = "optional scalar must be a string or integer"
        raise TypeError(message)
    return str(value)


def _boolean(payload: Mapping[str, object], key: str, *, location: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        message = f"{location}.{key} must be boolean"
        raise TypeError(message)
    return value


def _positive_int(payload: Mapping[str, object], key: str, *, location: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        message = f"{location}.{key} must be a positive integer"
        raise ValueError(message)
    return value


def _required_date(payload: Mapping[str, object], key: str, *, location: str) -> date:
    raw = _required_string(payload, key, location=location)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        message = f"{location}.{key} must be an ISO date"
        raise ValueError(message) from error


def _required_decimal(payload: Mapping[str, object], key: str, *, location: str) -> Decimal:
    raw = payload.get(key)
    if not isinstance(raw, str | int):
        message = f"{location}.{key} must be an exact decimal string"
        raise TypeError(message)
    try:
        result = Decimal(str(raw))
    except InvalidOperation as error:
        message = f"{location}.{key} must be an exact decimal string"
        raise ValueError(message) from error
    if not result.is_finite():
        message = f"{location}.{key} must be finite"
        raise ValueError(message)
    return result
