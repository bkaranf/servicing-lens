# ruff: noqa: C901, D103, EM101, EM102, PLR2004, TRY003, TRY004
"""Build an exact filing-XBRL manifest from a verified external acquisition snapshot.

This utility never acquires data. It verifies the supplied snapshot and every
content-addressed object before deterministically extracting only reviewed mappings.
Bulk SEC/cache material remains outside the repository; the emitted YAML contains
bounded lineage, expectations, and exact strings only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml

from mortgage_servicing_dashboard.financial_discovery import (
    FinancialClassification,
    FinancialFieldRegistry,
    RawFilingFactLocator,
    discover_retained_document_fields,
)

if __package__:
    from scripts.phase5_replay import check_outputs
else:
    from phase5_replay import check_outputs

_GOVERNED_SNAPSHOTS = {
    "a2a4cfe81cf2b3ccc23fd2b79cd906120aeeb86c6c5ac0c1874fdf1845fdb297": {
        "manifest_version": "phase5-cohort-a-v1",
        "issuer_ids": ("tfc", "wfc", "pfsi", "rkt"),
    },
    "4494c97fa6cd8dfe6bffcf9f8fdc55eaf8211ac0ef0c81106299d60868c5dae2": {
        "manifest_version": "phase5-cohort-b-v1",
        "issuer_ids": ("tfc", "wfc", "jpm", "bac", "usb", "pfsi", "rkt", "uwmc", "ritm", "ldi"),
    },
}
_REPRESENTATION = "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8"
_REVIEW_STATE = "FILING_XBRL_LINEAGE_VERIFIED"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_path(evidence_root: Path, digest: str) -> Path:
    return evidence_root / digest[:2] / f"{digest}.bin"


def _required_mapping(value: object, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a string-keyed mapping")
    return cast("dict[str, Any]", value)


def _required_list(value: object, *, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be a list")
    return cast("list[Any]", value)


def _verified_object(evidence_root: Path, source: dict[str, Any]) -> bytes:
    digest = str(source["sha256"])
    expected_length = int(source["byte_length"])
    path = _object_path(evidence_root, digest)
    payload = path.read_bytes()
    if len(payload) != expected_length or _sha256(payload) != digest:
        raise ValueError(f"retained object failed integrity verification: {digest}")
    return payload


def _fiscal_quarter(form: str, period_end: date) -> str:
    if form.startswith("10-K"):
        return "FY"
    markers = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}
    try:
        return markers[period_end.month]
    except KeyError as error:
        raise ValueError("quarterly filing period does not end on a fiscal quarter") from error


def _scalar(value: object) -> str | None:
    return None if value is None else str(value)


def _approved_fact(
    candidate: RawFilingFactLocator,
    *,
    original_label: str,
) -> dict[str, Any]:
    return {
        "qualified_concept": candidate.qualified_concept,
        "original_label": original_label,
        "raw_display_string": candidate.raw_value,
        "normalized_decimal_string": str(candidate.normalized_value),
        "source_element_ids": list(candidate.source_element_ids),
        "context_ref": candidate.context_ref,
        "entity_identifier": candidate.entity_identifier,
        "period_type": candidate.period_type.value,
        "period_start": (
            None if candidate.period_start is None else candidate.period_start.isoformat()
        ),
        "period_instant": candidate.period_end.isoformat(),
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


def _case(
    *,
    issuer: dict[str, Any],
    filing: dict[str, Any],
    mapping: Any,
    candidate: RawFilingFactLocator,
) -> dict[str, Any]:
    period_end = date.fromisoformat(str(filing["report_period"]))
    primary = _required_mapping(filing["primary_document"], location="primary_document")
    source = _required_mapping(filing["financial_document"], location="financial_document")
    classification = mapping.classification
    case_id = "-".join(
        (
            str(issuer["company_id"]),
            period_end.isoformat(),
            str(mapping.field_id).replace("_", "-"),
        )
    )
    return {
        "case_id": case_id,
        "case_kind": (
            "CORE_FINANCIAL"
            if classification is FinancialClassification.CORE_FINANCIAL
            else "SERVICING"
        ),
        "issuer_id": issuer["company_id"],
        "ticker": issuer["ticker"],
        "cik": issuer["cik"],
        "mapping_id": mapping.mapping_id,
        "field_id": mapping.field_id,
        "classification": classification.value,
        "fiscal_year": period_end.year,
        "fiscal_quarter": _fiscal_quarter(str(filing["form"]), period_end),
        "period_end": period_end.isoformat(),
        "filing_date": filing["filing_date"],
        "form": filing["form"],
        "amendment": str(filing["form"]).endswith("/A"),
        "revision_of_accession": None,
        "accession": filing["accession"],
        "primary_document": primary["document"],
        "primary_sequence": str(primary["sequence"]),
        "primary_document_type": primary["document_type"],
        "primary_description": primary["description"],
        "primary_source_url": primary["source_url"],
        "source_document": source["document"],
        "source_sequence": str(source["sequence"]),
        "source_document_type": source["document_type"],
        "source_description": source["description"],
        "source_is_primary": bool(source["is_primary"]),
        "source_url": source["source_url"],
        "source_route": "SEC_DOCUMENT_BYTES_VIA_EDGARTOOLS",
        "edgartools_source": {
            "acquisition_status": "QUALIFIED",
            "sha256": source["sha256"],
            "byte_length": int(source["byte_length"]),
            "representation": source["representation"],
            "capture_method": source["capture_method"],
            "retention_location": source["retention_location"],
            "retrieved_at": source["retrieved_at"],
            "integrity_verified": True,
        },
        "approved_fact": _approved_fact(candidate, original_label=mapping.display_name),
        "publication_authority": {
            "route": "EXACT_SEC_FILING_XBRL_LINEAGE",
            "company_facts_role": "OPTIONAL_CROSS_CHECK_ONLY",
            "snapshot_integrity_verified": True,
            "retained_object_integrity_verified": True,
        },
        "review_status": _REVIEW_STATE,
    }


def build_manifest(
    *,
    snapshot_path: Path,
    evidence_root: Path,
    registry_path: Path,
) -> dict[str, Any]:
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot_sha256 = _sha256(snapshot_bytes)
    profile = _GOVERNED_SNAPSHOTS.get(snapshot_sha256)
    if profile is None:
        raise ValueError("Phase 5 acquisition snapshot hash is not governed")
    issuer_ids = cast("tuple[str, ...]", profile["issuer_ids"])
    manifest_version = str(profile["manifest_version"])
    snapshot = _required_mapping(json.loads(snapshot_bytes), location="snapshot")
    if snapshot.get("complete") is not True or snapshot.get("stop") is not None:
        raise ValueError("Phase 5 acquisition snapshot is not complete")
    issuers = _required_list(snapshot.get("issuers"), location="snapshot.issuers")
    if tuple(str(item["company_id"]) for item in issuers) != issuer_ids:
        raise ValueError("Phase 5 snapshot issuer order or identity differs")

    registry = FinancialFieldRegistry.from_yaml(registry_path)
    cases: list[dict[str, Any]] = []
    verified_hashes: set[str] = set()
    for raw_issuer in issuers:
        issuer = _required_mapping(raw_issuer, location="snapshot issuer")
        filings = _required_list(issuer.get("filings"), location="issuer.filings")
        if len(filings) != 8:
            raise ValueError("each Phase 5 issuer must have exactly eight filings")
        for raw_filing in filings:
            filing = _required_mapping(raw_filing, location="issuer filing")
            source = _required_mapping(filing["financial_document"], location="financial_document")
            primary = _required_mapping(filing["primary_document"], location="primary_document")
            payload = _verified_object(evidence_root, source)
            verified_hashes.add(str(source["sha256"]))
            _verified_object(evidence_root, primary)
            verified_hashes.add(str(primary["sha256"]))
            if source["representation"] != _REPRESENTATION:
                raise ValueError("Phase 5 source representation is unsupported")

            period_end = date.fromisoformat(str(filing["report_period"]))
            mappings = registry.for_filing(
                cik=str(issuer["cik"]),
                form=str(filing["form"]),
                period_end=period_end,
            )
            if len(mappings) != 2:
                raise ValueError(
                    "each Phase 5 filing must resolve one common and one servicing map"
                )
            discoveries = discover_retained_document_fields(
                payload,
                issuer_id=str(issuer["company_id"]),
                cik=str(issuer["cik"]),
                evidence_id=str(source["sha256"]),
                accession_number=str(filing["accession"]),
                source_document=str(source["document"]),
                source_url=str(source["source_url"]),
                form=str(filing["form"]),
                filed=date.fromisoformat(str(filing["filing_date"])),
                registry=registry,
            )
            for mapping in mappings:
                matched_discoveries = tuple(
                    item for item in discoveries if item.mapping.mapping_id == mapping.mapping_id
                )
                if len(matched_discoveries) != 1:
                    raise ValueError("mapping discovery count differs from one")
                candidates = tuple(
                    item
                    for item in matched_discoveries[0].candidates
                    if item.period_end == period_end
                )
                if len(candidates) != 1:
                    raise ValueError(
                        f"exact-period fact candidate count differs from one: "
                        f"{issuer['company_id']} {filing['accession']} {mapping.mapping_id}"
                    )
                cases.append(
                    _case(
                        issuer=issuer,
                        filing=filing,
                        mapping=mapping,
                        candidate=candidates[0],
                    )
                )

    cases.sort(
        key=lambda item: (str(item["issuer_id"]), str(item["period_end"]), str(item["field_id"]))
    )
    expected_case_count = len(issuer_ids) * 8 * 2
    if (
        len(cases) != expected_case_count
        or len({str(item["case_id"]) for item in cases}) != expected_case_count
    ):
        raise ValueError("Phase 5 manifest case count or identity differs")
    return {
        "manifest_version": manifest_version,
        "mapping_version": registry.version,
        "status": _REVIEW_STATE,
        "publication_authority": "EXACT_SEC_FILING_XBRL_LINEAGE",
        "expected_case_count": expected_case_count,
        "allow_multiple_fields_per_filing": True,
        "approved_expectations": [item["case_id"] for item in cases],
        "governed_window": {
            "first_period_end": min(str(item["period_end"]) for item in cases),
            "last_period_end": max(str(item["period_end"]) for item in cases),
            "latest_annual_period_end": "2025-12-31",
            "filings_per_issuer": 8,
        },
        "acquisition_snapshot": {
            "sha256": snapshot_sha256,
            "schema_version": snapshot["schema_version"],
            "complete": True,
            "retry_count": snapshot["retry_count"],
            "retained_unique_object_count": len(verified_hashes),
            "all_object_hashes_and_lengths_verified": True,
        },
        "representation_policy": {
            "eligible_source": "Exact filing-level XBRL bytes acquired through public edgartools.",
            "retained_text": _REPRESENTATION,
            "company_facts": "OPTIONAL_CROSS_CHECK_ONLY",
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify tracked bounded replay inputs and generated Phase 5 outputs offline",
    )
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.check:
        check_outputs(Path(__file__).resolve().parents[1])
        return 0
    required = (args.snapshot, args.evidence_root, args.registry, args.output)
    if any(value is None for value in required):
        parser.error(
            "--snapshot, --evidence-root, --registry, and --output are required "
            "unless --check is used"
        )
    manifest = build_manifest(
        snapshot_path=cast("Path", args.snapshot).resolve(),
        evidence_root=cast("Path", args.evidence_root).resolve(),
        registry_path=cast("Path", args.registry).resolve(),
    )
    output = cast("Path", args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=False, width=120),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
