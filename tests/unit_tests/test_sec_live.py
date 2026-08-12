"""Offline contracts for opt-in live SEC discovery and acquisition."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.ingestion import (
    discover_live_sec_filings,
    run_live_sec_ingestion,
)
from mortgage_servicing_dashboard.repository import (
    config_directory,
    load_stage_a_configuration,
)
from mortgage_servicing_dashboard.sources import (
    AcquiredDocument,
    PublicSourceError,
    RecordedSourceDefinition,
    SecClient,
    parse_sec_submissions,
    sec_submissions_url,
)

_TEST_USER_AGENT = "Servicing Lens tests contact@example.test"


def _submissions_body(*, cik: str, accession: str, period_end: str) -> bytes:
    return json.dumps(
        {
            "cik": str(int(cik)),
            "filings": {
                "recent": {
                    "accessionNumber": [accession, "0000000000-24-000001"],
                    "filingDate": ["2026-07-29", "2024-01-01"],
                    "reportDate": [period_end, "2023-12-31"],
                    "acceptanceDateTime": ["2026-07-29T12:00:00Z", ""],
                    "form": ["8-K", "4"],
                    "primaryDocument": ["form8-k.htm", "ownership.xml"],
                    "items": ["2.02,9.01", ""],
                    "isXBRL": [1, 0],
                    "isInlineXBRL": [1, 0],
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _configured_live_transport() -> tuple[
    httpx.MockTransport,
    dict[str, int],
    dict[str, RecordedSourceDefinition],
]:
    root = config_directory()
    universe, _, data = load_stage_a_configuration(root)
    companies = cast("list[dict[str, Any]]", universe["companies"])
    source_payloads = cast("dict[str, dict[str, Any]]", data["sources"])
    definitions = {
        key: RecordedSourceDefinition.from_mapping(
            key=key,
            payload=payload,
            config_root=root,
        )
        for key, payload in source_payloads.items()
    }
    company_by_id = {str(item["id"]): item for item in companies}
    source_by_url = {source.url: source for source in definitions.values()}
    source_by_cik = {
        str(company_by_id[source.company_id]["cik"]): source for source in definitions.values()
    }
    state = {"document_version": 1, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        assert request.headers["user-agent"] == _TEST_USER_AGENT
        assert request.headers["accept-encoding"] == "identity"
        url = str(request.url)
        for cik, indexed_source in source_by_cik.items():
            if url == sec_submissions_url(cik):
                return httpx.Response(
                    200,
                    content=_submissions_body(
                        cik=cik,
                        accession=indexed_source.accession,
                        period_end=indexed_source.period_end,
                    ),
                    headers={"content-type": "application/json"},
                )
        document_source = source_by_url.get(url)
        if document_source is None:
            return httpx.Response(404)
        content = document_source.fixture_path.read_bytes()
        if state["document_version"] > 1:
            content += b"\n"
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": f'"fixture-v{state["document_version"]}"',
            },
        )

    return httpx.MockTransport(handler), state, definitions


def test_submissions_parser_validates_identity_filters_and_bounds(tmp_path: Path) -> None:
    cik = "0000092230"
    url = sec_submissions_url(cik)
    content = _submissions_body(
        cik=cik,
        accession="0000092230-26-000096",
        period_end="2026-06-30",
    )
    document = AcquiredDocument(
        url=url,
        content=content,
        media_type="application/json",
        sha256=hashlib.sha256(content).hexdigest(),
        cache_path=tmp_path / "submissions.bin",
    )

    filings = parse_sec_submissions(
        document=document,
        company_id="tfc",
        cik=cik,
        forms={"8-K"},
        filed_on_or_after=date(2025, 7, 1),
        max_filings=1,
    )

    assert len(filings) == 1
    assert filings[0].accession == "0000092230-26-000096"
    assert filings[0].cik == cik
    assert filings[0].items == ("2.02", "9.01")
    assert filings[0].primary_document_url.startswith(
        "https://www.sec.gov/Archives/edgar/data/92230/"
    )
    with pytest.raises(PublicSourceError, match="does not match"):
        parse_sec_submissions(
            document=document,
            company_id="pfsi",
            cik="0001745916",
        )
    with pytest.raises(ValueError, match="max_filings"):
        parse_sec_submissions(document=document, company_id="tfc", cik=cik, max_filings=0)


def test_sec_client_revalidates_cache_and_retries_only_transient_errors(
    tmp_path: Path,
) -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        if calls == 3:
            assert request.headers["if-none-match"] == '"v1"'
            return httpx.Response(304, request=request)
        return httpx.Response(
            200,
            content=b"official response",
            headers={"content-type": "text/html", "etag": '"v1"'},
            request=request,
        )

    url = "https://www.sec.gov/Archives/edgar/data/92230/filing.htm"
    with SecClient(
        user_agent=_TEST_USER_AGENT,
        cache_directory=tmp_path / "cache",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
        jitter=lambda _maximum: 0,
    ) as client:
        first = client.acquire(url)
        cached = client.acquire(url)
        revalidated = client.acquire(url, refresh=True)
    assert calls == 3
    assert delays == [0.5]
    assert first == cached == revalidated

    permanent_calls = 0

    def permanent(request: httpx.Request) -> httpx.Response:
        nonlocal permanent_calls
        permanent_calls += 1
        return httpx.Response(404, request=request)

    with (
        SecClient(
            user_agent=_TEST_USER_AGENT,
            cache_directory=tmp_path / "permanent",
            minimum_interval_seconds=0,
            transport=httpx.MockTransport(permanent),
        ) as client,
        pytest.raises(PublicSourceError, match="permanent"),
    ):
        client.acquire(url)
    assert permanent_calls == 1


def test_live_ingestion_discovers_each_cik_and_retains_changed_bytes(
    tmp_path: Path,
) -> None:
    transport, state, definitions = _configured_live_transport()
    cache = tmp_path / "cache"
    evidence = tmp_path / "evidence"

    first = run_live_sec_ingestion(
        user_agent=_TEST_USER_AGENT,
        cache_directory=cache,
        retention_root=evidence,
        transport=transport,
    )
    state["document_version"] = 2
    second = run_live_sec_ingestion(
        user_agent=_TEST_USER_AGENT,
        cache_directory=cache,
        retention_root=evidence,
        transport=transport,
    )

    assert len(first) == len(second) == len(definitions) == 2
    assert {item.company_id for item in first} == {"tfc", "pfsi"}
    for old, new in zip(first, second, strict=True):
        assert old.configured_source_key == new.configured_source_key
        assert old.source_key != new.source_key
        assert old.acquired_document.sha256 != new.acquired_document.sha256
        assert old.retention_path.is_file()
        assert new.retention_path.is_file()
        assert old.retention_path != new.retention_path
        assert old.runtime_definition.representation == "ORIGINAL_HTTP_RESPONSE"
        assert old.runtime_definition.capture_method == "sec_http_get"
        assert old.accession.replace("-", "") in old.source_key
        assert old.acquired_at.tzinfo is not None
        assert old.retained_at.tzinfo is not None
        assert "content" not in old.as_payload()
    assert state["calls"] == 8


def test_live_discovery_is_bounded_and_database_free(tmp_path: Path) -> None:
    transport, _, _ = _configured_live_transport()
    filings = discover_live_sec_filings(
        user_agent=_TEST_USER_AGENT,
        company="TFC",
        cache_directory=tmp_path / "cache",
        retention_root=tmp_path / "evidence",
        max_filings_per_company=1,
        transport=transport,
    )
    assert len(filings) == 1
    assert filings[0].company_id == "tfc"
    assert not list(tmp_path.glob("*.db"))


@pytest.mark.parametrize("command", [["discover", "--live"], ["ingest", "--live"]])
def test_live_cli_fails_closed_without_sec_identity(
    command: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MSD_SEC_USER_AGENT", raising=False)
    database = tmp_path / "must-not-exist.db"
    arguments = [*command, "--database-url", f"sqlite:///{database.as_posix()}"]
    if command[0] == "discover":
        arguments = command

    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MSD_SEC_USER_AGENT" in captured.err
    assert not database.exists()
