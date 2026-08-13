from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from mortgage_servicing_dashboard.edgar_tools import (
    EDGAR_TOOLS_BASE_URL,
    CompanySummary,
    EdgarToolsAuthenticationError,
    EdgarToolsClient,
    EdgarToolsContentError,
    EdgarToolsNotFoundError,
    EdgarToolsProviderUnavailableError,
    EdgarToolsQuotaBlockedError,
    EdgarToolsRateLimitError,
    EdgarToolsRedirectError,
    EdgarToolsSchemaError,
    EdgarToolsTierBlockedError,
    EdgarToolsTransportError,
    EdgarToolsUnsafeRequestError,
    FilingCollection,
    FilingDetail,
    FilingDocument,
    FilingPage,
    ProviderResponseMetadata,
    validate_provider_filename,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _json_response(
    status: int,
    payload: object,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    encoded = json.dumps(payload).encode()
    response_headers = {"content-type": "application/json", **(headers or {})}
    return httpx.Response(status, headers=response_headers, content=encoded)


def _client(
    handler: Handler,
    *,
    max_attempts: int = 3,
    max_response_bytes: int = 25_000_000,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> EdgarToolsClient:
    return EdgarToolsClient(
        api_key=SecretStr("synthetic-edgar-tools-key"),
        transport=httpx.MockTransport(handler),
        max_attempts=max_attempts,
        max_response_bytes=max_response_bytes,
        sleep=sleep or (lambda _: None),
        now=now,
    )


def _company_payload() -> dict[str, object]:
    return {
        "entity": {
            "company": {
                "cik": "0000092230",
                "name": "Truist Financial Corporation",
                "entity_type": "company",
                "fiscal_year_end": "1231",
            },
            "tickers": [{"ticker": "TFC"}],
        }
    }


def _filing_payload(*, page: int = 1, has_more: bool = False) -> dict[str, object]:
    return {
        "filings": [
            {
                "accession_number": f"0000092230-26-0000{page}",
                "form": "10-Q",
                "filing_date": "2026-08-01",
                "acceptance_datetime": "2026-08-01T12:00:00Z",
                "description": "Quarterly report",
                "sec_url": "https://www.sec.gov/Archives/example",
            }
        ],
        "pagination": {"page": page, "limit": 1, "total": 2, "has_more": has_more},
    }


def test_context_manager_health_and_safe_metadata() -> None:
    retrieved_at = datetime(2026, 8, 12, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.edgar.tools/v1/health"
        assert request.headers["authorization"] == "Bearer synthetic-edgar-tools-key"
        return _json_response(
            200,
            {"status": "healthy", "pagination": {"page": 1, "has_more": False}},
            headers={
                "x-ratelimit-limit": "1000",
                "x-ratelimit-remaining": "999",
                "x-ratelimit-reset": "soon",
                "retry-after": "1",
            },
        )

    with _client(handler, now=lambda: retrieved_at) as client:
        result = client.health()

    assert result.value["status"] == "healthy"
    assert result.metadata == ProviderResponseMetadata(
        endpoint="/health",
        safe_params=(),
        retrieved_at=retrieved_at,
        status_code=200,
        content_type="application/json",
        byte_length=len(result.raw_bytes),
        sha256=hashlib.sha256(result.raw_bytes).hexdigest(),
        rate_limit_limit="1000",
        rate_limit_remaining="999",
        rate_limit_reset="soon",
        retry_after="1",
        tier=None,
        pagination=(("has_more", "false"), ("page", "1")),
    )


@pytest.mark.parametrize(
    "filename",
    ["", ".", "..", "../file.htm", r"..\file.htm", "/file.htm", "C:file.htm", "%2e%2e%2ffile"],
)
def test_provider_filename_rejects_traversal(filename: str) -> None:
    with pytest.raises(EdgarToolsUnsafeRequestError, match="filename is unsafe"):
        validate_provider_filename(filename)


def test_provider_filename_accepts_inert_segment_and_rejects_bounds() -> None:
    assert validate_provider_filename("filing-10q.htm") == "filing-10q.htm"
    with pytest.raises(EdgarToolsUnsafeRequestError):
        validate_provider_filename("x" * 256)
    with pytest.raises(EdgarToolsUnsafeRequestError):
        validate_provider_filename("bad\x00name")


@pytest.mark.parametrize(
    ("base_url", "attempts", "size", "retry_after"),
    [
        ("https://example.test/v1/", 3, 100, 1.0),
        (EDGAR_TOOLS_BASE_URL, 0, 100, 1.0),
        (EDGAR_TOOLS_BASE_URL, 5, 100, 1.0),
        (EDGAR_TOOLS_BASE_URL, 1, 0, 1.0),
        (EDGAR_TOOLS_BASE_URL, 1, 25_000_001, 1.0),
        (EDGAR_TOOLS_BASE_URL, 1, 100, -1.0),
        (EDGAR_TOOLS_BASE_URL, 1, 100, float("inf")),
    ],
)
def test_client_configuration_bounds(
    base_url: str,
    attempts: int,
    size: int,
    retry_after: float,
) -> None:
    error = EdgarToolsUnsafeRequestError if base_url != EDGAR_TOOLS_BASE_URL else ValueError
    with pytest.raises(error):
        EdgarToolsClient(
            api_key=SecretStr("synthetic-key"),
            base_url=base_url,
            max_attempts=attempts,
            max_response_bytes=size,
            max_retry_after_seconds=retry_after,
        )


def test_empty_key_is_rejected_without_disclosure() -> None:
    with pytest.raises(ValueError, match="API key is empty") as captured:
        EdgarToolsClient(api_key=SecretStr(""))
    assert "synthetic" not in str(captured.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "/health",
        r"\health",
        "https://example.test/health",
        "//example.test/health",
        "companies/../health",
        "companies/%2e%2e/health",
        "companies//health",
        "health?redirect=true",
        "health#fragment",
    ],
)
def test_endpoint_allow_list_rejects_unsafe_relative_paths(endpoint: str) -> None:
    client = _client(lambda _: _json_response(200, {}))
    with pytest.raises(EdgarToolsUnsafeRequestError):
        client._request_json(endpoint)
    client.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://api.edgar.tools/v1/health",
        "https://example.test/v1/health",
        "https://user@api.edgar.tools/v1/health",
        "https://api.edgar.tools/not-v1/health",
    ],
)
def test_built_request_cannot_escape_host(url: str) -> None:
    request = httpx.Request("GET", url)
    with pytest.raises(EdgarToolsUnsafeRequestError):
        EdgarToolsClient._validate_built_request(request, endpoint="health")


def test_retry_after_supports_seconds_date_and_bounded_fallback() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    client = _client(lambda _: _json_response(200, {}), now=lambda: now)
    assert client._retry_delay(0, "2.5") == 2.5
    future = (now + timedelta(seconds=4)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert client._retry_delay(0, future) == 4.0
    assert client._retry_delay(3, "not-a-date") == 4.0
    assert client._retry_delay(0, "-1") == 0.5
    assert client._retry_delay(0, "100") == 30.0
    client.close()


@pytest.mark.parametrize(
    ("status", "payload", "error_type"),
    [
        (401, {"error": "invalid_api_key"}, EdgarToolsAuthenticationError),
        (403, {"required_tier": "professional"}, EdgarToolsTierBlockedError),
        (404, {"error": "not_found"}, EdgarToolsNotFoundError),
        (429, {"error": "monthly_quota_exceeded"}, EdgarToolsQuotaBlockedError),
        (429, {"error": "rate_limited"}, EdgarToolsRateLimitError),
        (500, {"error": "internal"}, EdgarToolsProviderUnavailableError),
        (400, {"error": "invalid"}, EdgarToolsSchemaError),
    ],
)
def test_http_error_taxonomy_has_only_safe_context(
    status: int,
    payload: dict[str, str],
    error_type: type[Exception],
) -> None:
    client = _client(lambda _: _json_response(status, payload), max_attempts=1)
    with pytest.raises(error_type) as captured:
        client.health()
    error = captured.value
    assert "invalid_api_key" not in str(error)
    if isinstance(error, EdgarToolsTierBlockedError):
        assert error.metadata is not None
        assert error.metadata.tier == "professional"
    client.close()


def test_retries_only_transient_statuses_and_honors_retry_after() -> None:
    statuses = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return _json_response(status, {"status": "ok"}, headers={"retry-after": "2"})

    client = _client(handler, sleep=sleeps.append)
    result = client.health()
    assert result.value == {"status": "ok"}
    assert sleeps == [2.0, 2.0]
    client.close()


def test_transport_failures_retry_then_raise() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        message = "synthetic timeout"
        raise httpx.ReadTimeout(message, request=request)

    client = _client(handler, sleep=sleeps.append)
    with pytest.raises(EdgarToolsTransportError, match="bounded retry"):
        client.health()
    assert calls == 3
    assert sleeps == [0.5, 1.0]
    client.close()


def test_non_retryable_error_is_attempted_once() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(401, {"error": "no"})

    client = _client(handler)
    with pytest.raises(EdgarToolsAuthenticationError):
        client.health()
    assert calls == 1
    client.close()


def test_redirect_is_rejected_without_following_location() -> None:
    client = _client(
        lambda _: httpx.Response(
            302,
            headers={"location": "https://www.sec.gov/Archives/forbidden"},
        )
    )
    with pytest.raises(EdgarToolsRedirectError):
        client.health()
    client.close()


@pytest.mark.parametrize(
    ("headers", "content"),
    [
        ({"content-type": "application/json", "content-length": "invalid"}, b"{}"),
        ({"content-type": "application/json", "content-length": "-1"}, b"{}"),
        ({"content-type": "application/json", "content-length": "11"}, b"{}"),
        ({"content-type": "text/html"}, b"{}"),
    ],
)
def test_response_size_and_content_type_are_bounded(
    headers: dict[str, str],
    content: bytes,
) -> None:
    client = _client(
        lambda _: httpx.Response(200, headers=headers, content=content),
        max_response_bytes=10,
    )
    with pytest.raises(EdgarToolsContentError):
        client.health()
    client.close()


def test_actual_stream_size_is_bounded_without_content_length() -> None:
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"oversized":true}',
        ),
        max_response_bytes=10,
    )
    with pytest.raises(EdgarToolsContentError):
        client.health()
    client.close()


@pytest.mark.parametrize(
    ("content", "content_type"),
    [(b"not-json", "application/json"), (b"\xff", "application/json")],
)
def test_malformed_json_and_encoding_fail_closed(content: bytes, content_type: str) -> None:
    client = _client(
        lambda _: httpx.Response(200, headers={"content-type": content_type}, content=content)
    )
    with pytest.raises(EdgarToolsSchemaError, match="valid UTF-8 JSON"):
        client.health()
    client.close()


def test_json_endpoint_rejects_missing_json_content() -> None:
    client = _client(lambda _: httpx.Response(200, content=b""))
    with pytest.raises(EdgarToolsContentError):
        client.health()
    client.close()


def test_json_decimal_never_routes_through_float() -> None:
    client = _client(
        lambda _: httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"value":0.1}'
        )
    )
    result = client.health()
    assert result.value["value"] == Decimal("0.1")
    assert not isinstance(result.value["value"], float)
    client.close()


def test_company_search_and_detail_are_typed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/search"):
            assert request.url.params["q"] == "TFC"
            return _json_response(
                200,
                {
                    "entities": [
                        {
                            "cik": "0000092230",
                            "name": "Truist Financial Corporation",
                            "ticker": "TFC",
                            "entity_type": "company",
                            "fiscal_year_end": "1231",
                        }
                    ]
                },
            )
        return _json_response(200, _company_payload())

    client = _client(handler)
    search = client.search_companies(" TFC ")
    detail = client.get_company("TFC")
    expected = CompanySummary(
        cik="0000092230",
        name="Truist Financial Corporation",
        ticker="TFC",
        entity_type="company",
        fiscal_year_end="1231",
    )
    assert search.value == (expected,)
    assert detail.value == expected
    assert seen == ["/v1/search", "/v1/companies/TFC"]
    client.close()


def test_company_detail_accepts_direct_ticker_and_string_ticker_list() -> None:
    payload = _company_payload()
    entity = cast("dict[str, object]", payload["entity"])
    company = cast("dict[str, object]", entity["company"])
    company["ticker"] = "TFC"
    entity["tickers"] = ["IGNORED"]
    client = _client(lambda _: _json_response(200, payload))
    assert client.get_company("TFC").value.ticker == "TFC"
    client.close()


@pytest.mark.parametrize("query", ["", "   ", "x" * 201])
def test_company_search_bounds(query: str) -> None:
    client = _client(lambda _: _json_response(200, {}))
    with pytest.raises(ValueError, match="search query"):
        client.search_companies(query)
    client.close()


def test_filing_discovery_detail_and_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filings"):
            page = int(request.url.params["page"])
            return _json_response(200, _filing_payload(page=page, has_more=page == 1))
        return _json_response(
            200,
            {
                "filing": {
                    "cik": "0000092230",
                    "accession_number": "0000092230-26-00001",
                    "filing_date": "2026-08-01",
                    "ticker": "TFC",
                    "sec_url": "https://www.sec.gov/Archives/example",
                }
            },
        )

    client = _client(handler)
    first = client.list_company_filings("0000092230", page=1, limit=1, form_type="10-Q")
    assert isinstance(first.value, FilingPage)
    assert first.value.has_more is True
    assert first.value.filings[0].provider_reported_sec_url is not None
    collection = client.list_all_company_filings("0000092230", max_pages=2, max_items=2)
    assert isinstance(collection, FilingCollection)
    assert len(collection.filings) == 2
    assert len(collection.page_metadata) == 2
    detail = client.get_filing("0000092230", "0000092230-26-00001")
    assert detail.value == FilingDetail(
        cik="0000092230",
        accession_number="0000092230-26-00001",
        filing_date="2026-08-01",
        ticker="TFC",
        provider_reported_sec_url="https://www.sec.gov/Archives/example",
    )
    client.close()


@pytest.mark.parametrize(("page", "limit"), [(0, 1), (21, 1), (1, 0), (1, 101)])
def test_filing_page_bounds(page: int, limit: int) -> None:
    client = _client(lambda _: _json_response(200, {}))
    with pytest.raises(ValueError, match="pagination"):
        client.list_company_filings("0000092230", page=page, limit=limit)
    client.close()


def test_filing_pagination_rejects_bad_boolean() -> None:
    payload = _filing_payload()
    cast("dict[str, object]", payload["pagination"])["has_more"] = "false"
    client = _client(lambda _: _json_response(200, payload))
    with pytest.raises(EdgarToolsSchemaError, match="has_more"):
        client.list_company_filings("0000092230")
    client.close()


@pytest.mark.parametrize(("max_pages", "max_items"), [(0, 1), (21, 1), (1, 0), (1, 2001)])
def test_aggregate_pagination_configuration_bounds(max_pages: int, max_items: int) -> None:
    client = _client(lambda _: _json_response(200, {}))
    with pytest.raises(ValueError, match="pagination bounds"):
        client.list_all_company_filings("0000092230", max_pages=max_pages, max_items=max_items)
    client.close()


def test_aggregate_pagination_rejects_stall_repeat_item_and_page_bounds() -> None:
    empty_client = _client(
        lambda _: _json_response(200, {**_filing_payload(has_more=True), "filings": []})
    )
    with pytest.raises(EdgarToolsSchemaError, match="stalled"):
        empty_client.list_all_company_filings("0000092230")
    empty_client.close()

    repeat_client = _client(lambda _: _json_response(200, _filing_payload(has_more=True)))
    with pytest.raises(EdgarToolsSchemaError, match="item bound"):
        repeat_client.list_all_company_filings("0000092230", max_items=1)
    with pytest.raises(EdgarToolsSchemaError, match="repeated"):
        repeat_client.list_all_company_filings("0000092230", max_items=3)
    with pytest.raises(EdgarToolsSchemaError, match="page bound"):
        repeat_client.list_all_company_filings("0000092230", max_pages=1, max_items=3)
    repeat_client.close()


def test_document_listing_and_fetch_preserve_provenance_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return _json_response(
                200,
                {
                    "documents": [
                        {
                            "filename": "filing.htm",
                            "type": "10-Q",
                            "sequence": 1,
                            "size": 4,
                            "sec_url": "https://www.sec.gov/Archives/example/filing.htm",
                        }
                    ]
                },
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"test")

    client = _client(handler)
    listing = client.list_filing_documents("0000092230", "0000092230-26-000001")
    assert listing.value == (
        FilingDocument(
            filename="filing.htm",
            document_type="10-Q",
            sequence=1,
            size=4,
            provider_reported_sec_url="https://www.sec.gov/Archives/example/filing.htm",
        ),
    )
    fetched = client.fetch_filing_document("0000092230", "0000092230-26-000001", "filing.htm")
    assert fetched.value == b"test"
    assert fetched.metadata.content_type == "text/html"
    client.close()


def test_document_listing_rejects_unsafe_provider_filename() -> None:
    client = _client(lambda _: _json_response(200, {"documents": [{"filename": "../secret"}]}))
    with pytest.raises(EdgarToolsUnsafeRequestError):
        client.list_filing_documents("0000092230", "accession")
    client.close()


def test_specialized_json_endpoints_send_bounded_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response(200, {"value": 0.1, "tier": "analyst"})

    client = _client(handler)
    financials = client.get_structured_financials("TFC", include_segments=False)
    client.search_full_text(query="servicing", ciks="92230", forms="10-Q")
    client.search_disclosures("TFC", query="servicing", max_results=5)
    assert financials.value["value"] == Decimal("0.1")
    assert financials.metadata.tier == "analyst"
    assert requests[0].url.params["include_segments"] == "false"
    assert requests[1].url.params["size"] == "10"
    assert requests[2].url.params["max_results"] == "5"
    client.close()


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.get_structured_financials("TFC", period_type="monthly"),
        lambda client: client.search_full_text(query="", ciks="1", forms="10-Q"),
        lambda client: client.search_full_text(query="x", ciks="1", forms="10-Q", size=101),
        lambda client: client.search_disclosures("TFC", query=""),
        lambda client: client.search_disclosures("TFC", query="x", max_results=26),
    ],
)
def test_specialized_endpoint_input_bounds(call: Callable[[EdgarToolsClient], object]) -> None:
    client = _client(lambda _: _json_response(200, {}))
    with pytest.raises(ValueError, match="EdgarTools"):
        call(client)
    client.close()


@pytest.mark.parametrize(
    ("payload", "call"),
    [
        ({"entities": {}}, lambda client: client.search_companies("TFC")),
        ({"entities": [{}]}, lambda client: client.search_companies("TFC")),
        ({"entity": []}, lambda client: client.get_company("TFC")),
        ({"filings": {}, "pagination": {}}, lambda client: client.list_company_filings("1")),
        ({"documents": {}}, lambda client: client.list_filing_documents("1", "a")),
        ({"filing": {}}, lambda client: client.get_filing("1", "a")),
        ([], lambda client: client.health()),
    ],
)
def test_unexpected_endpoint_schemas_fail_closed(
    payload: object,
    call: Callable[[EdgarToolsClient], object],
) -> None:
    client = _client(lambda _: _json_response(200, payload))
    with pytest.raises(EdgarToolsSchemaError):
        call(client)
    client.close()


def test_error_repr_does_not_contain_key_or_response_body() -> None:
    secret = "synthetic-secret-never-render"
    client = EdgarToolsClient(
        api_key=SecretStr(secret),
        transport=httpx.MockTransport(
            lambda _: _json_response(401, {"detail": "private provider message"})
        ),
        max_attempts=1,
    )
    with pytest.raises(EdgarToolsAuthenticationError) as captured:
        client.health()
    rendered = repr(captured.value)
    assert secret not in rendered
    assert "private provider message" not in rendered
    client.close()
