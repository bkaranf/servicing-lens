"""SQLAlchemy persistence model for evidence, observations, and review history."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, validates

_MONEY = Numeric(38, 10, asdecimal=True)


def utc_now() -> datetime:
    """Return the current UTC time for knowledge-time records."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative model base."""


class Company(Base):
    """Public company in a versioned selected universe."""

    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    ticker: Mapped[str] = mapped_column(String(16), unique=True)
    classification: Mapped[str] = mapped_column(String(32))
    universe_version: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Security(Base):
    """Exchange-listed security for a company."""

    __tablename__ = "securities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    ticker: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(32))
    security_type: Mapped[str] = mapped_column(String(64))


class ReportingEntity(Base):
    """Legal, regulatory, registrant, subsidiary, or segment entity."""

    __tablename__ = "reporting_entities"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    legal_name: Mapped[str] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(64))


class ReportingScope(Base):
    """Population and organizational scope attached to a fact."""

    __tablename__ = "reporting_scopes"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    name: Mapped[str] = mapped_column(String(255))
    portfolio_population: Mapped[str] = mapped_column(String(128))
    methodology: Mapped[str] = mapped_column(Text)


class EntityIdentifier(Base):
    """Typed public identifier such as CIK or RSSD."""

    __tablename__ = "entity_identifiers"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    scheme: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(128))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class EntityRelationship(Base):
    """Bitemporal corporate or reporting relationship."""

    __tablename__ = "entity_relationships"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    parent_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    child_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    relationship_type: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    known_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    known_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FiscalCalendarRegime(Base):
    """Effective fiscal-calendar convention."""

    __tablename__ = "fiscal_calendar_regimes"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    fiscal_year_end_month: Mapped[int] = mapped_column(Integer)
    fiscal_year_end_day: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class AccountingPolicyRegime(Base):
    """Effective accounting or valuation methodology."""

    __tablename__ = "accounting_policy_regimes"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    policy_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class CorporateAction(Base):
    """Corporate action that may break a trend or scope."""

    __tablename__ = "corporate_actions"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    action_type: Mapped[str] = mapped_column(String(64))
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, object]] = mapped_column(JSON)


class Filing(Base):
    """SEC or regulatory filing envelope."""

    __tablename__ = "filings"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    form_type: Mapped[str] = mapped_column(String(32))
    accession: Mapped[str] = mapped_column(String(40), unique=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[date] = mapped_column(Date)
    amendment_of_id: Mapped[str | None] = mapped_column(ForeignKey("filings.id"))


class FilingDocument(Base):
    """Document within a filing."""

    __tablename__ = "filing_documents"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    filing_id: Mapped[str] = mapped_column(ForeignKey("filings.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    document_type: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)


class SourceEvidence(Base):
    """Immutable content-addressed public evidence metadata."""

    __tablename__ = "source_evidence"
    __table_args__ = (
        UniqueConstraint(
            "content_sha256",
            "byte_length",
            name="uq_source_evidence_content_identity",
        ),
        CheckConstraint("length(content_sha256) = 64", name="ck_source_evidence_sha256_length"),
        CheckConstraint("byte_length > 0", name="ck_source_evidence_positive_length"),
    )
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_class: Mapped[str] = mapped_column(String(64))
    original_url: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accession_or_identifier: Mapped[str | None] = mapped_column(String(128))
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_length: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(128))
    representation: Mapped[str] = mapped_column(String(64))
    capture_method: Mapped[str] = mapped_column(String(128))
    parser_version: Mapped[str] = mapped_column(String(32))
    acquisition_run_id: Mapped[str] = mapped_column(String(96))
    reporting_entity_candidate: Mapped[str] = mapped_column(String(96))
    reporting_period_candidate: Mapped[str] = mapped_column(String(32))
    retention_location: Mapped[str] = mapped_column(Text)
    bounded_excerpt: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))


class RawXbrlFact(Base):
    """Unnormalized XBRL fact retained for replay."""

    __tablename__ = "raw_xbrl_facts"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"))
    concept: Mapped[str] = mapped_column(String(255))
    context_ref: Mapped[str] = mapped_column(String(255))
    raw_value: Mapped[str] = mapped_column(Text)
    unit_ref: Mapped[str | None] = mapped_column(String(128))


class RawRegulatoryFact(Base):
    """Unnormalized bank-regulatory fact scoped to its reporter."""

    __tablename__ = "raw_regulatory_facts"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"))
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    schedule: Mapped[str] = mapped_column(String(64))
    item_code: Mapped[str] = mapped_column(String(64))
    raw_value: Mapped[str] = mapped_column(Text)


class MetricDefinition(Base):
    """Stable metric identity."""

    __tablename__ = "metric_definitions"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))


class MetricDefinitionVersion(Base):
    """Effective semantic contract for a metric."""

    __tablename__ = "metric_definition_versions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metric_id: Mapped[str] = mapped_column(ForeignKey("metric_definitions.id"))
    semantic_version: Mapped[str] = mapped_column(String(32))
    business_meaning: Mapped[str] = mapped_column(Text)
    grain: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32))
    permitted_scopes: Mapped[list[str]] = mapped_column(JSON)
    rules: Mapped[dict[str, object]] = mapped_column(JSON)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class MetricAlias(Base):
    """Issuer label mapped to a versioned metric."""

    __tablename__ = "metric_aliases"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_definition_versions.id"))
    reporting_entity_id: Mapped[str | None] = mapped_column(ForeignKey("reporting_entities.id"))
    source_label: Mapped[str] = mapped_column(Text)


class EligibleSourceAssessment(Base):
    """Cell-level record of which eligible public sources were actually checked."""

    __tablename__ = "eligible_source_assessments"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "metric_version_id",
            "reporting_scope_id",
            "period_end",
            "assessment_version",
            name="uq_eligible_source_assessment_cell",
        ),
        CheckConstraint(
            "assessment_status IN ('DISCLOSURE_FOUND', 'CHECKED_COMPLETE', 'SOURCE_NOT_CHECKED')",
            name="ck_eligible_source_assessment_status",
        ),
        Index(
            "ix_eligible_source_assessment_status_period",
            "assessment_status",
            "period_end",
        ),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_definition_versions.id"))
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    reporting_scope_id: Mapped[str] = mapped_column(ForeignKey("reporting_scopes.id"))
    period_end: Mapped[date] = mapped_column(Date)
    assessment_status: Mapped[str] = mapped_column(String(32))
    eligible_source_inventory: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    checked_evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    checked_locators: Mapped[list[str]] = mapped_column(JSON)
    assessment_version: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetricObservation(Base):
    """Published or explicitly missing versioned observation."""

    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "metric_version_id",
            "reporting_entity_id",
            "reporting_scope_id",
            "period_end",
            "methodology",
            "knowledge_from",
            name="uq_observation_semantic_knowledge_key",
        ),
        CheckConstraint(
            "(observation_state = 'NOT_DISCLOSED' AND value IS NULL) OR "
            "(observation_state <> 'NOT_DISCLOSED' AND value IS NOT NULL)",
            name="ck_observation_value_state",
        ),
        Index("ix_metric_observations_semantic_digest", "semantic_key_digest"),
        Index("ix_metric_observations_period", "period_end", "metric_version_id"),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_definition_versions.id"))
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    reporting_scope_id: Mapped[str] = mapped_column(ForeignKey("reporting_scopes.id"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    fiscal_quarter: Mapped[int] = mapped_column(Integer)
    period_type: Mapped[str] = mapped_column(String(16))
    value: Mapped[Decimal | None] = mapped_column(_MONEY)
    currency: Mapped[str | None] = mapped_column(String(3))
    unit: Mapped[str] = mapped_column(String(32))
    scale: Mapped[str] = mapped_column(String(32))
    reported_decimals: Mapped[int | None] = mapped_column(Integer)
    reported_precision: Mapped[str] = mapped_column(String(128))
    observation_state: Mapped[str] = mapped_column(String(32))
    methodology: Mapped[str] = mapped_column(String(128))
    evidence_locator: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(64))
    parser_metadata: Mapped[dict[str, object]] = mapped_column(JSON)
    validation_summary: Mapped[str] = mapped_column(Text)
    publication_state: Mapped[str] = mapped_column(String(32))
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    semantic_key_digest: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    knowledge_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    knowledge_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("metric_observations.id")
    )
    quality_state: Mapped[str] = mapped_column(String(32))
    reported_label: Mapped[str] = mapped_column(Text)
    reported_value: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @validates("value")
    def reject_float_value(self, _: str, value: object) -> Decimal | None:
        """Reject binary floats at the authoritative ORM boundary."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str | int) and not isinstance(value, bool):
            return Decimal(value)
        if isinstance(value, float):
            msg = "authoritative observation values cannot be binary floats"
            raise TypeError(msg)
        msg = "authoritative observation values must be Decimal, integer, or decimal text"
        raise TypeError(msg)


class ObservationEvidence(Base):
    """Many-to-many observation evidence lineage."""

    __tablename__ = "observation_evidence"
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("metric_observations.id"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"), primary_key=True)
    evidence_role: Mapped[str] = mapped_column(String(32), default="primary")
    locator: Mapped[str] = mapped_column(Text)
    raw_label: Mapped[str] = mapped_column(Text)
    raw_value: Mapped[str] = mapped_column(Text)
    disclosed_unit: Mapped[str] = mapped_column(String(32))
    disclosed_scale: Mapped[str] = mapped_column(String(32))
    extraction_method: Mapped[str] = mapped_column(String(64))
    validation_status: Mapped[str] = mapped_column(String(32))


class ObservationRevision(Base):
    """Audited revision or supersession event."""

    __tablename__ = "observation_revisions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("metric_observations.id"))
    prior_observation_id: Mapped[str | None] = mapped_column(ForeignKey("metric_observations.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ComparabilityAssessment(Base):
    """Pairwise, as-of comparability result."""

    __tablename__ = "comparability_assessments"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    left_observation_id: Mapped[str] = mapped_column(ForeignKey("metric_observations.id"))
    right_observation_id: Mapped[str] = mapped_column(ForeignKey("metric_observations.id"))
    status: Mapped[str] = mapped_column(String(40))
    reasons: Mapped[list[str]] = mapped_column(JSON)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EarningsEvent(Base):
    """Filed or issuer earnings event."""

    __tablename__ = "earnings_events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    fiscal_year: Mapped[int] = mapped_column(Integer)
    fiscal_quarter: Mapped[int] = mapped_column(Integer)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"))


class PipelineRun(Base):
    """Idempotent ingestion run."""

    __tablename__ = "pipeline_runs"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    thread_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    requested_company_id: Mapped[str | None] = mapped_column(String(64))
    requested_periods: Mapped[list[str]] = mapped_column(JSON)
    code_version: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    terminal_outcomes: Mapped[dict[str, int]] = mapped_column(JSON)


class IngestionError(Base):
    """Structured non-secret ingestion failure."""

    __tablename__ = "ingestion_errors"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"))
    stage: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean)
    safe_message: Mapped[str] = mapped_column(Text)


class QuarantineCandidate(Base):
    """Unpublished extraction candidate awaiting deterministic or human review."""

    __tablename__ = "quarantine_candidates"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"))
    proposed_metric_id: Mapped[str] = mapped_column(String(96))
    raw_source_label: Mapped[str] = mapped_column(Text)
    raw_value: Mapped[str] = mapped_column(Text)
    proposed_normalized_value: Mapped[Decimal | None] = mapped_column(_MONEY)
    unit: Mapped[str] = mapped_column(String(32))
    scale: Mapped[str] = mapped_column(String(32))
    period_end: Mapped[date] = mapped_column(Date)
    reporting_entity_id: Mapped[str] = mapped_column(ForeignKey("reporting_entities.id"))
    reporting_scope_id: Mapped[str] = mapped_column(ForeignKey("reporting_scopes.id"))
    methodology: Mapped[str] = mapped_column(String(128))
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"))
    evidence_locator: Mapped[str] = mapped_column(Text)
    bounded_excerpt: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4, asdecimal=True))
    conflicts_and_uncertainties: Mapped[list[str]] = mapped_column(JSON)
    model_and_prompt_version: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")

    @validates("proposed_normalized_value")
    def reject_float_value(self, _: str, value: object) -> Decimal | None:
        """Reject binary floats at the unpublished authoritative boundary."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str | int) and not isinstance(value, bool):
            return Decimal(value)
        if isinstance(value, float):
            msg = "quarantine numeric candidates cannot be binary floats"
            raise TypeError(msg)
        msg = "quarantine numeric candidates must be Decimal, integer, or decimal text"
        raise TypeError(msg)


class HumanReviewDecision(Base):
    """Audited candidate approval or rejection."""

    __tablename__ = "human_review_decisions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("quarantine_candidates.id"))
    decision: Mapped[str] = mapped_column(String(16))
    reviewer: Mapped[str] = mapped_column(String(128))
    rationale: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    thread_id: Mapped[str] = mapped_column(String(128))
    resulting_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("metric_observations.id")
    )


def create_database_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for PostgreSQL or deterministic SQLite.

    Args:
        database_url: SQLAlchemy database URL.

    Returns:
        Configured engine.
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def default_database_url(data_dir: Path | None = None) -> str:
    """Return the local deterministic database URL.

    Args:
        data_dir: Optional application data directory.

    Returns:
        SQLite URL used only for local/demo/test operation.
    """
    root = data_dir or Path.cwd() / ".msi"
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(root / 'msi.db').as_posix()}"


def initialize_schema(engine: Engine) -> None:
    """Upgrade the schema through the reviewable Alembic history.

    Args:
        engine: Target database engine.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    migration_config = Config()
    migration_config.set_main_option(
        "script_location",
        str(_migration_script_location()),
    )
    migration_config.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.upgrade(migration_config, "head")


def _migration_script_location() -> Path:
    """Resolve migrations from a checkout or the wheel's shared-data payload."""
    candidates = (
        Path(__file__).resolve().parents[2] / "alembic",
        Path(sys.prefix) / "share" / "public-mortgage-servicing-intelligence" / "alembic",
    )
    for candidate in candidates:
        if (candidate / "env.py").is_file() and (candidate / "versions").is_dir():
            return candidate
    msg = "Alembic migration scripts were not found in the checkout or installed wheel"
    raise FileNotFoundError(msg)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit or roll back a short application transaction.

    Args:
        engine: Database engine.

    Yields:
        Transactional SQLAlchemy session.
    """
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
