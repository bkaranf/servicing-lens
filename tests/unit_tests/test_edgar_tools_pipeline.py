from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from mortgage_servicing_dashboard.edgar_tools import (
    EdgarToolsAuthenticationError,
    EdgarToolsClient,
    EdgarToolsContentError,
    EdgarToolsError,
    EdgarToolsNotFoundError,
    EdgarToolsProviderUnavailableError,
    EdgarToolsQuotaBlockedError,
    EdgarToolsRateLimitError,
    EdgarToolsSchemaError,
    EdgarToolsTierBlockedError,
    EdgarToolsTransportError,
    EdgarToolsUnsafeRequestError,
)
from mortgage_servicing_dashboard.edgar_tools_evidence import EdgarToolsEvidenceStore
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncPipeline,
    EdgarToolsSyncState,
    _classify_error,
)


def _json(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode(),
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> EdgarToolsClient:
    return EdgarToolsClient(
        api_key=SecretStr("synthetic-provider-key"),
        transport=httpx.MockTransport(handler),
        max_attempts=1,
    )


def _company() -> EdgarToolsCompany:
    return EdgarToolsCompany(company_id="tfc", ticker="TFC", cik="0000092230")


def _company_json(*, cik: str = "0000092230") -> dict[str, object]:
    return {
        "entity": {"company": {"cik": cik, "name": "Truist Financial Corporation", "ticker": "TFC"}}
    }


def _filings_json(
    *,
    form: str = "10-Q",
    filing_date: str = "2026-08-01",
    has_more: bool = False,
) -> dict[str, object]:
    return {
        "filings": [
            {
                "accession_number": "0000092230-26-000099",
                "form": form,
                "filing_date": filing_date,
                "sec_url": "https://www.sec.gov/Archives/example",
            }
        ],
        "pagination": {"page": 1, "limit": 100, "total": 1, "has_more": has_more},
    }


def _detail_json() -> dict[str, object]:
    return {
        "filing": {
            "cik": "0000092230",
            "accession_number": "0000092230-26-000099",
            "filing_date": "2026-08-01",
            "ticker": "TFC",
            "sec_url": "https://www.sec.gov/Archives/example",
        }
    }


def _documents_json(*, count: int = 1, document_type: str = "10-Q") -> dict[str, object]:
    return {
        "documents": [
            {
                "filename": f"document-{index}.htm",
                "type": document_type,
                "sequence": index + 1,
                "size": 20,
                "sec_url": "https://www.sec.gov/Archives/example/document.htm",
            }
            for index in range(count)
        ]
    }


def _successful_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/companies/TFC"):
        return _json(200, _company_json())
    if path.endswith("/companies/0000092230/filings"):
        return _json(200, _filings_json())
    if path.endswith("/documents"):
        return _json(200, _documents_json())
    if path.endswith("/0000092230-26-000099"):
        return _json(200, _detail_json())
    return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")


def test_sync_retains_lineage_but_fails_closed_without_qualified_parser(tmp_path: Path) -> None:
    client = _client(_successful_handler)
    pipeline = EdgarToolsSyncPipeline(
        client=client,
        evidence_store=EdgarToolsEvidenceStore(tmp_path),
    )

    summary = pipeline.sync_company(
        _company(),
        since=date(2026, 8, 5),
        dry_run=True,
        known_accessions=frozenset({"0000092230-26-000099"}),
    )
    payload = summary.as_payload()

    assert summary.overlap_start == "2026-07-29"
    assert summary.discovered_count == 1
    assert summary.eligible_count == 1
    assert summary.terminal_state is EdgarToolsSyncState.PARSER_UNQUALIFIED
    assert summary.filing_results[0].already_known is True
    assert len(summary.filing_results[0].retained_evidence_ids) == 3
    assert len(summary.retained_metadata_evidence_ids) == 2
    assert payload["published_count"] == 0
    assert payload["state_counts"] == {"PARSER_UNQUALIFIED": 1}
    assert "value" not in json.dumps(payload).lower()
    assert len(tuple(tmp_path.rglob("*.bin"))) == 5
    client.close()


def test_sync_with_no_eligible_filing_is_discovery_only(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/companies/TFC"):
            return _json(200, _company_json())
        return _json(200, _filings_json(form="S-1"))

    client = _client(handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())

    assert summary.discovered_count == 1
    assert summary.eligible_count == 0
    assert summary.filing_results == ()
    assert summary.terminal_state is EdgarToolsSyncState.DISCOVERED
    assert summary.as_payload()["state_counts"] == {}
    client.close()


def test_since_filter_uses_overlap_and_rejects_invalid_provider_date(tmp_path: Path) -> None:
    def old_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/companies/TFC"):
            return _json(200, _company_json())
        return _json(200, _filings_json(filing_date="2026-01-01"))

    client = _client(old_handler)
    pipeline = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path / "old")
    )
    assert pipeline.sync_company(_company(), since=date(2026, 8, 1)).eligible_count == 0
    client.close()

    def invalid_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/companies/TFC"):
            return _json(200, _company_json())
        return _json(200, _filings_json(filing_date="not-a-date"))

    client = _client(invalid_handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path / "invalid")
    ).sync_company(_company(), since=date(2026, 8, 1))
    assert summary.terminal_state is EdgarToolsSyncState.FAILED
    assert summary.filing_results[0].safe_detail == "provider filing date was invalid"
    client.close()


def test_governed_cik_mismatch_fails_closed(tmp_path: Path) -> None:
    client = _client(lambda _: _json(200, _company_json(cik="0000000001")))
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())
    assert summary.terminal_state is EdgarToolsSyncState.FAILED
    assert summary.eligible_count == 0
    client.close()


def test_provider_document_gap_is_not_not_disclosed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return _json(404, {"error": "not found"})
        return _successful_handler(request)

    client = _client(handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())
    result = summary.filing_results[0]
    assert result.state is EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER
    assert "NOT_DISCLOSED" not in json.dumps(summary.as_payload())
    assert len(result.retained_evidence_ids) == 1
    client.close()


def test_empty_document_list_is_source_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return _json(200, {"documents": []})
        return _successful_handler(request)

    client = _client(handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())
    assert summary.filing_results[0].state is EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER
    assert len(summary.filing_results[0].retained_evidence_ids) == 2
    client.close()


def test_excessive_document_list_is_quarantined_without_fetch(tmp_path: Path) -> None:
    fetch_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_calls
        if request.url.path.endswith("/documents"):
            return _json(200, _documents_json(count=26))
        if "document-" in request.url.path:
            fetch_calls += 1
        return _successful_handler(request)

    client = _client(handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())
    assert summary.filing_results[0].state is EdgarToolsSyncState.QUARANTINED
    assert fetch_calls == 0
    client.close()


def test_non_html_document_type_can_be_ineligible(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return _json(
                200,
                {
                    "documents": [
                        {
                            "filename": "image.jpg",
                            "type": "GRAPHIC",
                            "sequence": 1,
                            "size": 20,
                        }
                    ]
                },
            )
        return _successful_handler(request)

    client = _client(handler)
    summary = EdgarToolsSyncPipeline(
        client=client, evidence_store=EdgarToolsEvidenceStore(tmp_path)
    ).sync_company(_company())
    assert summary.filing_results[0].state is EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER
    client.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            EdgarToolsAuthenticationError("x", endpoint="/x"),
            EdgarToolsSyncState.AUTHENTICATION_BLOCKED,
        ),
        (EdgarToolsTierBlockedError("x", endpoint="/x"), EdgarToolsSyncState.TIER_BLOCKED),
        (EdgarToolsQuotaBlockedError("x", endpoint="/x"), EdgarToolsSyncState.QUOTA_BLOCKED),
        (EdgarToolsRateLimitError("x", endpoint="/x"), EdgarToolsSyncState.RATE_LIMITED),
        (
            EdgarToolsNotFoundError("x", endpoint="/x"),
            EdgarToolsSyncState.SOURCE_NOT_AVAILABLE_VIA_PROVIDER,
        ),
        (
            EdgarToolsProviderUnavailableError("x", endpoint="/x"),
            EdgarToolsSyncState.PROVIDER_UNAVAILABLE,
        ),
        (EdgarToolsTransportError("x", endpoint="/x"), EdgarToolsSyncState.PROVIDER_UNAVAILABLE),
        (EdgarToolsSchemaError("x", endpoint="/x"), EdgarToolsSyncState.FAILED),
        (EdgarToolsContentError("x", endpoint="/x"), EdgarToolsSyncState.FAILED),
        (EdgarToolsUnsafeRequestError("x", endpoint="/x"), EdgarToolsSyncState.FAILED),
        (EdgarToolsError("x", endpoint="/x"), EdgarToolsSyncState.FAILED),
    ],
)
def test_error_classification_is_explicit(
    error: EdgarToolsError,
    expected: EdgarToolsSyncState,
) -> None:
    state, detail = _classify_error(error)
    assert state is expected
    assert detail
