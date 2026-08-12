"""Phase 3 reconciliation across retained SEC and Phase 2 regulatory facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from mortgage_servicing_dashboard.metric_engine import (
    Completeness,
    DecisionDisposition,
    DecisionReason,
    MetricInput,
    MetricMethodology,
    MetricUnit,
    PeriodType,
    PublicationStatus,
    ValueState,
    load_metric_catalog,
    reconcile_cross_source,
)
from mortgage_servicing_dashboard.regulatory import (
    FrY9cBulkAdapter,
    RegulatorySourceFamily,
    aggregate_regulatory_metric,
    load_regulatory_config,
)
from mortgage_servicing_dashboard.sources import (
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
)

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "config"


def test_exact_tfc_sec_and_y9c_fixture_mismatch_quarantines_without_preference() -> None:
    """Reconcile actual retained SEC bytes to the native-scope Phase 2 fixture."""
    phase3 = cast(
        "dict[str, Any]",
        yaml.safe_load((_CONFIG / "phase3" / "tfc_sources.yaml").read_text(encoding="utf-8")),
    )
    source_payload = cast(
        "dict[str, Any]",
        phase3["sources"]["tfc_2026q2_qps"],
    )
    source = RecordedSourceDefinition.from_mapping(
        key="tfc_2026q2_qps",
        payload=source_payload,
        config_root=_CONFIG,
    )
    company = next(
        item
        for item in cast(
            "list[dict[str, Any]]",
            yaml.safe_load((_CONFIG / "universe.yaml").read_text(encoding="utf-8"))["companies"],
        )
        if item["id"] == "tfc"
    )
    acquired = RecordedEvidenceAcquirer().acquire(source)
    sec_candidate = next(
        candidate
        for candidate in StageARecordedDocumentParser().parse(
            source=source,
            content=acquired.content,
            company=company,
            quarters=cast("list[dict[str, Any]]", phase3["quarters"]),
        )
        if candidate.metric_id == "servicing_for_others_upb"
    )

    regulatory_config = load_regulatory_config(
        _CONFIG / "regulatory" / "regulatory_mappings.v1.yaml"
    )
    y9c_facts = FrY9cBulkAdapter(regulatory_config).parse(
        (_ROOT / "tests" / "fixtures" / "phase2" / "regulatory" / "fr_y9c_2026q2.txt").read_bytes(),
        rssd_id="1074156",
        report_date=date(2026, 6, 30),
    )
    y9c_total = aggregate_regulatory_metric(
        y9c_facts,
        metric_id="servicing_for_others_upb",
        required_components=(
            "one_to_four_family_with_recourse",
            "one_to_four_family_without_recourse",
        ),
    )
    sec_input = MetricInput(
        observation_id=sec_candidate.candidate_id,
        issuer_id="tfc",
        metric_id=sec_candidate.metric_id,
        metric_version="1.0.0",
        value=sec_candidate.normalized_value,
        unit=MetricUnit.USD,
        period_type=PeriodType.INSTANT,
        period_start=None,
        period_end=sec_candidate.period_end,
        reporting_entity_id="tfc_registrant",
        reporting_scope_id="tfc_consolidated_residential_mortgage_servicing",
        methodology=MetricMethodology.SEC_FILING_EXHIBIT,
        publication_status=PublicationStatus.PUBLISHED,
        value_state=ValueState.REPORTED_ACTUAL,
        completeness=Completeness.COMPLETE,
    )
    regulatory_input = MetricInput(
        observation_id="fixture:y9c:tfc:2026q2:servicing_for_others_upb",
        issuer_id="tfc",
        metric_id=y9c_total.metric_id,
        metric_version="1.0.0",
        value=y9c_total.value,
        unit=MetricUnit.USD,
        period_type=PeriodType.INSTANT,
        period_start=None,
        period_end=y9c_total.report_date,
        reporting_entity_id="tfc_bhc_regulatory_reporter",
        reporting_scope_id="tfc_bhc_regulatory",
        methodology=MetricMethodology.FR_Y9C,
        publication_status=PublicationStatus.PUBLISHED,
        value_state=ValueState.REPORTED_ACTUAL,
        completeness=Completeness.COMPLETE,
    )
    catalog = load_metric_catalog(
        _CONFIG / "metrics" / "catalog.yaml",
        extension_paths=(_CONFIG / "metrics" / "phase3_deepening.v1.yaml",),
    )

    decision = reconcile_cross_source(
        sec_input,
        regulatory_input,
        rule_id="tfc_sec_vs_y9c_servicing_for_others",
        catalog=catalog,
    )

    assert sec_input.value == Decimal(240764000000)
    assert regulatory_input.value == Decimal(1000000000)
    assert decision.disposition is DecisionDisposition.QUARANTINED
    assert decision.reasons == (DecisionReason.RECONCILIATION_VALUE_MISMATCH,)
    assert decision.absolute_difference == Decimal(239764000000)
    assert decision.quarantine_required is True
    assert not hasattr(decision, "preferred_observation")
    assert {fact.source_family for fact in y9c_facts} == {RegulatorySourceFamily.FR_Y9C}
