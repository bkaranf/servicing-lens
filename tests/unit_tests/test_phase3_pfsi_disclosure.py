from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from mortgage_servicing_dashboard.metric_engine import load_metric_catalog
from mortgage_servicing_dashboard.sources import RecordedSourceDefinition

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG_ROOT = REPOSITORY_ROOT / "config"
PFSI_CONFIG_PATH = CONFIG_ROOT / "phase3" / "pfsi_sources.yaml"
PFSI_MANIFEST_PATH = CONFIG_ROOT / "recorded_evidence" / "phase3" / "pfsi" / "manifest.v1.yaml"
PERIODS = {"2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"}
METRICS = {
    "ancillary_servicing_income",
    "bank_owned_loans_serviced_upb",
    "capitalized_servicing_rate_on_additions",
    "conventional_servicing_upb",
    "cost_to_service_per_loan",
    "delinquency_30_plus_count_rate",
    "delinquency_30_plus_upb_rate",
    "delinquency_60_plus_count_rate",
    "delinquency_60_plus_upb_rate",
    "delinquency_90_plus_count_rate",
    "delinquency_90_plus_upb_rate",
    "fhlmc_servicing_upb",
    "fnma_servicing_upb",
    "foreclosure_upb_rate",
    "gnma_servicing_upb",
    "government_servicing_upb",
    "interim_servicing_upb",
    "msr_additions",
    "msr_beginning_balance",
    "msr_ending_balance",
    "msr_fair_value",
    "msr_fair_value_assumption_change",
    "msr_fair_value_bps_of_related_upb",
    "msr_fair_value_inputs_or_assumptions_change",
    "msr_fair_value_market_change",
    "msr_fair_value_multiple_of_related_upb",
    "msr_hedging_result",
    "msr_purchases",
    "msr_realization_or_amortization",
    "msr_realization_passage_time_and_other",
    "msr_sales",
    "owned_msr_upb",
    "reo_upb",
    "servicing_adjusted_pretax_income",
    "servicing_fee_income",
    "servicing_for_others_upb",
    "servicing_loan_count",
    "servicing_operating_expense",
    "servicing_pretax_income",
    "servicing_revenue",
    "subservicing_upb",
    "total_servicing_upb",
    "weighted_average_servicing_fee_bps",
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


def _configuration() -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        yaml.safe_load(PFSI_CONFIG_PATH.read_text(encoding="utf-8")),
    )


def test_pfsi_phase3_retained_sources_are_exact_and_typed() -> None:
    data = _configuration()
    sources = cast("dict[str, dict[str, Any]]", data["sources"])
    manifest = cast(
        "dict[str, Any]",
        yaml.safe_load(PFSI_MANIFEST_PATH.read_text(encoding="utf-8")),
    )
    manifest_sources = cast("list[dict[str, Any]]", manifest["sources"])

    assert len(sources) == 12
    assert len(manifest_sources) == 12
    assert len({source["evidence_id"] for source in manifest_sources}) == 12
    assert {source["period_end"] for source in sources.values()} == PERIODS
    for manifest_source in manifest_sources:
        content = (PFSI_MANIFEST_PATH.parent / manifest_source["path"]).read_bytes()
        assert len(content) == manifest_source["byte_length"]
        assert hashlib.sha256(content).hexdigest() == manifest_source["sha256"]
        assert manifest_source["representation"] == "ORIGINAL_HTTP_RESPONSE"
        assert manifest_source["capture_method"] == "sec_http_get"
        assert manifest_source["url"].startswith("https://www.sec.gov/Archives/edgar/data/1745916/")
        assert manifest_source["accepted_at"]
        assert manifest_source["retrieved_at"]
        assert manifest_source["last_modified"]
        assert manifest_source["locators"]

    for key, payload in sources.items():
        definition = RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=CONFIG_ROOT,
        )
        content = definition.fixture_path.read_bytes()
        assert definition.company_id == "pfsi"
        assert definition.url.startswith("https://www.sec.gov/Archives/edgar/data/1745916/")
        assert definition.representation == "ORIGINAL_HTTP_RESPONSE"
        assert definition.capture_method == "sec_http_get"
        assert definition.media_type == "text/html"
        assert definition.rows == ()
        assert definition.byte_length == len(content)
        assert definition.content_sha256 == hashlib.sha256(content).hexdigest()
        assert payload["retrieved_at"]
        assert payload["last_modified"]
        assert payload["locator"]


def test_pfsi_phase3_matrix_covers_every_metric_period_cell() -> None:
    data = _configuration()
    sources = cast("dict[str, Any]", data["sources"])
    source_sets = cast("dict[str, list[str]]", data["source_sets"])
    cells = cast("list[dict[str, Any]]", data["eligible_source_assessment"]["cells"])
    cell_keys = {(cell["metric_id"], cell["period_end"]) for cell in cells}

    assert len(METRICS) == 53
    assert len(cells) == 212
    assert len(cell_keys) == 212
    assert cell_keys == {(metric, period) for metric in METRICS for period in PERIODS}
    assert set(source_sets) == PERIODS

    for cell in cells:
        assert cell["assessment_status"] in {
            "DISCLOSURE_FOUND",
            "CHECKED_COMPLETE",
            "SOURCE_NOT_CHECKED",
        }
        assert cell["result_state"] in {"PUBLISHED", "DERIVED", "NOT_DISCLOSED"}
        assert cell["checked_source_keys"] == source_sets[cell["period_end"]]
        assert all(key in sources for key in cell["evidence_source_keys"])
        if cell["result_state"] == "NOT_DISCLOSED":
            assert cell["assessment_status"] == "CHECKED_COMPLETE"
            assert cell["evidence_source_keys"] == []
        else:
            assert cell["assessment_status"] == "DISCLOSURE_FOUND"
            assert cell["evidence_source_keys"]

    # All four periods have their complete three-document eligible set, so no
    # unchecked state remains in this issuer package.
    assert {cell["assessment_status"] for cell in cells} == {
        "DISCLOSURE_FOUND",
        "CHECKED_COMPLETE",
    }

    publication = cast("dict[str, Any]", data["derivation_publication_assessment"])
    ready = {
        (metric_id, period)
        for metric_id, periods in publication["ready_metric_periods"].items()
        for period in periods
    }
    blocked = {
        (metric_id, period)
        for metric_id, assessment in publication["blocked_metric_periods"].items()
        for period in assessment["periods"]
    }
    derived = {
        (cell["metric_id"], cell["period_end"])
        for cell in cells
        if cell["result_state"] == "DERIVED"
    }
    assert not ready & blocked
    assert ready | blocked == derived
    for assessment in publication["blocked_metric_periods"].values():
        assert assessment["missing_canonical_input_metric_ids"]
        assert assessment["reason"]
    assert publication["blocked_metric_periods"] == {}
    assert len(ready) == len(derived) == 35


def test_pfsi_phase3_recipes_preserve_parser_and_scope_qualifiers() -> None:
    data = _configuration()
    recipes = cast("dict[str, Any]", data["recipes"])
    five_quarter = recipes["q2_release_five_quarter_table"]
    exact_msr = recipes["periodic_msr_fair_value_and_related_upb"]
    portfolio = recipes["periodic_portfolio_and_delinquency"]
    delinquency = recipes["derived_owned_portfolio_delinquency"]

    assert five_quarter["table_anchor"]
    assert five_quarter["column_headers"] == ["2Q26", "1Q26", "4Q25", "3Q25", "2Q25"]
    assert set(five_quarter["period_value_indices"]) == PERIODS
    assert {row["metric_id"] for row in five_quarter["rows"]} >= {
        "servicing_revenue",
        "servicing_fee_income",
        "servicing_loan_count",
    }
    assert {row["metric_id"] for row in exact_msr["rows"]} == {
        "owned_msr_upb",
        "msr_fair_value",
    }
    assert portfolio["reporting_scope_id"] == "pfsi_owned_msr_and_msl_portfolio"
    assert "source_reported_owned_msr_and_msl_upb" in portfolio["reconciliation"]["formula"]
    assert delinquency["denominator_source_field"] == ("source_reported_owned_msr_and_msl_upb")
    assert recipes["periodic_msr_rollforward"]["reconciliation"]["tolerance"] == "0"

    def assert_no_binary_float(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for nested in value.values():
                assert_no_binary_float(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_no_binary_float(nested)

    assert_no_binary_float(data)


def test_pfsi_capitalized_servicing_rate_unit_matches_catalog() -> None:
    data = _configuration()
    recipe = next(
        item
        for item in data["recipes"]["derived_msr_economics"]["recipes"]
        if item["metric_id"] == "capitalized_servicing_rate_on_additions"
    )
    catalog = load_metric_catalog(
        CONFIG_ROOT / "metrics" / "catalog.yaml",
        extension_paths=(CONFIG_ROOT / "metrics" / "phase3_deepening.v1.yaml",),
    )
    definition = catalog.definition("capitalized_servicing_rate_on_additions", "1.0.0")

    assert definition is not None
    assert recipe["output_unit"] == definition.unit.value == "basis_points"


def test_pfsi_phase3_decimal_derivations_and_rollforwards() -> None:
    expenses_millions = [Decimal("84.5"), Decimal("81.8"), Decimal("80.6"), Decimal(76)]
    adjacent_counts_thousands = [
        (Decimal(2704), Decimal(2746)),
        (Decimal(2746), Decimal(2788)),
        (Decimal(2788), Decimal(2725)),
        (Decimal(2725), Decimal(2753)),
    ]
    actual_period_days = [Decimal(92), Decimal(92), Decimal(90), Decimal(91)]
    costs = [
        (
            expense
            * Decimal(1000000)
            / ((beginning + ending) * Decimal(1000) / Decimal(2))
            * Decimal(365)
            / period_days
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        for expense, (beginning, ending), period_days in zip(
            expenses_millions,
            adjacent_counts_thousands,
            actual_period_days,
            strict=True,
        )
    ]
    assert costs == [Decimal("123.03"), Decimal("117.29"), Decimal("118.58"), Decimal("111.29")]

    assert (
        Decimal(9531249)
        + Decimal(700326)
        - Decimal(183514)
        - Decimal(1895)
        - Decimal(102519)
        - Decimal(289705)
    ) == Decimal(9653942)
    assert (
        Decimal(9598941)
        + Decimal(719586)
        + Decimal(6428)
        - Decimal(3922)
        + Decimal(183047)
        - Decimal(355044)
    ) == Decimal(10149036)
    assert (
        Decimal(10149036) + Decimal(648681) - Decimal(6370) + Decimal(118281) - Decimal(322834)
    ) == Decimal(10586794)

    q2_multiple = Decimal(10586794) / Decimal(488083247)
    assert q2_multiple.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN) == Decimal(
        "0.021691"
    )
    assert (q2_multiple * Decimal(10000)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_EVEN,
    ) == Decimal("216.91")


def test_phase3_did_not_change_stage_a_retained_bytes() -> None:
    expected = {
        "sec-pfsi-2026q2-earnings-ex99-1.html": (
            741531,
            "db128f08fa4fff4835e13467e6dc18f081983b64618ada3e6a7ee7097ade78cf",
        ),
        "sec-tfc-2026q2-qps-ex99-2.html": (
            1697426,
            "7353334b2f40cb48d0ed6dc6756378e93260d2e2b6541ea37d800790057a7883",
        ),
    }
    for name, (length, sha256) in expected.items():
        content = (CONFIG_ROOT / "recorded_evidence" / name).read_bytes()
        assert len(content) == length
        assert hashlib.sha256(content).hexdigest() == sha256
