from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from mortgage_servicing_dashboard.domain import (
    ComparisonInput,
    ObservationState,
    assess_comparability,
)
from mortgage_servicing_dashboard.regulatory import (
    BankRegulatoryAdapter,
    FfiecCdrBulkAdapter,
    FrY9cBulkAdapter,
    NicIdentityCrosswalk,
    RegulatoryDataError,
    RegulatoryReportingScope,
    RegulatorySourceFamily,
    aggregate_regulatory_metric,
    load_regulatory_config,
)

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "config" / "regulatory" / "regulatory_mappings.v1.yaml"
_FIXTURES = _ROOT / "tests" / "fixtures" / "phase2" / "regulatory"


def _config_payload() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(_CONFIG.read_text(encoding="utf-8")))


def _write_config(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_versioned_reporters_and_nic_crosswalk_keep_scopes_separate() -> None:
    config = load_regulatory_config(_CONFIG)
    report_date = date(2026, 6, 30)
    parent = config.reporter(
        rssd_id="001074156",
        source_family=RegulatorySourceFamily.FR_Y9C,
        report_date=report_date,
    )
    assert config.version == "regulatory-mappings-1.0.0"
    assert parent.rssd_id == "1074156"
    assert parent.ticker == "TFC"
    assert parent.cik == "0000092230"
    assert parent.reporting_scope is RegulatoryReportingScope.BANK_HOLDING_COMPANY_REGULATORY
    assert parent.nic_profile_url == "https://www.ffiec.gov/npw/Institution/Profile/1074156"

    bank = config.reporter(
        rssd_id="852320",
        source_family=RegulatorySourceFamily.FFIEC_CDR_CALL,
        report_date=report_date,
    )
    assert bank.parent_rssd_id == parent.rssd_id
    assert bank.reporting_scope is RegulatoryReportingScope.DEPOSITORY_INSTITUTION_REGULATORY
    with pytest.raises(RegulatoryDataError, match="cannot report FFIEC_CDR_CALL"):
        config.reporter(
            rssd_id=parent.rssd_id,
            source_family=RegulatorySourceFamily.FFIEC_CDR_CALL,
            report_date=report_date,
        )
    with pytest.raises(RegulatoryDataError, match="not effective"):
        config.reporter(
            rssd_id=bank.rssd_id,
            source_family=RegulatorySourceFamily.FFIEC_CDR_CALL,
            report_date=date(2019, 12, 6),
        )
    with pytest.raises(RegulatoryDataError, match="not uniquely configured"):
        config.reporter(
            rssd_id="999",
            source_family=RegulatorySourceFamily.FR_Y9C,
            report_date=report_date,
        )

    crosswalk = NicIdentityCrosswalk.from_csv((_FIXTURES / "nic_crosswalk.csv").read_bytes())
    assert len(crosswalk.records) == 2
    assert crosswalk.resolve(as_of=report_date, ticker="tfc") == crosswalk.resolve(
        as_of=report_date,
        cik="92230",
    )
    resolved_bank = crosswalk.resolve(as_of=report_date, rssd_id="000852320")
    assert resolved_bank.parent_rssd_id == parent.rssd_id
    assert resolved_bank.ticker is None
    with pytest.raises(RegulatoryDataError, match="requires RSSD"):
        crosswalk.resolve(as_of=report_date)
    with pytest.raises(RegulatoryDataError, match="exactly one"):
        crosswalk.resolve(as_of=report_date, ticker="TFC", rssd_id="852320")


def test_ffiec_and_y9c_adapters_return_exact_native_scope_facts() -> None:
    config = load_regulatory_config(_CONFIG)
    report_date = date(2026, 6, 30)
    call_facts = FfiecCdrBulkAdapter(config).parse(
        (_FIXTURES / "ffiec_cdr_call_2026q2.txt").read_bytes(),
        rssd_id="852320",
        report_date=report_date,
    )
    assert len(call_facts) == 5
    assert {item.schedule for item in call_facts} == {"RC-S", "RI", "RC-M"}
    assert {item.series for item in call_facts} == {
        "RCFDB804",
        "RCFDB805",
        "RIADB492",
        "RCFD3164",
        "RCFDA590",
    }
    recourse = next(item for item in call_facts if item.series == "RCFDB804")
    assert recourse.raw_value == "125000"
    assert recourse.normalized_value == Decimal(125000000)
    assert recourse.scale == "thousands"
    assert recourse.revision == "2026-07-31T18:15:00Z"
    assert recourse.reporting_scope is RegulatoryReportingScope.DEPOSITORY_INSTITUTION_REGULATORY
    assert recourse.source_family is RegulatorySourceFamily.FFIEC_CDR_CALL
    assert "RSSD 852320" in recourse.locator
    assert len(recourse.fact_id) == 64

    call_total = aggregate_regulatory_metric(
        call_facts,
        metric_id="servicing_for_others_upb",
        required_components=(
            "one_to_four_family_with_recourse",
            "one_to_four_family_without_recourse",
        ),
    )
    assert call_total.value == Decimal(400000000)
    assert call_total.reporting_scope is RegulatoryReportingScope.DEPOSITORY_INSTITUTION_REGULATORY
    assert len(call_total.input_fact_ids) == 2

    holding_facts = FrY9cBulkAdapter(config).parse(
        (_FIXTURES / "fr_y9c_2026q2.txt").read_bytes(),
        rssd_id="1074156",
        report_date=report_date,
    )
    assert len(holding_facts) == 5
    assert (
        BankRegulatoryAdapter(config).parse(
            (_FIXTURES / "fr_y9c_2026q2.txt").read_bytes(),
            source_family=RegulatorySourceFamily.FR_Y9C,
            rssd_id="1074156",
            report_date=report_date,
        )
        == holding_facts
    )
    assert {item.schedule for item in holding_facts} == {"HC-S", "HI", "HC-M"}
    assert all(
        item.reporting_scope is RegulatoryReportingScope.BANK_HOLDING_COMPANY_REGULATORY
        for item in holding_facts
    )
    assert next(
        item for item in holding_facts if item.series == "BHCK6438"
    ).normalized_value == Decimal(525000000)
    holding_total = aggregate_regulatory_metric(
        holding_facts,
        metric_id="servicing_for_others_upb",
        required_components=(
            "one_to_four_family_with_recourse",
            "one_to_four_family_without_recourse",
        ),
    )
    assert holding_total.value == Decimal(1000000000)
    scope_assessment = assess_comparability(
        ComparisonInput(
            metric_id="servicing_for_others_upb",
            metric_version="1.0.0",
            reporting_scope="tfc_bhc_regulatory",
            period_days=None,
            currency="USD",
            unit="USD",
            methodology="FR_Y9C",
            observation_state=ObservationState.REPORTED_ACTUAL,
            portfolio_population="bank_holding_company_consolidated",
        ),
        ComparisonInput(
            metric_id="servicing_for_others_upb",
            metric_version="1.0.0",
            reporting_scope="tfc_consolidated_residential_mortgage_servicing",
            period_days=None,
            currency="USD",
            unit="USD",
            methodology="SEC_FILING_EXHIBIT",
            observation_state=ObservationState.REPORTED_ACTUAL,
            portfolio_population="residential_servicing_for_others_and_bank_owned",
        ),
    )
    assert scope_assessment.status.value == "not_comparable"
    assert "reporting scopes differ" in scope_assessment.reasons
    mixed = (call_facts[0], replace(call_facts[1], reporting_scope=holding_total.reporting_scope))
    with pytest.raises(RegulatoryDataError, match="cannot mix"):
        aggregate_regulatory_metric(
            mixed,
            metric_id="servicing_for_others_upb",
            required_components=(
                "one_to_four_family_with_recourse",
                "one_to_four_family_without_recourse",
            ),
        )


def test_regulatory_parsers_fail_closed_on_identity_and_numeric_ambiguity() -> None:
    config = load_regulatory_config(_CONFIG)
    adapter = FfiecCdrBulkAdapter(config)
    report_date = date(2026, 6, 30)
    source = (_FIXTURES / "ffiec_cdr_call_2026q2.txt").read_bytes()
    with pytest.raises(RegulatoryDataError, match="exactly one requested"):
        adapter.parse(source, rssd_id="852320", report_date=date(2026, 3, 31))
    header, row, *_ = source.decode().splitlines()
    duplicate = f"{header}\n{row}\n{row}\n".encode()
    with pytest.raises(RegulatoryDataError, match="exactly one requested"):
        adapter.parse(duplicate, rssd_id="852320", report_date=report_date)
    bad_value = source.replace(b"\t125000\t", b"\tnot-a-number\t", 1)
    with pytest.raises(RegulatoryDataError, match="not an exact decimal"):
        adapter.parse(bad_value, rssd_id="852320", report_date=report_date)
    identity_only = (
        b"ID_RSSD\tReporting Period End Date\tLast Date/Time Submission Updated On\n"
        b"852320\t20260630\t2026-07-31T18:15:00Z\n"
    )
    with pytest.raises(RegulatoryDataError, match="no configured servicing facts"):
        adapter.parse(identity_only, rssd_id="852320", report_date=report_date)
    with pytest.raises(RegulatoryDataError, match="missing identity columns"):
        adapter.parse(b"wrong\theader\n1\t2\n", rssd_id="852320", report_date=report_date)
    with pytest.raises(RegulatoryDataError, match="not UTF-8"):
        adapter.parse(b"\xff", rssd_id="852320", report_date=report_date)
    with pytest.raises(RegulatoryDataError, match="invalid header"):
        adapter.parse(b"", rssd_id="852320", report_date=report_date)
    too_many = (
        b"ID_RSSD\tReporting Period End Date\tLast Date/Time Submission Updated On\tRCFDB804\n"
        b"852320\t20260630\trevision\t1\textra\n"
    )
    with pytest.raises(RegulatoryDataError, match="too many fields"):
        adapter.parse(too_many, rssd_id="852320", report_date=report_date)
    bad_date = source.replace(b"\t20260630\t", b"\tnot-a-date\t", 1)
    with pytest.raises(RegulatoryDataError, match="report date is invalid"):
        adapter.parse(bad_date, rssd_id="852320", report_date=report_date)
    blank_revision = source.replace(b"2026-07-31T18:15:00Z", b"", 1)
    with pytest.raises(RegulatoryDataError, match="must be nonempty text"):
        adapter.parse(blank_revision, rssd_id="852320", report_date=report_date)
    with pytest.raises(RegulatoryDataError, match="RSSD must contain"):
        adapter.parse(source, rssd_id="not-rssd", report_date=report_date)
    with pytest.raises(RegulatoryDataError, match="requires facts"):
        aggregate_regulatory_metric((), metric_id="x", required_components=("a",))
    with pytest.raises(RegulatoryDataError, match="missing, duplicated"):
        aggregate_regulatory_metric(
            (
                next(
                    item
                    for item in adapter.parse(source, rssd_id="852320", report_date=report_date)
                    if item.series == "RCFDB804"
                ),
            ),
            metric_id="servicing_for_others_upb",
            required_components=("one_to_four_family_with_recourse", "missing"),
        )


def test_regulatory_configuration_and_crosswalk_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(RegulatoryDataError, match="could not be loaded"):
        load_regulatory_config(missing)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(RegulatoryDataError, match="must be a mapping"):
        load_regulatory_config(invalid)
    with pytest.raises(RegulatoryDataError, match="missing required columns"):
        NicIdentityCrosswalk.from_csv(b"RSSD_ID,NAME\n1,Example\n")
    with pytest.raises(RegulatoryDataError, match="at least one record"):
        NicIdentityCrosswalk(())
    overlapping = (
        (_FIXTURES / "nic_crosswalk.csv")
        .read_text(encoding="utf-8")
        .replace(
            "852320,Truist Bank",
            "1074156,Truist Financial Corporation Duplicate",
        )
        .replace(",,,1074156,2019-12-07", ",TFC,0000092230,,2019-12-07")
    )
    with pytest.raises(RegulatoryDataError, match="overlapping"):
        NicIdentityCrosswalk.from_csv(overlapping.encode())

    config = load_regulatory_config(_CONFIG)
    with pytest.raises(RegulatoryDataError, match="source family"):
        replace(config, sources=()).source(RegulatorySourceFamily.FR_Y9C)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: cast("list[dict[str, Any]]", payload["sources"])[0].update(
                {"delimiter": "pipe"}
            ),
            "unsupported regulatory delimiter",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["sources"])[0].update(
                {"report_date_format": "%m/%d/%Y"}
            ),
            "unsupported regulatory report date format",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["reporters"])[1].update(
                {"rssd_id": "1074156"}
            ),
            "duplicate RSSD",
        ),
        (
            lambda payload: cast("list[Any]", payload["sources"]).pop(),
            "define every supported source family",
        ),
        (
            lambda payload: cast("list[Any]", payload["mappings"]).append(
                cast("list[Any]", payload["mappings"])[0]
            ),
            "duplicate effective item mappings",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["reporters"])[0].update(
                {"reporting_scope": "DEPOSITORY_INSTITUTION_REGULATORY"}
            ),
            "reporter type and scope conflict",
        ),
        (lambda payload: payload.update({"reporters": []}), "reporters must be a nonempty list"),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["reporters"])[0].update(
                {"permitted_source_families": []}
            ),
            "permitted_source_families must be a nonempty list",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["reporters"])[0].update(
                {"cik": "bad"}
            ),
            "CIK must contain",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["reporters"])[0].update(
                {"valid_from": "not-a-date"}
            ),
            "valid_from must be an ISO date",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["mappings"])[0].update(
                {"source_family": "OTHER"}
            ),
            "unsupported value OTHER",
        ),
        (
            lambda payload: cast("list[dict[str, Any]]", payload["mappings"])[0].update(
                {"definition_url": "https://example.test/item"}
            ),
            "official HTTPS host",
        ),
    ],
)
def test_regulatory_config_rejects_ambiguous_controlled_fields(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    payload = _config_payload()
    mutation(payload)
    path = _write_config(tmp_path / "mutated.yaml", payload)
    with pytest.raises(RegulatoryDataError, match=message):
        load_regulatory_config(path)
