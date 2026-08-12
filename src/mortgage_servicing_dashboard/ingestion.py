"""Typed deterministic ingestion services coordinated by a bounded LangGraph."""

# ruff: noqa: EM101

from __future__ import annotations

import hashlib
import json
import operator
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, assert_never, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from mortgage_servicing_dashboard.database import (
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
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
    PublicSourceError,
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    RetainedDocument,
    StageARecordedDocumentParser,
    TransientPublicSourceError,
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
    visited: Annotated[list[str], operator.add]
    review_decision: Literal["approve", "reject", "pending"]
    reviewer: str
    review_rationale: str
    published_count: int
    not_disclosed_count: int
    source_not_checked_count: int
    retry_counts: dict[str, int]
    terminal_status: TerminalStatus
    terminal_outcomes: dict[str, int]
    error_codes: list[str]
    audit_events: Annotated[list[str], operator.add]


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
    """Stage service contract invoked by every LangGraph node."""

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

    def _quarantine(self, state: IngestionState) -> IngestionUpdate:
        thread_id = _required_thread_id(state)
        prepare_stage_a(self.engine, config_dir=self.config_root, thread_id=thread_id)
        with Session(self.engine) as session:
            run = session.get(PipelineRun, state.get("run_id"))
            if run is not None:
                run.retry_count = sum(state.get("retry_counts", {}).values())
                session.commit()
        configured_ids = {
            str(recipe["candidate_id"])
            for definition in self.definitions.values()
            for recipe in definition.quarantine_rows
        }
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

    def _record_review(self, state: IngestionState) -> IngestionUpdate:
        decision = state.get("review_decision", "pending")
        if decision not in {"approve", "reject"}:
            raise IngestionServiceError(
                "REVIEW_DECISION_INVALID",
                "review resume must contain approve or reject",
            )
        repository = IntelligenceRepository(self.engine)
        for candidate_id in state.get("quarantine_candidate_ids", []):
            repository.record_review_decision(
                candidate_id=candidate_id,
                decision=decision,
                reviewer=state.get("reviewer", "langgraph-human-review"),
                rationale=state.get(
                    "review_rationale",
                    "explicit durable-thread review resume",
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
            pending_revalidation = session.scalars(
                select(QuarantineCandidate).where(
                    QuarantineCandidate.pipeline_run_id == state.get("run_id"),
                    QuarantineCandidate.status == "APPROVED_PENDING_REVALIDATION",
                )
            ).all()
            for candidate in pending_revalidation:
                candidate.status = "QUARANTINED_AFTER_REVALIDATION"
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


def _checkpoint_thread(config: RunnableConfig) -> str:
    configurable = cast("dict[str, object]", config.get("configurable", {}))
    return str(configurable.get("thread_id", ""))


def _failure_update(
    *,
    stage: StageName,
    state: IngestionState,
    code: str,
) -> dict[str, Any]:
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
        "visited": [stage],
        "terminal_status": "FAILED",
        "terminal_outcomes": outcomes,
        "error_codes": [code],
        "audit_events": [f"stage_failed:{stage}:{code}"],
    }


def _stage_node(
    stage: StageName,
    services: IngestionServices,
) -> Callable[[IngestionState, RunnableConfig], dict[str, Any]]:
    def node(state: IngestionState, config: RunnableConfig) -> dict[str, Any]:
        checkpoint_thread = _checkpoint_thread(config)
        if checkpoint_thread != state.get("thread_id"):
            return _failure_update(stage=stage, state=state, code="THREAD_MISMATCH")
        try:
            _validate_bounded_state(state)
            update = dict(services.execute(stage, state))
            update["visited"] = [stage]
        except IngestionServiceError as error:
            return _failure_update(stage=stage, state=state, code=error.code)
        else:
            return update

    node.__name__ = stage
    return node


def _continue_route(state: IngestionState) -> Literal["next", "audit"]:
    return "audit" if state.get("terminal_status") == "FAILED" else "next"


def _review_route(
    state: IngestionState,
) -> Literal["request_human_review", "publish", "audit"]:
    if state.get("terminal_status") == "FAILED":
        return "audit"
    if state.get("quarantine_candidate_ids"):
        return "request_human_review"
    return "publish"


def _request_review_node(
    services: IngestionServices,
) -> Callable[[IngestionState, RunnableConfig], dict[str, Any]]:
    def node(state: IngestionState, config: RunnableConfig) -> dict[str, Any]:
        if _checkpoint_thread(config) != state.get("thread_id"):
            return _failure_update(
                stage="request_human_review",
                state=state,
                code="THREAD_MISMATCH",
            )
        decision = interrupt(
            {
                "kind": "metric_candidate_review",
                "candidate_ids": list(state.get("quarantine_candidate_ids", [])),
                "allowed_decisions": ["approve", "reject"],
                "thread_id": state.get("thread_id"),
            }
        )
        if not isinstance(decision, dict) or decision.get("decision") not in {
            "approve",
            "reject",
        }:
            return _failure_update(
                stage="request_human_review",
                state=state,
                code="REVIEW_PAYLOAD_INVALID",
            )
        review_state = cast("IngestionState", dict(state))
        review_state["review_decision"] = decision["decision"]
        try:
            update = dict(services.execute("request_human_review", review_state))
        except IngestionServiceError as error:
            return _failure_update(
                stage="request_human_review",
                state=state,
                code=error.code,
            )
        update["visited"] = ["request_human_review"]
        update["review_decision"] = decision["decision"]
        return update

    return node


def create_ingestion_graph(
    *,
    services: IngestionServices | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile all 16 Stage A stages over typed services and bounded checkpoint state."""
    bound_services = services or StageAIngestionServices()
    builder = StateGraph(IngestionState)
    for stage in INGESTION_NODES:
        if stage == "request_human_review":
            builder.add_node(stage, cast("Any", _request_review_node(bound_services)))
        else:
            builder.add_node(
                stage,
                cast("Any", _stage_node(cast("StageName", stage), bound_services)),
            )
    builder.add_edge(START, INGESTION_NODES[0])
    quarantine_index = INGESTION_NODES.index("quarantine_ambiguous_candidates")
    linear_stages = INGESTION_NODES[:quarantine_index]
    for left, right in pairwise(linear_stages):
        builder.add_conditional_edges(
            left,
            _continue_route,
            {"next": right, "audit": "emit_audit_events"},
        )
    builder.add_conditional_edges(
        linear_stages[-1],
        _continue_route,
        {"next": "quarantine_ambiguous_candidates", "audit": "emit_audit_events"},
    )
    builder.add_conditional_edges(
        "quarantine_ambiguous_candidates",
        _review_route,
        {
            "request_human_review": "request_human_review",
            "publish": "publish_approved_observations",
            "audit": "emit_audit_events",
        },
    )
    builder.add_conditional_edges(
        "request_human_review",
        _continue_route,
        {"next": "publish_approved_observations", "audit": "emit_audit_events"},
    )
    builder.add_conditional_edges(
        "publish_approved_observations",
        _continue_route,
        {"next": "refresh_comparability_and_materializations", "audit": "emit_audit_events"},
    )
    builder.add_edge(
        "refresh_comparability_and_materializations",
        "emit_audit_events",
    )
    builder.add_edge("emit_audit_events", END)
    return builder.compile(checkpointer=checkpointer, name="public_servicing_ingestion_v1")


def resume_review(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    thread_id: str,
    decision: Literal["approve", "reject"],
) -> dict[str, Any]:
    """Resume a paused review using exactly its durable opaque thread ID."""
    config = RunnableConfig(configurable={"thread_id": thread_id})
    result = graph.invoke(Command[Any](resume={"decision": decision}), config=config)
    return dict(result)


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
    """Rebuild and resume the deterministic review graph on its persisted run thread."""
    with Session(engine) as session:
        candidate = session.get(QuarantineCandidate, candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        run = session.get(PipelineRun, candidate.pipeline_run_id)
        if run is None or run.thread_id != thread_id:
            msg = "review must resume the candidate's original thread"
            raise ValueError(msg)

    services = StageAIngestionServices(engine=engine, config_dir=config_dir)
    graph = create_ingestion_graph(services=services, checkpointer=InMemorySaver())
    config = RunnableConfig(configurable={"thread_id": thread_id})
    interrupted = graph.invoke(
        {
            "thread_id": thread_id,
            "source_keys": [],
            "visited": [],
            "review_decision": "pending",
            "reviewer": reviewer,
            "review_rationale": rationale,
            "published_count": 0,
            "audit_events": [],
        },
        config=config,
    )
    if candidate_id not in interrupted.get("quarantine_candidate_ids", []):
        msg = "candidate is not part of the interrupted review thread"
        raise ValueError(msg)
    resumed = resume_review(graph, thread_id=thread_id, decision=decision)
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
