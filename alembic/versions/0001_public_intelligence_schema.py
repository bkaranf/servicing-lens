"""Create the public servicing intelligence schema with explicit operations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_public_intelligence_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create each application table, constraint, and index explicitly."""
    op.create_table(
        "companies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("universe_version", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("ticker"),
    )
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("run_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("requested_company_id", sa.String(64)),
        sa.Column("requested_periods", sa.JSON(), nullable=False),
        sa.Column("code_version", sa.String(64), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("terminal_outcomes", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_key"),
    )
    op.create_table(
        "reporting_entities",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_table(
        "securities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("security_type", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_table(
        "reporting_scopes",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("portfolio_population", sa.String(128), nullable=False),
        sa.Column("methodology", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("scheme", sa.String(32), nullable=False),
        sa.Column("value", sa.String(128), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "entity_relationships",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("parent_entity_id", sa.String(96), nullable=False),
        sa.Column("child_entity_id", sa.String(96), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("known_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_to", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["parent_entity_id"], ["reporting_entities.id"]),
        sa.ForeignKeyConstraint(["child_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "fiscal_calendar_regimes",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("fiscal_year_end_month", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_end_day", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "accounting_policy_regimes",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("policy_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "filings",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("form_type", sa.String(32), nullable=False),
        sa.Column("accession", sa.String(40), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("amendment_of_id", sa.String(96)),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
        sa.ForeignKeyConstraint(["amendment_of_id"], ["filings.id"]),
        sa.UniqueConstraint("accession"),
    )
    op.create_table(
        "filing_documents",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("filing_id", sa.String(96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
    )
    op.create_table(
        "source_evidence",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("source_class", sa.String(64), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("accession_or_identifier", sa.String(128)),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("representation", sa.String(64), nullable=False),
        sa.Column("capture_method", sa.String(128), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("acquisition_run_id", sa.String(96), nullable=False),
        sa.Column("reporting_entity_candidate", sa.String(96), nullable=False),
        sa.Column("reporting_period_candidate", sa.String(32), nullable=False),
        sa.Column("retention_location", sa.Text(), nullable=False),
        sa.Column("bounded_excerpt", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("etag", sa.String(255)),
        sa.Column("last_modified", sa.String(255)),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_source_evidence_sha256_length",
        ),
        sa.CheckConstraint("byte_length > 0", name="ck_source_evidence_positive_length"),
        sa.UniqueConstraint(
            "content_sha256",
            "byte_length",
            name="uq_source_evidence_content_identity",
        ),
    )
    op.create_table(
        "raw_xbrl_facts",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("evidence_id", sa.String(96), nullable=False),
        sa.Column("concept", sa.String(255), nullable=False),
        sa.Column("context_ref", sa.String(255), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("unit_ref", sa.String(128)),
        sa.ForeignKeyConstraint(["evidence_id"], ["source_evidence.id"]),
    )
    op.create_table(
        "raw_regulatory_facts",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("evidence_id", sa.String(96), nullable=False),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("schedule", sa.String(64), nullable=False),
        sa.Column("item_code", sa.String(64), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["source_evidence.id"]),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "metric_definition_versions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("metric_id", sa.String(96), nullable=False),
        sa.Column("semantic_version", sa.String(32), nullable=False),
        sa.Column("business_meaning", sa.Text(), nullable=False),
        sa.Column("grain", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("permitted_scopes", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.ForeignKeyConstraint(["metric_id"], ["metric_definitions.id"]),
    )
    op.create_table(
        "metric_aliases",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("metric_version_id", sa.String(128), nullable=False),
        sa.Column("reporting_entity_id", sa.String(96)),
        sa.Column("source_label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["metric_version_id"], ["metric_definition_versions.id"]),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
    )
    op.create_table(
        "eligible_source_assessments",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=False),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("metric_version_id", sa.String(128), nullable=False),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("reporting_scope_id", sa.String(96), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("assessment_status", sa.String(32), nullable=False),
        sa.Column("eligible_source_inventory", sa.JSON(), nullable=False),
        sa.Column("checked_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("checked_locators", sa.JSON(), nullable=False),
        sa.Column("assessment_version", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assessment_status IN ('DISCLOSURE_FOUND', 'CHECKED_COMPLETE', 'SOURCE_NOT_CHECKED')",
            name="ck_eligible_source_assessment_status",
        ),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["metric_version_id"], ["metric_definition_versions.id"]),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
        sa.ForeignKeyConstraint(["reporting_scope_id"], ["reporting_scopes.id"]),
        sa.UniqueConstraint(
            "company_id",
            "metric_version_id",
            "reporting_scope_id",
            "period_end",
            "assessment_version",
            name="uq_eligible_source_assessment_cell",
        ),
    )
    op.create_index(
        "ix_eligible_source_assessment_status_period",
        "eligible_source_assessments",
        ["assessment_status", "period_end"],
    )
    op.create_table(
        "metric_observations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("metric_version_id", sa.String(128), nullable=False),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("reporting_scope_id", sa.String(96), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric(38, 10, asdecimal=True)),
        sa.Column("currency", sa.String(3)),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("scale", sa.String(32), nullable=False),
        sa.Column("reported_decimals", sa.Integer()),
        sa.Column("reported_precision", sa.String(128), nullable=False),
        sa.Column("observation_state", sa.String(32), nullable=False),
        sa.Column("methodology", sa.String(128), nullable=False),
        sa.Column("evidence_locator", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(64), nullable=False),
        sa.Column("parser_metadata", sa.JSON(), nullable=False),
        sa.Column("validation_summary", sa.Text(), nullable=False),
        sa.Column("publication_state", sa.String(32), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("semantic_key_digest", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("knowledge_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_to", sa.DateTime(timezone=True)),
        sa.Column("supersedes_observation_id", sa.String(128)),
        sa.Column("quality_state", sa.String(32), nullable=False),
        sa.Column("reported_label", sa.Text(), nullable=False),
        sa.Column("reported_value", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(observation_state = 'NOT_DISCLOSED' AND value IS NULL) OR "
            "(observation_state <> 'NOT_DISCLOSED' AND value IS NOT NULL)",
            name="ck_observation_value_state",
        ),
        sa.ForeignKeyConstraint(["metric_version_id"], ["metric_definition_versions.id"]),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
        sa.ForeignKeyConstraint(["reporting_scope_id"], ["reporting_scopes.id"]),
        sa.ForeignKeyConstraint(["supersedes_observation_id"], ["metric_observations.id"]),
        sa.UniqueConstraint(
            "metric_version_id",
            "reporting_entity_id",
            "reporting_scope_id",
            "period_end",
            "methodology",
            "knowledge_from",
            name="uq_observation_semantic_knowledge_key",
        ),
    )
    op.create_index(
        "ix_metric_observations_semantic_digest",
        "metric_observations",
        ["semantic_key_digest"],
    )
    op.create_index(
        "ix_metric_observations_period",
        "metric_observations",
        ["period_end", "metric_version_id"],
    )
    op.create_table(
        "observation_evidence",
        sa.Column("observation_id", sa.String(128), primary_key=True),
        sa.Column("evidence_id", sa.String(96), primary_key=True),
        sa.Column("evidence_role", sa.String(32), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("raw_label", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("disclosed_unit", sa.String(32), nullable=False),
        sa.Column("disclosed_scale", sa.String(32), nullable=False),
        sa.Column("extraction_method", sa.String(64), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["observation_id"], ["metric_observations.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["source_evidence.id"]),
    )
    op.create_table(
        "observation_revisions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("prior_observation_id", sa.String(128)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["observation_id"], ["metric_observations.id"]),
        sa.ForeignKeyConstraint(["prior_observation_id"], ["metric_observations.id"]),
    )
    op.create_table(
        "comparability_assessments",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("left_observation_id", sa.String(128), nullable=False),
        sa.Column("right_observation_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["left_observation_id"], ["metric_observations.id"]),
        sa.ForeignKeyConstraint(["right_observation_id"], ["metric_observations.id"]),
    )
    op.create_table(
        "earnings_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.Integer(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_id", sa.String(96), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["source_evidence.id"]),
    )
    op.create_table(
        "ingestion_errors",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
    )
    op.create_table(
        "quarantine_candidates",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=False),
        sa.Column("proposed_metric_id", sa.String(96), nullable=False),
        sa.Column("raw_source_label", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("proposed_normalized_value", sa.Numeric(38, 10, asdecimal=True)),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("scale", sa.String(32), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("reporting_entity_id", sa.String(96), nullable=False),
        sa.Column("reporting_scope_id", sa.String(96), nullable=False),
        sa.Column("methodology", sa.String(128), nullable=False),
        sa.Column("evidence_id", sa.String(96), nullable=False),
        sa.Column("evidence_locator", sa.Text(), nullable=False),
        sa.Column("bounded_excerpt", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4, asdecimal=True), nullable=False),
        sa.Column("conflicts_and_uncertainties", sa.JSON(), nullable=False),
        sa.Column("model_and_prompt_version", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["reporting_entity_id"], ["reporting_entities.id"]),
        sa.ForeignKeyConstraint(["reporting_scope_id"], ["reporting_scopes.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["source_evidence.id"]),
    )
    op.create_table(
        "human_review_decisions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("candidate_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("resulting_observation_id", sa.String(128)),
        sa.ForeignKeyConstraint(["candidate_id"], ["quarantine_candidates.id"]),
        sa.ForeignKeyConstraint(["resulting_observation_id"], ["metric_observations.id"]),
    )


def downgrade() -> None:
    """Drop application tables in explicit reverse dependency order."""
    op.drop_table("human_review_decisions")
    op.drop_table("quarantine_candidates")
    op.drop_table("ingestion_errors")
    op.drop_table("earnings_events")
    op.drop_table("comparability_assessments")
    op.drop_table("observation_revisions")
    op.drop_table("observation_evidence")
    op.drop_index("ix_metric_observations_period", table_name="metric_observations")
    op.drop_index("ix_metric_observations_semantic_digest", table_name="metric_observations")
    op.drop_table("metric_observations")
    op.drop_index(
        "ix_eligible_source_assessment_status_period",
        table_name="eligible_source_assessments",
    )
    op.drop_table("eligible_source_assessments")
    op.drop_table("metric_aliases")
    op.drop_table("metric_definition_versions")
    op.drop_table("raw_regulatory_facts")
    op.drop_table("raw_xbrl_facts")
    op.drop_table("source_evidence")
    op.drop_table("filing_documents")
    op.drop_table("filings")
    op.drop_table("accounting_policy_regimes")
    op.drop_table("fiscal_calendar_regimes")
    op.drop_table("entity_relationships")
    op.drop_table("entity_identifiers")
    op.drop_table("reporting_scopes")
    op.drop_table("corporate_actions")
    op.drop_table("securities")
    op.drop_table("reporting_entities")
    op.drop_table("pipeline_runs")
    op.drop_table("metric_definitions")
    op.drop_table("companies")
