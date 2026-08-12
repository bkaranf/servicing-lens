"""Add structured acquisition, regulatory scope, and calendar semantics.

Revision ID: 0002_phase2_structured_acquisition
Revises: 0001_public_intelligence_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_phase2_structured_acquisition"
down_revision: str | None = "0001_public_intelligence_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add exact context needed by the Phase 2 deterministic adapters."""
    with op.batch_alter_table("raw_xbrl_facts") as batch:
        batch.add_column(sa.Column("filing_id", sa.String(96)))
        batch.add_column(
            sa.Column("taxonomy", sa.String(128), nullable=False, server_default="unknown")
        )
        batch.add_column(
            sa.Column("entity_identifier", sa.String(128), nullable=False, server_default="unknown")
        )
        batch.add_column(sa.Column("decimals", sa.String(32)))
        batch.add_column(sa.Column("scale", sa.Numeric(38, 10, asdecimal=True)))
        batch.add_column(
            sa.Column("period_type", sa.String(16), nullable=False, server_default="instant")
        )
        batch.add_column(sa.Column("period_start", sa.Date()))
        batch.add_column(sa.Column("period_end", sa.Date()))
        batch.add_column(sa.Column("instant", sa.Date()))
        batch.add_column(sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(
            sa.Column("methodology", sa.String(64), nullable=False, server_default="SEC_XBRL")
        )
        batch.create_foreign_key(
            "fk_raw_xbrl_facts_filing_id_filings",
            "filings",
            ["filing_id"],
            ["id"],
        )
    with op.batch_alter_table("raw_xbrl_facts") as batch:
        batch.alter_column("taxonomy", existing_type=sa.String(128), server_default=None)
        batch.alter_column("entity_identifier", existing_type=sa.String(128), server_default=None)
        batch.alter_column("period_type", existing_type=sa.String(16), server_default=None)
        batch.alter_column("dimensions", existing_type=sa.JSON(), server_default=None)
        batch.alter_column("methodology", existing_type=sa.String(64), server_default=None)

    with op.batch_alter_table("raw_regulatory_facts") as batch:
        batch.add_column(sa.Column("reporting_scope_id", sa.String(96)))
        batch.add_column(
            sa.Column("source_family", sa.String(32), nullable=False, server_default="UNKNOWN")
        )
        batch.add_column(
            sa.Column("rssd_id", sa.String(16), nullable=False, server_default="UNKNOWN")
        )
        batch.add_column(
            sa.Column("report_date", sa.Date(), nullable=False, server_default="1900-01-01")
        )
        batch.add_column(
            sa.Column("period_type", sa.String(16), nullable=False, server_default="instant")
        )
        batch.add_column(sa.Column("unit", sa.String(32), nullable=False, server_default="USD"))
        batch.add_column(sa.Column("scale", sa.String(32), nullable=False, server_default="ones"))
        batch.add_column(
            sa.Column(
                "revision_identifier", sa.String(128), nullable=False, server_default="initial"
            )
        )
        batch.create_foreign_key(
            "fk_raw_regulatory_facts_reporting_scope_id_reporting_scopes",
            "reporting_scopes",
            ["reporting_scope_id"],
            ["id"],
        )
    op.execute(
        sa.text(
            "UPDATE raw_regulatory_facts SET reporting_scope_id = "
            "(SELECT reporting_scopes.id FROM reporting_scopes "
            "WHERE reporting_scopes.reporting_entity_id = "
            "raw_regulatory_facts.reporting_entity_id LIMIT 1)"
        )
    )
    with op.batch_alter_table("raw_regulatory_facts") as batch:
        batch.alter_column(
            "reporting_scope_id",
            existing_type=sa.String(96),
            nullable=False,
        )
        batch.alter_column("source_family", existing_type=sa.String(32), server_default=None)
        batch.alter_column("rssd_id", existing_type=sa.String(16), server_default=None)
        batch.alter_column("report_date", existing_type=sa.Date(), server_default=None)
        batch.alter_column("period_type", existing_type=sa.String(16), server_default=None)
        batch.alter_column("unit", existing_type=sa.String(32), server_default=None)
        batch.alter_column("scale", existing_type=sa.String(32), server_default=None)
        batch.alter_column("revision_identifier", existing_type=sa.String(128), server_default=None)

    with op.batch_alter_table("earnings_events") as batch:
        batch.add_column(sa.Column("period_end", sa.Date()))
        batch.add_column(
            sa.Column("event_kind", sa.String(32), nullable=False, server_default="FILED_ACTUAL")
        )
        batch.add_column(
            sa.Column("source_kind", sa.String(32), nullable=False, server_default="SEC_8_K_EX_99")
        )
        batch.add_column(sa.Column("filing_accession", sa.String(40)))
        batch.add_column(sa.Column("window_start", sa.Date()))
        batch.add_column(sa.Column("window_end", sa.Date()))
        batch.add_column(
            sa.Column("is_inferred", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("inference_basis", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    """Remove Phase 2 fields without affecting earlier retained evidence."""
    with op.batch_alter_table("earnings_events") as batch:
        batch.drop_column("inference_basis")
        batch.drop_column("is_inferred")
        batch.drop_column("window_end")
        batch.drop_column("window_start")
        batch.drop_column("filing_accession")
        batch.drop_column("source_kind")
        batch.drop_column("event_kind")
        batch.drop_column("period_end")

    with op.batch_alter_table("raw_regulatory_facts") as batch:
        batch.drop_constraint(
            "fk_raw_regulatory_facts_reporting_scope_id_reporting_scopes",
            type_="foreignkey",
        )
        batch.drop_column("revision_identifier")
        batch.drop_column("scale")
        batch.drop_column("unit")
        batch.drop_column("period_type")
        batch.drop_column("report_date")
        batch.drop_column("rssd_id")
        batch.drop_column("source_family")
        batch.drop_column("reporting_scope_id")

    with op.batch_alter_table("raw_xbrl_facts") as batch:
        batch.drop_constraint("fk_raw_xbrl_facts_filing_id_filings", type_="foreignkey")
        batch.drop_column("methodology")
        batch.drop_column("dimensions")
        batch.drop_column("instant")
        batch.drop_column("period_end")
        batch.drop_column("period_start")
        batch.drop_column("period_type")
        batch.drop_column("scale")
        batch.drop_column("decimals")
        batch.drop_column("entity_identifier")
        batch.drop_column("taxonomy")
        batch.drop_column("filing_id")
