"""Deterministic seeding and read-only queries for the Stage A data product."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import Engine, Select, func, or_, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    Company,
    EarningsEvent,
    EntityIdentifier,
    Filing,
    FilingDocument,
    FiscalCalendarRegime,
    MetricAlias,
    MetricDefinition,
    MetricDefinitionVersion,
    MetricObservation,
    ObservationEvidence,
    ReportingEntity,
    ReportingScope,
    Security,
    SourceEvidence,
    initialize_schema,
    utc_now,
)
from mortgage_servicing_dashboard.domain import (
    ComparabilityStatus,
    ComparisonInput,
    ObservationState,
    assess_comparability,
)


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """Serializable observation with its full semantic and evidence context."""

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
    value: str | None
    currency: str | None
    unit: str
    scale: str
    state: str
    quality_state: str
    methodology: str
    reporting_entity_id: str
    reporting_scope_id: str
    portfolio_population: str
    reported_label: str
    reported_value: str
    evidence_locator: str
    evidence_id: str | None
    source_url: str | None
    source_class: str | None
    accession_or_identifier: str | None
    retrieved_at: str | None
    published_at: str | None
    bounded_excerpt: str | None
    knowledge_from: str
    knowledge_to: str | None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return asdict(self)


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
        """Return a JSON-compatible representation."""
        payload = asdict(self)
        payload["left"] = self.left.as_dict()
        payload["right"] = self.right.as_dict()
        return payload


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
    """Load universe, metric catalog, and recorded public observations."""
    root = config_directory(explicit)
    return (
        _load_yaml(root / "universe.yaml"),
        _load_yaml(root / "metrics" / "catalog.yaml"),
        _load_yaml(root / "stage_a_data.yaml"),
    )


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _metric_display_name(metric_id: str) -> str:
    return metric_id.replace("_", " ").title().replace("Msr", "MSR").replace("Upb", "UPB")


def seed_stage_a(  # noqa: C901, PLR0912, PLR0915
    engine: Engine,
    *,
    config_dir: Path | None = None,
) -> dict[str, int]:
    """Idempotently materialize the reviewed Stage A fixture data."""
    initialize_schema(engine)
    universe, catalog, data = load_stage_a_configuration(config_dir)
    companies = cast("list[dict[str, Any]]", universe["companies"])
    metrics = cast("list[dict[str, Any]]", catalog["metrics"])
    quarters = cast("list[dict[str, Any]]", data["quarters"])
    sources = cast("dict[str, dict[str, Any]]", data["sources"])
    supplied = cast("list[dict[str, Any]]", data["observations"])
    supplied_by_key = {
        (item["company_id"], item["quarter"], item["metric_id"]): item for item in supplied
    }
    inserted = {"companies": 0, "metrics": 0, "evidence": 0, "observations": 0}
    known_at = _instant("2026-08-11T12:00:00Z")

    with Session(engine) as session:
        for company in companies:
            company_id = str(company["id"])
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
                entity_id = str(company["reporting_entity"])
                scope_id = str(company["reporting_scope"])
                session.add(
                    ReportingEntity(
                        id=entity_id,
                        company_id=company_id,
                        legal_name=str(company["legal_name"]),
                        entity_type="SEC_REGISTRANT",
                    )
                )
                population = (
                    "serviced_for_others_plus_bank_owned_residential_loans"
                    if company_id == "tfc"
                    else "owned_msr_plus_subservicing_plus_held_for_sale"
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
                inserted["companies"] += 1

        for metric in metrics:
            metric_id = str(metric["id"])
            version_id = f"{metric_id}:{metric['semantic_version']}"
            if session.get(MetricDefinition, metric_id) is None:
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
                inserted["metrics"] += 1

        evidence_ids: dict[str, str] = {}
        for source_key, source in sources.items():
            evidence_id = f"evidence:{source_key}"
            evidence_ids[source_key] = evidence_id
            if session.get(SourceEvidence, evidence_id) is not None:
                continue
            content_hash = _stable_hash(source)
            session.add(
                SourceEvidence(
                    id=evidence_id,
                    source_class=str(source["source_class"]),
                    original_url=str(source["url"]),
                    retrieved_at=known_at,
                    published_at=_instant(str(source["published_at"])),
                    accession_or_identifier=str(source["accession"]),
                    content_sha256=content_hash,
                    media_type=str(source["media_type"]),
                    parser_version="recorded-fixture-v1",
                    acquisition_run_id=str(data["dataset_version"]),
                    reporting_entity_candidate=f"{source['company_id']}_registrant",
                    reporting_period_candidate=str(source["period_end"]),
                    retention_location=f"recorded://{source_key}/{content_hash}",
                    bounded_excerpt=str(source["excerpt"]),
                )
            )
            filing_id = f"filing:{source_key}"
            company = next(item for item in companies if item["id"] == source["company_id"])
            session.add(
                Filing(
                    id=filing_id,
                    reporting_entity_id=str(company["reporting_entity"]),
                    form_type=(
                        "8-K EX-99.2"
                        if source["source_class"] == "SEC_EARNINGS_EXHIBIT"
                        else "10-Q/10-K"
                    ),
                    accession=str(source["accession"]),
                    filed_at=_instant(str(source["published_at"])),
                    period_end=date.fromisoformat(str(source["period_end"])),
                    amendment_of_id=None,
                )
            )
            session.add(
                FilingDocument(
                    id=f"document:{source_key}",
                    filing_id=filing_id,
                    sequence=1,
                    document_type="primary_or_exhibit",
                    filename=str(source["url"]).rsplit("/", maxsplit=1)[-1],
                    source_url=str(source["url"]),
                )
            )
            event_id = f"earnings:{source_key}"
            if session.get(EarningsEvent, event_id) is None:
                session.add(
                    EarningsEvent(
                        id=event_id,
                        company_id=str(source["company_id"]),
                        fiscal_year=int(str(source["period_end"])[:4]),
                        fiscal_quarter=((int(str(source["period_end"])[5:7]) - 1) // 3) + 1,
                        event_at=_instant(str(source["published_at"])),
                        evidence_id=evidence_id,
                    )
                )
            inserted["evidence"] += 1

        for company in companies:
            company_id = str(company["id"])
            entity_id = str(company["reporting_entity"])
            scope_id = str(company["reporting_scope"])
            for quarter in quarters:
                quarter_label = str(quarter["label"])
                quarter_source = evidence_ids[
                    f"{company_id}_{quarter['fiscal_year']}_q{quarter['fiscal_quarter']}"
                ]
                for metric in metrics:
                    metric_id = str(metric["id"])
                    observation_id = (
                        f"observation:{company_id}:{quarter['period_end']}:{metric_id}:v1"
                    )
                    if session.get(MetricObservation, observation_id) is not None:
                        continue
                    candidate = supplied_by_key.get((company_id, quarter_label, metric_id)) or {}
                    is_missing = not candidate
                    unit = str(metric["unit"])
                    scale = str(metric.get("scale", "ones"))
                    value = None if is_missing else Decimal(str(candidate["value"]))
                    evidence_row = session.get(SourceEvidence, quarter_source)
                    if evidence_row is None:
                        msg = f"missing evidence: {quarter_source}"
                        raise RuntimeError(msg)
                    state = (
                        ObservationState.NOT_DISCLOSED.value
                        if is_missing
                        else str(candidate.get("state", ObservationState.REPORTED_ACTUAL.value))
                    )
                    locator = (
                        f"{evidence_row.original_url} -- reviewed for {metric_id}; "
                        "no disclosure located"
                        if is_missing
                        else (
                            f"{evidence_row.original_url} -- {candidate['label']} ({quarter_label})"
                        )
                    )
                    session.add(
                        MetricObservation(
                            id=observation_id,
                            metric_version_id=f"{metric_id}:{metric['semantic_version']}",
                            reporting_entity_id=entity_id,
                            reporting_scope_id=scope_id,
                            period_start=date.fromisoformat(str(quarter["period_start"])),
                            period_end=date.fromisoformat(str(quarter["period_end"])),
                            fiscal_year=int(quarter["fiscal_year"]),
                            fiscal_quarter=int(quarter["fiscal_quarter"]),
                            period_type=str(metric["period_semantics"]),
                            value=value,
                            currency="USD" if unit in {"USD", "USD_per_loan"} else None,
                            unit=unit,
                            scale=scale,
                            reported_decimals=None,
                            observation_state=state,
                            methodology=(
                                "not_disclosed_after_source_review"
                                if is_missing
                                else str(candidate["methodology"])
                            ),
                            evidence_locator=locator,
                            extraction_method="deterministic_recorded_fixture",
                            parser_metadata={
                                "dataset_version": data["dataset_version"],
                                "precision_preserved": True,
                            },
                            valid_from=date.fromisoformat(str(quarter["period_end"])),
                            valid_to=None,
                            knowledge_from=known_at,
                            knowledge_to=None,
                            supersedes_observation_id=None,
                            quality_state="VALIDATED",
                            reported_label=(
                                "No disclosure located" if is_missing else str(candidate["label"])
                            ),
                            reported_value=(
                                "Not disclosed" if is_missing else str(candidate["reported_value"])
                            ),
                        )
                    )
                    session.add(
                        ObservationEvidence(
                            observation_id=observation_id,
                            evidence_id=quarter_source,
                            evidence_role="reviewed_source" if is_missing else "primary",
                        )
                    )
                    inserted["observations"] += 1
        session.commit()
    return inserted


def _as_of_instant(as_of: datetime | date | None) -> datetime:
    if as_of is None:
        return utc_now()
    if isinstance(as_of, datetime):
        return as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    return datetime.combine(as_of, time.max, tzinfo=UTC)


class IntelligenceRepository:
    """Read-only application queries over published observations."""

    def __init__(self, engine: Engine) -> None:
        """Bind read-only queries to an initialized engine."""
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
        """Return one immutable evidence record."""
        with Session(self._engine) as session:
            item = session.get(SourceEvidence, evidence_id)
            if item is None:
                return None
            return {
                "id": item.id,
                "source_class": item.source_class,
                "original_url": item.original_url,
                "retrieved_at": item.retrieved_at.isoformat(),
                "published_at": (
                    item.published_at.isoformat() if item.published_at is not None else None
                ),
                "accession_or_identifier": item.accession_or_identifier,
                "content_sha256": item.content_sha256,
                "media_type": item.media_type,
                "parser_version": item.parser_version,
                "retention_location": item.retention_location,
                "bounded_excerpt": item.bounded_excerpt,
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
                    "event_at": event.event_at.isoformat(),
                    "evidence_id": evidence.id,
                    "source_url": evidence.original_url,
                }
                for event, company, evidence in session.execute(statement)
            ]

    def freshness(self) -> dict[str, object]:
        """Return public materialization freshness and coverage boundaries."""
        with Session(self._engine) as session:
            retrieved_at = session.scalar(select(func.max(SourceEvidence.retrieved_at)))
            knowledge_at = session.scalar(select(func.max(MetricObservation.knowledge_from)))
            observation_count = session.scalar(select(func.count(MetricObservation.id))) or 0
        return {
            "dataset": "stage-a-recorded-2026-08-11",
            "retrieved_at": retrieved_at.isoformat() if retrieved_at is not None else None,
            "knowledge_at": knowledge_at.isoformat() if knowledge_at is not None else None,
            "observation_count": observation_count,
            "model_calls_enabled": False,
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
            )
        )
        if company_id is not None:
            statement = statement.where(Company.id == company_id)
        if metric_id is not None:
            statement = statement.where(MetricDefinition.id == metric_id)
        if period_end is not None:
            statement = statement.where(MetricObservation.period_end == period_end)
        return statement.order_by(
            MetricObservation.period_end,
            Company.id,
            MetricDefinition.id,
        )

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
            period_start=(
                observation.period_start.isoformat()
                if observation.period_start is not None
                else None
            ),
            period_end=observation.period_end.isoformat(),
            fiscal_year=observation.fiscal_year,
            fiscal_quarter=observation.fiscal_quarter,
            value=str(observation.value) if observation.value is not None else None,
            currency=observation.currency,
            unit=observation.unit,
            scale=observation.scale,
            state=observation.observation_state,
            quality_state=observation.quality_state,
            methodology=observation.methodology,
            reporting_entity_id=observation.reporting_entity_id,
            reporting_scope_id=observation.reporting_scope_id,
            portfolio_population=scope.portfolio_population,
            reported_label=observation.reported_label,
            reported_value=observation.reported_value,
            evidence_locator=observation.evidence_locator,
            evidence_id=evidence.id if evidence is not None else None,
            source_url=evidence.original_url if evidence is not None else None,
            source_class=evidence.source_class if evidence is not None else None,
            accession_or_identifier=(
                evidence.accession_or_identifier if evidence is not None else None
            ),
            retrieved_at=evidence.retrieved_at.isoformat() if evidence is not None else None,
            published_at=(
                evidence.published_at.isoformat()
                if evidence is not None and evidence.published_at is not None
                else None
            ),
            bounded_excerpt=evidence.bounded_excerpt if evidence is not None else None,
            knowledge_from=observation.knowledge_from.isoformat(),
            knowledge_to=(
                observation.knowledge_to.isoformat()
                if observation.knowledge_to is not None
                else None
            ),
        )

    def observations(
        self,
        *,
        as_of: datetime | date | None = None,
        company_id: str | None = None,
        metric_id: str | None = None,
        period_end: date | None = None,
        include_missing: bool = True,
    ) -> list[ObservationRecord]:
        """Query observations using transaction-time semantics."""
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
        with Session(self._engine) as session:
            return [self._record(row) for row in session.execute(statement).all()]

    def observation(self, observation_id: str) -> ObservationRecord | None:
        """Return one observation and provenance payload."""
        statement = self._observation_statement(as_of=None).where(
            MetricObservation.id == observation_id
        )
        with Session(self._engine) as session:
            row = session.execute(statement).first()
        return None if row is None else self._record(row)

    def latest_period_end(self) -> date | None:
        """Return the latest published quarter."""
        with Session(self._engine) as session:
            return session.scalar(select(func.max(MetricObservation.period_end)))

    def coverage(self, *, as_of: datetime | date | None = None) -> list[dict[str, object]]:
        """Summarize disclosure coverage without treating missing as zero."""
        records = self.observations(as_of=as_of)
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for item in records:
            key = (item.company_id, item.period_end)
            counts = grouped.setdefault(key, {"reported": 0, "missing": 0})
            bucket = "missing" if item.state == ObservationState.NOT_DISCLOSED.value else "reported"
            counts[bucket] += 1
        return [
            {
                "company_id": company_id,
                "period_end": period,
                "reported": counts["reported"],
                "missing": counts["missing"],
                "total": counts["reported"] + counts["missing"],
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
        """Build a pairwise, period-specific two-company comparison."""
        rows = self.observations(
            as_of=as_of,
            metric_id=metric_id,
            period_end=period_end,
        )
        by_company = {row.company_id: row for row in rows}
        if set(by_company) != {"tfc", "pfsi"}:
            return None
        left = by_company["tfc"]
        right = by_company["pfsi"]
        result = assess_comparability(
            ComparisonInput(
                metric_id=left.metric_id,
                metric_version=left.metric_version,
                reporting_scope=left.reporting_scope_id,
                period_days=_period_days(left),
                currency=left.currency,
                unit=left.unit,
                methodology=left.methodology,
                observation_state=ObservationState(left.state),
                portfolio_population=left.portfolio_population,
            ),
            ComparisonInput(
                metric_id=right.metric_id,
                metric_version=right.metric_version,
                reporting_scope=right.reporting_scope_id,
                period_days=_period_days(right),
                currency=right.currency,
                unit=right.unit,
                methodology=right.methodology,
                observation_state=ObservationState(right.state),
                portfolio_population=right.portfolio_population,
            ),
        )
        if result.status is not ComparabilityStatus.INSUFFICIENT_INFORMATION and metric_id in {
            "total_servicing_upb",
            "servicing_revenue",
            "servicing_pretax_income",
        }:
            result = type(result)(
                ComparabilityStatus.NOT_COMPARABLE,
                tuple(
                    dict.fromkeys(
                        (*result.reasons, "issuer-defined populations or economics differ")
                    )
                ),
            )
        return ComparisonRecord(
            metric_id=metric_id,
            period_end=period_end.isoformat(),
            left=left,
            right=right,
            status=result.status.value,
            reasons=result.reasons,
        )


def _period_days(record: ObservationRecord) -> int | None:
    if record.period_start is None:
        return None
    return (
        date.fromisoformat(record.period_end) - date.fromisoformat(record.period_start)
    ).days + 1
