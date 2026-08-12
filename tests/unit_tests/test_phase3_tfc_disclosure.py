"""TFC Phase 3 evidence, disclosure-map, and exact-recipe contracts."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from mortgage_servicing_dashboard.sources import (
    RecordedEvidenceAcquirer,
    RecordedSourceDefinition,
    StageARecordedDocumentParser,
    _TableRows,
)

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "config" / "phase3" / "tfc_sources.yaml"
_MANIFEST = _ROOT / "config" / "recorded_evidence" / "phase3" / "tfc" / "manifest.v1.yaml"
_PERIODS = {"2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"}
_PHASE3_ADDITIONS = {
    "capitalized_servicing_rate_on_additions",
    "delinquency_30_plus_upb_rate",
    "delinquency_60_plus_upb_rate",
    "delinquency_90_plus_upb_rate",
    "foreclosure_upb_rate",
    "msr_fair_value_bps_of_related_upb",
    "msr_fair_value_inputs_or_assumptions_change",
    "msr_fair_value_multiple_of_related_upb",
    "msr_hedging_result",
    "msr_realization_passage_time_and_other",
    "reo_upb",
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


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _source_definitions() -> dict[str, RecordedSourceDefinition]:
    config = _load(_CONFIG)
    return {
        key: RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=_ROOT / "config",
        )
        for key, payload in cast("dict[str, dict[str, Any]]", config["sources"]).items()
    }


def test_tfc_phase3_original_response_manifest_hashes_and_typed_sources() -> None:
    """Every canonical byte body must match its immutable manifest identity."""
    manifest = _load(_MANIFEST)
    sources = cast("list[dict[str, Any]]", manifest["sources"])

    assert len(sources) == 9
    assert len({source["evidence_id"] for source in sources}) == 9
    for manifest_source in sources:
        path = _MANIFEST.parent / str(manifest_source["path"])
        content = path.read_bytes()
        assert len(content) == int(manifest_source["byte_length"])
        assert hashlib.sha256(content).hexdigest() == manifest_source["sha256"]
        assert manifest_source["representation"] == "ORIGINAL_HTTP_RESPONSE"
        assert manifest_source["capture_method"] == "sec_http_get"
        assert str(manifest_source["url"]).startswith("https://")
        assert manifest_source["locators"]

    definitions = _source_definitions()
    assert set(definitions) == {
        "tfc_2025q3_10q",
        "tfc_2025q3_qps",
        "tfc_2025q4_10k",
        "tfc_2025q4_qps",
        "tfc_2026q1_10q",
        "tfc_2026q1_qps",
        "tfc_2026q2_10q",
        "tfc_2026q2_qps",
    }
    acquirer = RecordedEvidenceAcquirer()
    for source_definition in definitions.values():
        acquired = acquirer.acquire(source_definition)
        assert acquired.sha256 == source_definition.content_sha256
        assert acquired.byte_length == source_definition.byte_length
        assert source_definition.representation == "ORIGINAL_HTTP_RESPONSE"


def test_tfc_assessment_expands_to_exactly_53_metrics_by_four_periods() -> None:
    """The disclosure map cannot silently omit a metric-period cell."""
    config = _load(_CONFIG)
    catalog = _load(_ROOT / "config" / "metrics" / "catalog.yaml")
    catalog_ids = {str(metric["id"]) for metric in cast("list[dict[str, Any]]", catalog["metrics"])}
    expected = catalog_ids | _PHASE3_ADDITIONS
    cells = cast(
        "dict[str, dict[str, dict[str, Any]]]",
        config["eligible_source_assessment"]["cells"],
    )
    eligible_sets = cast(
        "dict[str, list[str]]",
        config["eligible_source_assessment"]["eligible_source_sets"],
    )

    assert len(expected) == 53
    assert set(cells) == expected
    assert sum(len(periods) for periods in cells.values()) == 212
    for metric_id, periods in cells.items():
        assert set(periods) == _PERIODS, metric_id
        for period, assessment in periods.items():
            status = assessment["assessment_status"]
            assert status in {"DISCLOSURE_FOUND", "CHECKED_COMPLETE", "SOURCE_NOT_CHECKED"}
            if assessment["result_state"] == "NOT_DISCLOSED":
                assert status == "CHECKED_COMPLETE"
                assert assessment["checked_source_keys"] == eligible_sets[period]
                assert len(assessment["checked_locators"]) == len(eligible_sets[period])
                assert assessment["reason_code"]
            else:
                assert status == "DISCLOSURE_FOUND"


def test_tfc_current_period_rows_and_rollforwards_are_exact_decimal() -> None:
    """Independent expected values must reconcile exact retained table text."""
    definitions = _source_definitions()
    parser = StageARecordedDocumentParser()
    qps_expected = {
        "tfc_2025q3_qps": ("221,274", "58,396", "279,670"),
        "tfc_2025q4_qps": ("228,383", "57,583", "285,966"),
        "tfc_2026q1_qps": ("233,870", "57,386", "291,256"),
        "tfc_2026q2_qps": ("240,764", "57,894", "298,658"),
    }
    labels = (
        "Loans serviced for others",
        "Bank-owned loans serviced",
        "Total servicing portfolio",
    )
    for source_key, qps_values in qps_expected.items():
        content = definitions[source_key].fixture_path.read_bytes()
        current = tuple(
            parser.extract_row_values(content=content, raw_label=label)[0] for label in labels
        )
        assert current == qps_values

    rollforwards = {
        "tfc_2025q3_10q": (3430, 182, 160, 0, -16, 232, 3524),
        "tfc_2025q4_10k": (3431, 339, 239, 0, 39, 324, 3724),
        "tfc_2026q1_10q": (3724, 131, 85, None, 13, 85, 3868),
        "tfc_2026q2_10q": (3724, 275, 197, None, 39, 189, 4046),
    }
    ending_labels = {
        "tfc_2025q3_10q": "Residential MSRs, carrying value, September 30",
        "tfc_2025q4_10k": "Residential MSRs, carrying value, December 31",
        "tfc_2026q1_10q": "Residential MSRs, carrying value, March 31",
        "tfc_2026q2_10q": "Residential MSRs, carrying value, June 30",
    }

    def exact_source_decimal(raw: str) -> Decimal:
        negative = raw.startswith("(") and raw.endswith(")")
        normalized = raw.strip("()").replace(",", "")
        value = Decimal(normalized)
        return -value if negative else value

    for source_key, rollforward_values in rollforwards.items():
        source = definitions[source_key]
        text = source.fixture_path.read_text(encoding="utf-8")
        rows = _TableRows()
        rows.feed(text)
        raw_rows = {row[0]: row for row in rows.rows}
        beginning, acquired, additions, sales, _change, _realization, ending = rollforward_values
        assert exact_source_decimal(
            raw_rows["Residential MSRs, carrying value, January 1"][2]
        ) == Decimal(beginning)
        assert exact_source_decimal(raw_rows["Acquired"][1]) == Decimal(acquired)
        assert exact_source_decimal(raw_rows["Additions"][1]) == Decimal(additions)
        if sales is None:
            assert "Sales" not in raw_rows
            sales_magnitude = Decimal(0)
        else:
            assert raw_rows["Sales"][1] == "—"
            sales_magnitude = Decimal(sales)
        change_label = next(
            label
            for label in raw_rows
            if label.startswith("Change in fair value due to changes in valuation inputs")
        )
        change_value = exact_source_decimal(raw_rows[change_label][1])
        raw_realization = raw_rows[
            "Realization of expected net servicing cash flows, passage of time, and other"
        ][1]
        realization_magnitude = abs(exact_source_decimal(raw_realization))
        ending_value = exact_source_decimal(raw_rows[ending_labels[source_key]][2])
        reconciled = (
            Decimal(beginning)
            + Decimal(acquired)
            + Decimal(additions)
            - sales_magnitude
            + change_value
            - realization_magnitude
        )
        assert reconciled == Decimal(ending) == ending_value


def test_every_tfc_phase3_source_parses_to_valid_exact_candidates() -> None:
    """Every reported assessment cell must have one valid configured candidate."""
    config = _load(_CONFIG)
    universe = _load(_ROOT / "config" / "universe.yaml")
    company = next(
        company
        for company in cast("list[dict[str, Any]]", universe["companies"])
        if company["id"] == "tfc"
    )
    expected_counts = {
        "tfc_2025q3_10q": 9,
        "tfc_2025q3_qps": 5,
        "tfc_2025q4_10k": 8,
        "tfc_2025q4_qps": 5,
        "tfc_2026q1_10q": 8,
        "tfc_2026q1_qps": 5,
        "tfc_2026q2_10q": 7,
        "tfc_2026q2_qps": 5,
    }
    parser = StageARecordedDocumentParser()
    candidate_cells: set[tuple[str, str]] = set()
    for key, definition in _source_definitions().items():
        acquired = RecordedEvidenceAcquirer().acquire(definition)
        candidates = parser.parse(
            source=definition,
            content=acquired.content,
            company=company,
            quarters=cast("list[dict[str, Any]]", config["quarters"]),
        )
        assert len(candidates) == expected_counts[key]
        assert all(candidate.company_id == "tfc" for candidate in candidates)
        assert all(isinstance(candidate.normalized_value, Decimal) for candidate in candidates)
        assert all(candidate.evidence_id == f"evidence:{key}" for candidate in candidates)
        assert all(candidate.evidence_locator for candidate in candidates)
        candidate_cells.update(
            (candidate.metric_id, candidate.period_end.isoformat()) for candidate in candidates
        )

    assessment_cells = cast(
        "dict[str, dict[str, dict[str, Any]]]",
        config["eligible_source_assessment"]["cells"],
    )
    reported_cells = {
        (metric_id, period)
        for metric_id, periods in assessment_cells.items()
        for period, assessment in periods.items()
        if assessment["result_state"] == "REPORTED"
    }
    assert len(candidate_cells) == 52
    assert len(reported_cells) == 34
    assert reported_cells < candidate_cells
    assert len(candidate_cells - reported_cells) == 18


def test_tfc_msr_multiple_derivations_are_reproducible() -> None:
    """Derived MSR multiples use exact same-date reported inputs only."""
    inputs = (
        ("3524", "221274", "0.015926", "159.26"),
        ("3724", "228383", "0.016306", "163.06"),
        ("3868", "233870", "0.016539", "165.39"),
        ("4046", "240764", "0.016805", "168.05"),
    )
    for fair_value, related_upb, expected_multiple, expected_bps in inputs:
        multiple = (Decimal(fair_value) / Decimal(related_upb)).quantize(
            Decimal("0.000001"),
            rounding=ROUND_HALF_EVEN,
        )
        bps = (Decimal(fair_value) / Decimal(related_upb) * Decimal(10000)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_EVEN,
        )
        assert multiple == Decimal(expected_multiple)
        assert bps == Decimal(expected_bps)
