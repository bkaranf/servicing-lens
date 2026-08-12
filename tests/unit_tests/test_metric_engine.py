"""Socket-blocked deterministic tests for the shared Phase 3 metric engine."""

from __future__ import annotations

import socket
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pytest_socket import SocketBlockedError

from mortgage_servicing_dashboard.metric_engine import (
    AllowedScopeRelationship,
    AnnualizationParameters,
    AveragingParameters,
    CatalogViolationCode,
    ComparabilityDecision,
    Completeness,
    DecisionDisposition,
    DecisionReason,
    DerivationDecision,
    DerivationRequest,
    DimensionRequirement,
    DimensionTaxonomy,
    MetricCatalog,
    MetricDimension,
    MetricEngineError,
    MetricInput,
    MetricMethodology,
    MetricUnit,
    PeriodType,
    PublicationStatus,
    QuantizationRule,
    RoundingMethod,
    ValueState,
    assess_metric_comparability,
    catalog_invariant_violations,
    derive_metric,
    load_metric_catalog,
    normalize_reported_value,
    reconcile_cross_source,
    validate_catalog_invariants,
    validate_metric_input,
)

_ROOT = Path(__file__).resolve().parents[2]
_BASE_CATALOG = _ROOT / "config" / "metrics" / "catalog.yaml"
_PHASE3_CATALOG = _ROOT / "config" / "metrics" / "phase3_deepening.v1.yaml"
_QUARTER_START = date(2026, 4, 1)
_QUARTER_END = date(2026, 6, 30)
_BEGINNING = date(2026, 3, 31)
_PORTFOLIO_DIMENSIONS = (MetricDimension("portfolio_population", "owned_msr"),)
_MSR_DIMENSIONS = (MetricDimension("msr_population", "owned_msr"),)


def _catalog() -> MetricCatalog:
    return load_metric_catalog(
        _BASE_CATALOG,
        extension_paths=(_PHASE3_CATALOG,),
    )


def _input(  # noqa: PLR0913
    observation_id: str,
    metric_id: str,
    value: str | None,
    *,
    unit: MetricUnit = MetricUnit.USD,
    period_type: PeriodType = PeriodType.INSTANT,
    period_start: date | None = None,
    period_end: date = _QUARTER_END,
    dimensions: tuple[MetricDimension, ...] = _MSR_DIMENSIONS,
    issuer_id: str = "tfc",
    entity_id: str = "tfc_consolidated",
    scope_id: str = "tfc_consolidated_residential_mortgage_servicing",
    methodology: MetricMethodology = MetricMethodology.SEC_FILING_XBRL,
    metric_version: str = "1.0.0",
    publication_status: PublicationStatus = PublicationStatus.PUBLISHED,
    value_state: ValueState = ValueState.REPORTED_ACTUAL,
    completeness: Completeness = Completeness.COMPLETE,
    formula_version: str | None = None,
) -> MetricInput:
    return MetricInput(
        observation_id=observation_id,
        issuer_id=issuer_id,
        metric_id=metric_id,
        metric_version=metric_version,
        value=None if value is None else Decimal(value),
        unit=unit,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        reporting_entity_id=entity_id,
        reporting_scope_id=scope_id,
        methodology=methodology,
        publication_status=publication_status,
        value_state=value_state,
        completeness=completeness,
        dimensions=dimensions,
        formula_version=formula_version,
    )


def _msr_request(
    metric_id: str, *, fair_value: str = "250", upb: str = "10000"
) -> DerivationRequest:
    return DerivationRequest(
        derived_observation_id=f"derived:{metric_id}:2026Q2",
        metric_id=metric_id,
        metric_version="1.0.0",
        issuer_id="tfc",
        reporting_entity_id="tfc_consolidated",
        reporting_scope_id="tfc_consolidated_residential_mortgage_servicing",
        period_type=PeriodType.INSTANT,
        period_start=None,
        period_end=_QUARTER_END,
        dimensions=_MSR_DIMENSIONS,
        inputs=(
            ("fair_value", _input("obs:fv", "msr_fair_value", fair_value)),
            ("related_upb", _input("obs:upb", "owned_msr_upb", upb)),
        ),
    )


def _cost_request() -> DerivationRequest:
    return DerivationRequest(
        derived_observation_id="derived:cost:2026Q2",
        metric_id="cost_to_service_per_loan",
        metric_version="2.0.0",
        issuer_id="tfc",
        reporting_entity_id="tfc_consolidated",
        reporting_scope_id="tfc_consolidated_residential_mortgage_servicing",
        period_type=PeriodType.DURATION,
        period_start=_QUARTER_START,
        period_end=_QUARTER_END,
        dimensions=_PORTFOLIO_DIMENSIONS,
        inputs=(
            (
                "expense",
                _input(
                    "obs:expense",
                    "servicing_operating_expense",
                    "2100",
                    period_type=PeriodType.DURATION,
                    period_start=_QUARTER_START,
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
            (
                "beginning_loan_count",
                _input(
                    "obs:begin-count",
                    "servicing_loan_count",
                    "100",
                    unit=MetricUnit.COUNT,
                    period_end=_BEGINNING,
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
            (
                "ending_loan_count",
                _input(
                    "obs:end-count",
                    "servicing_loan_count",
                    "110",
                    unit=MetricUnit.COUNT,
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
        ),
        averaging=AveragingParameters(_BEGINNING, _QUARTER_END),
        annualization=AnnualizationParameters(observed_days=91, basis_days=Decimal(365)),
    )


def test_default_test_session_blocks_network_sockets() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket()


def test_catalog_composes_base_and_phase3_and_has_all_requested_metrics() -> None:
    catalog = _catalog()
    requested = {
        "msr_hedging_result",
        "msr_fair_value_multiple_of_related_upb",
        "msr_fair_value_bps_of_related_upb",
        "capitalized_servicing_rate_on_additions",
        "delinquency_30_plus_upb_rate",
        "delinquency_60_plus_upb_rate",
        "delinquency_90_plus_upb_rate",
        "foreclosure_upb_rate",
        "reo_upb",
        "msr_fair_value_inputs_or_assumptions_change",
        "msr_realization_passage_time_and_other",
    }

    assert catalog.base_version == "1.0.0"
    assert catalog.extension_versions == ("phase3-metric-deepening-1.0.0",)
    assert requested <= {definition.metric_id for definition in catalog.definitions}
    assert {item.semantic_version for item in catalog.versions("cost_to_service_per_loan")} == {
        "1.0.0",
        "2.0.0",
    }
    assert catalog_invariant_violations(catalog) == ()
    validate_catalog_invariants(catalog)


def test_catalog_governs_support_metrics_and_their_six_derivations() -> None:
    catalog = _catalog()
    support_ids = {
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
    derived_ids = {
        "government_servicing_upb",
        "conventional_servicing_upb",
        "capitalized_servicing_rate_on_additions",
        "delinquency_30_plus_upb_rate",
        "delinquency_90_plus_upb_rate",
        "foreclosure_upb_rate",
    }

    assert all(catalog.definition(metric_id, "1.0.0") is not None for metric_id in support_ids)
    for metric_id in derived_ids:
        version = "2.0.0" if metric_id.endswith("servicing_upb") else "1.0.0"
        definition = catalog.definition(metric_id, version)
        assert definition is not None
        assert definition.derivation is not None
        assert MetricMethodology.DETERMINISTIC_DERIVED in definition.methodologies


def test_catalog_dimensions_preserve_delinquency_and_mix_methodology() -> None:
    catalog = _catalog()
    delinquency = catalog.definition("delinquency_90_plus_upb_rate", "1.0.0")
    mix = catalog.definition("gnma_servicing_upb", "2.0.0")

    assert delinquency is not None
    assert {item.taxonomy: item.fixed_value for item in delinquency.dimensions} == {
        "portfolio_population": None,
        "delinquency_measure_basis": "upb",
        "delinquency_threshold": "90_plus",
        "delinquency_denominator": "unpaid_principal_balance",
        "delinquency_foreclosure_treatment": None,
        "delinquency_bankruptcy_treatment": None,
        "delinquency_forbearance_treatment": None,
    }
    assert mix is not None
    assert {item.taxonomy: item.fixed_value for item in mix.dimensions}[
        "portfolio_mix_category"
    ] == "gnma"
    assert {item.taxonomy: item.fixed_value for item in mix.dimensions}[
        "portfolio_mix_overlap"
    ] is None


def test_general_sum_and_ratio_formulas_are_exact_and_lineaged() -> None:
    dimensions = tuple(
        sorted(
            (
                MetricDimension("portfolio_population", "owned_msr_and_msl"),
                MetricDimension("portfolio_mix_category", "government"),
                MetricDimension("portfolio_mix_basis", "upb"),
                MetricDimension("portfolio_mix_overlap", "mutually_exclusive"),
            )
        )
    )
    component_dimensions = {
        metric_id: tuple(
            sorted(
                (
                    MetricDimension("portfolio_population", "owned_msr_and_msl"),
                    MetricDimension("servicing_component", component),
                )
            )
        )
        for metric_id, component in (
            ("fha_servicing_upb", "fha"),
            ("va_servicing_upb", "va"),
            ("usda_servicing_upb", "usda"),
        )
    }
    government = derive_metric(
        DerivationRequest(
            derived_observation_id="derived:government",
            metric_id="government_servicing_upb",
            metric_version="2.0.0",
            issuer_id="pfsi",
            reporting_entity_id="pfsi_registrant",
            reporting_scope_id="pfsi_owned_msr_and_msl_portfolio",
            period_type=PeriodType.INSTANT,
            period_start=None,
            period_end=_QUARTER_END,
            dimensions=dimensions,
            inputs=tuple(
                (
                    role,
                    _input(
                        f"obs:{role}",
                        metric_id,
                        value,
                        issuer_id="pfsi",
                        entity_id="pfsi_registrant",
                        scope_id="pfsi_owned_msr_and_msl_portfolio",
                        dimensions=component_dimensions[metric_id],
                    ),
                )
                for role, metric_id, value in (
                    ("fha", "fha_servicing_upb", "10"),
                    ("va", "va_servicing_upb", "20"),
                    ("usda", "usda_servicing_upb", "30"),
                )
            ),
        ),
        _catalog(),
    )

    assert government.result is not None
    assert government.result.value == Decimal("60.00")
    assert [item.input_role for item in government.result.lineage] == ["fha", "va", "usda"]

    delinquency_dimensions = tuple(
        sorted(
            (
                MetricDimension("portfolio_population", "owned_msr_and_msl"),
                MetricDimension("delinquency_measure_basis", "upb"),
                MetricDimension("delinquency_threshold", "90_plus"),
                MetricDimension("delinquency_denominator", "unpaid_principal_balance"),
                MetricDimension("delinquency_foreclosure_treatment", "excluded"),
                MetricDimension("delinquency_bankruptcy_treatment", "source_defined"),
                MetricDimension("delinquency_forbearance_treatment", "source_defined"),
            )
        )
    )
    denominator_dimensions = (MetricDimension("portfolio_population", "owned_msr_and_msl"),)
    rate = derive_metric(
        DerivationRequest(
            derived_observation_id="derived:90-plus",
            metric_id="delinquency_90_plus_upb_rate",
            metric_version="1.0.0",
            issuer_id="pfsi",
            reporting_entity_id="pfsi_registrant",
            reporting_scope_id="pfsi_owned_msr_and_msl_portfolio",
            period_type=PeriodType.INSTANT,
            period_start=None,
            period_end=_QUARTER_END,
            dimensions=delinquency_dimensions,
            inputs=(
                (
                    "numerator",
                    _input(
                        "obs:90-plus",
                        "delinquency_90_plus_upb",
                        "25",
                        issuer_id="pfsi",
                        entity_id="pfsi_registrant",
                        scope_id="pfsi_owned_msr_and_msl_portfolio",
                        dimensions=delinquency_dimensions,
                        methodology=MetricMethodology.DELINQUENCY_UPB_REPORTED,
                    ),
                ),
                (
                    "denominator",
                    _input(
                        "obs:owned-total",
                        "owned_msr_msl_upb",
                        "1000",
                        issuer_id="pfsi",
                        entity_id="pfsi_registrant",
                        scope_id="pfsi_owned_msr_and_msl_portfolio",
                        dimensions=denominator_dimensions,
                    ),
                ),
            ),
        ),
        _catalog(),
    )
    zero = derive_metric(
        replace(
            _msr_request("msr_fair_value_multiple_of_related_upb"),
            inputs=(
                _msr_request("msr_fair_value_multiple_of_related_upb").inputs[0],
                (
                    "related_upb",
                    replace(
                        _msr_request("msr_fair_value_multiple_of_related_upb").inputs[1][1],
                        value=Decimal(0),
                    ),
                ),
            ),
        ),
        _catalog(),
    )
    assert rate.result is not None
    assert rate.result.value == Decimal("0.0250000000")
    assert zero.reasons == (DecisionReason.DENOMINATOR_NOT_POSITIVE,)


def test_catalog_rejects_mismatched_extension_base_version(tmp_path: Path) -> None:
    extension = tmp_path / "bad.yaml"
    extension.write_text(
        """schema_version: "1"
extension_version: "test"
requires_base_catalog_version: "9.9.9"
dimension_taxonomies: []
metric_versions: []
cross_source_rules: []
""",
        encoding="utf-8",
    )

    with pytest.raises(MetricEngineError, match="requires base catalog"):
        load_metric_catalog(_BASE_CATALOG, extension_paths=(extension,))


def test_full_catalog_invariant_helper_reports_all_fail_closed_categories() -> None:
    catalog = _catalog()
    derived = catalog.definition("cost_to_service_per_loan", "2.0.0")
    assert derived is not None
    assert derived.derivation is not None
    first_input = derived.derivation.inputs[0]
    invalid_derivation = replace(
        derived.derivation,
        inputs=(
            replace(
                first_input,
                allowed_scope_pairs=(
                    AllowedScopeRelationship("", "output"),
                    AllowedScopeRelationship("same", "same"),
                    AllowedScopeRelationship("duplicate", "output"),
                    AllowedScopeRelationship("duplicate", "output"),
                ),
            ),
            first_input,
            replace(first_input, role="unknown", metric_ids=("unknown_metric",)),
        ),
    )
    invalid_definition = replace(
        derived,
        semantic_version="invalid",
        scale=Decimal(0),
        methodologies=(),
        dimensions=(
            DimensionRequirement("missing_taxonomy", None),
            DimensionRequirement("portfolio_population", "invalid_member"),
            DimensionRequirement("portfolio_population", None),
        ),
        comparability=replace(
            derived.comparability,
            dimensions=("missing_comparison_dimension",),
        ),
        derivation=invalid_derivation,
    )
    first_taxonomy = catalog.dimension_taxonomies[0]
    first_rule = catalog.cross_source_rules[0]
    invalid_rule = replace(
        first_rule,
        rule_id="invalid_rule",
        metric_id="unknown_metric",
        sec_methodologies=(),
        allowed_scope_pairs=(),
        absolute_tolerance=Decimal(-1),
    )
    invalid_catalog = replace(
        catalog,
        definitions=(
            *catalog.definitions,
            catalog.definitions[0],
            invalid_definition,
        ),
        dimension_taxonomies=(
            *catalog.dimension_taxonomies,
            first_taxonomy,
            DimensionTaxonomy("empty_taxonomy", ()),
        ),
        cross_source_rules=(
            *catalog.cross_source_rules,
            first_rule,
            invalid_rule,
        ),
    )

    codes = {item.code for item in catalog_invariant_violations(invalid_catalog)}

    assert {
        CatalogViolationCode.DUPLICATE_METRIC_VERSION,
        CatalogViolationCode.DUPLICATE_TAXONOMY,
        CatalogViolationCode.EMPTY_TAXONOMY,
        CatalogViolationCode.UNKNOWN_TAXONOMY,
        CatalogViolationCode.INVALID_FIXED_DIMENSION,
        CatalogViolationCode.DUPLICATE_DIMENSION,
        CatalogViolationCode.INVALID_SCALE,
        CatalogViolationCode.DERIVED_METHODOLOGY_MISSING,
        CatalogViolationCode.DUPLICATE_INPUT_ROLE,
        CatalogViolationCode.UNKNOWN_INPUT_METRIC,
        CatalogViolationCode.UNKNOWN_COMPARABILITY_DIMENSION,
        CatalogViolationCode.DUPLICATE_CROSS_SOURCE_RULE,
        CatalogViolationCode.UNKNOWN_RECONCILIATION_METRIC,
        CatalogViolationCode.INVALID_RECONCILIATION_TOLERANCE,
        CatalogViolationCode.INVALID_RECONCILIATION_METHODS,
        CatalogViolationCode.EMPTY_SCOPE_PAIR,
        CatalogViolationCode.DUPLICATE_SCOPE_PAIR,
        CatalogViolationCode.SAME_SCOPE_PAIR,
    } <= codes
    with pytest.raises(MetricEngineError, match="metric catalog invariant violations"):
        validate_catalog_invariants(invalid_catalog)


def test_constructor_invariants_reject_ambiguous_contexts_and_non_decimal_controls() -> None:
    with pytest.raises(MetricEngineError, match="nonblank"):
        MetricDimension("", "value")
    with pytest.raises(TypeError, match="quantum must be Decimal"):
        QuantizationRule(1.0, RoundingMethod.ROUND_HALF_EVEN)  # type: ignore[arg-type]
    with pytest.raises(MetricEngineError, match="finite and positive"):
        QuantizationRule(Decimal(0), RoundingMethod.ROUND_HALF_EVEN)
    with pytest.raises(MetricEngineError, match="strictly ordered"):
        AveragingParameters(_QUARTER_END, _QUARTER_END)
    with pytest.raises(MetricEngineError, match="positive integer"):
        AnnualizationParameters(observed_days=False, basis_days=Decimal(365))
    with pytest.raises(TypeError, match="basis days must be Decimal"):
        AnnualizationParameters(observed_days=91, basis_days=365.0)  # type: ignore[arg-type]
    with pytest.raises(MetricEngineError, match="finite and positive"):
        AnnualizationParameters(observed_days=91, basis_days=Decimal(0))

    instant = _input("obs:instant", "msr_fair_value", "1")
    with pytest.raises(MetricEngineError, match="instant input"):
        replace(instant, period_start=_QUARTER_START)
    with pytest.raises(MetricEngineError, match="duration input"):
        replace(instant, period_type=PeriodType.DURATION)
    unsorted = (
        MetricDimension("z", "one"),
        MetricDimension("a", "two"),
    )
    with pytest.raises(MetricEngineError, match="must be sorted"):
        replace(instant, dimensions=unsorted)
    duplicate = (
        MetricDimension("same", "one"),
        MetricDimension("same", "two"),
    )
    with pytest.raises(MetricEngineError, match="cannot repeat"):
        replace(instant, dimensions=duplicate)

    request = _msr_request("msr_fair_value_multiple_of_related_upb")
    with pytest.raises(MetricEngineError, match="must not be blank"):
        replace(request, derived_observation_id=" ")
    with pytest.raises(MetricEngineError, match="instant output"):
        replace(request, period_start=_QUARTER_START)
    with pytest.raises(MetricEngineError, match="duration output"):
        replace(request, period_type=PeriodType.DURATION)
    with pytest.raises(MetricEngineError, match="must be sorted"):
        replace(request, dimensions=unsorted)
    with pytest.raises(MetricEngineError, match="cannot repeat"):
        replace(request, dimensions=duplicate)

    with pytest.raises(MetricEngineError, match="validated derivation"):
        DerivationDecision(DecisionDisposition.VALIDATED, (), None)
    with pytest.raises(MetricEngineError, match="quarantined derivation"):
        DerivationDecision(DecisionDisposition.QUARANTINED, (), None)


@pytest.mark.parametrize(
    ("raw", "scale", "expected"),
    [
        ("1,234.50", Decimal(1), Decimal("1234.50")),
        ("(2.25)", Decimal(1000000), Decimal("-2250000.00")),
    ],
)
def test_reported_value_normalization_is_decimal_only(
    raw: str,
    scale: Decimal,
    expected: Decimal,
) -> None:
    assert normalize_reported_value(raw, scale=scale) == expected


def test_reported_value_normalization_rejects_float_and_nonfinite() -> None:
    with pytest.raises(TypeError, match="scale must be Decimal"):
        normalize_reported_value("1", scale=1.0)  # type: ignore[arg-type]
    with pytest.raises(MetricEngineError, match="finite"):
        normalize_reported_value("NaN")


def test_msr_multiple_and_bps_are_exact_quantized_decimals() -> None:
    catalog = _catalog()
    multiple = derive_metric(
        _msr_request("msr_fair_value_multiple_of_related_upb"),
        catalog,
    )
    bps = derive_metric(
        _msr_request("msr_fair_value_bps_of_related_upb"),
        catalog,
    )

    assert multiple.disposition is DecisionDisposition.VALIDATED
    assert multiple.result is not None
    assert multiple.result.value == Decimal("0.0250000000")
    assert bps.result is not None
    assert bps.result.value == Decimal("250.000000")
    assert bps.result.trace.unquantized_value == Decimal(250)
    assert bps.result.trace.quantum == Decimal("0.000001")


def test_derived_lineage_maps_directly_to_orm_shape_in_role_order() -> None:
    decision = derive_metric(_cost_request(), _catalog())

    assert decision.disposition is DecisionDisposition.VALIDATED
    assert decision.result is not None
    assert decision.result.value == Decimal("80.22")
    assert decision.result.trace.observed_days == 91
    assert decision.result.trace.basis_days == Decimal(365)
    assert [item.input_role for item in decision.result.lineage] == [
        "expense",
        "beginning_loan_count",
        "ending_loan_count",
    ]
    assert [item.input_ordinal for item in decision.result.lineage] == [0, 1, 2]
    assert asdict(decision.result.lineage[0]) == {
        "derived_observation_id": "derived:cost:2026Q2",
        "input_observation_id": "obs:expense",
        "input_role": "expense",
        "input_ordinal": 0,
        "formula_version": "1.0.0",
        "input_value": Decimal(2100),
    }


def test_annualized_rate_requires_explicit_average_and_actual_day_basis() -> None:
    request = DerivationRequest(
        derived_observation_id="derived:fee-rate:2026Q2",
        metric_id="weighted_average_servicing_fee_bps",
        metric_version="2.0.0",
        issuer_id="tfc",
        reporting_entity_id="tfc_consolidated",
        reporting_scope_id="tfc_consolidated_residential_mortgage_servicing",
        period_type=PeriodType.DURATION,
        period_start=_QUARTER_START,
        period_end=_QUARTER_END,
        dimensions=_PORTFOLIO_DIMENSIONS,
        inputs=(
            (
                "fee_income",
                _input(
                    "obs:fee",
                    "servicing_fee_income",
                    "25",
                    period_type=PeriodType.DURATION,
                    period_start=_QUARTER_START,
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
            (
                "beginning_upb",
                _input(
                    "obs:begin-upb",
                    "owned_msr_upb",
                    "9500",
                    period_end=_BEGINNING,
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
            (
                "ending_upb",
                _input(
                    "obs:end-upb",
                    "owned_msr_upb",
                    "10500",
                    dimensions=_PORTFOLIO_DIMENSIONS,
                ),
            ),
        ),
        averaging=AveragingParameters(_BEGINNING, _QUARTER_END),
        annualization=AnnualizationParameters(observed_days=91, basis_days=Decimal(365)),
    )

    decision = derive_metric(request, _catalog())

    assert decision.result is not None
    assert decision.result.value == Decimal("100.274725")
    assert decision.result.trace.observed_days == 91
    assert decision.result.trace.basis_days == Decimal(365)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"publication_status": PublicationStatus.UNPUBLISHED},
            DecisionReason.INPUT_UNPUBLISHED,
        ),
        ({"completeness": Completeness.PARTIAL}, DecisionReason.INPUT_INCOMPLETE),
        ({"value_state": ValueState.ESTIMATED}, DecisionReason.INPUT_NOT_ACTUAL),
        ({"value_state": ValueState.PARTIAL}, DecisionReason.INPUT_NOT_ACTUAL),
        ({"value": None}, DecisionReason.INPUT_VALUE_MISSING),
        ({"reporting_scope_id": "other_scope"}, DecisionReason.INPUT_SCOPE_MISMATCH),
    ],
)
def test_derivation_quarantines_nonactual_partial_or_mismatched_inputs(
    change: dict[str, object],
    reason: DecisionReason,
) -> None:
    request = _msr_request("msr_fair_value_multiple_of_related_upb")
    role, observation = request.inputs[0]
    changed = replace(observation, **change)  # type: ignore[arg-type]
    decision = derive_metric(
        replace(request, inputs=((role, changed), request.inputs[1])), _catalog()
    )

    assert decision.disposition is DecisionDisposition.QUARANTINED
    assert decision.result is None
    assert reason in decision.reasons


def test_derivation_rejects_duplicate_or_missing_published_input_ids() -> None:
    request = _msr_request("msr_fair_value_multiple_of_related_upb")
    duplicate_id = replace(request.inputs[1][1], observation_id="obs:fv")
    duplicate = derive_metric(
        replace(request, inputs=(request.inputs[0], ("related_upb", duplicate_id))),
        _catalog(),
    )
    missing_role = derive_metric(replace(request, inputs=(request.inputs[0],)), _catalog())

    assert DecisionReason.INPUT_OBSERVATION_ID_DUPLICATE in duplicate.reasons
    assert missing_role.reasons == (DecisionReason.INPUT_ROLE_MISSING,)


def test_derivation_rejects_zero_denominator_and_implicit_averaging() -> None:
    zero = derive_metric(
        _msr_request("msr_fair_value_bps_of_related_upb", upb="0"),
        _catalog(),
    )
    no_average = derive_metric(replace(_cost_request(), averaging=None), _catalog())
    no_annualization = derive_metric(replace(_cost_request(), annualization=None), _catalog())

    assert zero.reasons == (DecisionReason.DENOMINATOR_NOT_POSITIVE,)
    assert DecisionReason.AVERAGING_PARAMETERS_MISSING in no_average.reasons
    assert DecisionReason.ANNUALIZATION_PARAMETERS_MISSING in no_annualization.reasons


def test_pfsi_cost_scope_bridge_is_governed_and_any_other_scope_quarantines() -> None:
    request = replace(
        _cost_request(),
        issuer_id="pfsi",
        reporting_entity_id="pfsi_registrant",
        reporting_scope_id="pfsi_servicing_segment",
    )
    inputs = tuple(
        (
            role,
            replace(
                observation,
                issuer_id="pfsi",
                reporting_entity_id="pfsi_registrant",
                reporting_scope_id=(
                    "pfsi_servicing_segment"
                    if role == "expense"
                    else "pfsi_total_servicing_portfolio"
                ),
            ),
        )
        for role, observation in request.inputs
    )

    permitted = derive_metric(replace(request, inputs=inputs), _catalog())
    wrong = derive_metric(
        replace(
            request,
            inputs=(
                inputs[0],
                ("beginning_loan_count", replace(inputs[1][1], reporting_scope_id="unknown")),
                inputs[2],
            ),
        ),
        _catalog(),
    )

    assert permitted.disposition is DecisionDisposition.VALIDATED
    assert DecisionReason.INPUT_SCOPE_MISMATCH in wrong.reasons


def test_tfc_msr_scope_bridge_is_governed_and_any_other_scope_quarantines() -> None:
    request = replace(
        _msr_request("msr_fair_value_multiple_of_related_upb"),
        reporting_scope_id="tfc_owned_residential_msr",
        inputs=(
            (
                "fair_value",
                replace(
                    _msr_request("msr_fair_value_multiple_of_related_upb").inputs[0][1],
                    reporting_scope_id="tfc_owned_residential_msr",
                ),
            ),
            (
                "related_upb",
                replace(
                    _msr_request("msr_fair_value_multiple_of_related_upb").inputs[1][1],
                    metric_id="servicing_for_others_upb",
                    reporting_scope_id="tfc_consolidated_residential_mortgage_servicing",
                ),
            ),
        ),
    )

    permitted = derive_metric(request, _catalog())
    wrong = derive_metric(
        replace(
            request,
            inputs=(
                request.inputs[0],
                ("related_upb", replace(request.inputs[1][1], reporting_scope_id="unknown")),
            ),
        ),
        _catalog(),
    )

    assert permitted.disposition is DecisionDisposition.VALIDATED
    assert DecisionReason.INPUT_SCOPE_MISMATCH in wrong.reasons


def test_metric_validation_requires_exact_delinquency_dimensions_and_range() -> None:
    dimensions = tuple(
        sorted(
            (
                MetricDimension("portfolio_population", "servicing_for_others"),
                MetricDimension("delinquency_measure_basis", "upb"),
                MetricDimension("delinquency_threshold", "90_plus"),
                MetricDimension("delinquency_denominator", "unpaid_principal_balance"),
                MetricDimension("delinquency_foreclosure_treatment", "excluded"),
                MetricDimension("delinquency_bankruptcy_treatment", "source_defined"),
                MetricDimension("delinquency_forbearance_treatment", "source_defined"),
            )
        )
    )
    observation = _input(
        "obs:delinquency",
        "delinquency_90_plus_upb_rate",
        "0.0325",
        unit=MetricUnit.RATIO,
        dimensions=dimensions,
        methodology=MetricMethodology.DELINQUENCY_UPB_REPORTED,
    )

    assert (
        validate_metric_input(observation, _catalog()).disposition is DecisionDisposition.VALIDATED
    )
    invalid = validate_metric_input(replace(observation, value=Decimal("1.1")), _catalog())
    wrong_basis = validate_metric_input(
        replace(
            observation,
            dimensions=tuple(
                sorted(
                    replace(item, value="count")
                    if item.name == "delinquency_measure_basis"
                    else item
                    for item in dimensions
                )
            ),
        ),
        _catalog(),
    )
    assert invalid.reasons == (DecisionReason.VALUE_RULE_FAILED,)
    assert DecisionReason.FIXED_DIMENSION_MISMATCH in wrong_basis.reasons


def test_comparability_requires_exact_methodology_dimensions_and_formula_version() -> None:
    left = _input(
        "derived:left",
        "msr_fair_value_bps_of_related_upb",
        "250",
        unit=MetricUnit.BASIS_POINTS,
        methodology=MetricMethodology.DETERMINISTIC_DERIVED,
        formula_version="1.0.0",
    )
    right = replace(left, observation_id="derived:right")
    comparable = assess_metric_comparability(left, right, _catalog())
    changed = assess_metric_comparability(
        left,
        replace(right, formula_version="2.0.0"),
        _catalog(),
    )

    assert comparable == ComparabilityDecision(comparable=True, reasons=())
    assert changed.comparable is False
    assert changed.reasons == (DecisionReason.INPUT_FORMULA_VERSION_MISSING,)


def _tfc_cross_source_pair(
    sec_value: str, regulatory_value: str
) -> tuple[MetricInput, MetricInput]:
    sec = _input(
        "sec:tfc:sfo",
        "servicing_for_others_upb",
        sec_value,
        dimensions=(),
    )
    regulatory = replace(
        sec,
        observation_id="y9c:tfc:sfo",
        value=Decimal(regulatory_value),
        reporting_entity_id="tfc_bhc",
        reporting_scope_id="tfc_bhc_regulatory",
        methodology=MetricMethodology.FR_Y9C,
    )
    return sec, regulatory


def test_tfc_sec_vs_regulatory_reconciliation_validates_exact_match() -> None:
    sec, regulatory = _tfc_cross_source_pair("298658000000", "298658000000")
    decision = reconcile_cross_source(
        sec,
        regulatory,
        rule_id="tfc_sec_vs_y9c_servicing_for_others",
        catalog=_catalog(),
    )

    assert decision.disposition is DecisionDisposition.VALIDATED
    assert decision.reasons == ()
    assert decision.absolute_difference == Decimal(0)
    assert decision.quarantine_required is False


def test_tfc_sec_vs_regulatory_mismatch_quarantines_without_preference() -> None:
    sec, regulatory = _tfc_cross_source_pair("298658000000", "298657000000")
    decision = reconcile_cross_source(
        sec,
        regulatory,
        rule_id="tfc_sec_vs_y9c_servicing_for_others",
        catalog=_catalog(),
    )

    assert decision.disposition is DecisionDisposition.QUARANTINED
    assert decision.reasons == (DecisionReason.RECONCILIATION_VALUE_MISMATCH,)
    assert decision.absolute_difference == Decimal(1000000)
    assert decision.quarantine_required is True
    assert not hasattr(decision, "preferred_observation")


def test_tfc_reconciliation_quarantines_scope_or_source_semantics_mismatch() -> None:
    sec, regulatory = _tfc_cross_source_pair("10", "10")
    wrong_scope = reconcile_cross_source(
        sec,
        replace(regulatory, reporting_scope_id="tfc_bank_regulatory"),
        rule_id="tfc_sec_vs_y9c_servicing_for_others",
        catalog=_catalog(),
    )
    wrong_source = reconcile_cross_source(
        sec,
        replace(regulatory, methodology=MetricMethodology.FFIEC_CALL_REPORT),
        rule_id="tfc_sec_vs_y9c_servicing_for_others",
        catalog=_catalog(),
    )

    assert wrong_scope.reasons == (DecisionReason.RECONCILIATION_SEMANTICS_MISMATCH,)
    assert wrong_source.reasons == (DecisionReason.RECONCILIATION_SEMANTICS_MISMATCH,)
