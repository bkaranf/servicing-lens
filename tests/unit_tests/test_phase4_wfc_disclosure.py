"""Offline contracts for the WFC Phase 4a disclosure-map evidence package."""

from __future__ import annotations

import hashlib
import json
import re
import socket
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from mortgage_servicing_dashboard.metric_engine import load_metric_catalog
from mortgage_servicing_dashboard.sources import (
    PublicSourceError,
    StageARecordedDocumentParser,
)

_ROOT = Path(__file__).parents[2]
_CONFIG_PATH = _ROOT / "config" / "phase4" / "wfc_sources.yaml"
_EVIDENCE_ROOT = _ROOT / "config" / "recorded_evidence" / "phase4" / "wfc"
_MANIFEST_PATH = _EVIDENCE_ROOT / "manifest.v1.yaml"
_MAP_PATH = _ROOT / "proposals" / "disclosure_map" / "WFC.md"
_PERIODS = {"Q3_2025", "Q4_2025", "Q1_2026", "Q2_2026"}


@pytest.fixture(autouse=True)
def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        message = "WFC disclosure-map tests must not use sockets"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def _yaml_mapping(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def _manifest_sources() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", _yaml_mapping(_MANIFEST_PATH)["sources"])


def _source_bytes(source_key: str) -> bytes:
    source = next(item for item in _manifest_sources() if item["source_key"] == source_key)
    return (_EVIDENCE_ROOT / str(source["path"])).read_bytes()


def test_wfc_identity_periods_and_exact_sec_accessions() -> None:
    config = _yaml_mapping(_CONFIG_PATH)
    identity = config["identity"]

    assert identity == {
        "company_id": "wfc",
        "legal_name": "Wells Fargo & Company",
        "sec_conformed_name": "WELLS FARGO & COMPANY/MN",
        "ticker": "WFC",
        "exchange": "NYSE",
        "cik": "0000072971",
        "fiscal_year_end": "12-31",
        "fiscal_calendar": "CALENDAR_YEAR",
        "bhc_rssd": "1120754",
        "depository_name": "Wells Fargo Bank, National Association",
        "depository_rssd": "451965",
        "identity_evidence": identity["identity_evidence"],
    }
    assert set(config["quarters"]) == _PERIODS

    submissions = json.loads(_source_bytes("wfc_sec_submissions"))
    assert submissions["name"] == "WELLS FARGO & COMPANY/MN"
    assert str(submissions["cik"]).zfill(10) == "0000072971"
    assert submissions["tickers"][0] == "WFC"
    assert submissions["exchanges"][0] == "NYSE"
    assert submissions["fiscalYearEnd"] == "1231"

    accessions = {
        (item["fiscal_period"], item["form"], item["accession"])
        for item in _manifest_sources()
        if "accession" in item
    }
    assert accessions == {
        ("2025Q3", "8-K", "0000072971-25-000239"),
        ("2025Q3", "10-Q", "0000072971-25-000253"),
        ("2025Q4", "8-K", "0000072971-26-000009"),
        ("2025Q4", "10-K", "0000072971-26-000133"),
        ("2026Q1", "8-K", "0000072971-26-000213"),
        ("2026Q1", "10-Q", "0000072971-26-000217"),
        ("2026Q2", "8-K", "0000072971-26-000288"),
        ("2026Q2", "10-Q", "0000072971-26-000302"),
    }
    for source in _manifest_sources():
        if "accession" in source:
            assert str(source["accession"]).replace("-", "") in str(source["url"])


def test_wfc_evidence_is_exact_content_addressed_original_http() -> None:
    sources = _manifest_sources()
    assert len(sources) == len({item["source_key"] for item in sources}) == 18
    assert len({item["sha256"] for item in sources}) == 18

    manifested_paths: set[Path] = set()
    for source in sources:
        digest = str(source["sha256"])
        relative = Path(str(source["path"]))
        path = _EVIDENCE_ROOT / relative
        manifested_paths.add(path.resolve())
        content = path.read_bytes()
        assert relative.as_posix() == f"sha256/{digest[:2]}/{digest}.bin"
        assert path.name == f"{digest}.bin"
        assert len(content) == source["byte_length"]
        assert hashlib.sha256(content).hexdigest() == digest
        assert source["representation"] == "ORIGINAL_HTTP_RESPONSE"
        assert source["capture_method"] == "sec_http_get"
        assert str(source["url"]).startswith(("https://www.sec.gov/", "https://data.sec.gov/"))

    retained = {item.resolve() for item in _EVIDENCE_ROOT.rglob("*.bin")}
    assert retained == manifested_paths


def test_wfc_matrix_matches_the_exact_53_metric_catalog() -> None:
    config = _yaml_mapping(_CONFIG_PATH)
    cells = cast("dict[str, dict[str, Any]]", config["eligible_source_assessment"]["cells"])
    catalog = load_metric_catalog(
        _ROOT / "config" / "metrics" / "catalog.yaml",
        extension_paths=(_ROOT / "config" / "metrics" / "phase3_deepening.v1.yaml",),
    )
    catalog_ids = {item.metric_id for item in catalog.definitions}

    assert set(cells) == catalog_ids
    assert len(cells) == 53
    assert all(set(item["periods"]) == _PERIODS for item in cells.values())
    actual_counts = Counter(state for item in cells.values() for state in item["periods"].values())
    assert actual_counts == Counter({"R": 31, "D": 8, "ND": 173})
    configured_counts = config["eligible_source_assessment"]["classification_counts"]
    assert actual_counts == Counter(configured_counts)
    assert configured_counts["SOURCE_NOT_CHECKED"] == 0
    assert (
        sum(actual_counts.values()) == config["eligible_source_assessment"]["expected_cells"] == 212
    )


def test_wfc_scope_and_fail_closed_boundary_traps() -> None:
    config = _yaml_mapping(_CONFIG_PATH)
    cells = config["eligible_source_assessment"]["cells"]
    disclosures = config["disclosures"]

    assert cells["servicing_revenue"]["periods"] == {
        "Q3_2025": "R",
        "Q4_2025": "ND",
        "Q1_2026": "R",
        "Q2_2026": "R",
    }
    assert "Q4_2025" not in disclosures["servicing_revenue"]["period_evidence"]
    assert cells["msr_fair_value_inputs_or_assumptions_change"]["periods"] == dict.fromkeys(
        _PERIODS, "ND"
    )
    assert cells["servicing_fee_income"]["periods"] == dict.fromkeys(_PERIODS, "ND")
    assert cells["foreclosure_upb"]["periods"] == dict.fromkeys(_PERIODS, "ND")
    assert cells["foreclosure_upb_rate"]["periods"] == dict.fromkeys(_PERIODS, "ND")

    scopes = {item["id"]: item for item in config["reporting_scopes"]}
    servicing = scopes["wfc_consolidated_residential_mortgage_servicing"]
    entities = {item["id"]: item for item in config["reporting_entities"]}
    home_lending = scopes["wfc_home_lending_owned_loan_metrics"]
    assert servicing["exclusions"] == ["residential mortgage loans subserviced for others"]
    assert entities["wfc_home_lending_operating_unit"]["type"] == "DISCLOSED_OPERATING_UNIT"
    assert home_lending["reporting_entity_id"] == "wfc_home_lending_operating_unit"
    assert home_lending["category"] == "CONSOLIDATED_COMPANY"
    assert home_lending["canonical_servicing_scope"] is False
    assert home_lending["exclusions"] == [
        "government-insured-or-guaranteed loans",
        "loans held for sale",
        "nonaccrual loans",
    ]


def test_wfc_regulatory_gate_names_native_reporters_and_expected_items() -> None:
    config = _yaml_mapping(_CONFIG_PATH)
    regulatory = config["regulatory_research_expectations"]
    assert regulatory["status"] == "REGULATORY_RESEARCH_NOT_ACQUIRED"
    sources = {item["family"]: item for item in regulatory["sources"]}
    assert sources["FR_Y9C"]["reporter_rssd"] == "1120754"
    assert sources["FFIEC_CDR_CALL"]["reporter_rssd"] == "451965"
    assert {item["series"] for item in sources["FR_Y9C"]["expected_items"]} == {
        "BHCKB804",
        "BHCKB805",
        "BHCKF699",
        "BHCKB492",
        "BHCK3164",
        "BHCK6438",
    }
    assert {item["series"] for item in sources["FFIEC_CDR_CALL"]["expected_items"]} == {
        "RCFDB804",
        "RCFDB805",
        "RCFDF699",
        "RIADB492",
        "RCFD3164",
        "RCFDA590",
    }
    research = regulatory["research_only_not_grid_eligible"]
    assert len(research) == 2
    assert set(research[0]["source_items"]) == {"BHCKB492", "RIADB492"}
    assert "mortgages, credit cards, and other financial assets" in research[0]["reason"]
    assert set(research[1]["source_items"]) == {"BHCKF699", "RCFDF699", "RCONF699"}
    assert "owned_msr_and_msl" in research[1]["reason"]


def test_wfc_retained_rows_prove_format_change_and_rounding_boundary() -> None:
    parser = StageARecordedDocumentParser()
    q1_supplement = _source_bytes("wfc_2026q1_supplement")
    q2_supplement = _source_bytes("wfc_2026q2_supplement")
    q2_periodic = _source_bytes("wfc_2026q2_10q")

    q1_third_party = parser.extract_row_values(
        content=q1_supplement,
        raw_label="Third party mortgage loans serviced ($ in billions, period-end) (8)",
    )
    assert q1_third_party[0] == "386.6"
    with pytest.raises(PublicSourceError, match="row was not found"):
        parser.extract_row_values(
            content=q1_supplement,
            raw_label="Mortgage servicing rights (MSR) carrying value (period-end)",
        )

    preliminary = parser.extract_row_values(
        content=q2_supplement,
        raw_label="Third party mortgage loans serviced ($ in billions, period-end) (6)",
    )
    final = parser.extract_row_values(
        content=q2_periodic,
        raw_label="Loans serviced for others, unpaid principal balance ($ in billions)",
    )
    assert preliminary[0] == "361.4"
    assert final[0] == "362"


def test_wfc_markdown_matrix_is_complete_and_matches_machine_config() -> None:
    config = _yaml_mapping(_CONFIG_PATH)
    expected = config["eligible_source_assessment"]["cells"]
    markdown = _MAP_PATH.read_text(encoding="utf-8")
    rows = re.findall(
        r"^\| `([^`]+)` \| ([A-Z_]+) \| ([A-Z_]+) \| ([A-Z_]+) \| ([A-Z_]+) \|",
        markdown,
        flags=re.MULTILINE,
    )
    actual = {
        metric_id: dict(zip(("Q3_2025", "Q4_2025", "Q1_2026", "Q2_2026"), statuses, strict=True))
        for metric_id, *statuses in rows
    }
    assert len(actual) == 53
    assert actual == {metric_id: item["periods"] for metric_id, item in expected.items()}


def test_wfc_research_config_has_no_authoritative_numeric_values() -> None:
    payload = _yaml_mapping(_CONFIG_PATH)
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
