"""Schema contracts for exact derived-observation input lineage."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric, inspect

from mortgage_servicing_dashboard.database import (
    DerivedObservationInput,
    create_database_engine,
    initialize_schema,
)


def test_derived_input_rejects_binary_float() -> None:
    with pytest.raises(TypeError, match="cannot be binary floats"):
        DerivedObservationInput(
            derived_observation_id="derived",
            input_observation_id="input",
            input_role="numerator",
            input_ordinal=0,
            formula_version="1.0.0",
            input_value=1.25,
        )


def test_derived_input_accepts_exact_decimal() -> None:
    lineage = DerivedObservationInput(
        derived_observation_id="derived",
        input_observation_id="input",
        input_role="numerator",
        input_ordinal=0,
        formula_version="1.0.0",
        input_value=Decimal("1.25"),
    )
    assert lineage.input_value == Decimal("1.25")


def test_phase3_lineage_migration_has_exact_schema() -> None:
    engine = create_database_engine("sqlite://")
    initialize_schema(engine)
    inspector = inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("derived_observation_inputs")}
    assert set(columns) == {
        "derived_observation_id",
        "input_observation_id",
        "input_role",
        "input_ordinal",
        "formula_version",
        "input_value",
    }
    input_type = columns["input_value"]["type"]
    assert isinstance(input_type, Numeric)
    assert input_type.precision == 38
    assert input_type.scale == 10
    foreign_keys = {
        tuple(item["constrained_columns"]): tuple(item["referred_columns"])
        for item in inspector.get_foreign_keys("derived_observation_inputs")
    }
    assert foreign_keys == {
        ("derived_observation_id",): ("id",),
        ("input_observation_id",): ("id",),
    }
    engine.dispose()
