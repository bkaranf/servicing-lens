"""Strictly opt-in, non-publishing smoke test for the hosted EdgarTools boundary."""

from __future__ import annotations

import os

import pytest

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.edgar_tools import EdgarToolsClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("MSD_RUN_LIVE_EDGAR_TOOLS_SMOKE") != "1"
        or not os.environ.get("EDGAR_API_KEY"),
        reason="requires explicit EdgarTools live-smoke opt in and environment key",
    ),
]


def test_live_edgar_tools_read_only_surface() -> None:
    """Resolve TFC and require one exact governed filing's document listing."""
    settings = AppSettings()
    with EdgarToolsClient(api_key=settings.require_edgar_api_key()) as client:
        health = client.health()
        assert health.value.get("status") in {"ok", "healthy"}
        company = client.get_company("TFC")
        assert company.value.cik == "0000092230"
        filings = client.list_company_filings("0000092230", page=1, limit=10, form_type="10-Q")
        assert filings.value.filings
        documents = client.list_filing_documents("0000092230", "0000092230-26-000099")
        assert documents.value
