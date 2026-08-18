"""Offline validation for the evidence-vetted Phase 5 supported universe."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml

from mortgage_servicing_dashboard.financial_discovery import (
    FinancialFieldRegistry,
    RawFilingFactLocator,
    discover_retained_document_fields,
)

_ROOT = Path(__file__).resolve().parents[2]
_BASE_PATH = _ROOT / "config" / "phase5" / "cohort-b-universe.v1.yaml"
_UNIVERSE_PATH = _ROOT / "config" / "phase5" / "supported-universe.v1.yaml"
_EVIDENCE_PATH = _ROOT / "config" / "phase5" / "evidence-cases.v1.yaml"
_REGISTRY_PATH = _ROOT / "config" / "phase5" / "registry-evidence-fields.v1.yaml"
_INDEX_PATH = _ROOT / "tests" / "fixtures" / "phase5" / "replay-index.v1.yaml"
_EXPECTED_UNIVERSE_HASH = "b6920b7936a3fb5cdc7dabc2a095dbb690b1cbceb7610c133c948a2b2a0495dd"
_EXPECTED_EVIDENCE_HASH = "e03f350aea4dee6f56669b78ae71fc129e8423258a8dd5b2c234cd7a6747b06e"
_BANK_IDS = ("tfc", "wfc", "jpm", "bac", "usb", "c", "pnc", "fitb", "cfg", "key")
_NONBANK_IDS = (
    "pfsi",
    "rkt",
    "uwmc",
    "ritm",
    "ldi",
    "two",
    "chmi",
    "nly",
    "foa",
    "vel",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CIK = re.compile(r"^\d{10}$")


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_float(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_float(nested)


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for nested in value.values():
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_all_keys(nested))
    return keys


def _fact_matches(candidate: RawFilingFactLocator, expected: dict[str, Any]) -> bool:
    return bool(
        candidate.qualified_concept == expected["qualified_concept"]
        and candidate.raw_value == expected["raw_display_string"]
        and str(candidate.normalized_value) == expected["normalized_decimal_string"]
        and list(candidate.source_element_ids) == expected["source_element_ids"]
        and candidate.context_ref == expected["context_ref"]
        and candidate.entity_identifier == expected["entity_identifier"]
        and candidate.period_type.value == expected["period_type"]
        and (None if candidate.period_start is None else candidate.period_start.isoformat())
        == expected["period_start"]
        and candidate.period_end.isoformat() == expected["period_end"]
        and [{"dimension": item.dimension, "member": item.member} for item in candidate.dimensions]
        == expected["dimensions"]
        and candidate.unit == expected["unit"]
        and (None if candidate.decimals is None else str(candidate.decimals))
        == expected["decimals"]
        and str(candidate.scale) == expected["scale"]
        and candidate.source_sign == expected["source_sign"]
        and candidate.source_precision == expected["source_precision"]
        and candidate.presentation_sign.value == expected["presentation_sign"]
        and candidate.source_object_count == expected["source_object_count"]
        and list(candidate.source_locators) == expected["source_locators"]
    )


def test_supported_universe_has_exact_evidence_vetted_counts_and_references() -> None:
    universe = _load_yaml(_UNIVERSE_PATH)
    base = _load_yaml(_BASE_PATH)
    evidence = _load_yaml(_EVIDENCE_PATH)
    assert hashlib.sha256(_UNIVERSE_PATH.read_bytes()).hexdigest() == _EXPECTED_UNIVERSE_HASH
    assert hashlib.sha256(_EVIDENCE_PATH.read_bytes()).hexdigest() == _EXPECTED_EVIDENCE_HASH
    assert universe["schema_version"] == "phase5-supported-universe-v1"
    assert universe["status"] == "EVIDENCE_VETTED"
    assert universe["ranking_claim"] == "NONE"
    assert universe["supported_company_count"] == 20
    assert universe["supported_bank_count"] == universe["supported_nonbank_count"] == 10
    assert tuple(universe["supported_bank_ids"]) == _BANK_IDS
    assert tuple(universe["supported_nonbank_ids"]) == _NONBANK_IDS
    assert evidence["record_count"] == 173
    assert evidence["phase_b_record_count"] == 160
    assert evidence["phase_c_record_count"] == 13

    base_ref = universe["base_registry"]
    assert base_ref["path"] == _BASE_PATH.name
    assert base_ref["company_count"] == 10
    assert hashlib.sha256(_BASE_PATH.read_bytes()).hexdigest() == base_ref["sha256"]
    base_companies = cast("list[dict[str, Any]]", base["companies"])
    expansion = cast("list[dict[str, Any]]", universe["expansion_companies"])
    assert len(base_companies) == len(expansion) == 10
    companies = [*base_companies, *expansion]
    required = {
        "id",
        "legal_name",
        "ticker",
        "cik",
        "classification",
        "current_sec_status",
        "most_recent_filing",
        "qualifying_forms",
        "material_servicing_evidence",
        "latest_servicing_upb_or_msr",
        "expected_scope",
        "corporate_actions",
        "onboarding_status",
        "exclusion_or_risk_notes",
    }
    assert all(required <= company.keys() for company in companies)
    assert len({company["id"] for company in companies}) == 20
    assert len({company["ticker"] for company in companies}) == 20
    assert len({company["cik"] for company in companies}) == 20
    assert all(_CIK.fullmatch(company["cik"]) for company in companies)
    assert all(
        company["current_sec_status"] == "ACTIVE_CURRENT_REGISTRANT" for company in companies
    )
    assert {company["id"] for company in companies if company["classification"] == "bank"} == set(
        _BANK_IDS
    )
    assert {
        company["id"] for company in companies if company["classification"] == "nonbank"
    } == set(_NONBANK_IDS)
    forbidden = {
        "authoritative_observations",
        "exact_value",
        "normalized_value",
        "raw_value",
        "raw_display_string",
        "normalized_decimal_string",
        "context_id",
        "context_ref",
        "locator",
        "source_locator",
        "source_locators",
        "scale",
        "unit",
    }
    assert not forbidden & _all_keys(universe)
    _assert_no_float(universe)


def test_expansion_claims_resolve_only_to_complete_tracked_evidence_cases() -> None:
    universe = _load_yaml(_UNIVERSE_PATH)
    evidence = _load_yaml(_EVIDENCE_PATH)
    records = {
        item["evidence_case_id"]: item for item in cast("list[dict[str, Any]]", evidence["records"])
    }
    expansion = cast("list[dict[str, Any]]", universe["expansion_companies"])
    assert tuple(company["id"] for company in expansion) == _BANK_IDS[5:] + _NONBANK_IDS[5:]
    audit = universe["phase_c_acquisition_audit"]
    assert audit["retrieval_method"] == "PUBLIC_EDGARTOOLS_5_48_CENTRALIZED_ADAPTER"
    assert audit["bounded_replay_fixture_count"] == 10
    assert audit["prior_seven_filing_coverage_status"] == "NOT_TRACKED_FOR_BOUNDED_REPLAY"

    for company in expansion:
        cik = cast("str", company["cik"])
        assert company["qualifying_forms"] == ["10-Q"]
        assert company["onboarding_status"] == "C_REGISTRY_EVIDENCE_VERIFIED_NOT_PUBLISHED"
        assert company["latest_annual"]["status"] == "NOT_TRACKED_FOR_BOUNDED_REPLAY"
        assert company["latest_annual"]["evidence_case_ids"] == []

        material = company["material_servicing_evidence"]
        latest_value = company["latest_servicing_upb_or_msr"]
        assert material["evidence_case_ids"] == latest_value["evidence_case_ids"]
        assert material["evidence_cases_path"] == _EVIDENCE_PATH.name
        assert latest_value["evidence_cases_path"] == _EVIDENCE_PATH.name
        assert material["evidence_case_ids"]
        assert all(
            case_id in records and records[case_id]["issuer_id"] == company["id"]
            for case_id in material["evidence_case_ids"]
        )
        for claim_name in (
            "identity_evidence",
            "most_recent_filing",
            "filing_coverage_evidence",
            "material_servicing_evidence",
            "latest_servicing_upb_or_msr",
        ):
            claim = company[claim_name]
            assert claim["evidence_cases_path"] == _EVIDENCE_PATH.name
            assert claim["evidence_case_ids"]
            for case_id in claim["evidence_case_ids"]:
                record = records[case_id]
                filing = cast("dict[str, Any]", record["filing"])
                original = cast("dict[str, Any]", record["original_document"])
                assert record["issuer_id"] == company["id"]
                assert record["cik"] == cik
                assert all(filing[key] for key in ("accession", "form", "filed", "report_period"))
                assert original["source_url"].startswith("https://www.sec.gov/Archives/")
                assert _SHA256.fullmatch(original["sha256"])
                assert original["byte_length"] > 0
                assert original["locators"]


def test_phase_c_all_selected_facts_parse_from_distinct_bounded_artifacts() -> None:
    evidence = _load_yaml(_EVIDENCE_PATH)
    registry = FinancialFieldRegistry.from_yaml(_REGISTRY_PATH)
    records = [
        item for item in cast("list[dict[str, Any]]", evidence["records"]) if item["cohort"] == "C"
    ]
    assert len(records) == 13
    parsed_cache: dict[str, Any] = {}
    for record in records:
        original = cast("dict[str, Any]", record["original_document"])
        fixture = cast("dict[str, Any]", record["replay_fixture"])
        assert original["sha256"] != fixture["sha256"]
        assert original["byte_length"] > fixture["byte_length"]
        assert original["representation"] == "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
        assert fixture["representation"] == "BOUNDED_DERIVED_REPLAY_EXCERPT"
        assert original["locators"]
        fixture_path = _ROOT / fixture["path"]
        payload = fixture_path.read_bytes()
        assert len(payload) == fixture["byte_length"]
        assert hashlib.sha256(payload).hexdigest() == fixture["sha256"]
        fixture_id = cast("str", fixture["fixture_id"])
        if fixture_id not in parsed_cache:
            filing = cast("dict[str, Any]", record["filing"])
            parsed_cache[fixture_id] = discover_retained_document_fields(
                payload,
                issuer_id=record["issuer_id"],
                cik=record["cik"],
                evidence_id=fixture["sha256"],
                accession_number=filing["accession"],
                source_document=original["document"],
                source_url=original["source_url"],
                form=filing["form"],
                filed=date.fromisoformat(filing["filed"]),
                registry=registry,
            )
        discoveries = parsed_cache[fixture_id]
        discovery = next(
            item for item in discoveries if item.mapping.mapping_id == record["mapping_id"]
        )
        matches = [
            candidate
            for candidate in discovery.candidates
            if _fact_matches(candidate, cast("dict[str, Any]", record["fact"]))
        ]
        assert len(matches) == 1
        original_stems = {item.rsplit(";occurrence=", 1)[0] for item in original["locators"]}
        assert {
            item.rsplit(";occurrence=", 1)[0] for item in matches[0].source_locators
        } <= original_stems
    assert len(parsed_cache) == 10


def test_phase_c_fixture_and_expected_fact_tampering_is_rejected() -> None:
    evidence = _load_yaml(_EVIDENCE_PATH)
    record = next(
        item for item in cast("list[dict[str, Any]]", evidence["records"]) if item["cohort"] == "C"
    )
    fixture = cast("dict[str, Any]", record["replay_fixture"])
    original = cast("dict[str, Any]", record["original_document"])
    filing = cast("dict[str, Any]", record["filing"])
    expected = cast("dict[str, Any]", record["fact"])
    payload = (_ROOT / fixture["path"]).read_bytes()
    needle = expected["raw_display_string"].encode()
    assert needle in payload
    element_id = expected["source_element_ids"][0].encode()
    element_position = payload.index(b'id="' + element_id + b'"')
    value_position = payload.index(needle, element_position, element_position + 4096)
    tampered = (
        payload[:value_position]
        + b"999999999999999999999999"
        + payload[value_position + len(needle) :]
    )
    assert hashlib.sha256(tampered).hexdigest() != fixture["sha256"]
    discoveries = discover_retained_document_fields(
        tampered,
        issuer_id=record["issuer_id"],
        cik=record["cik"],
        evidence_id=hashlib.sha256(tampered).hexdigest(),
        accession_number=filing["accession"],
        source_document=original["document"],
        source_url=original["source_url"],
        form=filing["form"],
        filed=date.fromisoformat(filing["filed"]),
        registry=FinancialFieldRegistry.from_yaml(_REGISTRY_PATH),
    )
    discovery = next(
        item for item in discoveries if item.mapping.mapping_id == record["mapping_id"]
    )
    assert not any(_fact_matches(candidate, expected) for candidate in discovery.candidates)


def test_unsupported_corporate_action_narratives_are_removed_fail_closed() -> None:
    universe = _load_yaml(_UNIVERSE_PATH)
    expansion = {company["id"]: company for company in universe["expansion_companies"]}
    assert all(not company["corporate_actions"] for company in expansion.values())
    assert expansion["two"]["servicing_role"] == "PUBLIC_MSR_OWNER_NOT_ASSERTED_AS_DIRECT_OPERATOR"
    assert "no servicing-operator population is asserted" in expansion["two"]["expected_scope"]
    assert "No earlier or external operator scope" in expansion["foa"]["exclusion_or_risk_notes"]

    for company_id in ("chmi", "nly"):
        assert expansion[company_id]["servicing_role"] == (
            "PUBLIC_MSR_OWNER_NOT_ASSERTED_AS_DIRECT_OPERATOR"
        )
    unsupported = universe["unsupported_corporate_action_notes"]
    assert unsupported["status"] == "NOT_DISCLOSED"
    assert set(unsupported["issuer_ids"]) == {"two", "foa", "rkt", "ritm", "ldi", "onit", "ghld"}
    assert "tracked bounded replay locators" in unsupported["reason"]


def test_unsupported_exclusions_do_not_create_unproven_company_or_action_claims() -> None:
    universe = _load_yaml(_UNIVERSE_PATH)
    assert "additional_exclusions" not in universe
    assert "inherited_exclusions_and_boundaries" not in universe
    assert "ghld" not in universe["supported_nonbank_ids"]
    assert universe["unsupported_corporate_action_notes"]["status"] == "NOT_DISCLOSED"
