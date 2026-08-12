"""Persistence contracts for the issuer-scoped WFC Phase 4a layer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.database import (
    AccountingPolicyRegime,
    EntityIdentifier,
    EntityRelationship,
    FiscalCalendarRegime,
    ReportingEntity,
    ReportingScope,
    create_database_engine,
)
from mortgage_servicing_dashboard.repository import seed_phase4_wfc, seed_stage_a


def test_wfc_seed_fails_before_writes_without_explicit_publication_authority(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    (config / "phase4").mkdir(parents=True)
    (config / "universe.yaml").write_text("companies: []\n", encoding="utf-8")
    (config / "phase4" / "wfc_sources.yaml").write_text(
        "status: DISCLOSURE_MAP_RESEARCH_ONLY\n"
        "publication_authorized: false\n"
        "parser_implemented: false\n",
        encoding="utf-8",
    )
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'unauthorized.db').as_posix()}")
    with pytest.raises(ValueError, match="not authorized for publication"):
        seed_phase4_wfc(engine, config_dir=config)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_wfc_universe_identity_and_scopes_are_persisted_without_stage_a_drift(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'wfc-universe.db').as_posix()}")
    first = seed_stage_a(engine)
    second = seed_stage_a(engine)
    assert first["companies"] == 3
    assert second == {
        "companies": 0,
        "metrics": 0,
        "evidence": 0,
        "source_assessments": 0,
        "observations": 0,
    }

    with Session(engine) as session:
        entities = {
            item.id: item
            for item in session.scalars(
                select(ReportingEntity).where(ReportingEntity.company_id == "wfc")
            )
        }
        assert set(entities) == {
            "wfc_sec_registrant",
            "wfc_home_lending_operating_unit",
            "wfc_bhc_regulatory_reporter",
            "wells_fargo_bank_na_regulatory_reporter",
        }
        assert entities["wfc_bhc_regulatory_reporter"].legal_name == "Wells Fargo & Company"
        assert (
            entities["wells_fargo_bank_na_regulatory_reporter"].legal_name
            == "Wells Fargo Bank, National Association"
        )

        scopes = {
            item.id: item
            for item in session.scalars(
                select(ReportingScope)
                .join(
                    ReportingEntity,
                    ReportingScope.reporting_entity_id == ReportingEntity.id,
                )
                .where(ReportingEntity.company_id == "wfc")
            )
        }
        assert scopes["wfc_consolidated_residential_mortgage_servicing"].portfolio_population == (
            "residential_mortgages_serviced_for_others_excluding_subservicing"
        )
        assert scopes["wfc_owned_residential_msr"].portfolio_population == (
            "owned_residential_msr_at_fair_value"
        )
        assert scopes["wfc_home_lending_owned_loan_metrics"].reporting_entity_id == (
            "wfc_home_lending_operating_unit"
        )

        identifiers = {
            (item.reporting_entity_id, item.scheme): item.value
            for item in session.scalars(
                select(EntityIdentifier).where(
                    EntityIdentifier.reporting_entity_id.in_(set(entities))
                )
            )
        }
        assert identifiers[("wfc_sec_registrant", "SEC_CIK")] == "0000072971"
        assert identifiers[("wfc_bhc_regulatory_reporter", "RSSD")] == "1120754"
        assert identifiers[("wells_fargo_bank_na_regulatory_reporter", "RSSD")] == "451965"
        assert identifiers[("wells_fargo_bank_na_regulatory_reporter", "FDIC_CERT")] == "3511"

        relationships = {
            (item.parent_entity_id, item.child_entity_id): item.relationship_type
            for item in session.scalars(
                select(EntityRelationship).where(
                    EntityRelationship.child_entity_id.in_(set(entities))
                )
            )
        }
        assert relationships[("wfc_sec_registrant", "wfc_home_lending_operating_unit")] == (
            "REPORTS_SEGMENT"
        )
        assert relationships[("wfc_sec_registrant", "wfc_bhc_regulatory_reporter")] == (
            "REGULATED_AS"
        )
        assert (
            relationships[
                ("wfc_bhc_regulatory_reporter", "wells_fargo_bank_na_regulatory_reporter")
            ]
            == "OWNS"
        )

        assert (
            len(
                list(
                    session.scalars(
                        select(FiscalCalendarRegime).where(
                            FiscalCalendarRegime.reporting_entity_id.in_(set(entities))
                        )
                    )
                )
            )
            == 4
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(AccountingPolicyRegime).where(
                            AccountingPolicyRegime.reporting_entity_id.in_(set(entities))
                        )
                    )
                )
            )
            == 4
        )
    engine.dispose()


def test_existing_phase3_universe_semantics_are_explicit_and_unchanged() -> None:
    root = Path(__file__).resolve().parents[2]
    universe = yaml.safe_load((root / "config" / "universe.yaml").read_text(encoding="utf-8"))
    companies = {item["id"]: item for item in universe["companies"]}
    assert companies["tfc"]["reporting_scope_definition"] == {
        "name": "Tfc Consolidated Residential Mortgage Servicing",
        "portfolio_population": "residential_servicing_for_others_and_bank_owned",
        "methodology": "Issuer-defined public servicing disclosure scope.",
    }
    assert companies["pfsi"]["reporting_scope_definition"] == {
        "name": "Pfsi Servicing Segment",
        "portfolio_population": "owned_msr_subservicing_and_held_for_sale",
        "methodology": "Issuer-defined public servicing disclosure scope.",
    }
