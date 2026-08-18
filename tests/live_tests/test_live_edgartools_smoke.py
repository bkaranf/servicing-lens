"""One strictly opt-in sequential smoke test for the public edgartools lane."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.edgartools_adapter import (
    EdgarBootstrapConfig,
    EdgarToolsAdapter,
)
from mortgage_servicing_dashboard.edgartools_adapter.retention import GeneralEvidenceStore

pytestmark = [
    pytest.mark.live,
    pytest.mark.enable_socket,
    pytest.mark.skipif(
        os.environ.get("MSI_RUN_LIVE_EDGARTOOLS") != "1" or not os.environ.get("EDGAR_IDENTITY"),
        reason="requires explicit public-edgartools live opt-in and EDGAR_IDENTITY",
    ),
]


@dataclass(frozen=True, slots=True)
class _GovernedFiling:
    ticker: str
    cik: str
    accession: str
    primary_document: str
    sha256: str
    byte_length: int


_GOVERNED_FILINGS = (
    _GovernedFiling(
        ticker="TFC",
        cik="0000092230",
        accession="0000092230-26-000099",
        primary_document="tfc-20260630.htm",
        sha256="8b4e75df610503670a55802f4a29e36fae8bb9195b78abddd0272b11f1d0efed",
        byte_length=6_452_566,
    ),
    _GovernedFiling(
        ticker="PFSI",
        cik="0001745916",
        accession="0001104659-26-090486",
        primary_document="pfsi-20260630x10q.htm",
        sha256="9b0062e5c2d62e2abe50a89a5b6f606140d6eaee6e3ef27ee824529292642155",
        byte_length=7_492_215,
    ),
)


def test_live_public_edgartools_tfc_then_pfsi_replays_exact_evidence(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Process the governed TFC filing before PFSI through one public adapter."""
    settings = AppSettings()
    store = GeneralEvidenceStore(tmp_path / "evidence")
    adapter = EdgarToolsAdapter.from_config(
        EdgarBootstrapConfig(
            identity=settings.require_edgar_identity(),
            runtime_root=tmp_path,
        ),
        evidence_store=store,
    )
    processed: list[str] = []

    for governed in _GOVERNED_FILINGS:
        company = adapter.company(governed.ticker)
        assert company.cik == governed.cik
        assert governed.ticker in company.tickers

        filing = adapter.filing(governed.accession, expected_cik=governed.cik)
        assert filing.cik == governed.cik
        assert filing.accession_number == governed.accession
        assert filing.primary_document == governed.primary_document
        assert filing.homepage_url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert governed.accession.replace("-", "") in filing.homepage_url

        attachments = adapter.attachments(governed.accession, expected_cik=governed.cik)
        primary = next(
            attachment
            for attachment in attachments
            if attachment.document == governed.primary_document
        )
        assert primary.is_primary is True
        assert primary.cik == governed.cik
        assert primary.accession_number == governed.accession
        assert primary.source_url.startswith("https://www.sec.gov/Archives/edgar/data/")

        acquisition = adapter.acquire_attachment(
            governed.accession,
            governed.primary_document,
            expected_cik=governed.cik,
            retain=True,
        )
        assert acquisition.attachment == primary
        assert acquisition.content.cik == governed.cik
        assert acquisition.content.accession_number == governed.accession
        assert acquisition.content.document == governed.primary_document
        assert acquisition.content.byte_length == governed.byte_length
        assert acquisition.content.sha256 == governed.sha256
        assert hashlib.sha256(acquisition.content.content).hexdigest() == governed.sha256
        assert acquisition.retained is not None
        assert acquisition.retained.content_sha256 == governed.sha256
        assert acquisition.retained.byte_length == governed.byte_length
        assert acquisition.retained.retention_location == f"content-sha256://{governed.sha256}"

        xbrl = adapter.filing_xbrl(governed.accession, expected_cik=governed.cik)
        assert xbrl is not None
        assert xbrl.cik == governed.cik
        assert xbrl.accession_number == governed.accession
        assert xbrl.source_document == governed.primary_document
        assert xbrl.source_url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert xbrl.contexts
        assert xbrl.units
        assert xbrl.facts
        raw_fact = next(fact for fact in xbrl.facts if fact.raw_value)
        assert raw_fact.taxonomy
        assert raw_fact.concept
        assert raw_fact.raw_value == raw_fact.raw_value.strip()
        assert raw_fact.context_ref == raw_fact.context.context_id
        assert raw_fact.context.period_type or raw_fact.context.period_instant

        replayed = store.retain(acquisition.content)
        assert replayed.content_sha256 == acquisition.retained.content_sha256
        assert replayed.byte_length == acquisition.retained.byte_length
        assert replayed.retention_location == acquisition.retained.retention_location
        assert replayed.representation == acquisition.retained.representation
        assert replayed.capture_method == acquisition.retained.capture_method
        retained_files = tuple((tmp_path / "evidence").rglob("*.bin"))
        assert len(retained_files) == len(processed) + 1
        assert any(path.read_bytes() == acquisition.content.content for path in retained_files)
        processed.append(governed.ticker)

    assert processed == ["TFC", "PFSI"]
