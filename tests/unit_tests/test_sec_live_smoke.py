"""Explicitly opt-in smoke check for the official SEC submissions endpoint."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mortgage_servicing_dashboard.sources import (
    SecClient,
    parse_sec_submissions,
    sec_submissions_url,
)

pytestmark = [pytest.mark.live, pytest.mark.enable_socket]


@pytest.mark.skipif(
    os.environ.get("MSD_RUN_LIVE_SEC_SMOKE") != "1" or not os.environ.get("MSD_SEC_USER_AGENT"),
    reason="set MSD_RUN_LIVE_SEC_SMOKE=1 and MSD_SEC_USER_AGENT to opt in",
)
def test_official_sec_submissions_smoke(tmp_path: Path) -> None:
    cik = "0000092230"
    user_agent = os.environ["MSD_SEC_USER_AGENT"]
    with SecClient(user_agent=user_agent, cache_directory=tmp_path, max_attempts=2) as client:
        document = client.acquire(sec_submissions_url(cik), refresh=True)
    filings = parse_sec_submissions(
        document=document,
        company_id="tfc",
        cik=cik,
        max_filings=1,
    )
    assert document.media_type == "application/json"
    assert filings
