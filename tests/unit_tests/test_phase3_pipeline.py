"""Production Phase 3 retained-evidence pipeline contracts."""

from __future__ import annotations

import hashlib
import socket
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mortgage_servicing_dashboard.metric_engine import MetricMethodology, derive_metric
from mortgage_servicing_dashboard.phase3 import (
    Phase3Error,
    _candidate_identity,
    _replay_normalization,
    _validate_reported_candidate,
    load_phase3_dataset,
)

_CONFIG = Path(__file__).parents[2] / "config"


def test_phase3_pipeline_has_complete_exact_parity_and_lineage() -> None:
    dataset = load_phase3_dataset(_CONFIG)

    assert len(dataset.evidence) == 21
    assert len(dataset.assessments) == 424
    assert len(dataset.reported_candidates) == 120
    assert len(dataset.support_candidates) == 40
    assert len(dataset.derived_candidates) == 43
    assert dataset.blocked_derivations == ()
    assert len(dataset.missing_cells) == 222

    evidence_ids = {item.evidence_id for item in dataset.evidence}
    assert len(evidence_ids) == len(dataset.evidence)
    for evidence in dataset.evidence:
        content = evidence.actual_fixture_path.read_bytes()
        assert len(content) == evidence.byte_length
        assert hashlib.sha256(content).hexdigest() == evidence.sha256
        assert evidence.representation == "ORIGINAL_HTTP_RESPONSE"
        assert evidence.capture_method == "sec_http_get"
        assert evidence.retention_location == f"content-sha256://{evidence.sha256}"
        assert evidence.actual_fixture_path.name == f"{evidence.sha256}.bin"

    measured = (*dataset.reported_candidates, *dataset.support_candidates)
    assert all(isinstance(item.candidate.normalized_value, Decimal) for item in measured)
    assert all(item.candidate.evidence_id in evidence_ids for item in measured)
    assert all(item.candidate.evidence_locator for item in measured)
    assert all(not isinstance(item.candidate.normalized_value, float) for item in measured)
    for item in measured:
        definition = dataset.catalog.definition(
            item.candidate.metric_id, item.candidate.metric_version
        )
        assert definition is not None
        assert MetricMethodology(item.candidate.methodology) in definition.methodologies
        assert (
            _replay_normalization(item.candidate.raw_value, item.normalization_trace)
            == item.candidate.normalized_value
        )

    assessment_keys = {
        (item.company_id, item.metric_id, item.period_end) for item in dataset.assessments
    }
    assert len(assessment_keys) == len(dataset.assessments)
    selected_candidate_keys = {
        (item.candidate.company_id, item.candidate.metric_id, item.candidate.period_end)
        for item in dataset.reported_candidates
    } | {
        (item.candidate.company_id, item.candidate.metric_id, item.candidate.period_end)
        for item in dataset.support_candidates
        if item.candidate.metric_id
        in {
            "fha_servicing_upb",
            "va_servicing_upb",
            "usda_servicing_upb",
            "closed_end_second_lien_servicing_upb",
            "other_servicing_upb",
            "owned_msr_msl_upb",
            "msr_additions_related_upb",
            "delinquency_30_to_89_upb",
            "delinquency_90_plus_upb",
            "foreclosure_upb",
        }
    }
    expected_reported = {
        (item.company_id, item.metric_id, item.period_end)
        for item in dataset.assessments
        if item.result_state == "PUBLISHED"
    }
    assert selected_candidate_keys == expected_reported

    for candidate in dataset.derived_candidates:
        decision = derive_metric(candidate.request, dataset.catalog)
        assert decision.result is not None
        assert decision.result.value == candidate.normalized_value
        assert decision.result.trace == candidate.trace
        assert tuple(item.input_observation_id for item in decision.result.lineage) == (
            candidate.input_candidate_ids
        )


def test_phase3_pipeline_exact_values_and_support_unblockers() -> None:
    dataset = load_phase3_dataset(_CONFIG)
    reported = {
        (item.candidate.company_id, item.candidate.metric_id, item.candidate.period_end): item
        for item in (*dataset.reported_candidates, *dataset.support_candidates)
    }
    derived = {
        (item.company_id, item.metric_id, item.period_end): item
        for item in dataset.derived_candidates
    }

    tfc = reported[("tfc", "total_servicing_upb", date(2026, 6, 30))]
    assert tfc.candidate.raw_value == "298,658"
    assert tfc.candidate.normalized_value == Decimal(298658000000)

    pfsi_expense = reported[("pfsi", "servicing_operating_expense", date(2025, 9, 30))]
    assert pfsi_expense.candidate.raw_value == "(84.5)"
    assert pfsi_expense.candidate.normalized_value == Decimal("84500000.0")

    fha = reported[("pfsi", "fha_servicing_upb", date(2026, 6, 30))]
    assert fha.candidate.raw_value == "170,495,110"
    assert fha.candidate.normalized_value == Decimal(170495110000)
    assert {item.name for item in fha.dimensions} == {
        "portfolio_population",
        "servicing_component",
    }

    cost = derived[("pfsi", "cost_to_service_per_loan", date(2025, 9, 30))]
    assert cost.normalized_value == Decimal("123.03")
    assert cost.trace.unquantized_value != cost.normalized_value
    assert len(cost.input_candidate_ids) == 3

    multiple = derived[("pfsi", "msr_fair_value_bps_of_related_upb", date(2026, 6, 30))]
    assert multiple.normalized_value == Decimal("216.905498")

    revenue = {
        period: reported[("pfsi", "servicing_revenue", period)].candidate.normalized_value
        for period in (
            date(2025, 9, 30),
            date(2025, 12, 31),
            date(2026, 3, 31),
            date(2026, 6, 30),
        )
    }
    assert tuple(revenue.values()) == tuple(
        Decimal(value) for value in ("259000000", "154000000", "125000000", "137000000")
    )
    assert not set(revenue.values()) & {
        Decimal(12000000),
        Decimal(13000000),
        Decimal(23000000),
    }

    q2_30 = reported[("pfsi", "delinquency_30_to_89_upb", date(2026, 6, 30))]
    q2_90 = reported[("pfsi", "delinquency_90_plus_upb", date(2026, 6, 30))]
    assert q2_30.candidate.raw_value == "16,753,926"
    assert q2_90.candidate.raw_value == "13,339,471"
    assert "Owned servicing" in q2_30.candidate.evidence_locator
    foreclosure_rates = tuple(
        derived[("pfsi", "foreclosure_upb_rate", period)].normalized_value
        for period in (
            date(2025, 9, 30),
            date(2025, 12, 31),
            date(2026, 3, 31),
            date(2026, 6, 30),
        )
    )
    assert foreclosure_rates == tuple(
        Decimal(value)
        for value in (
            "0.0030597373",
            "0.0030615487",
            "0.0035216019",
            "0.0035557211",
        )
    )

    interim = reported[("pfsi", "interim_servicing_upb", date(2026, 6, 30))]
    assert interim.candidate.raw_value == "—"
    assert interim.candidate.normalized_value == 0
    assert interim.candidate.reporting_scope_id == "pfsi_interim_servicing_portfolio"

    assert not any(
        item.candidate.metric_id == "msr_beginning_balance" for item in reported.values()
    )


def test_phase3_not_disclosed_cells_have_no_numeric_candidate() -> None:
    dataset = load_phase3_dataset(_CONFIG)
    numeric_keys = {
        (item.candidate.company_id, item.candidate.metric_id, item.candidate.period_end)
        for item in (*dataset.reported_candidates, *dataset.support_candidates)
    } | {(item.company_id, item.metric_id, item.period_end) for item in dataset.derived_candidates}
    missing_keys = {
        (item.company_id, item.metric_id, item.period_end) for item in dataset.missing_cells
    }
    assert not numeric_keys & missing_keys
    assert all(item.reason_code for item in dataset.missing_cells)
    assert all(
        item.reporting_entity_id and item.reporting_scope_id for item in dataset.missing_cells
    )
    assert all(
        len(item.locators) == len(item.source_keys) and all(item.locators)
        for item in dataset.missing_cells
    )


def test_phase3_validation_and_identity_fail_closed() -> None:
    dataset = load_phase3_dataset(_CONFIG)
    wrapper = next(
        item
        for item in dataset.reported_candidates
        if item.candidate.metric_id == "servicing_revenue"
    )
    bad = replace(wrapper.candidate, methodology="DELINQUENCY_UPB_REPORTED")
    with pytest.raises(Phase3Error, match="methodology"):
        _validate_reported_candidate(dataset.catalog, bad, wrapper.dimensions)

    original_id = wrapper.candidate.candidate_id
    assert original_id == _candidate_identity(
        wrapper.candidate,
        wrapper.candidate.methodology,
        wrapper.dimensions,
        wrapper.source_methodology,
    )
    changed = replace(wrapper.candidate, raw_label=wrapper.candidate.raw_label + " changed")
    assert _candidate_identity(changed, changed.methodology, wrapper.dimensions) != original_id


def test_phase3_loader_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        message = "socket access is prohibited"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "create_connection", blocked)
    assert len(load_phase3_dataset(_CONFIG).evidence) == 21
