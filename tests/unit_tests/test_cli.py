"""Tests for the network-free CLI."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncState,
    EdgarToolsSyncSummary,
    PreparedEdgarToolsSync,
)


def test_doctor_json_contains_only_safe_readiness_data(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SYNTHETIC_TEST_PROVIDER_KEY_NOT_VALID"
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    identity = "Servicing Lens synthetic-contact@example.test"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)
    monkeypatch.setenv("EDGAR_API_KEY", "synthetic-edgar-tools-test-key")

    exit_code = main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["capabilities"]["status"] == "ready"
    assert payload["configuration"]["edgar_identity_configured"] is True
    assert "edgar_api_key_configured" not in payload["configuration"]
    assert "edgar_api_base_url" not in payload["configuration"]
    assert "SYNTHETIC_TEST_KEY" not in captured.out
    assert identity not in captured.out
    assert secret not in captured.out


def test_cli_reports_invalid_config_without_echo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["doctor"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out
    assert captured.err == ""


def test_doctor_text_entrypoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["doctor"]) == 0
    assert "customer data access: disabled" in capsys.readouterr().out


def test_python_module_entrypoint(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["msi", "doctor"])
    with pytest.raises(SystemExit) as error:
        runpy.run_module("mortgage_servicing_dashboard.__main__", run_name="__main__")

    assert error.value.code == 0
    assert "status: ready" in capsys.readouterr().out


def test_sync_requires_identity_and_valid_since(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    assert main(["sync", "--company", "TFC", "--dry-run"]) == 2
    assert "EDGAR_IDENTITY" in capsys.readouterr().err

    monkeypatch.setenv("EDGAR_IDENTITY", "Synthetic Operator operator@example.test")
    assert main(["sync", "--company", "TFC", "--since", "invalid"]) == 2
    assert "Invalid isoformat" in capsys.readouterr().err


class _StubPipeline:
    state = EdgarToolsSyncState.DISCOVERED
    companies: ClassVar[list[EdgarToolsCompany]] = []
    persistences: ClassVar[list[object | None]] = []
    options: ClassVar[list[dict[str, object]]] = []
    events: ClassVar[list[str]] = []
    committed_batches: ClassVar[list[tuple[PreparedEdgarToolsSync, ...]]] = []

    def __init__(self, **options: object) -> None:
        type(self).companies = []
        type(self).options = []
        type(self).events = []
        type(self).committed_batches = []
        type(self).persistences.append(options.get("persistence"))

    def prepare_company(
        self,
        company: EdgarToolsCompany,
        **options: object,
    ) -> PreparedEdgarToolsSync:
        type(self).companies.append(company)
        type(self).options.append(options)
        type(self).events.append(f"prepare:{company.company_id}")
        return PreparedEdgarToolsSync(
            summary=EdgarToolsSyncSummary(
                company_id=company.company_id,
                ticker=company.ticker,
                cik=company.cik,
                dry_run=bool(options["dry_run"]),
                overlap_start=None,
                discovered_count=0,
                eligible_count=0,
                filing_results=(),
                retained_metadata_evidence_ids=(),
                terminal_state=type(self).state,
            ),
            validated_filings=(),
        )

    def persist_prepared_batch(
        self,
        prepared: tuple[PreparedEdgarToolsSync, ...],
    ) -> tuple[EdgarToolsSyncSummary, ...]:
        type(self).events.append("persist")
        type(self).committed_batches.append(prepared)
        return tuple(item.summary for item in prepared)


def test_sync_all_has_safe_bounded_output_and_nonzero_blocker_exit(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identity = "Synthetic Operator operator@example.test"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsAdapter.from_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("mortgage_servicing_dashboard.cli.EdgarToolsSyncPipeline", _StubPipeline)
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.create_database_engine",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not create an engine"),
    )
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.initialize_schema",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not initialize a schema"),
    )
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not seed legacy data"),
    )

    _StubPipeline.state = EdgarToolsSyncState.DISCOVERED
    assert main(["sync", "--all", "--dry-run", "--runtime-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["provider"] == "PUBLIC_EDGARTOOLS"
    assert [item.ticker for item in _StubPipeline.companies] == ["TFC", "PFSI"]
    assert _StubPipeline.options == [
        {"since": None, "dry_run": True, "known_accessions": frozenset()},
        {"since": None, "dry_run": True, "known_accessions": frozenset()},
    ]
    assert _StubPipeline.events == ["prepare:tfc", "prepare:pfsi"]
    assert payload["results"][0]["published_count"] == 0
    assert identity not in captured.out
    assert _StubPipeline.persistences[-1] is None

    _StubPipeline.state = EdgarToolsSyncState.PARSER_UNQUALIFIED
    assert main(["sync", "--company", "TFC", "--dry-run"]) == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["terminal_state"] == (
        "PARSER_UNQUALIFIED"
    )


def test_sync_reports_safe_retention_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGAR_IDENTITY", "Synthetic Operator operator@example.test")

    def fail_store(_: Path) -> object:
        message = "synthetic retention failure"
        raise OSError(message)

    monkeypatch.setattr("mortgage_servicing_dashboard.cli.GeneralEvidenceStore", fail_store)
    assert main(["sync", "--company", "TFC", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "synthetic retention failure" in captured.err


def test_live_sync_requires_explicit_isolated_database_url(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGAR_IDENTITY", "Synthetic Operator operator@example.test")
    monkeypatch.delenv("MSI_DATABASE_URL", raising=False)

    assert main(["sync", "--company", "TFC"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit isolated --database-url" in captured.err


def test_live_sync_all_loads_known_accessions_then_prepares_both_before_one_commit(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EDGAR_IDENTITY", "Synthetic Operator operator@example.test")
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsAdapter.from_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("mortgage_servicing_dashboard.cli.EdgarToolsSyncPipeline", _StubPipeline)

    class FakeEngine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.create_database_engine",
        lambda *_args, **_kwargs: engine,
    )

    class FakeRepository:
        def __init__(self, actual_engine: object) -> None:
            assert actual_engine is engine

        def known_accessions(self, company_id: str) -> frozenset[str]:
            return frozenset({f"known-{company_id}"})

    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.AtomicEdgarToolsRepository",
        FakeRepository,
    )
    _StubPipeline.state = EdgarToolsSyncState.VALIDATED
    database_url = f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}"

    assert main(["sync", "--all", "--database-url", database_url]) == 0

    assert _StubPipeline.options == [
        {
            "since": None,
            "dry_run": False,
            "known_accessions": frozenset({"known-tfc"}),
        },
        {
            "since": None,
            "dry_run": False,
            "known_accessions": frozenset({"known-pfsi"}),
        },
    ]
    assert _StubPipeline.events == ["prepare:tfc", "prepare:pfsi", "persist"]
    assert len(_StubPipeline.committed_batches) == 1
    assert engine.disposed is True
    assert json.loads(capsys.readouterr().out)["provider"] == "PUBLIC_EDGARTOOLS"


def test_validate_empty_database_never_invokes_legacy_seed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("validate must not seed legacy data"),
    )
    database_url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"

    assert main(["validate", "--database-url", database_url]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["companies"] == 0
    assert payload["metrics"] == 0
    assert payload["observations"] == 0
    assert payload["latest_period_end"] == "None"


def test_calendar_and_serve_empty_database_never_invoke_legacy_seed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("read commands must not seed legacy data"),
    )
    calendar_url = f"sqlite:///{(tmp_path / 'empty-calendar.db').as_posix()}"
    assert main(["calendar", "--database-url", calendar_url]) == 0
    assert "calendar" in json.loads(capsys.readouterr().out)

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.uvicorn.run",
        lambda _app, *, host, port: calls.append((host, port)),
    )
    serve_url = f"sqlite:///{(tmp_path / 'empty-serve.db').as_posix()}"
    assert main(["serve", "--database-url", serve_url]) == 0
    assert calls == [("127.0.0.1", 8000)]
