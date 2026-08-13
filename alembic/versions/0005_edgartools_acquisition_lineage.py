"""Add exact edgartools acquisition lineage.

Revision ID: 0005_edgartools_acquisition_lineage
Revises: 0004_edgartools_document_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_edgartools_acquisition_lineage"
down_revision: str | None = "0004_edgartools_document_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable columns so legacy evidence remains readable."""
    with op.batch_alter_table("filings") as batch_op:
        batch_op.add_column(
            sa.Column("acceptance_timestamp", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("source_evidence") as batch_op:
        batch_op.add_column(sa.Column("source_tool_version", sa.String(32), nullable=True))
    with op.batch_alter_table("raw_xbrl_facts") as batch_op:
        batch_op.add_column(sa.Column("source_sign", sa.String(8), nullable=True))
        batch_op.add_column(sa.Column("source_precision", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("presentation_sign", sa.String(16), nullable=True))


def downgrade() -> None:
    """Remove only the additive acquisition-lineage columns."""
    with op.batch_alter_table("source_evidence") as batch_op:
        batch_op.drop_column("source_tool_version")
    with op.batch_alter_table("raw_xbrl_facts") as batch_op:
        batch_op.drop_column("presentation_sign")
        batch_op.drop_column("source_precision")
        batch_op.drop_column("source_sign")
    with op.batch_alter_table("filings") as batch_op:
        batch_op.drop_column("acceptance_timestamp")
