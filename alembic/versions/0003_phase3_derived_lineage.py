"""Add exact input-revision lineage for derived Phase 3 metrics.

Revision ID: 0003_phase3_derived_lineage
Revises: 0002_phase2_structured_acquisition
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_phase3_derived_lineage"
down_revision: str | None = "0002_phase2_structured_acquisition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _backfill_observation_regimes() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT * FROM metric_observations")).mappings().all()
    for row in rows:
        entity_id = str(row["reporting_entity_id"])
        period_end = row["period_end"]
        fiscal_id = connection.execute(
            sa.text(
                "SELECT id FROM fiscal_calendar_regimes "
                "WHERE reporting_entity_id=:entity_id AND effective_from<=:period_end "
                "AND (effective_to IS NULL OR effective_to>:period_end) ORDER BY id LIMIT 1"
            ),
            {"entity_id": entity_id, "period_end": period_end},
        ).scalar_one_or_none()
        if fiscal_id is None:
            fiscal_id = f"{entity_id}:calendar"
            connection.execute(
                sa.text(
                    "INSERT INTO fiscal_calendar_regimes "
                    "(id, reporting_entity_id, fiscal_year_end_month, fiscal_year_end_day, "
                    "effective_from, effective_to) VALUES "
                    "(:id,:entity_id,12,31,:effective_from,NULL)"
                ),
                {"id": fiscal_id, "entity_id": entity_id, "effective_from": "1900-01-01"},
            )
        policy_id = connection.execute(
            sa.text(
                "SELECT id FROM accounting_policy_regimes "
                "WHERE reporting_entity_id=:entity_id AND effective_from<=:period_end "
                "AND (effective_to IS NULL OR effective_to>:period_end) ORDER BY id LIMIT 1"
            ),
            {"entity_id": entity_id, "period_end": period_end},
        ).scalar_one_or_none()
        if policy_id is None:
            policy_id = f"{entity_id}:us-gaap"
            connection.execute(
                sa.text(
                    "INSERT INTO accounting_policy_regimes "
                    "(id, reporting_entity_id, policy_name, description, effective_from, "
                    "effective_to) VALUES (:id,:entity_id,'US_GAAP_ISSUER_REPORTED',"
                    ":description,:effective_from,NULL)"
                ),
                {
                    "id": policy_id,
                    "entity_id": entity_id,
                    "description": "Governed legacy issuer-reported accounting basis.",
                    "effective_from": "1900-01-01",
                },
            )
        digest_payload = {
            "metric_version_id": row["metric_version_id"],
            "reporting_entity_id": entity_id,
            "reporting_scope_id": row["reporting_scope_id"],
            "period_start": _iso(row["period_start"]),
            "period_end": _iso(period_end),
            "period_type": row["period_type"],
            "fiscal_calendar_regime_id": fiscal_id,
            "accounting_policy_regime_id": policy_id,
            "observation_state": row["observation_state"],
            "methodology": row["methodology"],
            "currency": row["currency"],
            "unit": row["unit"],
            "scale": row["scale"],
            "dimensions": {},
        }
        digest = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE metric_observations SET fiscal_calendar_regime_id=:fiscal_id, "
                "accounting_policy_regime_id=:policy_id, semantic_key_digest=:digest "
                "WHERE id=:observation_id"
            ),
            {
                "fiscal_id": fiscal_id,
                "policy_id": policy_id,
                "digest": digest,
                "observation_id": row["id"],
            },
        )


def upgrade() -> None:
    """Create exact, ordered input lineage for derived observations."""
    op.add_column(
        "metric_observations",
        sa.Column(
            "dimensions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "metric_observations",
        sa.Column("fiscal_calendar_regime_id", sa.String(96), nullable=True),
    )
    op.add_column(
        "metric_observations",
        sa.Column("accounting_policy_regime_id", sa.String(96), nullable=True),
    )
    _backfill_observation_regimes()
    with op.batch_alter_table("metric_observations") as batch_op:
        batch_op.alter_column("fiscal_calendar_regime_id", nullable=False)
        batch_op.alter_column("accounting_policy_regime_id", nullable=False)
        batch_op.create_foreign_key(
            "fk_metric_observations_fiscal_calendar_regime_id",
            "fiscal_calendar_regimes",
            ["fiscal_calendar_regime_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_metric_observations_accounting_policy_regime_id",
            "accounting_policy_regimes",
            ["accounting_policy_regime_id"],
            ["id"],
        )
        batch_op.drop_constraint(
            "uq_observation_semantic_knowledge_key",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_observation_semantic_digest_knowledge",
            ["semantic_key_digest", "knowledge_from"],
        )
    op.create_table(
        "derived_observation_inputs",
        sa.Column("derived_observation_id", sa.String(128), nullable=False),
        sa.Column("input_observation_id", sa.String(128), nullable=False),
        sa.Column("input_role", sa.String(64), nullable=False),
        sa.Column("input_ordinal", sa.Integer(), nullable=False),
        sa.Column("formula_version", sa.String(64), nullable=False),
        sa.Column("input_value", sa.Numeric(38, 10, asdecimal=True), nullable=False),
        sa.CheckConstraint(
            "input_ordinal >= 0",
            name="ck_derived_observation_input_ordinal_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["derived_observation_id"],
            ["metric_observations.id"],
            name="fk_derived_observation_inputs_derived_observation_id_metric_observations",
        ),
        sa.ForeignKeyConstraint(
            ["input_observation_id"],
            ["metric_observations.id"],
            name="fk_derived_observation_inputs_input_observation_id_metric_observations",
        ),
        sa.PrimaryKeyConstraint(
            "derived_observation_id",
            "input_observation_id",
            name="pk_derived_observation_inputs",
        ),
        sa.UniqueConstraint(
            "derived_observation_id",
            "input_ordinal",
            name="uq_derived_observation_input_ordinal",
        ),
    )


def downgrade() -> None:
    """Remove derived lineage without altering source observations."""
    op.drop_table("derived_observation_inputs")
    with op.batch_alter_table("metric_observations") as batch_op:
        batch_op.drop_constraint(
            "uq_observation_semantic_digest_knowledge",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_observation_semantic_knowledge_key",
            [
                "metric_version_id",
                "reporting_entity_id",
                "reporting_scope_id",
                "period_end",
                "methodology",
                "knowledge_from",
            ],
        )
        batch_op.drop_constraint(
            "fk_metric_observations_accounting_policy_regime_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_metric_observations_fiscal_calendar_regime_id",
            type_="foreignkey",
        )
    op.drop_column("metric_observations", "accounting_policy_regime_id")
    op.drop_column("metric_observations", "fiscal_calendar_regime_id")
    op.drop_column("metric_observations", "dimensions")
