# ruff: noqa: D103, EM101, EM102, PLR0913, TRY003
"""Generate and verify the bounded, offline Phase 5 replay contract.

The tracked XML files are deliberately small derived excerpts, not copies of the
original SEC documents.  This module verifies each excerpt under its own content
identity, parses it through the production filing-XBRL adapter, and keeps the
verified original-document identity separate in every generated evidence case.
It performs no acquisition and has no network code.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml

from mortgage_servicing_dashboard.financial_discovery import (
    FinancialFieldRegistry,
    RawFilingFactLocator,
    discover_retained_document_fields,
)

_EXPECTED_INDEX_SHA256 = "e9b984f54a3f47df60526a894f1cb103ac8856eb9bd21972dc098fb019b52b72"
_EXPECTED_FIXTURE_COUNT = 90
_EXPECTED_FIXTURE_BYTES = 1_668_729
_EXPECTED_B_CASES = 160
_EXPECTED_C_CASES = 13
_REPLAY_REPRESENTATION = "BOUNDED_DERIVED_REPLAY_EXCERPT"
_REPLAY_CAPTURE = "offline_bounded_xbrl_replay_excerpt"
_ORIGINAL_REPRESENTATION = "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
_ORIGINAL_CAPTURE = "edgartools_attachment_text_utf8"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COHORT_A_IDS = ("tfc", "wfc", "pfsi", "rkt")
_COHORT_B_IDS = ("tfc", "wfc", "jpm", "bac", "usb", "pfsi", "rkt", "uwmc", "ritm", "ldi")
_FINANCIAL_AUTHORITY_KEYS = {
    "amount",
    "authoritative_observations",
    "display_value",
    "exact_value",
    "financial_value",
    "normalized_decimal_string",
    "normalized_value",
    "proposed_normalized_value",
    "raw_display_string",
    "raw_value",
    "reported_value",
    "value",
}
_UNIVERSE_EVIDENCE_DETAIL_KEYS = {
    "context_id",
    "context_ref",
    "locator",
    "normalized_decimal_string",
    "raw_display_string",
    "raw_value",
    "scale",
    "source_locator",
    "source_locators",
    "unit",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _yaml_bytes(payload: object) -> bytes:
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
        width=120,
    ).encode()


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise ValueError(f"{path.name} must contain a string-keyed mapping")
    return cast("dict[str, Any]", loaded)


def _mapping(value: object, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a string-keyed mapping")
    return cast("dict[str, Any]", value)


def _list(value: object, *, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{location} must be a list")
    return value


def _scalar(value: object) -> str | None:
    return None if value is None else str(value)


def _fact_payload(candidate: RawFilingFactLocator) -> dict[str, Any]:
    return {
        "qualified_concept": candidate.qualified_concept,
        "raw_display_string": candidate.raw_value,
        "normalized_decimal_string": str(candidate.normalized_value),
        "source_element_ids": list(candidate.source_element_ids),
        "context_ref": candidate.context_ref,
        "entity_identifier": candidate.entity_identifier,
        "period_type": candidate.period_type.value,
        "period_start": (
            None if candidate.period_start is None else candidate.period_start.isoformat()
        ),
        "period_end": candidate.period_end.isoformat(),
        "dimensions": [
            {"dimension": item.dimension, "member": item.member} for item in candidate.dimensions
        ],
        "unit": candidate.unit,
        "decimals": _scalar(candidate.decimals),
        "scale": str(candidate.scale),
        "source_sign": candidate.source_sign,
        "source_precision": candidate.source_precision,
        "presentation_sign": candidate.presentation_sign.value,
        "source_object_count": candidate.source_object_count,
        "source_locators": list(candidate.source_locators),
        "semantic_fact_count": 1,
    }


def _expected_b_fact(case: dict[str, Any]) -> dict[str, Any]:
    approved = _mapping(case["approved_fact"], location="cohort B approved_fact")
    return {
        "qualified_concept": approved["qualified_concept"],
        "raw_display_string": approved["raw_display_string"],
        "normalized_decimal_string": approved["normalized_decimal_string"],
        "source_element_ids": approved["source_element_ids"],
        "context_ref": approved["context_ref"],
        "entity_identifier": approved["entity_identifier"],
        "period_type": approved["period_type"],
        "period_start": approved["period_start"],
        "period_end": approved["period_instant"],
        "dimensions": approved["dimensions"],
        "unit": approved["unit"],
        "decimals": _scalar(approved["decimals"]),
        "scale": approved["scale"],
        "source_sign": approved["source_sign"],
        "source_precision": approved["source_precision"],
        "presentation_sign": approved["presentation_sign"],
        "source_object_count": approved["source_object_count"],
        "source_locators": approved["source_locators"],
        "semantic_fact_count": approved["semantic_fact_count"],
    }


def _verified_fixture(root: Path, entry: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    fixture = _mapping(entry["fixture"], location="fixture index fixture")
    relative = Path(str(fixture["path"]))
    path = (root / relative).resolve()
    fixture_root = (root / "tests" / "fixtures" / "phase5" / "replay").resolve()
    if not path.is_relative_to(fixture_root):
        raise ValueError("replay fixture path escapes the governed fixture root")
    payload = path.read_bytes()
    if len(payload) != int(fixture["byte_length"]) or _sha256(payload) != fixture["sha256"]:
        raise ValueError(f"replay fixture identity mismatch: {entry['fixture_id']}")
    label = (
        f"BOUNDED DERIVED REPLAY EXCERPT; NOT ORIGINAL SEC DOCUMENT BYTES; "
        f"fixture={entry['fixture_id']}"
    ).encode()
    if label not in payload[:1024]:
        raise ValueError("replay fixture is missing its bounded-derived label")
    if (
        fixture["representation"] != _REPLAY_REPRESENTATION
        or fixture["capture_method"] != _REPLAY_CAPTURE
    ):
        raise ValueError("replay fixture representation is not governed")
    return payload, fixture


def _verify_index(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = root / "tests" / "fixtures" / "phase5" / "replay-index.v1.yaml"
    raw = path.read_bytes()
    if _sha256(raw) != _EXPECTED_INDEX_SHA256:
        raise ValueError("Phase 5 replay index hash differs from the reviewed recording")
    index = _load_yaml(path)
    entries = [
        _mapping(item, location="fixture index entry")
        for item in _list(index.get("fixtures"), location="fixture index fixtures")
    ]
    if len(entries) != _EXPECTED_FIXTURE_COUNT or int(index["fixture_count"]) != len(entries):
        raise ValueError("Phase 5 replay fixture count differs")
    by_id: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for entry in entries:
        fixture_id = str(entry["fixture_id"])
        if fixture_id in by_id:
            raise ValueError("Phase 5 replay fixture identifiers are not unique")
        _, fixture = _verified_fixture(root, entry)
        total_bytes += int(fixture["byte_length"])
        original = _mapping(entry["original"], location="fixture original")
        original_url = urlsplit(str(original["source_url"]))
        accession = str(entry["accession"])
        cik = str(entry["cik"]).zfill(10)
        expected_path = (
            f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{original['document']}"
        )
        if (
            original["representation"] != _ORIGINAL_REPRESENTATION
            or original["capture_method"] != _ORIGINAL_CAPTURE
            or _SHA256.fullmatch(str(original["sha256"])) is None
            or original_url.scheme != "https"
            or original_url.hostname != "www.sec.gov"
            or original_url.path != expected_path
            or not _list(original["locators"], location="original locators")
            or original["sha256"] == fixture["sha256"]
            or int(original["byte_length"]) <= int(fixture["byte_length"])
        ):
            raise ValueError("original and replay fixture identities are not distinct")
        by_id[fixture_id] = entry
    if total_bytes != _EXPECTED_FIXTURE_BYTES:
        raise ValueError("Phase 5 replay fixture byte total differs")
    return index, by_id


def _discover_candidate(
    *,
    root: Path,
    entry: dict[str, Any],
    registry: FinancialFieldRegistry,
    mapping_id: str,
) -> RawFilingFactLocator:
    payload, fixture = _verified_fixture(root, entry)
    original = _mapping(entry["original"], location="fixture original")
    period = date.fromisoformat(str(entry["report_period"]))
    discoveries = discover_retained_document_fields(
        payload,
        issuer_id=str(entry["issuer_id"]),
        cik=str(entry["cik"]).zfill(10),
        evidence_id=str(fixture["sha256"]),
        accession_number=str(entry["accession"]),
        source_document=str(original["document"]),
        source_url=str(original["source_url"]),
        form=str(entry["form"]),
        filed=date.fromisoformat(str(entry["filed"])),
        registry=registry,
    )
    selected = tuple(item for item in discoveries if item.mapping.mapping_id == mapping_id)
    if len(selected) != 1:
        raise ValueError(
            f"replay mapping discovery count differs: {entry['fixture_id']} {mapping_id}"
        )
    candidates = tuple(item for item in selected[0].candidates if item.period_end == period)
    original_locators = {str(item) for item in _list(original["locators"], location="locators")}
    original_locator_stems = {item.rsplit(";occurrence=", 1)[0] for item in original_locators}
    if len(candidates) > 1:
        candidates = tuple(
            candidate
            for candidate in candidates
            if {item.rsplit(";occurrence=", 1)[0] for item in candidate.source_locators}
            <= original_locator_stems
        )
    if len(candidates) != 1:
        raise ValueError(
            f"replay exact-period candidate count differs: {entry['fixture_id']} {mapping_id}"
        )
    candidate = candidates[0]
    replay_locator_stems = {item.rsplit(";occurrence=", 1)[0] for item in candidate.source_locators}
    if not replay_locator_stems <= original_locator_stems:
        raise ValueError(
            "parsed replay locator is absent from original-document recording: "
            f"{entry['fixture_id']} {mapping_id} {candidate.source_locators}"
        )
    return candidate


def _original_payload(entry: dict[str, Any]) -> dict[str, Any]:
    original = _mapping(entry["original"], location="fixture original")
    return {
        "document": original["document"],
        "source_url": original["source_url"],
        "sha256": original["sha256"],
        "byte_length": int(original["byte_length"]),
        "representation": original["representation"],
        "capture_method": original["capture_method"],
        "locators": list(original["locators"]),
    }


def _fixture_payload(entry: dict[str, Any]) -> dict[str, Any]:
    fixture = _mapping(entry["fixture"], location="fixture")
    return {
        "fixture_id": entry["fixture_id"],
        "path": fixture["path"],
        "sha256": fixture["sha256"],
        "byte_length": int(fixture["byte_length"]),
        "representation": fixture["representation"],
        "capture_method": fixture["capture_method"],
        "derivation": "SELECTED_CONCEPT_FACTS_WITH_REFERENCED_CONTEXTS_UNITS_AND_NAMESPACES",
        "is_original_sec_document": False,
    }


def _evidence_record(
    *,
    entry: dict[str, Any],
    case_id: str,
    mapping_id: str,
    field_id: str,
    classification: str,
    metric_kind: str,
    candidate: RawFilingFactLocator,
) -> dict[str, Any]:
    return {
        "evidence_case_id": case_id,
        "cohort": entry["cohort"],
        "issuer_id": entry["issuer_id"],
        "ticker": entry["ticker"],
        "cik": str(entry["cik"]).zfill(10),
        "mapping_id": mapping_id,
        "field_id": field_id,
        "classification": classification,
        "metric_kind": metric_kind,
        "filing": {
            "accession": entry["accession"],
            "form": entry["form"],
            "filed": entry["filed"],
            "report_period": entry["report_period"],
        },
        "original_document": _original_payload(entry),
        "replay_fixture": _fixture_payload(entry),
        "locator_policy": (
            "Original-document locators are authoritative; fact locators below are replay-excerpt "
            "document-order locators and may have a different occurrence suffix."
        ),
        "fact": _fact_payload(candidate),
    }


def _build_evidence_cases(
    root: Path,
    index: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    b_manifest = _load_yaml(root / "config" / "phase5" / "cohort-b-sources.v1.yaml")
    b_registry = FinancialFieldRegistry.from_yaml(
        root / "config" / "phase5" / "financial_fields.v1.yaml"
    )
    b_by_accession = {
        str(item["accession"]): item for item in entries.values() if item["cohort"] == "B"
    }
    records: list[dict[str, Any]] = []
    for raw_case in _list(b_manifest["cases"], location="cohort B cases"):
        case = _mapping(raw_case, location="cohort B case")
        entry = b_by_accession.get(str(case["accession"]))
        if entry is None:
            raise ValueError("cohort B case has no replay fixture")
        original = _mapping(entry["original"], location="fixture original")
        source = _mapping(case["edgartools_source"], location="cohort B source")
        if (
            original["document"] != case["source_document"]
            or original["source_url"] != case["source_url"]
            or original["sha256"] != source["sha256"]
            or int(original["byte_length"]) != int(source["byte_length"])
        ):
            raise ValueError("cohort B original source identity differs from replay index")
        candidate = _discover_candidate(
            root=root,
            entry=entry,
            registry=b_registry,
            mapping_id=str(case["mapping_id"]),
        )
        parsed_fact = _fact_payload(candidate)
        if parsed_fact != _expected_b_fact(case):
            raise ValueError(
                f"cohort B replay fact differs from reviewed expectation: {case['case_id']}"
            )
        records.append(
            _evidence_record(
                entry=entry,
                case_id=str(case["case_id"]),
                mapping_id=str(case["mapping_id"]),
                field_id=str(case["field_id"]),
                classification=str(case["classification"]),
                metric_kind=str(case["field_id"]).upper(),
                candidate=candidate,
            )
        )
    if len(records) != _EXPECTED_B_CASES:
        raise ValueError("cohort B evidence-case count differs")

    c_config = _load_yaml(root / "config" / "phase5" / "registry-evidence-fields.v1.yaml")
    c_registry = FinancialFieldRegistry.from_yaml(
        root / "config" / "phase5" / "registry-evidence-fields.v1.yaml"
    )
    c_by_issuer = {
        str(item["issuer_id"]): item for item in entries.values() if item["cohort"] == "C"
    }
    for raw_mapping in _list(c_config["mappings"], location="registry evidence mappings"):
        mapping = _mapping(raw_mapping, location="registry evidence mapping")
        entry = c_by_issuer.get(str(mapping["issuer_id"]))
        if entry is None:
            raise ValueError("registry evidence mapping has no replay fixture")
        candidate = _discover_candidate(
            root=root,
            entry=entry,
            registry=c_registry,
            mapping_id=str(mapping["mapping_id"]),
        )
        records.append(
            _evidence_record(
                entry=entry,
                case_id=str(mapping["evidence_case_id"]),
                mapping_id=str(mapping["mapping_id"]),
                field_id=str(mapping["metric_id"]),
                classification=str(mapping["classification"]),
                metric_kind=str(mapping["metric_kind"]),
                candidate=candidate,
            )
        )
    if len(records) != _EXPECTED_B_CASES + _EXPECTED_C_CASES:
        raise ValueError("combined Phase 5 evidence-case count differs")
    by_case = {str(item["evidence_case_id"]): item for item in records}
    if len(by_case) != len(records):
        raise ValueError("Phase 5 evidence-case identifiers are not unique")
    index_path = root / "tests" / "fixtures" / "phase5" / "replay-index.v1.yaml"
    result = {
        "schema_version": "phase5-evidence-cases-v1",
        "as_of": "2026-08-18",
        "generation": "REAL_SEC_FILING_XBRL_PARSER_OVER_BOUNDED_DERIVED_REPLAY_EXCERPTS",
        "fixture_index": {
            "path": "../../tests/fixtures/phase5/replay-index.v1.yaml",
            "sha256": _sha256(index_path.read_bytes()),
            "fixture_count": int(index["fixture_count"]),
            "fixture_byte_length": _EXPECTED_FIXTURE_BYTES,
        },
        "original_document_policy": (
            "Original full-document SHA-256, length, SEC URL, accession, document, and locators "
            "are provenance only; replay fixture hashes identify the separately tracked excerpts."
        ),
        "record_count": len(records),
        "phase_b_record_count": _EXPECTED_B_CASES,
        "phase_c_record_count": _EXPECTED_C_CASES,
        "records": records,
    }
    return result, by_case


def _build_replay_manifest(
    root: Path,
    entries: dict[str, dict[str, Any]],
    evidence_by_case: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = deepcopy(_load_yaml(root / "config" / "phase5" / "cohort-b-sources.v1.yaml"))
    result["manifest_version"] = "phase5-cohort-b-replay-v1"
    result["replay_only"] = True
    result["replay_fixture_index"] = {
        "path": "../../tests/fixtures/phase5/replay-index.v1.yaml",
        "sha256": _EXPECTED_INDEX_SHA256,
        "fixture_count": _EXPECTED_FIXTURE_COUNT,
        "fixture_byte_length": _EXPECTED_FIXTURE_BYTES,
    }
    result["representation_policy"] = {
        "eligible_source": "Bounded tracked replay excerpts parsed offline for regression only.",
        "replay": _REPLAY_REPRESENTATION,
        "original": _ORIGINAL_REPRESENTATION,
        "identity_rule": "REPLAY_AND_ORIGINAL_SHA256_AND_LENGTH_MUST_REMAIN_DISTINCT",
    }
    by_accession = {
        str(item["accession"]): item for item in entries.values() if item["cohort"] == "B"
    }
    cases: list[dict[str, Any]] = []
    for raw_case in _list(result["cases"], location="replay cases"):
        case = _mapping(raw_case, location="replay case")
        entry = by_accession[str(case["accession"])]
        fixture = _mapping(entry["fixture"], location="replay fixture")
        original_source = deepcopy(_mapping(case["edgartools_source"], location="source"))
        original_source.update(
            {
                "document": case["source_document"],
                "source_url": case["source_url"],
                "source_locators": deepcopy(case["approved_fact"]["source_locators"]),
            }
        )
        case["original_edgartools_source"] = original_source
        case["edgartools_source"] = {
            "acquisition_status": "OFFLINE_REPLAY_VERIFIED",
            "sha256": fixture["sha256"],
            "byte_length": int(fixture["byte_length"]),
            "representation": fixture["representation"],
            "capture_method": fixture["capture_method"],
            "retention_location": f"content-sha256://{fixture['sha256']}",
            "retrieved_at": original_source["retrieved_at"],
            "integrity_verified": True,
            "fixture_path": fixture["path"],
            "is_original_sec_document": False,
        }
        evidence = evidence_by_case[str(case["case_id"])]
        fact = deepcopy(_mapping(evidence["fact"], location="evidence fact"))
        fact["original_label"] = case["approved_fact"]["original_label"]
        fact["period_instant"] = fact.pop("period_end")
        case["approved_fact"] = fact
        case["source_route"] = "OFFLINE_BOUNDED_REPLAY_OF_SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS"
        case["publication_authority"] = {
            "route": "OFFLINE_REGRESSION_REPLAY_ONLY",
            "original_sec_lineage_reviewed": True,
            "fixture_is_not_original_sec_document": True,
        }
        cases.append(case)
    result["cases"] = cases
    return result


def _reject_keys(value: object, forbidden: set[str], *, location: str) -> None:
    if isinstance(value, dict):
        overlap = forbidden & set(value)
        if overlap:
            raise ValueError(f"{location} embeds manual financial authority: {sorted(overlap)}")
        for nested in value.values():
            _reject_keys(nested, forbidden, location=location)
    elif isinstance(value, list):
        for nested in value:
            _reject_keys(nested, forbidden, location=location)


def _case_ids(value: object, *, location: str) -> list[str]:
    case_ids = [str(item) for item in _list(value, location=location)]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{location} must contain distinct evidence-case identifiers")
    return case_ids


def _evidence_reference(case_ids: list[str]) -> dict[str, Any]:
    return {
        "evidence_case_ids": case_ids,
        "evidence_cases_path": "evidence-cases.v1.yaml",
    }


def _validate_evidence_reference(
    record: dict[str, Any],
    *,
    company: dict[str, Any],
) -> None:
    filing = _mapping(record["filing"], location="evidence filing")
    original = _mapping(record["original_document"], location="evidence original document")
    fact = _mapping(record["fact"], location="evidence fact")
    required_filing = ("accession", "form", "filed", "report_period")
    required_original = ("document", "source_url", "sha256", "byte_length", "locators")
    if any(not filing.get(key) for key in required_filing):
        raise ValueError("evidence-case filing identity is incomplete")
    if any(not original.get(key) for key in required_original):
        raise ValueError("evidence-case original source identity is incomplete")
    source_url = urlsplit(str(original["source_url"]))
    if source_url.scheme != "https" or source_url.hostname != "www.sec.gov":
        raise ValueError("evidence-case source URL is not an official SEC URL")
    if (
        _SHA256.fullmatch(str(original["sha256"])) is None
        or int(original["byte_length"]) <= 0
        or not _list(original["locators"], location="original source locators")
        or not _list(fact["source_locators"], location="parsed fact locators")
    ):
        raise ValueError("evidence-case source hash, length, or exact locator is incomplete")
    if (
        record["issuer_id"] != company["id"]
        or record["ticker"] != company["ticker"]
        or str(record["cik"]).zfill(10) != str(company["cik"]).zfill(10)
    ):
        raise ValueError("company identity differs from its evidence-case reference")


def _filing_reference_ids(records: list[dict[str, Any]]) -> list[str]:
    by_accession: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        filing = _mapping(record["filing"], location="evidence filing")
        by_accession.setdefault(str(filing["accession"]), []).append(record)
    ordered = sorted(
        by_accession.values(),
        key=lambda group: str(_mapping(group[0]["filing"], location="filing")["report_period"]),
    )
    return [
        str(
            min(
                group,
                key=lambda item: (
                    item["classification"] == "CORE_FINANCIAL",
                    str(item["evidence_case_id"]),
                ),
            )["evidence_case_id"]
        )
        for group in ordered
    ]


def _latest_filing_case_ids(
    records: list[dict[str, Any]],
    *,
    annual_only: bool = False,
) -> list[str]:
    eligible = [
        record
        for record in records
        if not annual_only
        or str(_mapping(record["filing"], location="evidence filing")["form"]).startswith("10-K")
    ]
    if not eligible:
        return []
    latest_period = max(
        str(_mapping(record["filing"], location="evidence filing")["report_period"])
        for record in eligible
    )
    return sorted(
        str(record["evidence_case_id"])
        for record in eligible
        if _mapping(record["filing"], location="evidence filing")["report_period"] == latest_period
    )


def _build_company_registry(
    metadata_companies: list[Any],
    *,
    cohort: str,
    evidence_by_case: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    companies: list[dict[str, Any]] = []
    for raw_company in metadata_companies:
        company = deepcopy(_mapping(raw_company, location=f"cohort {cohort} company"))
        case_ids = _case_ids(
            company.pop("evidence_case_ids"),
            location=f"{company.get('id')} evidence_case_ids",
        )
        referenced = [evidence_by_case.get(case_id) for case_id in case_ids]
        if any(record is None for record in referenced):
            raise ValueError("company evidence-case reference does not resolve")
        referenced_records = [cast("dict[str, Any]", record) for record in referenced]
        if any(record["cohort"] != cohort for record in referenced_records):
            raise ValueError("company evidence-case reference resolves to the wrong cohort")
        cohort_records = [
            record
            for record in evidence_by_case.values()
            if record["cohort"] == cohort and record["issuer_id"] == company["id"]
        ]
        for record in cohort_records:
            _validate_evidence_reference(record, company=company)
        if not cohort_records:
            raise ValueError("company has no parser-derived evidence cases")
        corporate_actions = _list(company.get("corporate_actions"), location="corporate_actions")
        if corporate_actions:
            raise ValueError("corporate-action narratives require dedicated tracked locators")
        forms = sorted(
            {
                str(_mapping(record["filing"], location="evidence filing")["form"])
                for record in cohort_records
            }
        )
        company["identity_evidence"] = _evidence_reference(case_ids)
        company["most_recent_filing"] = _evidence_reference(
            _latest_filing_case_ids(cohort_records),
        )
        annual_ids = _latest_filing_case_ids(cohort_records, annual_only=True)
        if annual_ids:
            company["latest_annual"] = _evidence_reference(annual_ids)
        else:
            company["latest_annual"] = {
                "status": "NOT_TRACKED_FOR_BOUNDED_REPLAY",
                "evidence_case_ids": [],
                "evidence_cases_path": "evidence-cases.v1.yaml",
            }
        company["qualifying_forms"] = forms
        company["filing_coverage_evidence"] = _evidence_reference(
            _filing_reference_ids(cohort_records),
        )
        company["material_servicing_evidence"] = _evidence_reference(case_ids)
        company["latest_servicing_upb_or_msr"] = {
            "kind": "+".join(sorted({str(record["metric_kind"]) for record in referenced_records})),
            **_evidence_reference(case_ids),
        }
        companies.append(company)
    return companies


def _build_cohort_b_universe(
    root: Path,
    evidence_cases: dict[str, Any],
    evidence_by_case: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metadata = _load_yaml(root / "config" / "phase5" / "cohort-b-universe.metadata.v1.yaml")
    _reject_keys(metadata, _FINANCIAL_AUTHORITY_KEYS, location="cohort B universe metadata")
    companies = _build_company_registry(
        _list(metadata["companies"], location="cohort B metadata companies"),
        cohort="B",
        evidence_by_case=evidence_by_case,
    )
    if tuple(str(company["id"]) for company in companies) != _COHORT_B_IDS:
        raise ValueError("cohort B universe identity or order differs")
    result = {
        "schema_version": "phase5-cohort-b-universe-v1",
        "as_of": metadata["as_of"],
        "status": "END_TO_END_PUBLISHED",
        "issuer_count": len(companies),
        "bank_count": sum(company["classification"] == "bank" for company in companies),
        "nonbank_count": sum(company["classification"] == "nonbank" for company in companies),
        "source_manifest": "cohort-b-replay.v1.yaml",
        "source_manifest_case_count": _EXPECTED_B_CASES,
        "latest_value_policy": (
            "Resolve only parser-derived generated evidence-case IDs; this registry contains "
            "no raw, normalized, or exact financial value."
        ),
        "evidence_cases": {
            "path": "evidence-cases.v1.yaml",
            "sha256": _sha256(_yaml_bytes(evidence_cases)),
            "record_count": int(evidence_cases["record_count"]),
            "phase_b_record_count": int(evidence_cases["phase_b_record_count"]),
        },
        "companies": companies,
        "unsupported_corporate_action_notes": metadata["unsupported_corporate_action_notes"],
    }
    _reject_keys(result, _UNIVERSE_EVIDENCE_DETAIL_KEYS, location="cohort B universe")
    return result


def _build_cohort_a_universe(
    cohort_b: dict[str, Any],
    *,
    evidence_cases: dict[str, Any],
) -> dict[str, Any]:
    companies = [
        deepcopy(company)
        for company in _list(cohort_b["companies"], location="cohort B companies")
        if company["id"] in _COHORT_A_IDS
    ]
    companies.sort(key=lambda company: _COHORT_A_IDS.index(str(company["id"])))
    for company in companies:
        company["onboarding_status"] = "A_END_TO_END_PUBLISHED"
    if tuple(str(company["id"]) for company in companies) != _COHORT_A_IDS:
        raise ValueError("cohort A universe identity or order differs")
    result = {
        "schema_version": "phase5-cohort-a-universe-v1",
        "as_of": cohort_b["as_of"],
        "status": "END_TO_END_PUBLISHED_FROM_BOUNDED_REPLAY",
        "issuer_count": len(companies),
        "bank_count": 2,
        "nonbank_count": 2,
        "source_manifest": "cohort-b-replay.v1.yaml",
        "source_manifest_case_count": len(companies) * 8 * 2,
        "latest_value_policy": cohort_b["latest_value_policy"],
        "evidence_cases": {
            "path": "evidence-cases.v1.yaml",
            "sha256": _sha256(_yaml_bytes(evidence_cases)),
            "phase_a_reference_count": len(companies) * 8 * 2,
        },
        "companies": companies,
        "unsupported_corporate_action_notes": cohort_b["unsupported_corporate_action_notes"],
    }
    _reject_keys(result, _UNIVERSE_EVIDENCE_DETAIL_KEYS, location="cohort A universe")
    return result


def _build_supported_universe(
    root: Path,
    evidence_cases: dict[str, Any],
    evidence_by_case: dict[str, dict[str, Any]],
    cohort_b: dict[str, Any],
) -> dict[str, Any]:
    metadata = deepcopy(
        _load_yaml(root / "config" / "phase5" / "supported-universe.metadata.v1.yaml")
    )
    metadata["schema_version"] = "phase5-supported-universe-v1"
    metadata["status"] = "EVIDENCE_VETTED"
    metadata["generation"] = "SEC_ONLY_METADATA_PLUS_PARSER_DERIVED_EVIDENCE_CASE_REFERENCES"
    evidence_bytes = _yaml_bytes(evidence_cases)
    metadata["evidence_cases"] = {
        "path": "evidence-cases.v1.yaml",
        "sha256": _sha256(evidence_bytes),
        "record_count": int(evidence_cases["record_count"]),
        "phase_c_record_count": int(evidence_cases["phase_c_record_count"]),
    }
    audit = _mapping(metadata["phase_c_acquisition_audit"], location="phase C audit")
    audit["bounded_replay_representation"] = _REPLAY_REPRESENTATION
    audit["bounded_replay_fixture_index_sha256"] = _EXPECTED_INDEX_SHA256
    base = _mapping(metadata["base_registry"], location="base registry")
    base["sha256"] = _sha256(_yaml_bytes(cohort_b))
    metadata["expansion_companies"] = _build_company_registry(
        _list(metadata["expansion_companies"], location="expansion companies"),
        cohort="C",
        evidence_by_case=evidence_by_case,
    )
    _reject_keys(metadata, _UNIVERSE_EVIDENCE_DETAIL_KEYS, location="supported universe")
    return metadata


def _validate_cohort_a_sources(
    root: Path,
    evidence_by_case: dict[str, dict[str, Any]],
) -> None:
    manifest = _load_yaml(root / "config" / "phase5" / "cohort-a-sources.v1.yaml")
    cases = [
        _mapping(item, location="cohort A source case")
        for item in _list(manifest["cases"], location="cohort A source cases")
    ]
    if len(cases) != len(_COHORT_A_IDS) * 8 * 2:
        raise ValueError("cohort A source case count differs")
    for case in cases:
        evidence = evidence_by_case.get(str(case["case_id"]))
        if evidence is None or evidence["issuer_id"] not in _COHORT_A_IDS:
            raise ValueError("cohort A source case has no tracked replay evidence")
        if _expected_b_fact(case) != evidence["fact"]:
            raise ValueError("cohort A source fact differs from tracked parser replay")


def _validate_authority_inputs(root: Path) -> None:
    phase5 = root / "config" / "phase5"
    governed_paths = {
        *phase5.glob("*universe*.yaml"),
        *phase5.glob("*registry*.yaml"),
        phase5 / "financial_fields.v1.yaml",
    }
    for path in sorted(governed_paths):
        payload = _load_yaml(path)
        _reject_keys(payload, _FINANCIAL_AUTHORITY_KEYS, location=path.name)
        if "universe" in path.name:
            _reject_keys(payload, _UNIVERSE_EVIDENCE_DETAIL_KEYS, location=path.name)


def build_outputs(root: Path) -> dict[Path, bytes]:
    _validate_authority_inputs(root)
    index, entries = _verify_index(root)
    evidence_cases, evidence_by_case = _build_evidence_cases(root, index, entries)
    _validate_cohort_a_sources(root, evidence_by_case)
    replay_manifest = _build_replay_manifest(root, entries, evidence_by_case)
    cohort_b = _build_cohort_b_universe(root, evidence_cases, evidence_by_case)
    cohort_a = _build_cohort_a_universe(cohort_b, evidence_cases=evidence_cases)
    supported_universe = _build_supported_universe(
        root,
        evidence_cases,
        evidence_by_case,
        cohort_b,
    )
    phase5 = root / "config" / "phase5"
    return {
        phase5 / "evidence-cases.v1.yaml": _yaml_bytes(evidence_cases),
        phase5 / "cohort-b-replay.v1.yaml": _yaml_bytes(replay_manifest),
        phase5 / "cohort-b-universe.v1.yaml": _yaml_bytes(cohort_b),
        phase5 / "cohort-a-universe.v1.yaml": _yaml_bytes(cohort_a),
        phase5 / "supported-universe.v1.yaml": _yaml_bytes(supported_universe),
    }


def write_outputs(root: Path) -> None:
    for path, payload in build_outputs(root).items():
        path.write_bytes(payload)


def check_outputs(root: Path) -> None:
    mismatches: list[str] = []
    for path, expected in build_outputs(root).items():
        if not path.exists() or path.read_bytes() != expected:
            mismatches.append(path.relative_to(root).as_posix())
    if mismatches:
        raise ValueError("generated Phase 5 replay outputs differ: " + ", ".join(mismatches))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.write:
        write_outputs(root)
    else:
        check_outputs(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
