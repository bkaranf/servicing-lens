from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

import mortgage_servicing_dashboard.edgar_tools_pipeline as pipeline_module
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    AtomicPersistenceResult,
    CommittedCaseOutcome,
    CommittedCaseState,
    EdgarToolsCompany,
    EdgarToolsSyncPipeline,
    EdgarToolsSyncState,
    GoldenManifest,
    ValidatedFiling,
)
from mortgage_servicing_dashboard.edgartools_adapter.dto import (
    AcquiredContent,
    Attachment,
    AttachmentAcquisition,
    Company,
    ContentRepresentation,
    Filing,
    RetainedContent,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import (
    AdapterState,
    AdapterTransportError,
)
from mortgage_servicing_dashboard.financial_discovery import (
    FinancialDiscoveryError,
    FinancialFieldRegistry,
)

_ROOT = Path(__file__).parents[2]
_REGISTRY = FinancialFieldRegistry.from_yaml(_ROOT / "config" / "financial_fields.v1.yaml")
_NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
_TFC = EdgarToolsCompany("tfc", "TFC", "0000092230")
_PFSI = EdgarToolsCompany("pfsi", "PFSI", "0001745916")


def _inline_xbrl(*, cik: str, context: str, raw: str, scale: int, decimals: int) -> bytes:
    comparative_context = f"{context}-comparative"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025"><body><ix:resources>
 <xbrli:context id="{context}"><xbrli:entity><xbrli:identifier
  scheme="http://www.sec.gov/CIK">{int(cik)}</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:context id="{comparative_context}"><xbrli:entity><xbrli:identifier
  scheme="http://www.sec.gov/CIK">{int(cik)}</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 </ix:resources><ix:nonFraction id="assets-1" name="us-gaap:Assets"
 contextRef="{context}" unitRef="USD" decimals="{decimals}" scale="{scale}"
 format="ixt:num-dot-decimal">{raw}</ix:nonFraction>
 <ix:nonFraction id="assets-comparative" name="us-gaap:Assets"
 contextRef="{comparative_context}" unitRef="USD" decimals="{decimals}" scale="{scale}"
 format="ixt:num-dot-decimal">1</ix:nonFraction></body></html>""".encode()


def _case(  # noqa: PLR0913 - explicit fixture fields mirror one manifest case.
    *,
    issuer: str,
    ticker: str,
    cik: str,
    accession: str,
    form: str,
    filed: date,
    document: str,
    raw: str,
    scale: int,
    decimals: int,
) -> tuple[dict[str, object], bytes]:
    context = f"ctx-{issuer}-{form.lower().replace('-', '')}"
    payload = _inline_xbrl(
        cik=cik,
        context=context,
        raw=raw,
        scale=scale,
        decimals=decimals,
    )
    digest = hashlib.sha256(payload).hexdigest()
    source_url = f"https://www.sec.gov/Archives/{accession}/{document}"
    return {
        "case_id": f"{issuer}-{accession}",
        "case_kind": "ANNUAL_CORE_FINANCIAL"
        if form.startswith("10-K")
        else "QUARTERLY_CORE_FINANCIAL",
        "issuer_id": issuer,
        "ticker": ticker,
        "cik": cik,
        "field_id": "total_assets",
        "classification": "CORE_FINANCIAL",
        "fiscal_year": 2026,
        "fiscal_quarter": "FY" if form.startswith("10-K") else "Q2",
        "period_end": "2026-06-30",
        "form": form,
        "filing_date": filed.isoformat(),
        "amendment": form.endswith("/A"),
        "accession": accession,
        "primary_document": document,
        "primary_sequence": "1",
        "primary_document_type": form,
        "primary_description": form,
        "source_url": source_url,
        "edgartools_source": {
            "sha256": digest,
            "byte_length": len(payload),
            "representation": "EDGARTOOLS_LIBRARY_TEXT_CANONICAL_UTF8",
            "capture_method": "edgartools_attachment_text_utf8",
        },
        "approved_fact": {
            "qualified_concept": "us-gaap:Assets",
            "original_label": "Total assets",
            "raw_display_string": raw,
            "normalized_decimal_string": str(Decimal(raw) * (Decimal(10) ** scale)),
            "source_element_ids": ["assets-1"],
            "context_ref": context,
            "period_type": "instant",
            "period_instant": "2026-06-30",
            "dimensions": [],
            "unit": "USD",
            "decimals": str(decimals),
            "scale": str(Decimal(10) ** scale),
            "source_sign": None,
            "source_precision": None,
            "presentation_sign": "POSITIVE",
            "source_object_count": 1,
            "semantic_fact_count": 1,
        },
        "review_status": "INDEPENDENTLY_CROSS_CHECKED",
    }, payload


@pytest.fixture
def manifest_and_payloads() -> tuple[dict[str, object], dict[str, bytes]]:
    definitions = (
        (
            "tfc",
            "TFC",
            "0000092230",
            "0000092230-26-000030",
            "10-K",
            date(2026, 2, 24),
            "tfc-annual.htm",
            "547538",
            6,
            -6,
        ),
        (
            "tfc",
            "TFC",
            "0000092230",
            "0000092230-26-000099",
            "10-Q",
            date(2026, 7, 31),
            "tfc-quarter.htm",
            "556023",
            6,
            -6,
        ),
        (
            "pfsi",
            "PFSI",
            "0001745916",
            "0001104659-26-018142",
            "10-K",
            date(2026, 2, 20),
            "pfsi-annual.htm",
            "29388689",
            3,
            -3,
        ),
        (
            "pfsi",
            "PFSI",
            "0001745916",
            "0001104659-26-090486",
            "10-Q",
            date(2026, 8, 4),
            "pfsi-quarter.htm",
            "29859451",
            3,
            -3,
        ),
    )
    built = tuple(
        _case(
            issuer=item[0],
            ticker=item[1],
            cik=item[2],
            accession=item[3],
            form=item[4],
            filed=item[5],
            document=item[6],
            raw=item[7],
            scale=item[8],
            decimals=item[9],
        )
        for item in definitions
    )
    cases = [item[0] for item in built]
    manifest: dict[str, object] = {
        "manifest_version": "test-1",
        "mapping_version": "financial-fields-v1",
        "status": "INDEPENDENTLY_CROSS_CHECKED",
        "approved_expectations": [case["case_id"] for case in cases],
        "cases": cases,
    }
    payloads = {str(case["accession"]): payload for case, payload in built}
    return manifest, payloads


class _Adapter:
    def __init__(
        self,
        manifest: Mapping[str, object],
        payloads: Mapping[str, bytes],
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.fail_operation: str | None = None
        self.payloads = dict(payloads)
        self.companies = {
            "0000092230": Company("0000092230", "Truist", ("TFC",)),
            "0001745916": Company("0001745916", "PennyMac", ("PFSI",)),
        }
        cases = manifest["cases"]
        assert isinstance(cases, list)
        self.filings_by_cik: dict[str, list[Filing]] = {}
        self.attachments_by_accession: dict[str, tuple[Attachment, ...]] = {}
        for raw_case in cases:
            assert isinstance(raw_case, dict)
            case = raw_case
            cik = str(case["cik"])
            accession = str(case["accession"])
            document = str(case["primary_document"])
            source_url = str(case["source_url"])
            filing = Filing(
                cik=cik,
                accession_number=accession,
                company_name="Synthetic public company",
                form=str(case["form"]),
                filing_date=date.fromisoformat(str(case["filing_date"])),
                acceptance_timestamp=_NOW,
                report_period=date.fromisoformat(str(case["period_end"])),
                primary_document=document,
                amendment=bool(case["amendment"]),
                is_xbrl=True,
                is_inline_xbrl=True,
                size=len(payloads[accession]),
                homepage_url=f"https://www.sec.gov/{accession}",
                text_url=f"https://www.sec.gov/{accession}.txt",
            )
            attachment = Attachment(
                cik=cik,
                accession_number=accession,
                document=document,
                sequence="1",
                description=str(case["form"]),
                attachment_type=str(case["form"]),
                size=len(payloads[accession]),
                source_url=source_url,
                is_primary=True,
                is_binary=False,
            )
            self.filings_by_cik.setdefault(cik, []).append(filing)
            self.attachments_by_accession[accession] = (attachment,)

    def company(self, cik_or_ticker: str) -> Company:
        self.calls.append(("company", cik_or_ticker))
        self._fail_if_requested("company")
        return self.companies[cik_or_ticker]

    def filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: date | tuple[date, date] | None = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        self.calls.append(("filings", cik, forms, filing_date, include_amendments))
        self._fail_if_requested("filings")
        return tuple(self.filings_by_cik[cik])

    def attachments(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        self.calls.append(("attachments", accession, expected_cik))
        self._fail_if_requested("attachments")
        return self.attachments_by_accession[accession]

    def acquire_attachment(
        self,
        accession: str,
        document: str,
        *,
        expected_cik: str | None = None,
        retain: bool = True,
    ) -> AttachmentAcquisition:
        self.calls.append(("acquire_attachment", accession, document, expected_cik, retain))
        self._fail_if_requested("acquire_attachment")
        attachment = self.attachments_by_accession[accession][0]
        payload = self.payloads[accession]
        digest = hashlib.sha256(payload).hexdigest()
        content = AcquiredContent(
            cik=attachment.cik,
            accession_number=accession,
            document=document,
            source_url=attachment.source_url,
            content=payload,
            media_type="text/html",
            representation=ContentRepresentation.LIBRARY_TEXT_UTF8,
            capture_method="edgartools_attachment_text_utf8",
            sha256=digest,
            retrieved_at=_NOW,
        )
        retained = RetainedContent(
            content_sha256=digest,
            byte_length=len(payload),
            retention_location=f"content-sha256://{digest}",
            retained_at=_NOW,
            representation=ContentRepresentation.LIBRARY_TEXT_UTF8,
            capture_method="edgartools_attachment_text_utf8",
            media_type="text/html",
            source_url=attachment.source_url,
        )
        return AttachmentAcquisition(attachment, content, retained if retain else None)

    def _fail_if_requested(self, operation: str) -> None:
        if self.fail_operation == operation:
            message = "synthetic adapter failure"
            raise AdapterTransportError(
                message,
                state=AdapterState.TRANSPORT_ERROR,
                operation=operation,
            )


class _AtomicRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[ValidatedFiling, ...]] = []

    def persist_atomically(
        self,
        results: tuple[ValidatedFiling, ...],
    ) -> AtomicPersistenceResult:
        self.calls.append(results)
        return AtomicPersistenceResult(
            outcomes=tuple(
                CommittedCaseOutcome(
                    case_id=result.case_id,
                    accession_number=result.accession_number,
                    state=CommittedCaseState.PUBLISHED,
                )
                for result in results
            ),
            observations=len(results),
        )


def _pipeline(
    manifest: Mapping[str, object],
    payloads: Mapping[str, bytes],
    *,
    persistence: (
        _AtomicRecorder | Callable[[tuple[ValidatedFiling, ...]], AtomicPersistenceResult] | None
    ) = None,
) -> tuple[EdgarToolsSyncPipeline, _Adapter]:
    adapter = _Adapter(manifest, payloads)
    return EdgarToolsSyncPipeline(
        adapter=adapter,
        registry=_REGISTRY,
        golden_manifest=manifest,
        persistence=persistence,
    ), adapter


def _cases(manifest: Mapping[str, object], issuer: str) -> list[dict[str, Any]]:
    values = manifest["cases"]
    assert isinstance(values, list)
    return [case for case in values if isinstance(case, dict) and case["issuer_id"] == issuer]


def test_tfc_dry_run_validates_two_cases_without_persistence(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    pipeline, adapter = _pipeline(manifest, payloads, persistence=recorder)

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.terminal_state is EdgarToolsSyncState.VALIDATED
    assert summary.discovered_count == summary.eligible_count == summary.validated_count == 2
    assert summary.core_count == 2
    assert summary.optional_count == summary.published_count == summary.failed_count == 0
    assert summary.linked_count == 0
    assert recorder.calls == []
    assert summary.call_counts.as_payload() == {
        "company": 1,
        "filings": 1,
        "attachments": 2,
        "acquire_attachment": 2,
        "discover_retained_document_fields": 2,
        "persistence": 0,
        "fallback": 0,
        "retry": 0,
        "company_facts": 0,
        "filing_xbrl": 0,
        "filing_structure": 0,
    }
    assert [call[0] for call in adapter.calls] == [
        "company",
        "filings",
        "attachments",
        "acquire_attachment",
        "attachments",
        "acquire_attachment",
    ]
    assert adapter.calls[1][2:] == (
        ("10-K", "10-K/A", "10-Q", "10-Q/A"),
        (date(2026, 2, 24), date(2026, 7, 31)),
        True,
    )


def test_pfsi_live_sync_calls_atomic_persistence_once_with_exact_lineage(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    pipeline, _ = _pipeline(manifest, payloads, persistence=recorder)

    summary = pipeline.sync_company(_PFSI)

    assert summary.terminal_state is EdgarToolsSyncState.PUBLISHED
    assert summary.validated_count == summary.published_count == 2
    assert summary.call_counts.persistence == 1
    assert len(recorder.calls) == 1
    assert tuple(result.accession_number for result in recorder.calls[0]) == (
        summary.approved_accessions
    )
    assert all(result.normalized_value.as_tuple().exponent == 0 for result in recorder.calls[0])
    assert all(result.source_element_ids == ("assets-1",) for result in recorder.calls[0])
    assert all(result.amendment is False for result in recorder.calls[0])
    annual, quarterly = recorder.calls[0]
    assert (annual.fiscal_year, annual.fiscal_quarter) == (2026, "FY")
    assert (quarterly.fiscal_year, quarterly.fiscal_quarter) == (2026, "Q2")


def test_committed_case_outcomes_drive_states_and_never_publish_quarantine(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads

    def persist(results: tuple[ValidatedFiling, ...]) -> AtomicPersistenceResult:
        first, second = results
        return AtomicPersistenceResult(
            outcomes=(
                CommittedCaseOutcome(
                    case_id=first.case_id,
                    accession_number=first.accession_number,
                    state=CommittedCaseState.QUARANTINED,
                ),
                CommittedCaseOutcome(
                    case_id=second.case_id,
                    accession_number=second.accession_number,
                    state=CommittedCaseState.LINKED,
                ),
            ),
            linked=1,
            quarantined=1,
        )

    pipeline, _ = _pipeline(manifest, payloads, persistence=persist)

    summary = pipeline.sync_company(_PFSI)

    assert summary.terminal_state is EdgarToolsSyncState.QUARANTINED
    assert [result.state for result in summary.filing_results] == [
        EdgarToolsSyncState.QUARANTINED,
        EdgarToolsSyncState.LINKED,
    ]
    assert summary.published_count == 0
    assert summary.linked_count == 1
    assert summary.quarantined_count == 1


def test_prepared_multi_company_batch_never_commits_after_second_company_failure(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    pipeline, adapter = _pipeline(manifest, payloads, persistence=recorder)
    tfc = pipeline.prepare_company(_TFC)
    adapter.fail_operation = "company"
    pfsi = pipeline.prepare_company(_PFSI)

    summaries = pipeline.persist_prepared_batch((tfc, pfsi))

    assert [summary.terminal_state for summary in summaries] == [
        EdgarToolsSyncState.VALIDATED,
        EdgarToolsSyncState.FAILED,
    ]
    assert recorder.calls == []


def test_summary_is_bounded_and_never_contains_bodies_values_or_identity(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, _ = _pipeline(manifest, payloads)

    payload = pipeline.sync_company(_TFC, dry_run=True).as_payload()
    serialized = json.dumps(payload, sort_keys=True)

    assert len(payload["filings"]) == 2  # type: ignore[arg-type]
    assert "547538" not in serialized
    assert "<html" not in serialized
    assert "@" not in serialized
    assert "identity" not in serialized.lower()
    assert payload["fallback_call_count"] == payload["retry_count"] == 0


def test_identity_mismatch_stops_before_listing(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    adapter.companies[_TFC.cik] = Company("0000000001", "Wrong", ("TFC",))

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.terminal_state is EdgarToolsSyncState.FAILED
    assert summary.failed_count == 1
    assert adapter.calls == [("company", _TFC.cik)]


def test_overlap_and_unseen_logic_reprocesses_recent_known_and_old_unseen(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    cases = _cases(manifest, "tfc")
    _annual, quarterly = cases

    summary = pipeline.sync_company(
        _TFC,
        since=date(2026, 8, 4),
        dry_run=True,
        known_accessions=frozenset({str(quarterly["accession"])}),
    )

    assert summary.overlap_start == "2026-07-28"
    assert summary.eligible_count == 2
    assert [result.already_known for result in summary.filing_results] == [False, True]
    assert len([call for call in adapter.calls if call[0] == "acquire_attachment"]) == 2

    pipeline, _ = _pipeline(manifest, payloads)
    all_known = frozenset(str(case["accession"]) for case in cases)
    filtered = pipeline.sync_company(
        _TFC,
        since=date(2026, 8, 4),
        dry_run=True,
        known_accessions=all_known,
    )
    assert filtered.eligible_count == 1
    assert filtered.approved_accessions == (str(quarterly["accession"]),)


def test_known_original_does_not_suppress_unseen_amendment(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    source_manifest, source_payloads = manifest_and_payloads
    manifest = deepcopy(source_manifest)
    payloads = dict(source_payloads)
    annual = _cases(manifest, "tfc")[0]
    original_accession = str(annual["accession"])
    amendment_accession = "0000092230-26-000031"
    annual["accession"] = amendment_accession
    annual["form"] = "10-K/A"
    annual["amendment"] = True
    annual["revision_of_accession"] = original_accession
    annual["case_id"] = "tfc-amendment"
    annual["source_url"] = str(annual["source_url"]).replace(
        original_accession, amendment_accession
    )
    approved = manifest["approved_expectations"]
    assert isinstance(approved, list)
    approved[0] = "tfc-amendment"
    payloads[amendment_accession] = payloads.pop(original_accession)

    pipeline, _ = _pipeline(manifest, payloads)
    summary = pipeline.sync_company(
        _TFC,
        since=date(2026, 8, 4),
        dry_run=True,
        known_accessions=frozenset({original_accession}),
    )

    amendment = summary.filing_results[0]
    assert amendment.accession_number == amendment_accession
    assert amendment.amendment is True
    assert amendment.already_known is False


@pytest.mark.parametrize("primary_count", [0, 2])
def test_missing_or_duplicate_primary_blocks_atomic_persistence(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    primary_count: int,
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    pipeline, adapter = _pipeline(manifest, payloads, persistence=recorder)
    accession = str(_cases(manifest, "tfc")[0]["accession"])
    primary = adapter.attachments_by_accession[accession][0]
    adapter.attachments_by_accession[accession] = tuple(
        replace(primary, sequence=str(index + 1)) for index in range(primary_count)
    )

    summary = pipeline.sync_company(_TFC)

    expected = EdgarToolsSyncState.MISMATCH if primary_count == 0 else EdgarToolsSyncState.AMBIGUOUS
    assert summary.filing_results[0].state is expected
    assert summary.terminal_state is EdgarToolsSyncState.QUARANTINED
    assert recorder.calls == []


def test_filing_metadata_mismatch_is_classified_without_document_calls(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    adapter.filings_by_cik[_TFC.cik][0] = replace(
        adapter.filings_by_cik[_TFC.cik][0],
        report_period=date(2026, 3, 31),
    )

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert "report_period" in summary.filing_results[0].safe_detail
    assert len([call for call in adapter.calls if call[0] == "attachments"]) == 1


def test_missing_acceptance_timestamp_fails_before_document_calls(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    adapter.filings_by_cik[_TFC.cik][0] = replace(
        adapter.filings_by_cik[_TFC.cik][0],
        acceptance_timestamp=None,
    )

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert "acceptance_timestamp" in summary.filing_results[0].safe_detail
    assert len([call for call in adapter.calls if call[0] == "attachments"]) == 1


def test_parse_value_mismatch_quarantines_batch_and_persists_nothing(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    case = _cases(manifest, "tfc")[0]
    approved_fact = case["approved_fact"]
    assert isinstance(approved_fact, dict)
    approved_fact["normalized_decimal_string"] = "1"
    pipeline, _ = _pipeline(manifest, payloads, persistence=recorder)

    summary = pipeline.sync_company(_TFC)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert "decimal" in summary.filing_results[0].safe_detail
    assert summary.validated_count == 1
    assert recorder.calls == []


def test_conflicting_raw_facts_are_ambiguous_and_never_persisted(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    case = _cases(manifest, "tfc")[0]
    accession = str(case["accession"])
    original = payloads[accession]
    conflicting_fact = (
        b'<ix:nonFraction id="assets-2" name="us-gaap:Assets" '
        b'contextRef="ctx-tfc-10k" unitRef="USD" decimals="-6" scale="6" '
        b'format="ixt:num-dot-decimal">547539</ix:nonFraction></body>'
    )
    duplicate = original.replace(
        b"</body>",
        conflicting_fact,
    )
    payloads[accession] = duplicate
    source = case["edgartools_source"]
    assert isinstance(source, dict)
    source["sha256"] = hashlib.sha256(duplicate).hexdigest()
    source["byte_length"] = len(duplicate)
    pipeline, _ = _pipeline(manifest, payloads, persistence=recorder)

    summary = pipeline.sync_company(_TFC)

    assert summary.filing_results[0].state is EdgarToolsSyncState.AMBIGUOUS
    assert recorder.calls == []


def test_retained_hash_mismatch_skips_parser_and_atomic_callback(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    case = _cases(manifest, "tfc")[0]
    source = case["edgartools_source"]
    assert isinstance(source, dict)
    source["sha256"] = "0" * 64
    pipeline, _ = _pipeline(manifest, payloads, persistence=recorder)

    summary = pipeline.sync_company(_TFC)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert "approved_hash" in summary.filing_results[0].safe_detail
    assert summary.call_counts.discover_retained_document_fields == 1
    assert recorder.calls == []


def test_non_dry_run_without_persistence_fails_closed(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, _ = _pipeline(manifest, payloads)

    summary = pipeline.sync_company(_TFC)

    assert summary.terminal_state is EdgarToolsSyncState.FAILED
    assert summary.validated_count == 2
    assert summary.published_count == 0
    assert summary.failed_count == 1


def test_callable_persistence_receives_one_complete_tuple(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    calls: list[tuple[ValidatedFiling, ...]] = []

    def persist(results: tuple[ValidatedFiling, ...]) -> AtomicPersistenceResult:
        calls.append(results)
        return AtomicPersistenceResult(
            outcomes=tuple(
                CommittedCaseOutcome(
                    case_id=result.case_id,
                    accession_number=result.accession_number,
                    state=CommittedCaseState.PUBLISHED,
                )
                for result in results
            ),
            observations=len(results),
        )

    pipeline, _ = _pipeline(manifest, payloads, persistence=persist)

    summary = pipeline.sync_company(_TFC)

    assert summary.terminal_state is EdgarToolsSyncState.PUBLISHED
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_real_manifest_parses_as_exact_four_case_contract() -> None:
    payload = yaml.safe_load(
        (_ROOT / "tests" / "fixtures" / "edgartools" / "golden-sources.v1.yaml").read_text(
            encoding="utf-8"
        )
    )

    manifest = GoldenManifest.from_mapping(payload)

    assert len(manifest.cases) == 4
    assert {case.filing_date for case in manifest.cases} == {
        date(2026, 2, 20),
        date(2026, 2, 24),
        date(2026, 7, 31),
        date(2026, 8, 4),
    }
    annual = tuple(case for case in manifest.cases if case.form == "10-K")
    quarterly = tuple(case for case in manifest.cases if case.form == "10-Q")
    assert {(case.fiscal_year, case.fiscal_quarter) for case in annual} == {(2025, "FY")}
    assert {(case.fiscal_year, case.fiscal_quarter) for case in quarterly} == {(2026, "Q2")}


@pytest.mark.parametrize(
    "mutation",
    [
        "unapproved",
        "wrong_count",
        "wrong_approved_ids",
        "duplicate_accession",
    ],
)
def test_manifest_rejects_invalid_global_contract(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    mutation: str,
) -> None:
    source, _ = manifest_and_payloads
    manifest = deepcopy(source)
    cases = manifest["cases"]
    approved = manifest["approved_expectations"]
    assert isinstance(cases, list)
    assert isinstance(approved, list)
    if mutation == "unapproved":
        manifest["status"] = "REVIEW_REQUIRED"
    elif mutation == "wrong_count":
        cases.pop()
        approved.pop()
    elif mutation == "wrong_approved_ids":
        approved[0] = "not-a-case"
    else:
        assert isinstance(cases[0], dict)
        assert isinstance(cases[1], dict)
        cases[1]["accession"] = cases[0]["accession"]

    expected = {
        "unapproved": "not independently approved",
        "wrong_count": "exactly four",
        "wrong_approved_ids": "four unique cases",
        "duplicate_accession": "accessions must be unique",
    }[mutation]
    with pytest.raises(ValueError, match=expected):
        GoldenManifest.from_mapping(manifest)


def test_pipeline_rejects_registry_version_or_case_mapping_mismatch(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest_payload, payloads = manifest_and_payloads
    manifest = GoldenManifest.from_mapping(manifest_payload)
    adapter = _Adapter(manifest_payload, payloads)
    wrong_version = replace(_REGISTRY, version="wrong")
    with pytest.raises(ValueError, match="versions differ"):
        EdgarToolsSyncPipeline(
            adapter=adapter,
            registry=wrong_version,
            golden_manifest=manifest,
        )

    no_tfc = replace(
        _REGISTRY,
        mappings=tuple(mapping for mapping in _REGISTRY.mappings if mapping.issuer_id != "tfc"),
    )
    with pytest.raises(ValueError, match="requires one selected-field mapping"):
        EdgarToolsSyncPipeline(
            adapter=adapter,
            registry=no_tfc,
            golden_manifest=manifest,
        )

    changed = deepcopy(manifest_payload)
    first = _cases(changed, "tfc")[0]
    fact = first["approved_fact"]
    assert isinstance(fact, dict)
    fact["original_label"] = "Wrong label"
    with pytest.raises(ValueError, match="semantics differ"):
        EdgarToolsSyncPipeline(
            adapter=adapter,
            registry=_REGISTRY,
            golden_manifest=changed,
        )


@pytest.mark.parametrize("operation", ["company", "filings", "attachments", "acquire_attachment"])
def test_adapter_errors_are_classified_without_persistence(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    operation: str,
) -> None:
    manifest, payloads = manifest_and_payloads
    recorder = _AtomicRecorder()
    pipeline, adapter = _pipeline(manifest, payloads, persistence=recorder)
    adapter.fail_operation = operation

    summary = pipeline.sync_company(_TFC)

    assert summary.terminal_state is EdgarToolsSyncState.FAILED
    assert recorder.calls == []


def test_unknown_governed_company_fails_before_adapter_call(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)

    summary = pipeline.sync_company(EdgarToolsCompany("other", "OTHER", "0000000001"))

    assert summary.failed_count == 1
    assert adapter.calls == []


@pytest.mark.parametrize("discovery_shape", ["missing", "duplicate"])
def test_manifest_intersection_classifies_missing_or_duplicate_filing(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    discovery_shape: str,
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    filings = adapter.filings_by_cik[_TFC.cik]
    if discovery_shape == "missing":
        filings.pop(0)
    else:
        filings.append(filings[0])

    summary = pipeline.sync_company(_TFC, dry_run=True)

    expected = (
        EdgarToolsSyncState.MISMATCH
        if discovery_shape == "missing"
        else EdgarToolsSyncState.AMBIGUOUS
    )
    assert summary.filing_results[0].state is expected


def test_primary_identity_mismatch_fails_before_acquisition(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    accession = str(_cases(manifest, "tfc")[0]["accession"])
    primary = adapter.attachments_by_accession[accession][0]
    adapter.attachments_by_accession[accession] = (
        replace(primary, source_url="https://www.sec.gov/wrong"),
    )

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert len([call for call in adapter.calls if call[0] == "acquire_attachment"]) == 1


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("sequence", "2"), ("attachment_type", "EX-99"), ("description", "wrong")],
)
def test_primary_source_metadata_mismatch_fails_before_acquisition(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    attribute: str,
    value: str,
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, adapter = _pipeline(manifest, payloads)
    accession = str(_cases(manifest, "tfc")[0]["accession"])
    primary = adapter.attachments_by_accession[accession][0]
    mismatch = (
        replace(primary, sequence=value)
        if attribute == "sequence"
        else replace(primary, attachment_type=value)
        if attribute == "attachment_type"
        else replace(primary, description=value)
    )
    adapter.attachments_by_accession[accession] = (mismatch,)

    summary = pipeline.sync_company(_TFC, dry_run=True)

    assert summary.filing_results[0].state is EdgarToolsSyncState.MISMATCH
    assert len([call for call in adapter.calls if call[0] == "acquire_attachment"]) == 1


@pytest.mark.parametrize("document_shape", ["invalid", "not_found"])
def test_parser_error_or_unavailable_field_fails_closed(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    document_shape: str,
) -> None:
    manifest, payloads = manifest_and_payloads
    case = _cases(manifest, "tfc")[0]
    accession = str(case["accession"])
    if document_shape == "invalid":
        payload = b"not inline xbrl"
    else:
        payload = payloads[accession].replace(b"us-gaap:Assets", b"us-gaap:Liabilities")
    payloads[accession] = payload
    source = case["edgartools_source"]
    assert isinstance(source, dict)
    source["sha256"] = hashlib.sha256(payload).hexdigest()
    source["byte_length"] = len(payload)
    pipeline, _ = _pipeline(manifest, payloads)

    summary = pipeline.sync_company(_TFC, dry_run=True)

    expected = (
        EdgarToolsSyncState.FAILED if document_shape == "invalid" else EdgarToolsSyncState.MISMATCH
    )
    assert summary.filing_results[0].state is expected


def test_internal_helpers_cover_fail_closed_manifest_types(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
) -> None:
    manifest, payloads = manifest_and_payloads
    pipeline, _ = _pipeline(manifest, payloads)
    with pytest.raises(RuntimeError, match="not configured"):
        pipeline._persist(())
    assert pipeline_module._terminal_state((), persistence_missing=False, published_count=0) is (
        EdgarToolsSyncState.DISCOVERED
    )
    assert pipeline_module._raw_display_decimal("(1,234)") == Decimal(-1234)
    with pytest.raises(FinancialDiscoveryError):
        pipeline_module._raw_display_decimal("not-a-number")
    with pytest.raises(ValueError, match="string-keyed"):
        pipeline_module._mapping([], location="test")
    with pytest.raises(TypeError, match="sequence"):
        pipeline_module._sequence("bad", location="test")
    with pytest.raises(ValueError, match="nonblank"):
        pipeline_module._required_string({"field": ""}, "field", location="test")
    with pytest.raises(ValueError, match="nonblank"):
        pipeline_module._string_item("", location="test")
    assert pipeline_module._optional_string(None) is None
    assert pipeline_module._optional_scalar_string(None) is None
    with pytest.raises(TypeError, match="scalar"):
        pipeline_module._optional_scalar_string([])
    with pytest.raises(TypeError, match="boolean"):
        pipeline_module._boolean({"field": "true"}, "field", location="test")
    with pytest.raises(ValueError, match="positive"):
        pipeline_module._positive_int({"field": 0}, "field", location="test")
    with pytest.raises(ValueError, match="ISO date"):
        pipeline_module._required_date({"field": "bad"}, "field", location="test")
    with pytest.raises(TypeError, match="decimal"):
        pipeline_module._required_decimal({"field": []}, "field", location="test")
    with pytest.raises(ValueError, match="decimal"):
        pipeline_module._required_decimal({"field": "bad"}, "field", location="test")
    with pytest.raises(ValueError, match="finite"):
        pipeline_module._required_decimal({"field": "NaN"}, "field", location="test")


@pytest.mark.parametrize(
    "case_error",
    ["review", "amendment", "annual_as_q4", "quarter_as_fy"],
)
def test_case_level_review_amendment_and_fiscal_contracts_fail_closed(
    manifest_and_payloads: tuple[dict[str, object], dict[str, bytes]],
    case_error: str,
) -> None:
    manifest, _ = manifest_and_payloads
    case = _cases(manifest, "tfc")[0]
    if case_error == "review":
        case["review_status"] = "REVIEW_REQUIRED"
    elif case_error == "amendment":
        case["amendment"] = True
    elif case_error == "annual_as_q4":
        case["fiscal_quarter"] = "Q4"
    else:
        quarter = _cases(manifest, "tfc")[1]
        quarter["fiscal_quarter"] = "FY"

    expected = {
        "review": "independently approved",
        "amendment": "amendment marker",
        "annual_as_q4": "fiscal quarter marker",
        "quarter_as_fy": "fiscal quarter marker",
    }[case_error]
    with pytest.raises(ValueError, match=expected):
        GoldenManifest.from_mapping(manifest)
