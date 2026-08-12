"""Production contracts for the offline WFC Phase 4a parser."""

from __future__ import annotations

import copy
import hashlib
import socket
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import mortgage_servicing_dashboard.phase4_wfc as wfc_module
from mortgage_servicing_dashboard.metric_engine import MetricDimension, derive_metric
from mortgage_servicing_dashboard.phase3 import load_phase3_dataset
from mortgage_servicing_dashboard.phase4_wfc import (
    WfcPhase4Error,
    apply_wfc_normalization_trace,
    load_wfc_phase4_dataset,
)

_CONFIG = Path(__file__).parents[2] / "config"
_PERIODS = (
    date(2025, 9, 30),
    date(2025, 12, 31),
    date(2026, 3, 31),
    date(2026, 6, 30),
)


@pytest.fixture(autouse=True)
def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        message = "WFC Phase 4 parser tests must remain offline"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_wfc_phase4_dataset_has_exact_counts_hashes_and_parity() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)

    assert dataset.dataset_version == "phase4-wfc-1.0.0"
    assert dataset.status == "PUBLICATION_VALIDATED"
    assert dataset.publication_authorized is True
    assert dataset.parser_implemented is True
    assert dataset.parser_name == "wfc_phase4_retained_sec_html"
    assert dataset.parser_version == "1.0.0"
    assert len(dataset.evidence) == 18
    assert len(dataset.assessments) == 212
    assert len(dataset.reported_candidates) == 31
    assert len(dataset.support_candidates) == 4
    assert len(dataset.derived_candidates) == 8
    assert dataset.blocked_derivations == ()
    assert len(dataset.missing_cells) == 173
    assert len(dataset.regulatory_research_expectations) == 2

    evidence_ids = {item.evidence_id for item in dataset.evidence}
    source_keys = {item.source_key for item in dataset.evidence}
    assert len(evidence_ids) == len(source_keys) == 18
    for evidence in dataset.evidence:
        content = evidence.actual_fixture_path.read_bytes()
        assert len(content) == evidence.byte_length
        assert hashlib.sha256(content).hexdigest() == evidence.sha256
        assert evidence.retention_location == f"content-sha256://{evidence.sha256}"
        assert evidence.actual_fixture_path.name == f"{evidence.sha256}.bin"
        assert evidence.representation == "ORIGINAL_HTTP_RESPONSE"
        assert evidence.capture_method == "sec_http_get"

    published = {
        (item.metric_id, item.period_end)
        for item in dataset.assessments
        if item.result_state == "PUBLISHED"
    }
    candidates = {
        (item.candidate.metric_id, item.candidate.period_end)
        for item in dataset.reported_candidates
    }
    assert candidates == published
    assert all(item.source_key in source_keys for item in dataset.reported_candidates)
    assert all(item.source_key in source_keys for item in dataset.support_candidates)

    for assessment in dataset.assessments:
        definition = dataset.catalog.versions(assessment.metric_id)[-1]
        assert {item.name for item in assessment.dimensions} == {
            item.taxonomy for item in definition.dimensions
        }


def test_wfc_phase4_exact_reported_values_and_raw_tokens() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    facts = {
        (item.candidate.metric_id, item.candidate.period_end): item
        for item in dataset.reported_candidates
    }

    expected: dict[str, tuple[Decimal | None, ...]] = {
        "servicing_for_others_upb": tuple(
            Decimal(value) for value in (434000000000, 397000000000, 387000000000, 362000000000)
        ),
        "total_servicing_upb": (Decimal(680000000000), Decimal(641000000000), None, None),
        "bank_owned_loans_serviced_upb": (
            Decimal(246000000000),
            Decimal(244000000000),
            None,
            None,
        ),
        "servicing_revenue": (Decimal(159000000), None, Decimal(105000000), Decimal(119000000)),
        "msr_fair_value": tuple(
            Decimal(value) for value in (6167000000, 5696000000, 5608000000, 5343000000)
        ),
        "msr_ending_balance": tuple(
            Decimal(value) for value in (6167000000, 5696000000, 5608000000, 5343000000)
        ),
        "msr_realization_or_amortization": (
            Decimal(192000000),
            None,
            Decimal(162000000),
            Decimal(162000000),
        ),
        "msr_fair_value_market_change": (
            Decimal(1000000),
            None,
            Decimal(28000000),
            Decimal(55000000),
        ),
        "msr_fair_value_assumption_change": (
            Decimal(97000000),
            None,
            Decimal(28000000),
            Decimal(47000000),
        ),
        "msr_hedging_result": (
            Decimal(2000000),
            None,
            Decimal(-26000000),
            Decimal(-55000000),
        ),
    }
    for metric_id, values in expected.items():
        assert (
            tuple(
                facts[(metric_id, period)].candidate.normalized_value
                if (metric_id, period) in facts
                else None
                for period in _PERIODS
            )
            == values
        )

    q3_realization = facts[("msr_realization_or_amortization", _PERIODS[0])]
    q1_hedge = facts[("msr_hedging_result", _PERIODS[2])]
    assert q3_realization.candidate.raw_value == "(192)"
    assert q1_hedge.candidate.raw_value == "(26)"
    assert all(
        apply_wfc_normalization_trace(item.candidate.raw_value, item.normalization_trace)
        == item.candidate.normalized_value
        for item in facts.values()
    )


def test_wfc_phase4_period_scope_methodology_and_dimensions_are_exact() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    for wrapper in dataset.reported_candidates:
        item = wrapper.candidate
        definition = dataset.catalog.definition(item.metric_id, item.metric_version)
        assert definition is not None
        assert item.unit == definition.unit.value == "USD"
        assert item.methodology in {method.value for method in definition.methodologies}
        assert item.reporting_entity_id == "wfc_sec_registrant"
        assert item.evidence_locator
        if item.period_type == "instant":
            assert item.period_start is None
        else:
            assert item.period_type == "duration"
            assert item.period_start is not None
            assert item.period_start <= item.period_end

        if item.metric_id in {
            "msr_fair_value",
            "msr_ending_balance",
            "msr_fair_value_market_change",
            "msr_fair_value_assumption_change",
            "msr_hedging_result",
        }:
            assert tuple((dimension.name, dimension.value) for dimension in wrapper.dimensions) == (
                ("msr_population", "owned_msr"),
            )
        else:
            assert wrapper.dimensions == ()

    scopes = {
        item.candidate.metric_id: item.candidate.reporting_scope_id
        for item in dataset.reported_candidates
    }
    assert scopes["servicing_for_others_upb"] == "wfc_consolidated_residential_mortgage_servicing"
    assert scopes["servicing_revenue"] == "wfc_mortgage_banking_servicing_economics"
    assert scopes["msr_fair_value"] == "wfc_owned_residential_msr"


def test_wfc_phase4_suppresses_q4_annual_flows_and_combined_rows() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    q4_metrics = {
        item.candidate.metric_id
        for item in dataset.reported_candidates
        if item.candidate.period_end == date(2025, 12, 31)
    }
    assert q4_metrics == {
        "servicing_for_others_upb",
        "total_servicing_upb",
        "bank_owned_loans_serviced_upb",
        "msr_fair_value",
        "msr_ending_balance",
    }
    assert not q4_metrics & {
        "servicing_revenue",
        "msr_realization_or_amortization",
        "msr_fair_value_market_change",
        "msr_fair_value_assumption_change",
        "msr_hedging_result",
        "msr_additions",
        "msr_purchases",
        "msr_sales",
        "msr_fair_value_inputs_or_assumptions_change",
    }
    assert not any(
        item.candidate.raw_label in {"Originations/purchases", "Sales and other"}
        for item in dataset.reported_candidates
    )


def test_wfc_phase4_governed_derivations_have_exact_lineage_and_scope_bridge() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    assert dataset.blocked_derivations == ()
    assert {(item.metric_id, item.period_end) for item in dataset.derived_candidates} == {
        (metric_id, period)
        for metric_id in (
            "msr_fair_value_multiple_of_related_upb",
            "msr_fair_value_bps_of_related_upb",
        )
        for period in _PERIODS
    }
    values = {
        (item.metric_id, item.period_end): item.normalized_value
        for item in dataset.derived_candidates
    }
    assert tuple(
        values[("msr_fair_value_multiple_of_related_upb", period)] for period in _PERIODS
    ) == tuple(
        Decimal(value) for value in ("0.0142096774", "0.0143476071", "0.0144909561", "0.0147596685")
    )
    assert tuple(
        values[("msr_fair_value_bps_of_related_upb", period)] for period in _PERIODS
    ) == tuple(Decimal(value) for value in ("142.096774", "143.476071", "144.909561", "147.596685"))
    for candidate in dataset.derived_candidates:
        decision = derive_metric(candidate.request, dataset.catalog)
        assert decision.result is not None
        assert decision.result.value == candidate.normalized_value
        assert decision.result.trace == candidate.trace
        assert candidate.formula_version == "1.1.0"
        assert candidate.input_roles == ("fair_value", "related_upb")
        assert len(candidate.input_candidate_ids) == 2

    support = dataset.support_candidates
    assert {item.candidate.metric_id for item in support} == {"wfc_residential_msr_related_upb"}
    assert all(item.candidate.reporting_scope_id == "wfc_owned_residential_msr" for item in support)
    assert all(
        item.dimensions == (MetricDimension("msr_population", "owned_msr"),) for item in support
    )


def test_wfc_phase4_derivation_fails_closed_without_governed_support_context() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    candidate = dataset.derived_candidates[0]
    request = candidate.request
    supplied = dict(request.inputs)

    raw_upb = next(
        item
        for item in dataset.reported_candidates
        if item.candidate.metric_id == "servicing_for_others_upb"
        and item.candidate.period_end == request.period_end
    )
    wrong_scope = replace(
        supplied["related_upb"],
        observation_id=raw_upb.candidate.candidate_id,
        metric_id=raw_upb.candidate.metric_id,
        metric_version=raw_upb.candidate.metric_version,
        reporting_scope_id=raw_upb.candidate.reporting_scope_id,
        dimensions=(),
    )
    decision = derive_metric(
        replace(
            request, inputs=(("fair_value", supplied["fair_value"]), ("related_upb", wrong_scope))
        ),
        dataset.catalog,
    )
    assert {reason.value for reason in decision.reasons} >= {
        "INPUT_SCOPE_MISMATCH",
        "INPUT_DIMENSION_MISMATCH",
    }

    wrong_dimensions = replace(
        supplied["related_upb"],
        dimensions=(MetricDimension("msr_population", "issuer_disclosed"),),
    )
    decision = derive_metric(
        replace(
            request,
            inputs=(("fair_value", supplied["fair_value"]), ("related_upb", wrong_dimensions)),
        ),
        dataset.catalog,
    )
    assert "INPUT_DIMENSION_MISMATCH" in {reason.value for reason in decision.reasons}

    legacy = derive_metric(replace(request, metric_version="1.0.0"), dataset.catalog)
    assert "INPUT_METRIC_MISMATCH" in {reason.value for reason in legacy.reasons}


def test_wfc_metric_extension_does_not_regress_phase3_derivations() -> None:
    dataset = load_phase3_dataset(_CONFIG)
    assert len(dataset.derived_candidates) == 43
    assert dataset.blocked_derivations == ()


def test_wfc_phase4_not_disclosed_assessments_have_precise_checks() -> None:
    dataset = load_wfc_phase4_dataset(_CONFIG)
    numeric_keys = {
        (item.candidate.metric_id, item.candidate.period_end)
        for item in dataset.reported_candidates
    } | {(item.metric_id, item.period_end) for item in dataset.derived_candidates}
    missing_keys = {(item.metric_id, item.period_end) for item in dataset.missing_cells}
    assert not numeric_keys & missing_keys
    assert all(
        item.reason_code == "CHECKED_COMPLETE_NOT_DISCLOSED" for item in dataset.missing_cells
    )
    assert all(
        item.source_keys
        and len(item.source_keys) == len(item.locators)
        and all(locator.strip() for locator in item.locators)
        for item in dataset.missing_cells
    )
    assert all(item.result_state != "SOURCE_NOT_CHECKED" for item in dataset.assessments)


def test_wfc_phase4_fails_closed_on_configured_header_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_yaml = wfc_module._yaml

    def mutated_yaml(path: Path) -> dict[str, Any]:
        payload = copy.deepcopy(original_yaml(path))
        if path.name == "wfc_sources.yaml":
            payload["offline_parser"]["sources"]["Q2_2026"]["income_anchor"] = (
                "not an issuer header"
            )
        return payload

    monkeypatch.setattr(wfc_module, "_yaml", mutated_yaml)
    with pytest.raises(WfcPhase4Error, match="not uniquely qualified"):
        load_wfc_phase4_dataset(_CONFIG)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "DISCLOSURE_MAP_RESEARCH_ONLY", "status is not publication validated"),
        ("publication_authorized", False, "publication is not authorized"),
        ("parser_implemented", False, "parser is not marked implemented"),
    ],
)
def test_wfc_phase4_fails_closed_on_publication_gate_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    original_yaml = wfc_module._yaml

    def mutated_yaml(path: Path) -> dict[str, Any]:
        payload = copy.deepcopy(original_yaml(path))
        if path.name == "wfc_sources.yaml":
            payload[field] = value
        return payload

    monkeypatch.setattr(wfc_module, "_yaml", mutated_yaml)
    with pytest.raises(WfcPhase4Error, match=message):
        load_wfc_phase4_dataset(_CONFIG)


def test_wfc_phase4_config_contains_recipes_not_authoritative_values() -> None:
    payload = wfc_module._yaml(_CONFIG / "phase4" / "wfc_sources.yaml")
    assert payload["parser_implemented"] is True
    forbidden = {"raw_value", "normalized_value", "reported_value", "observations"}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
