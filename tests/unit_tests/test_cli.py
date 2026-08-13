"""Tests for the network-free CLI."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self

import pytest

from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.edgar_tools_evidence import EdgarToolsEvidenceError
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncState,
    EdgarToolsSyncSummary,
)


def test_doctor_json_contains_only_safe_readiness_data(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SYNTHETIC_TEST_PROVIDER_KEY_NOT_VALID"
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    monkeypatch.delenv("MSD_MODEL", raising=False)
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")
    identity = "Servicing Lens synthetic-contact@example.test"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)
    monkeypatch.setenv("EDGAR_API_KEY", "synthetic-edgar-tools-test-key")

    exit_code = main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["capabilities"]["status"] == "ready"
    assert payload["configuration"]["remote_tracing_allowed"] is False
    assert payload["configuration"]["edgar_identity_configured"] is True
    assert payload["configuration"]["edgar_api_key_configured"] is True
    assert payload["configuration"]["edgar_api_base_url"] == "https://api.edgar.tools/v1/"
    assert "SYNTHETIC_TEST_KEY" not in captured.out
    assert identity not in captured.out
    assert secret not in captured.out


def test_cli_reports_invalid_config_without_echo(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MSD_MODEL", raising=False)
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "true")

    exit_code = main(["doctor"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "Configuration is invalid" in captured.err


def test_doctor_text_entrypoint(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")
    monkeypatch.delenv("MSD_MODEL", raising=False)

    assert main(["doctor"]) == 0
    assert "customer data access: disabled" in capsys.readouterr().out


def test_python_module_entrypoint(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["msd-foundation", "doctor"])
    monkeypatch.setenv("MSD_ENABLE_MODEL_CALLS", "false")
    monkeypatch.delenv("MSD_MODEL", raising=False)

    with pytest.raises(SystemExit) as error:
        runpy.run_module("mortgage_servicing_dashboard.__main__", run_name="__main__")

    assert error.value.code == 0
    assert "status: ready" in capsys.readouterr().out


def test_sync_requires_key_and_valid_since(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGAR_API_KEY", raising=False)
    assert main(["sync", "--company", "TFC", "--dry-run"]) == 2
    assert "EDGAR_API_KEY" in capsys.readouterr().err

    monkeypatch.setenv("EDGAR_API_KEY", "synthetic-edgar-tools-key")
    assert main(["sync", "--company", "TFC", "--since", "invalid"]) == 2
    assert "Invalid isoformat" in capsys.readouterr().err


class _StubClient:
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _: type[BaseException] | None,
        __: BaseException | None,
        ___: TracebackType | None,
    ) -> None:
        return None


class _StubPipeline:
    state = EdgarToolsSyncState.DISCOVERED
    companies: ClassVar[list[EdgarToolsCompany]] = []

    def __init__(self, **_: object) -> None:
        type(self).companies = []

    def sync_company(
        self,
        company: EdgarToolsCompany,
        **options: object,
    ) -> EdgarToolsSyncSummary:
        assert options == {"since": None, "dry_run": True}
        type(self).companies.append(company)
        return EdgarToolsSyncSummary(
            company_id=company.company_id,
            ticker=company.ticker,
            cik=company.cik,
            dry_run=True,
            overlap_start=None,
            discovered_count=0,
            eligible_count=0,
            filing_results=(),
            retained_metadata_evidence_ids=(),
            terminal_state=type(self).state,
        )


def test_sync_all_has_safe_bounded_output_and_nonzero_blocker_exit(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EDGAR_API_KEY", "synthetic-edgar-tools-key")
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsClient", lambda **_: _StubClient()
    )
    monkeypatch.setattr("mortgage_servicing_dashboard.cli.EdgarToolsSyncPipeline", _StubPipeline)

    _StubPipeline.state = EdgarToolsSyncState.DISCOVERED
    assert main(["sync", "--all", "--dry-run", "--runtime-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["provider"] == "EDGAR_TOOLS_REST_API"
    assert [item.ticker for item in _StubPipeline.companies] == ["TFC", "PFSI"]
    assert payload["results"][0]["published_count"] == 0
    assert "synthetic-edgar-tools-key" not in captured.out

    _StubPipeline.state = EdgarToolsSyncState.PARSER_UNQUALIFIED
    assert main(["sync", "--company", "TFC", "--dry-run"]) == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["terminal_state"] == (
        "PARSER_UNQUALIFIED"
    )


def test_sync_reports_safe_retention_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGAR_API_KEY", "synthetic-edgar-tools-key")
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsClient", lambda **_: _StubClient()
    )

    def fail_store(_: Path) -> object:
        message = "synthetic retention failure"
        raise EdgarToolsEvidenceError(message)

    monkeypatch.setattr("mortgage_servicing_dashboard.cli.EdgarToolsEvidenceStore", fail_store)
    assert main(["sync", "--company", "TFC"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "synthetic retention failure" in captured.err
