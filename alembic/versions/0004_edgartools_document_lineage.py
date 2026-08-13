"""Add filing-document lineage for edgartools-retained evidence.

Revision ID: 0004_edgartools_document_lineage
Revises: 0003_phase3_derived_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_edgartools_document_lineage"
down_revision: str | None = "0003_phase3_derived_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable columns without rewriting legacy filing documents."""
    with op.batch_alter_table("filing_documents") as batch_op:
        batch_op.add_column(sa.Column("source_evidence_id", sa.String(96), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("is_primary", sa.Boolean(), nullable=True))
        batch_op.create_foreign_key(
            "fk_filing_documents_source_evidence_id",
            "source_evidence",
            ["source_evidence_id"],
            ["id"],
        )


def downgrade() -> None:
    """Remove only the additive edgartools document-lineage columns."""
    with op.batch_alter_table("filing_documents") as batch_op:
        batch_op.drop_constraint(
            "fk_filing_documents_source_evidence_id",
            type_="foreignkey",
        )
        batch_op.drop_column("is_primary")
        batch_op.drop_column("description")
        batch_op.drop_column("source_evidence_id")
