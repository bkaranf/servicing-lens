"""Typed deterministic ingestion services and explicit state transitions."""

# ruff: noqa: EM101

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, assert_never, cast

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from mortgage_servicing_dashboard.database import (
    IngestionError,
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
    initialize_schema,
    utc_now,
)
from mortgage_servicing_dashboard.domain import (
    ParsedObservationCandidate,
    normalize_reported_value,
    validate_candidate,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    prepare_stage_a,
    seed_stage_a,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    ContentAddressedEvidenceStore,
    LiveSecAcquisition,
    PublicSourceError,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    RetainedDocument,
    SecClient,
    SecFilingMetadata,
    StageARecordedDocumentParser,
    TransientPublicSourceError,
    normalize_sec_cik,
    parse_sec_submissions,
    prepare_live_sec_acquisition,
    sec_submissions_url,
)

INGESTION_NODES = (
    "discover_sources",
    "acquire_source",
    "hash_and_store",
    "parse_document",
    "resolve_entity_and_scope",
    "resolve_fiscal_period",
    "map_metric",
    "normalize_value_and_units",
    "apply_effective_dated_rules",
    "reconcile_and_validate",
    "deduplicate_and_supersede",
    "quarantine_ambiguous_candidates",
    "request_human_review",
    "publish_approved_observations",
    "refresh_comparability_and_materializations",
    "emit_audit_events",
)

StageName = Literal[
    "discover_sources",
    "acquire_source",
    "hash_and_store",
    "parse_document",
    "resolve_entity_and_scope",
    "resolve_fiscal_period",
    "map_metric",
    "normalize_value_and_units",
    "apply_effective_dated_rules",
    "reconcile_and_validate",
    "deduplicate_and_supersede",
    "quarantine_ambiguous_candidates",
    "request_human_review",
    "publish_approved_observations",
    "refresh_comparability_and_materializations",
    "emit_audit_events",
]
TerminalStatus = Literal["RUNNING", "AWAITING_REVIEW", "COMPLETED", "FAILED"]

_MAX_SOURCES = 8
_MAX_CANDIDATES = 512
_MAX_AUDIT_EVENTS = 64
_MAX_ERRORS = 16
_MAX_RETRIES = 3
_MAX_THREAD_ID_LENGTH = 128
_MAX_STATE_STRING_LENGTH = 512
_LIVE_SEC_FORMS = frozenset({"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"})
_MAX_LIVE_FILINGS_PER_COMPANY = 64


class IngestionState(TypedDict, total=False):
    """Serializable bounded orchestration metadata; never raw evidence or values."""

    thread_id: str
    run_key: str
    run_id: str
    source_keys: list[str]
    evidence_ids: list[str]
    candidate_ids: list[str]
    validated_candidate_ids: list[str]
    quarantine_candidate_ids: list[str]
    visited: list[str]
    review_decision: Literal["approve", "reject", "pending"]
    review_candidate_id: str
    reviewer: str
    review_rationale: str
    published_count: int
    not_disclosed_count: int
    source_not_checked_count: int
    retry_counts: dict[str, int]
    terminal_status: TerminalStatus
    terminal_outcomes: dict[str, int]
    error_codes: list[str]
    audit_events: list[str]


class IngestionUpdate(TypedDict, total=False):
    """Bounded partial state returned by one deterministic stage service."""

    run_key: str
    run_id: str
    source_keys: list[str]
    evidence_ids: list[str]
    candidate_ids: list[str]
    validated_candidate_ids: list[str]
    quarantine_candidate_ids: list[str]
    review_decision: Literal["approve", "reject", "pending"]
    published_count: int
    not_disclosed_count: int
    source_not_checked_count: int
    retry_counts: dict[str, int]
    terminal_status: TerminalStatus
    terminal_outcomes: dict[str, int]
    error_codes: list[str]
    audit_events: list[str]


class DocumentAcquirer(Protocol):
    """Typed byte-acquisition boundary used by the orchestration service."""

    def acquire(self, source: RecordedSourceDefinition) -> AcquiredDocument:
        """Acquire one configured source as exact bytes."""


class IngestionServices(Protocol):
    """Stage service contract invoked by each explicit runtime transition."""

    def execute(self, stage: StageName, state: IngestionState) -> IngestionUpdate:
        """Execute one deterministic stage and return bounded metadata only."""


class IngestionServiceError(RuntimeError):
    """Safe classified service failure suitable for checkpoint state."""

    def __init__(self, code: str, safe_message: str, *, retryable: bool = False) -> None:
        """Attach a stable code and non-sensitive message to a stage failure."""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class _Workspace:
    documents: dict[str, AcquiredDocument]
    retained: dict[str, RetainedDocument]
    candidates: tuple[ParsedObservationCandidate, ...]


def _live_sec_directories(
    *,
    cache_directory: Path | None,
    retention_root: Path | None,
) -> tuple[Path, Path]:
    live_root = Path.cwd() / ".msi" / "sec"
    return (
        cache_directory or live_root / "cache",
        retention_root or live_root / "evidence",
    )


def _selected_live_companies(
    companies: list[dict[str, Any]],
    company: str | None,
) -> list[dict[str, Any]]:
    if company is None:
        return companies
    requested = company.casefold()
    selected = [
        item
        for item in companies
        if requested in {str(item["id"]).casefold(), str(item["ticker"]).casefold()}
    ]
    if len(selected) != 1:
        msg = "live SEC discovery company is outside the configured universe"
        raise ValueError(msg)
    return selected


def _discover_live_company_filings(
    *,
    client: SecClient,
    store: ContentAddressedEvidenceStore,
    company: dict[str, Any],
    filed_on_or_after: date,
    max_filings: int,
) -> tuple[SecFilingMetadata, ...]:
    cik = normalize_sec_cik(str(company["cik"]))
    document = client.acquire(sec_submissions_url(cik), refresh=True)
    retained = store.retain(document)
    if store.verify(retained) != document.content:
        msg = "content-addressed SEC submissions replay did not match the response"
        raise PublicSourceError(msg)
    return parse_sec_submissions(
        document=document,
        company_id=str(company["id"]),
        cik=cik,
        forms=_LIVE_SEC_FORMS,
        filed_on_or_after=filed_on_or_after,
        max_filings=max_filings,
    )


def discover_live_sec_filings(  # noqa: PLR0913
    *,
    user_agent: str,
    company: str | None = None,
    config_dir: Path | None = None,
    cache_directory: Path | None = None,
    retention_root: Path | None = None,
    max_filings_per_company: int = _MAX_LIVE_FILINGS_PER_COMPANY,
    transport: httpx.BaseTransport | None = None,
) -> tuple[SecFilingMetadata, ...]:
    """Discover bounded recent filings from each configured company's SEC index.

    Args:
        user_agent: Required identifying application and contact string.
        company: Optional configured ticker or stable company identifier.
        config_dir: Optional versioned configuration root.
        cache_directory: Optional application-owned HTTP response cache.
        retention_root: Optional content-addressed evidence root.
        max_filings_per_company: Per-CIK bound after form and date filters.
        transport: Optional test transport; omitted runs the controlled live client.

    Returns:
        Bounded allow-listed filing metadata without opening a database.
    """
    config_root = config_directory(config_dir)
    universe, _, data = load_stage_a_configuration(config_root)
    companies = _selected_live_companies(
        cast("list[dict[str, Any]]", universe["companies"]),
        company,
    )
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    filed_on_or_after = min(date.fromisoformat(str(item["period_start"])) for item in quarters)
    cache_root, evidence_root = _live_sec_directories(
        cache_directory=cache_directory,
        retention_root=retention_root,
    )
    store = ContentAddressedEvidenceStore(evidence_root)
    discovered: list[SecFilingMetadata] = []
    with SecClient(
        user_agent=user_agent,
        cache_directory=cache_root,
        transport=transport,
    ) as client:
        for configured_company in companies:
            discovered.extend(
                _discover_live_company_filings(
                    client=client,
                    store=store,
                    company=configured_company,
                    filed_on_or_after=filed_on_or_after,
                    max_filings=max_filings_per_company,
                )
            )
    return tuple(discovered)


def _validate_live_candidates(
    candidates: tuple[ParsedObservationCandidate, ...],
) -> None:
    for candidate in candidates:
        result = validate_candidate(candidate)
        if not result.valid:
            msg = f"live SEC candidate failed deterministic validation: {result.code}"
            raise PublicSourceError(msg)
    by_key = {
        (candidate.company_id, candidate.period_end, candidate.metric_id): candidate
        for candidate in candidates
    }
    tfc_periods = {
        candidate.period_end for candidate in candidates if candidate.company_id == "tfc"
    }
    for period_end in tfc_periods:
        required = (
            ("tfc", period_end, "total_servicing_upb"),
            ("tfc", period_end, "servicing_for_others_upb"),
            ("tfc", period_end, "bank_owned_loans_serviced_upb"),
        )
        if not all(key in by_key for key in required):
            continue
        total = by_key[required[0]].normalized_value
        components = by_key[required[1]].normalized_value + by_key[required[2]].normalized_value
        if total != components:
            msg = "live SEC TFC servicing total failed deterministic reconciliation"
            raise PublicSourceError(msg)


def run_live_sec_ingestion(
    *,
    user_agent: str,
    config_dir: Path | None = None,
    cache_directory: Path | None = None,
    retention_root: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[LiveSecAcquisition, ...]:
    """Acquire and validate configured SEC documents for repository publication.

    This function performs only governed discovery, HTTP acquisition,
    content-addressed retention, deterministic parsing, and candidate validation.
    Database filing linkage, evidence insertion, and observation revisions remain
    the repository layer's responsibility.

    Args:
        user_agent: Required identifying application and contact string.
        config_dir: Optional versioned configuration root.
        cache_directory: Optional application-owned HTTP response cache.
        retention_root: Optional immutable content-addressed evidence root.
        transport: Optional test transport; omitted runs the controlled live client.

    Returns:
        One content-specific acquisition for each configured live SEC source.

    Raises:
        PublicSourceError: If discovery, acquisition, replay, parsing, or validation fails.
    """
    config_root = config_directory(config_dir)
    universe, _, data = load_stage_a_configuration(config_root)
    companies = cast("list[dict[str, Any]]", universe["companies"])
    company_by_id = {str(item["id"]): item for item in companies}
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    filed_on_or_after = min(date.fromisoformat(str(item["period_start"])) for item in quarters)
    source_payloads = cast("dict[str, dict[str, Any]]", data["sources"])
    definitions = {
        key: RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=config_root,
        )
        for key, payload in source_payloads.items()
    }
    cache_root, evidence_root = _live_sec_directories(
        cache_directory=cache_directory,
        retention_root=retention_root,
    )
    store = ContentAddressedEvidenceStore(evidence_root)
    parser = StageARecordedDocumentParser()
    all_candidates: list[ParsedObservationCandidate] = []
    acquisitions: list[LiveSecAcquisition] = []
    with SecClient(
        user_agent=user_agent,
        cache_directory=cache_root,
        transport=transport,
    ) as client:
        discovered_by_company = {
            str(company["id"]): _discover_live_company_filings(
                client=client,
                store=store,
                company=company,
                filed_on_or_after=filed_on_or_after,
                max_filings=_MAX_LIVE_FILINGS_PER_COMPANY,
            )
            for company in companies
        }
        for source_key in sorted(definitions):
            definition = definitions[source_key]
            matches = [
                filing
                for filing in discovered_by_company[definition.company_id]
                if filing.accession == definition.accession
            ]
            if len(matches) != 1:
                msg = "configured SEC accession was not uniquely discovered for its CIK"
                raise PublicSourceError(msg)
            acquired_document = client.acquire(definition.url, refresh=True)
            retained_document = store.retain(acquired_document)
            acquisition = prepare_live_sec_acquisition(
                source=definition,
                cik=str(company_by_id[definition.company_id]["cik"]),
                discovered_filing=matches[0],
                acquired_document=acquired_document,
                retained_document=retained_document,
            )
            candidates = parser.parse(
                source=acquisition.runtime_definition,
                content=store.verify(retained_document),
                company=company_by_id[definition.company_id],
                quarters=quarters,
            )
            _validate_live_candidates(candidates)
            all_candidates.extend(candidates)
            acquisitions.append(acquisition)
    _validate_live_candidates(tuple(all_candidates))
    return tuple(acquisitions)


class StageAIngestionServices:
    """Real offline Stage A services over verified retained SEC documents."""

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        config_dir: Path | None = None,
        retention_root: Path | None = None,
        acquirer: DocumentAcquirer | None = None,
        max_acquisition_attempts: int = 3,
    ) -> None:
        """Bind deterministic services without enabling unrestricted network access."""
        if max_acquisition_attempts < 1 or max_acquisition_attempts > _MAX_RETRIES:
            msg = "acquisition attempts must be bounded to 1..3"
            raise ValueError(msg)
        self.engine = engine or create_database_engine("sqlite:///:memory:")
        self.config_root = config_directory(config_dir)
        self.universe, self.catalog, self.data = load_stage_a_configuration(self.config_root)
        self.companies = cast("list[dict[str, Any]]", self.universe["companies"])
        self.metrics = cast("list[dict[str, Any]]", self.catalog["metrics"])
        self.quarters = cast("list[dict[str, Any]]", self.data["quarters"])
        source_payloads = cast("dict[str, dict[str, Any]]", self.data["sources"])
        self.definitions = {
            key: RecordedSourceDefinition.from_mapping(
                key=key,
                payload=payload,
                config_root=self.config_root,
            )
            for key, payload in source_payloads.items()
        }
        self._acquirer = acquirer or RecordedEvidenceAcquirer()
        self._max_acquisition_attempts = max_acquisition_attempts
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        if retention_root is None:
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="msi-evidence-retention-"
            )
            retention_root = Path(self._temporary_directory.name)
        self._store = ContentAddressedEvidenceStore(retention_root)
        self._parser = StageARecordedDocumentParser()
        self._workspace = _Workspace({}, {}, ())

    def _selected_sources(self, state: IngestionState) -> tuple[str, ...]:
        requested = tuple(state.get("source_keys") or sorted(self.definitions))
        configured = tuple(sorted(self.definitions))
        if tuple(sorted(requested)) != configured:
            msg = "Stage A publication requires the complete configured eligible source set"
            raise IngestionServiceError("INCOMPLETE_SOURCE_SET", msg)
        return requested

    def _run_identity(self, source_keys: tuple[str, ...]) -> tuple[str, str]:
        payload = {
            "dataset_version": str(self.data["dataset_version"]),
            "evidence": sorted(self.definitions[key].content_sha256 for key in source_keys),
            "parser_versions": sorted(self.definitions[key].parser_version for key in source_keys),
            "source_assessment": self.data["eligible_source_assessment"],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        run_key = hashlib.sha256(encoded).hexdigest()
        return run_key, f"pipeline:{run_key[:32]}"

    def _discover(self, state: IngestionState) -> IngestionUpdate:
        source_keys = self._selected_sources(state)
        run_key, run_id = self._run_identity(source_keys)
        return {
            "source_keys": list(source_keys),
            "run_key": run_key,
            "run_id": run_id,
            "retry_counts": {},
            "review_decision": "pending",
            "terminal_status": "RUNNING",
            "terminal_outcomes": {
                "PUBLISHED": 0,
                "NOT_DISCLOSED": 0,
                "SOURCE_NOT_CHECKED": 0,
                "QUARANTINED": 0,
                "FAILED": 0,
            },
            "error_codes": [],
            "audit_events": [f"sources_discovered:{len(source_keys)}"],
        }

    def _acquire(self, state: IngestionState) -> IngestionUpdate:
        documents: dict[str, AcquiredDocument] = {}
        retries = 0
        for key in self._selected_sources(state):
            for attempt in range(1, self._max_acquisition_attempts + 1):
                try:
                    documents[key] = self._acquirer.acquire(self.definitions[key])
                    break
                except TransientPublicSourceError as error:
                    if attempt == self._max_acquisition_attempts:
                        raise IngestionServiceError(
                            "ACQUISITION_RETRY_EXHAUSTED",
                            "transient acquisition failed after bounded retries",
                            retryable=True,
                        ) from error
                    retries += 1
                except PublicSourceError as error:
                    raise IngestionServiceError(
                        "EVIDENCE_INTEGRITY_FAILED",
                        "recorded evidence failed deterministic integrity verification",
                    ) from error
        self._workspace = _Workspace(documents, {}, ())
        retry_counts = dict(state.get("retry_counts", {}))
        retry_counts["acquire_source"] = retries
        return {
            "retry_counts": retry_counts,
            "terminal_status": "RUNNING",
            "audit_events": [f"sources_acquired:{len(documents)}:retries={retries}"],
        }

    def _hash_and_store(self, state: IngestionState) -> IngestionUpdate:
        retained: dict[str, RetainedDocument] = {}
        for key in self._selected_sources(state):
            document = self._workspace.documents.get(key)
            if document is None:
                raise IngestionServiceError(
                    "ACQUIRED_DOCUMENT_MISSING",
                    "hashing requires a successfully acquired source",
                )
            item = self._store.retain(document)
            verified = self._store.verify(item)
            if verified != document.content:
                raise IngestionServiceError(
                    "RETENTION_VERIFICATION_FAILED",
                    "content-addressed evidence did not replay exactly",
                )
            retained[key] = item
        self._workspace = _Workspace(self._workspace.documents, retained, ())
        return {
            "evidence_ids": [f"evidence:{key}" for key in sorted(retained)],
            "terminal_status": "RUNNING",
            "audit_events": [f"evidence_retained:{len(retained)}"],
        }

    def _parse(self, state: IngestionState) -> IngestionUpdate:
        company_by_id = {str(item["id"]): item for item in self.companies}
        candidates: list[ParsedObservationCandidate] = []
        for key in self._selected_sources(state):
            retained = self._workspace.retained.get(key)
            if retained is None:
                raise IngestionServiceError(
                    "RETAINED_EVIDENCE_MISSING",
                    "parsing requires verified retained evidence",
                )
            definition = self.definitions[key]
            content = self._store.verify(retained)
            try:
                parsed = self._parser.parse(
                    source=definition,
                    content=content,
                    company=company_by_id[definition.company_id],
                    quarters=self.quarters,
                )
            except PublicSourceError as error:
                raise IngestionServiceError(
                    "DETERMINISTIC_PARSE_FAILED",
                    "an allow-listed source row could not be parsed unambiguously",
                ) from error
            candidates.extend(parsed)
        self._workspace = _Workspace(
            self._workspace.documents,
            self._workspace.retained,
            tuple(candidates),
        )
        return {
            "candidate_ids": [item.candidate_id for item in candidates],
            "terminal_status": "RUNNING",
            "audit_events": [f"candidates_parsed:{len(candidates)}"],
        }

    def _resolve_entities(self) -> IngestionUpdate:
        identities = {
            (str(item["id"]), str(item["reporting_entity"]), str(item["reporting_scope"]))
            for item in self.companies
        }
        for candidate in self._workspace.candidates:
            identity = (
                candidate.company_id,
                candidate.reporting_entity_id,
                candidate.reporting_scope_id,
            )
            if identity not in identities:
                raise IngestionServiceError(
                    "ENTITY_SCOPE_UNRESOLVED",
                    "candidate reporting entity or scope is not allow-listed",
                )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"entity_scope_resolved:{len(self._workspace.candidates)}"],
        }

    def _resolve_periods(self) -> IngestionUpdate:
        configured = {
            (
                date.fromisoformat(str(item["period_end"])),
                int(item["fiscal_year"]),
                int(item["fiscal_quarter"]),
            )
            for item in self.quarters
        }
        for candidate in self._workspace.candidates:
            if (
                candidate.period_end,
                candidate.fiscal_year,
                candidate.fiscal_quarter,
            ) not in configured:
                raise IngestionServiceError(
                    "FISCAL_PERIOD_UNRESOLVED",
                    "candidate period is outside the versioned fiscal calendar",
                )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"fiscal_periods_resolved:{len(configured)}"],
        }

    def _map_metrics(self) -> IngestionUpdate:
        known = {str(item["id"]) for item in self.metrics}
        if any(candidate.metric_id not in known for candidate in self._workspace.candidates):
            raise IngestionServiceError(
                "METRIC_UNMAPPED",
                "candidate metric is absent from the versioned catalog",
            )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"metrics_mapped:{len(self._workspace.candidates)}"],
        }

    def _normalize(self) -> IngestionUpdate:
        recipe_by_key = {
            (definition.company_id, str(recipe["metric_id"]), str(recipe["raw_label"])): recipe
            for definition in self.definitions.values()
            for recipe in definition.rows
        }
        for candidate in self._workspace.candidates:
            recipe = recipe_by_key[(candidate.company_id, candidate.metric_id, candidate.raw_label)]
            recalculated = normalize_reported_value(
                candidate.raw_value,
                rule=str(recipe["normalization"]),
            )
            if recalculated != candidate.normalized_value or not isinstance(
                candidate.normalized_value, Decimal
            ):
                raise IngestionServiceError(
                    "NORMALIZATION_MISMATCH",
                    "candidate failed exact Decimal normalization replay",
                )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"values_normalized_exactly:{len(self._workspace.candidates)}"],
        }

    def _apply_rules(self) -> IngestionUpdate:
        effective_from = date.fromisoformat(str(self.catalog["effective_period"]).split("/")[0])
        if any(candidate.period_end < effective_from for candidate in self._workspace.candidates):
            raise IngestionServiceError(
                "NO_EFFECTIVE_METRIC_RULE",
                "candidate predates the effective metric catalog",
            )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"effective_rules_applied:{effective_from.isoformat()}"],
        }

    def _validate(self) -> IngestionUpdate:
        validated: list[str] = []
        for candidate in self._workspace.candidates:
            result = validate_candidate(candidate)
            if not result.valid:
                raise IngestionServiceError(result.code, result.summary)
            validated.append(candidate.candidate_id)
        self._reconcile_tfc_totals()
        return {
            "validated_candidate_ids": validated,
            "terminal_status": "RUNNING",
            "audit_events": [f"candidates_validated:{len(validated)}"],
        }

    def _reconcile_tfc_totals(self) -> None:
        by_key = {
            (candidate.company_id, candidate.period_end, candidate.metric_id): candidate
            for candidate in self._workspace.candidates
        }
        periods = {
            candidate.period_end
            for candidate in self._workspace.candidates
            if candidate.company_id == "tfc"
        }
        for period_end in periods:
            total = by_key[("tfc", period_end, "total_servicing_upb")].normalized_value
            components = (
                by_key[("tfc", period_end, "servicing_for_others_upb")].normalized_value
                + by_key[("tfc", period_end, "bank_owned_loans_serviced_upb")].normalized_value
            )
            if total != components:
                raise IngestionServiceError(
                    "RECONCILIATION_FAILED",
                    "directly reported TFC total does not reconcile to reported components",
                )

    def _deduplicate(self, state: IngestionState) -> IngestionUpdate:
        identities: set[tuple[str, str, date]] = set()
        duplicates: list[str] = []
        for candidate in self._workspace.candidates:
            identity = (candidate.semantic_key_digest, candidate.metric_id, candidate.period_end)
            if identity in identities:
                duplicates.append(candidate.candidate_id)
            identities.add(identity)
        if duplicates:
            return {
                "quarantine_candidate_ids": sorted(set(duplicates)),
                "terminal_status": "RUNNING",
                "audit_events": [f"duplicates_quarantined:{len(duplicates)}"],
            }
        return {
            "quarantine_candidate_ids": list(state.get("quarantine_candidate_ids", [])),
            "terminal_status": "RUNNING",
            "audit_events": ["deduplication_completed:duplicates=0"],
        }

    def _configured_quarantine_ids(self) -> set[str]:
        return {
            str(recipe["candidate_id"])
            for definition in self.definitions.values()
            for recipe in definition.quarantine_rows
        }

    def _quarantine(self, state: IngestionState) -> IngestionUpdate:
        thread_id = _required_thread_id(state)
        prepare_stage_a(self.engine, config_dir=self.config_root, thread_id=thread_id)
        configured_ids = self._configured_quarantine_ids()
        duplicate_ids = set(state.get("quarantine_candidate_ids", [])) - configured_ids
        candidates_by_id = {
            candidate.candidate_id: candidate for candidate in self._workspace.candidates
        }
        with Session(self.engine) as session:
            run = session.get(PipelineRun, state.get("run_id"))
            if run is not None:
                run.retry_count = sum(state.get("retry_counts", {}).values())
                for candidate_id in sorted(duplicate_ids):
                    candidate = candidates_by_id.get(candidate_id)
                    if (
                        candidate is None
                        or session.get(QuarantineCandidate, candidate_id) is not None
                    ):
                        continue
                    session.add(
                        QuarantineCandidate(
                            id=candidate.candidate_id,
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
                            evidence_id=candidate.evidence_id,
                            evidence_locator=candidate.evidence_locator,
                            bounded_excerpt=(f"{candidate.raw_label}: {candidate.raw_value}")[
                                :_MAX_STATE_STRING_LENGTH
                            ],
                            confidence=Decimal("0.5000"),
                            conflicts_and_uncertainties=[
                                "duplicate semantic candidate requires deterministic resolution"
                            ],
                            model_and_prompt_version=None,
                            status="PENDING",
                        )
                    )
                session.commit()
        candidate_ids = sorted(configured_ids | set(state.get("quarantine_candidate_ids", [])))
        status: TerminalStatus = "AWAITING_REVIEW" if candidate_ids else "RUNNING"
        return {
            "quarantine_candidate_ids": candidate_ids,
            "terminal_status": status,
            "terminal_outcomes": {
                "PUBLISHED": 0,
                "NOT_DISCLOSED": 0,
                "SOURCE_NOT_CHECKED": 0,
                "QUARANTINED": len(candidate_ids),
                "FAILED": 0,
            },
            "audit_events": [f"candidates_quarantined:{len(candidate_ids)}"],
        }

    def _revalidate_quarantine_candidate(self, candidate: QuarantineCandidate) -> bool:
        """Replay one approved row against the freshly retained evidence.

        A quarantine candidate is never promoted merely because a reviewer approved
        it.  The source bytes, configured row, exact text, normalized Decimal, and
        deterministic mapping must still agree at publication time.  The current
        Stage A quarantine recipe is intentionally ambiguous, so an exact replay is
        useful evidence but remains unpublished.
        """
        for definition in self.definitions.values():
            for recipe in definition.quarantine_rows:
                if str(recipe["candidate_id"]) != candidate.id:
                    continue
                retained = self._workspace.retained.get(definition.key)
                if retained is None:
                    return False
                try:
                    values = self._parser.extract_row_values(
                        content=self._store.verify(retained),
                        raw_label=str(recipe["raw_label"]),
                        occurrence=int(recipe.get("row_occurrence", 0)),
                    )
                    raw_value = values[int(recipe.get("value_index", 0))]
                    normalized = normalize_reported_value(
                        raw_value,
                        rule=str(recipe["normalization"]),
                    )
                except (IndexError, KeyError, PublicSourceError, ValueError):
                    return False
                company = next(
                    (item for item in self.companies if item["id"] == definition.company_id),
                    None,
                )
                if company is None:
                    return False
                return (
                    retained.sha256 == definition.content_sha256
                    and candidate.evidence_id == f"evidence:{definition.key}"
                    and candidate.raw_source_label == str(recipe["raw_label"])
                    and raw_value == candidate.raw_value
                    and normalized == candidate.proposed_normalized_value
                    and candidate.proposed_metric_id == str(recipe["proposed_metric_id"])
                    and candidate.unit == str(recipe["canonical_unit"])
                    and candidate.scale == str(recipe["reported_scale"])
                    and candidate.period_end == date.fromisoformat(str(recipe["period_end"]))
                    and candidate.methodology == str(recipe["proposed_methodology"])
                    and candidate.reporting_entity_id == str(company["reporting_entity"])
                    and candidate.reporting_scope_id == str(company["reporting_scope"])
                    and not candidate.conflicts_and_uncertainties
                )
        return False

    def _record_review(self, state: IngestionState) -> IngestionUpdate:
        decision = state.get("review_decision", "pending")
        if not state.get("quarantine_candidate_ids"):
            return {
                "terminal_status": "RUNNING",
                "audit_events": ["review_not_required:candidates=0"],
            }
        if decision not in {"approve", "reject"}:
            raise IngestionServiceError(
                "REVIEW_DECISION_INVALID",
                "review resume must contain approve or reject",
            )
        target = state.get("review_candidate_id")
        candidate_ids = list(state.get("quarantine_candidate_ids", []))
        if target is not None:
            if target not in candidate_ids:
                raise IngestionServiceError(
                    "REVIEW_CANDIDATE_NOT_FOUND",
                    "review candidate is not part of this pipeline run",
                )
            candidate_ids = [target]
        repository = IntelligenceRepository(self.engine)
        for candidate_id in candidate_ids:
            repository.record_review_decision(
                candidate_id=candidate_id,
                decision=decision,
                reviewer=state.get("reviewer", "local-reviewer"),
                rationale=state.get(
                    "review_rationale",
                    "explicit deterministic review resume",
                ),
                thread_id=_required_thread_id(state),
            )
        return {
            "terminal_status": "RUNNING",
            "audit_events": [f"review_recorded:{decision}"],
        }

    def _publish(self, state: IngestionState) -> IngestionUpdate:
        thread_id = _required_thread_id(state)
        with Session(self.engine) as session:
            configured_ids = self._configured_quarantine_ids()
            run_candidates = session.scalars(
                select(QuarantineCandidate).where(
                    QuarantineCandidate.pipeline_run_id == state.get("run_id")
                )
            ).all()
            runtime_duplicates = [
                candidate for candidate in run_candidates if candidate.id not in configured_ids
            ]
            if runtime_duplicates:
                for candidate in runtime_duplicates:
                    if candidate.status != "REJECTED":
                        candidate.status = "QUARANTINED_AFTER_REVALIDATION"
                session.commit()
                raise IngestionServiceError(
                    "UNRESOLVED_RUNTIME_DUPLICATE",
                    "runtime-detected duplicate candidates block publication",
                )
            pending_revalidation = session.scalars(
                select(QuarantineCandidate).where(
                    QuarantineCandidate.pipeline_run_id == state.get("run_id"),
                    QuarantineCandidate.status == "APPROVED_PENDING_REVALIDATION",
                )
            ).all()
            for candidate in pending_revalidation:
                # Quarantine rows are deliberately ambiguous mappings.  Re-read the
                # retained source and compare the exact row value before any public
                # observation write.  A changed/missing row or a remaining conflict
                # fails closed and never becomes an observation.
                revalidated = self._revalidate_quarantine_candidate(candidate)
                candidate.status = "QUARANTINED_AFTER_REVALIDATION"
                if revalidated:
                    candidate.conflicts_and_uncertainties = [
                        *candidate.conflicts_and_uncertainties,
                        "candidate has no deterministic publication mapping",
                    ]
            session.commit()
        seed_stage_a(self.engine, config_dir=self.config_root, thread_id=thread_id)
        with Session(self.engine) as session:
            run = session.get(PipelineRun, state.get("run_id"))
            if run is None:
                raise IngestionServiceError(
                    "PIPELINE_RUN_MISSING",
                    "publication did not produce an auditable pipeline run",
                )
            outcomes = {key: int(value) for key, value in run.terminal_outcomes.items()}
        return {
            "published_count": outcomes["PUBLISHED"],
            "not_disclosed_count": outcomes["NOT_DISCLOSED"],
            "source_not_checked_count": outcomes["SOURCE_NOT_CHECKED"],
            "terminal_outcomes": outcomes,
            "terminal_status": "COMPLETED",
            "audit_events": [
                (
                    "publication_completed:"
                    f"published={outcomes['PUBLISHED']}:"
                    f"not_disclosed={outcomes['NOT_DISCLOSED']}:"
                    f"source_not_checked={outcomes['SOURCE_NOT_CHECKED']}:"
                    f"quarantined={outcomes['QUARANTINED']}"
                )
            ],
        }

    def _refresh(self, state: IngestionState) -> IngestionUpdate:
        repository = IntelligenceRepository(self.engine)
        coverage_cells = len(repository.coverage())
        return {
            "terminal_status": state.get("terminal_status", "COMPLETED"),
            "audit_events": [f"materializations_refreshed:coverage_cells={coverage_cells}"],
        }

    @staticmethod
    def _audit(state: IngestionState) -> IngestionUpdate:
        status = state.get("terminal_status", "COMPLETED")
        return {
            "terminal_status": status,
            "audit_events": [
                (
                    f"ingestion_terminal:{status.lower()}:"
                    f"published={state.get('published_count', 0)}:"
                    f"not_disclosed={state.get('not_disclosed_count', 0)}"
                    f":source_not_checked={state.get('source_not_checked_count', 0)}"
                )
            ],
        }

    def execute(  # noqa: C901, PLR0911, PLR0912
        self,
        stage: StageName,
        state: IngestionState,
    ) -> IngestionUpdate:
        """Execute a typed stage; raw bytes and values remain in service memory only."""
        try:
            if stage == "discover_sources":
                return self._discover(state)
            if stage == "acquire_source":
                return self._acquire(state)
            if stage == "hash_and_store":
                return self._hash_and_store(state)
            if stage == "parse_document":
                return self._parse(state)
            if stage == "resolve_entity_and_scope":
                return self._resolve_entities()
            if stage == "resolve_fiscal_period":
                return self._resolve_periods()
            if stage == "map_metric":
                return self._map_metrics()
            if stage == "normalize_value_and_units":
                return self._normalize()
            if stage == "apply_effective_dated_rules":
                return self._apply_rules()
            if stage == "reconcile_and_validate":
                return self._validate()
            if stage == "deduplicate_and_supersede":
                return self._deduplicate(state)
            if stage == "quarantine_ambiguous_candidates":
                return self._quarantine(state)
            if stage == "request_human_review":
                return self._record_review(state)
            if stage == "publish_approved_observations":
                return self._publish(state)
            if stage == "refresh_comparability_and_materializations":
                return self._refresh(state)
            if stage == "emit_audit_events":
                return self._audit(state)
            assert_never(stage)
        except IngestionServiceError:
            raise
        except (KeyError, TypeError, ValueError, PublicSourceError) as error:
            raise IngestionServiceError(
                "DETERMINISTIC_STAGE_FAILED",
                f"{stage} failed closed on deterministic input",
            ) from error


def _required_thread_id(state: IngestionState) -> str:
    thread_id = state.get("thread_id", "")
    if not thread_id or len(thread_id) > _MAX_THREAD_ID_LENGTH:
        raise IngestionServiceError(
            "THREAD_ID_INVALID",
            "a bounded opaque thread identifier is required",
        )
    return thread_id


def _validate_bounded_state(state: IngestionState) -> None:
    _required_thread_id(state)
    bounded_lists = (
        ("source_keys", _MAX_SOURCES),
        ("evidence_ids", _MAX_SOURCES),
        ("candidate_ids", _MAX_CANDIDATES),
        ("validated_candidate_ids", _MAX_CANDIDATES),
        ("quarantine_candidate_ids", _MAX_CANDIDATES),
        ("audit_events", _MAX_AUDIT_EVENTS),
        ("error_codes", _MAX_ERRORS),
    )
    for field, maximum in bounded_lists:
        values = cast("list[object]", state.get(cast("Any", field), []))
        if len(values) > maximum or any(
            not isinstance(value, str) or len(value) > _MAX_STATE_STRING_LENGTH for value in values
        ):
            msg = f"{field} exceeds the bounded orchestration-state contract"
            raise IngestionServiceError("STATE_BOUND_EXCEEDED", msg)
    if any(value < 0 or value > _MAX_RETRIES for value in state.get("retry_counts", {}).values()):
        raise IngestionServiceError(
            "RETRY_BOUND_EXCEEDED",
            "retry counters must remain within the configured bound",
        )
    for field in ("reviewer", "review_rationale"):
        value = state.get(cast("Any", field))
        if value is not None and (
            not isinstance(value, str) or len(value) > _MAX_STATE_STRING_LENGTH
        ):
            msg = f"{field} exceeds the bounded orchestration-state contract"
            raise IngestionServiceError("STATE_BOUND_EXCEEDED", msg)


def _failure_update(*, stage: StageName, state: IngestionState, code: str) -> IngestionUpdate:
    """Build a bounded failure transition; the audit stage always follows it."""
    outcomes = {
        "PUBLISHED": int(state.get("terminal_outcomes", {}).get("PUBLISHED", 0)),
        "NOT_DISCLOSED": int(state.get("terminal_outcomes", {}).get("NOT_DISCLOSED", 0)),
        "SOURCE_NOT_CHECKED": int(state.get("terminal_outcomes", {}).get("SOURCE_NOT_CHECKED", 0)),
        "QUARANTINED": int(
            state.get("terminal_outcomes", {}).get(
                "QUARANTINED",
                len(state.get("quarantine_candidate_ids", [])),
            )
        ),
        "FAILED": int(state.get("terminal_outcomes", {}).get("FAILED", 0)) + 1,
    }
    return {
        "terminal_status": "FAILED",
        "terminal_outcomes": outcomes,
        "error_codes": [*state.get("error_codes", []), code],
        "audit_events": [f"stage_failed:{stage}:{code}"],
    }


def _merge_update(state: IngestionState, update: IngestionUpdate) -> IngestionState:
    """Apply one stage's bounded update without framework-specific reducers."""
    merged = dict(state)
    for key, value in update.items():
        if key in {"visited", "audit_events"}:
            previous = cast("list[str]", merged.get(key, []))
            merged[key] = [*previous, *cast("list[str]", value)]
        else:
            merged[key] = value
    return cast("IngestionState", merged)


def _default_outcomes() -> dict[str, int]:
    return {
        "PUBLISHED": 0,
        "NOT_DISCLOSED": 0,
        "SOURCE_NOT_CHECKED": 0,
        "QUARANTINED": 0,
        "FAILED": 0,
    }


def _validate_transition_identity(
    state: IngestionState,
    stage: StageName,
    update: IngestionUpdate,
) -> None:
    """Reject discovery output that escapes the run identity bound at entry."""
    if stage == "discover_sources" and (
        (update.get("run_key") is not None and update["run_key"] != state.get("run_key"))
        or (update.get("run_id") is not None and update["run_id"] != state.get("run_id"))
    ):
        raise IngestionServiceError(
            "RUN_IDENTITY_MISMATCH",
            "discovery no longer matches the bound pipeline run identity",
        )


class DeterministicIngestionRuntime:
    """Run the 16 ingestion stages as explicit typed Python transitions.

    The runtime deliberately keeps only bounded metadata in ``IngestionState``.
    Evidence bytes and parsed values remain in ``StageAIngestionServices`` until
    the existing repository publication functions perform their immutable writes.
    ``resume`` reconstructs this object from the database and replays the stages,
    so a CLI process boundary does not depend on an in-memory checkpoint.
    """

    def __init__(self, services: IngestionServices | None = None) -> None:
        """Bind the real Stage A services used for deterministic persistence."""
        self.services = services or StageAIngestionServices()
        self._engine = getattr(self.services, "engine", None)

    @property
    def engine(self) -> Engine:
        """Expose the bound repository engine for callers and diagnostics."""
        if not isinstance(self._engine, Engine):
            msg = "runtime persistence requires StageAIngestionServices"
            raise TypeError(msg)
        return self._engine

    def _initial_state(
        self,
        *,
        thread_id: str | None,
        state: IngestionState | None,
    ) -> IngestionState:
        initial: dict[str, Any] = dict(state or {})
        configured = cast("dict[str, Any]", getattr(self.services, "definitions", {}))
        requested = tuple(initial.get("source_keys") or sorted(configured))
        identity = getattr(self.services, "_run_identity", None)
        if callable(identity):
            run_key, default_run_id = identity(requested)
        else:
            payload = {"service": type(self.services).__name__, "source_keys": requested}
            run_key = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            default_run_id = f"pipeline:{run_key[:32]}"
        resolved_thread = thread_id or str(initial.get("thread_id") or f"thread:{run_key[:24]}")
        initial.update(
            {
                "thread_id": resolved_thread,
                "run_key": run_key,
                "run_id": default_run_id,
                "source_keys": list(initial.get("source_keys") or []),
                "visited": list(initial.get("visited") or []),
                "review_decision": initial.get("review_decision", "pending"),
                "published_count": int(initial.get("published_count", 0)),
                "not_disclosed_count": int(initial.get("not_disclosed_count", 0)),
                "source_not_checked_count": int(initial.get("source_not_checked_count", 0)),
                "retry_counts": dict(initial.get("retry_counts") or {}),
                "terminal_status": initial.get("terminal_status", "RUNNING"),
                "terminal_outcomes": dict(initial.get("terminal_outcomes") or _default_outcomes()),
                "error_codes": list(initial.get("error_codes") or []),
                "audit_events": list(initial.get("audit_events") or []),
            }
        )
        return cast("IngestionState", initial)

    def _ensure_pipeline_run(self, state: IngestionState) -> PipelineRun:
        """Create the idempotent run row before acquisition or parsing begins."""
        if self._engine is None:
            msg = "runtime persistence requires StageAIngestionServices"
            raise TypeError(msg)
        initialize_schema(self.engine)
        run_id = str(state["run_id"])
        run_key = str(state["run_key"])
        now = utc_now()
        quarters = cast("list[dict[str, Any]]", getattr(self.services, "quarters", []))
        data = cast("dict[str, Any]", getattr(self.services, "data", {}))
        requested_periods = [str(item["period_end"]) for item in quarters]
        with Session(self.engine) as session:
            run = session.get(PipelineRun, run_id)
            if run is not None:
                if run.thread_id != state["thread_id"]:
                    raise IngestionServiceError(
                        "THREAD_MISMATCH",
                        "idempotent run already belongs to a different thread",
                    )
                return run
            run = PipelineRun(
                id=run_id,
                run_key=run_key,
                status="RUNNING",
                thread_id=str(state["thread_id"]),
                started_at=now,
                completed_at=None,
                error_count=0,
                retry_count=0,
                requested_company_id=None,
                requested_periods=requested_periods,
                code_version="stage-a-explicit-runtime-v1",
                config_version=str(data.get("dataset_version", "runtime-v1")),
                parser_version="2.0.0",
                terminal_outcomes=_default_outcomes(),
            )
            session.add(run)
            session.commit()
            return run

    def _persist_progress(self, state: IngestionState) -> None:
        """Persist status, outcomes, retry count, and terminal timestamps."""
        if self._engine is None:
            return
        run_id = state.get("run_id")
        if not run_id:
            return
        with Session(self.engine) as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return
            status = state.get("terminal_status", "RUNNING")
            run.status = str(status)
            run.error_count = len(state.get("error_codes", []))
            run.retry_count = sum(state.get("retry_counts", {}).values())
            run.terminal_outcomes = dict(state.get("terminal_outcomes", _default_outcomes()))
            if status in {"FAILED", "COMPLETED"}:
                run.completed_at = run.completed_at or utc_now()
            session.commit()

    def _persist_failure(
        self,
        state: IngestionState,
        *,
        stage: StageName,
        error: str,
        retryable: bool,
    ) -> None:
        """Store one safe classified failure for audit and replay diagnostics."""
        if self._engine is None:
            return
        run_id = state.get("run_id")
        if not run_id:
            return
        code = state.get("error_codes", ["DETERMINISTIC_STAGE_FAILED"])[-1]
        error_id = hashlib.sha256(f"{run_id}:{stage}:{code}".encode()).hexdigest()[:32]
        with Session(self.engine) as session:
            if session.get(IngestionError, error_id) is None:
                session.add(
                    IngestionError(
                        id=error_id,
                        pipeline_run_id=run_id,
                        stage=stage,
                        error_code=code,
                        retryable=retryable,
                        safe_message=error,
                    )
                )
                session.commit()

    def _persisted_state(self, state: IngestionState) -> IngestionState | None:
        """Reconstruct bounded terminal metadata from a persisted run."""
        if self._engine is None:
            return None
        with Session(self.engine) as session:
            run = session.get(PipelineRun, state.get("run_id"))
            if run is None:
                return None
            if run.thread_id != state["thread_id"]:
                raise IngestionServiceError(
                    "THREAD_MISMATCH",
                    "run must be resumed with its original thread",
                )
            status = str(run.status)
            if status not in {"AWAITING_REVIEW", "COMPLETED", "FAILED"}:
                return None
            candidate_ids = list(
                session.scalars(
                    select(QuarantineCandidate.id).where(
                        QuarantineCandidate.pipeline_run_id == run.id
                    )
                ).all()
            )
            terminal_status: TerminalStatus = (
                "AWAITING_REVIEW"
                if status == "AWAITING_REVIEW"
                else "FAILED"
                if status == "FAILED"
                else "COMPLETED"
            )
            visited = list(INGESTION_NODES)
            if terminal_status == "AWAITING_REVIEW":
                visited = list(INGESTION_NODES[: INGESTION_NODES.index("request_human_review")])
            return cast(
                "IngestionState",
                {
                    **state,
                    "quarantine_candidate_ids": sorted(candidate_ids),
                    "terminal_status": terminal_status,
                    "terminal_outcomes": dict(run.terminal_outcomes or _default_outcomes()),
                    "published_count": int((run.terminal_outcomes or {}).get("PUBLISHED", 0)),
                    "not_disclosed_count": int(
                        (run.terminal_outcomes or {}).get("NOT_DISCLOSED", 0)
                    ),
                    "source_not_checked_count": int(
                        (run.terminal_outcomes or {}).get("SOURCE_NOT_CHECKED", 0)
                    ),
                    "visited": visited,
                    "audit_events": [f"pipeline_reconstructed:{status.lower()}"],
                },
            )

    def _transition(self, state: IngestionState, stage: StageName) -> IngestionState:
        """Execute one stage and persist failures as an audit-first transition."""
        try:
            _validate_bounded_state(state)
            update = self.services.execute(stage, state)
            _validate_transition_identity(state, stage, update)
        except IngestionServiceError as error:
            failed = _merge_update(
                state,
                _failure_update(stage=stage, state=state, code=error.code),
            )
            failed["visited"] = [*failed.get("visited", []), stage]
            self._persist_failure(
                failed,
                stage=stage,
                error=error.safe_message,
                retryable=error.retryable,
            )
            self._persist_progress(failed)
            return failed
        transitioned = _merge_update(state, update)
        transitioned["visited"] = [*transitioned.get("visited", []), stage]
        self._persist_progress(transitioned)
        return transitioned

    def _run_state(  # noqa: C901
        self,
        state: IngestionState,
        *,
        pause_for_review: bool,
    ) -> IngestionState:
        """Advance linearly through the exact stage tuple."""
        raw_state = cast("dict[str, Any]", state)
        review_context = {
            key: raw_state[key]
            for key in ("review_decision", "review_candidate_id", "reviewer", "review_rationale")
            if key in raw_state
        }
        for stage_value in INGESTION_NODES:
            stage = cast("StageName", stage_value)
            if stage == "request_human_review":
                if pause_for_review and state.get("quarantine_candidate_ids"):
                    state["terminal_status"] = "AWAITING_REVIEW"
                    self._persist_progress(state)
                    break
                if (
                    state.get("quarantine_candidate_ids")
                    and state.get("review_decision") == "pending"
                ):
                    state["terminal_status"] = "AWAITING_REVIEW"
                    self._persist_progress(state)
                    break
            state = self._transition(state, stage)
            if stage != "request_human_review":
                raw_state = cast("dict[str, Any]", state)
                for key, value in review_context.items():
                    raw_state[key] = value
            if state.get("terminal_status") == "FAILED":
                if stage != "emit_audit_events":
                    state = self._transition(state, "emit_audit_events")
                break
            if stage == "request_human_review" and state.get("quarantine_candidate_ids"):
                pending = None
                if self._engine is not None:
                    with Session(self.engine) as session:
                        pending = session.scalar(
                            select(QuarantineCandidate.id).where(
                                QuarantineCandidate.pipeline_run_id == state.get("run_id"),
                                QuarantineCandidate.status == "PENDING",
                            )
                        )
                if pending is not None:
                    state["terminal_status"] = "AWAITING_REVIEW"
                    self._persist_progress(state)
                    break
        return state

    def run(
        self,
        state: IngestionState | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Run from discovery to completion or the durable review boundary."""
        current = self._initial_state(thread_id=thread_id, state=state)
        if self._engine is not None:
            initialize_schema(self.engine)
        persisted = self._persisted_state(current)
        if persisted is not None:
            return dict(persisted)
        if self._engine is not None:
            self._ensure_pipeline_run(current)
        return dict(self._run_state(current, pause_for_review=True))

    def resume(
        self,
        *,
        thread_id: str,
        decision: Literal["approve", "reject"],
        candidate_id: str,
        reviewer: str = "local-reviewer",
        rationale: str = "explicit deterministic review resume",
    ) -> dict[str, Any]:
        """Reconstruct a persisted run and resume it on the original thread."""
        if decision not in {"approve", "reject"}:
            msg = "review decision must be approve or reject"
            raise ValueError(msg)
        if self._engine is None:
            msg = "review resume requires a persisted StageAIngestionServices run"
            raise ValueError(msg)
        initialize_schema(self.engine)
        with Session(self.engine) as session:
            candidate = session.get(QuarantineCandidate, candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            run = session.get(PipelineRun, candidate.pipeline_run_id)
            if run is None or run.thread_id != thread_id:
                msg = "review must resume the candidate's original thread"
                raise ValueError(msg)
            if candidate_id not in set(
                session.scalars(
                    select(QuarantineCandidate.id).where(
                        QuarantineCandidate.pipeline_run_id == run.id
                    )
                ).all()
            ):
                msg = "review candidate is not part of the persisted run"
                raise ValueError(msg)
            persisted_run_key = run.run_key
            persisted_run_id = run.id
        source_keys = sorted(cast("dict[str, object]", getattr(self.services, "definitions", {})))
        current = self._initial_state(
            thread_id=thread_id,
            state=cast("IngestionState", {"source_keys": source_keys}),
        )
        if current.get("run_key") != persisted_run_key or current.get("run_id") != persisted_run_id:
            msg = "persisted review run no longer matches the current source configuration"
            raise ValueError(msg)
        current["review_decision"] = decision
        current["review_candidate_id"] = candidate_id
        current["reviewer"] = reviewer
        current["review_rationale"] = rationale
        return dict(self._run_state(current, pause_for_review=False))


def create_ingestion_runtime(
    *,
    services: IngestionServices | None = None,
) -> DeterministicIngestionRuntime:
    """Create the framework-free deterministic ingestion runtime."""
    return DeterministicIngestionRuntime(services=services)


def run_cli_review_resume(  # noqa: PLR0913
    *,
    engine: Engine,
    candidate_id: str,
    thread_id: str,
    decision: Literal["approve", "reject"],
    reviewer: str,
    rationale: str,
    config_dir: Path | None = None,
) -> dict[str, object]:
    """Rebuild services from configuration and resume the persisted run thread."""
    services = StageAIngestionServices(engine=engine, config_dir=config_dir)
    runtime = DeterministicIngestionRuntime(services)
    resumed = runtime.resume(
        thread_id=thread_id,
        candidate_id=candidate_id,
        decision=decision,
        reviewer=reviewer,
        rationale=rationale,
    )
    if resumed.get("terminal_status") == "FAILED":
        codes = ",".join(cast("list[str]", resumed.get("error_codes", [])))
        msg = f"review resume failed closed: {codes or 'DETERMINISTIC_STAGE_FAILED'}"
        raise ValueError(msg)
    with Session(engine) as session:
        reviewed = session.get(QuarantineCandidate, candidate_id)
        if reviewed is None:
            raise KeyError(candidate_id)
        status = reviewed.status
    return {
        "candidate_id": candidate_id,
        "decision": decision,
        "status": status,
        "thread_id": thread_id,
        "terminal_status": resumed.get("terminal_status"),
        "terminal_outcomes": resumed.get("terminal_outcomes", {}),
    }
