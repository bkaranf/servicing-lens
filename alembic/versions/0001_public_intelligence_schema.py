"""Create the public servicing intelligence schema."""

from __future__ import annotations

from alembic import op
from mortgage_servicing_dashboard.database import Base

revision = "0001_public_intelligence_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all application-owned tables."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop all application-owned tables in dependency order."""
    Base.metadata.drop_all(bind=op.get_bind())
