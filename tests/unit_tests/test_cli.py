"""Tests for the network-free CLI."""

from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import ClassVar

import pytest
import scripts.phase5_replay as phase5_replay_module
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import mortgage_servicing_dashboard.cli as cli_module
from mortgage_servicing_dashboard.cli import main
from mortgage_servicing_dashboard.database import (
    SourceEvidence,
    create_database_engine,
    default_database_url,
    initialize_schema,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncState,
    EdgarToolsSyncSummary,
    PreparedEdgarToolsSync,
)
from mortgage_servicing_dashboard.edgartools_adapter import (
    EdgarBootstrapConfig,
    EdgarToolsAdapter,
)
from mortgage_servicing_dashboard.financial_discovery import FinancialFieldRegistry

_PHASE5_B_TICKERS = ("TFC", "WFC", "JPM", "BAC", "USB", "PFSI", "RKT", "UWMC", "RITM", "LDI")
_PHASE5_B_IDS = tuple(ticker.lower() for ticker in _PHASE5_B_TICKERS)
_ROOT = Path(__file__).resolve().parents[2]


def _current_database_url(tmp_path: Path, name: str = "current.db") -> str:
    database_url = f"sqlite:///{(tmp_path / name).as_posix()}"
    engine = create_database_engine(database_url)
    initialize_schema(engine)
    engine.dispose()
    return database_url


def test_doctor_json_contains_only_safe_readiness_data(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SYNTHETIC_TEST_PROVIDER_KEY_NOT_VALID"
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    identity = "Servicing Lens synthetic-contact@example.test"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)

    exit_code = main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["capabilities"]["status"] == "ready"
    assert payload["stage"] == "expanded_comparison"
    assert payload["capabilities"]["phase"] == "expanded_comparison"
    assert payload["readiness"] == {
        "checked": [
            "packaged_phase5_configuration",
            "cohort_a_source_manifest",
            "cohort_b_source_manifest",
            "financial_field_mapping_version",
            "registered_company_scope",
            "network_default",
        ],
        "network_contacted": False,
        "not_checked": [
            "database_contents",
            "live_sec_connectivity",
            "production_readiness",
        ],
        "phase5_runtime_configuration": "available",
        "status": "ready_for_local_read_only_workflows",
    }
    assert payload["registered_scope"] == {
        "bank_count": 5,
        "cohort": "phase5-cohort-b",
        "company_count": 10,
        "default_for_live_commands": True,
        "nonbank_count": 5,
    }
    assert tuple(payload["universe"]) == _PHASE5_B_TICKERS
    assert payload["phase5_runtime"] == {
        "financial_mapping_version": "financial-fields-phase5-v1",
        "source_case_counts": {"cohort_a": 64, "cohort_b": 160},
        "source_manifest_versions": {
            "cohort_a": "phase5-cohort-a-v1",
            "cohort_b": "phase5-cohort-b-v1",
        },
    }
    assert payload["stage_role"] == "legacy_retained_dataset_compatibility"
    assert payload["capabilities"]["phase_role"] == "legacy_retained_dataset_compatibility"
    assert payload["configuration"]["edgar_identity_configured"] is True
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
    output = capsys.readouterr().out
    assert "comparison capability: expanded_comparison" in output
    assert "legacy Phase 3 role: legacy_retained_dataset_compatibility" in output
    assert "registered scope: Phase 5 cohort B (5 banks + 5 nonbanks)" in output
    assert "customer data access: disabled" in output


def test_help_maps_safe_user_workflows_without_legacy_default_claims() -> None:
    help_text = cli_module.build_parser().format_help()

    assert "Commands are network-free unless --live is supplied or sync is invoked" in help_text
    assert "registered companies" in help_text
    assert "live filing discovery" in help_text
    assert "offline legacy ingestion" in help_text
    assert "offline Phase 5 replay" in help_text
    assert "live Phase 5 ingestion" in help_text
    assert "coverage and evidence" in help_text
    assert "TFC, PFSI" not in help_text


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("doctor", "never contacts the SEC"),
        ("discover", "Without --live"),
        ("ingest", "Offline default"),
        ("sync", "always performs live SEC access"),
        ("validate", "without creating, migrating, or seeding"),
        ("coverage", "without creating, migrating, or seeding"),
        ("evidence", "bounded replay excerpts are excluded"),
        ("serve", "/api/v1/coverage"),
    ],
)
def test_command_help_distinguishes_offline_live_and_inspection_workflows(
    command: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_module.build_parser().parse_args([command, "--help"])

    assert exit_info.value.code == 0
    assert expected in capsys.readouterr().out


def test_offline_company_discovery_and_cohort_selection_are_registry_defined(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsAdapter.from_config",
        lambda *_args, **_kwargs: pytest.fail("offline discovery must not construct an adapter"),
    )

    assert main(["discover"]) == 0
    default_payload = json.loads(capsys.readouterr().out)
    assert default_payload["cohort"] == "phase5-cohort-b"
    assert default_payload["network_contacted"] is False
    assert tuple(item["ticker"] for item in default_payload["companies"]) == _PHASE5_B_TICKERS
    assert default_payload["legacy_stage_a_sources"] == default_payload["sources"]
    assert "retained for compatibility" in default_payload["sources_scope"]

    assert main(["discover", "--phase5-cohort-a"]) == 0
    cohort_a = json.loads(capsys.readouterr().out)
    assert cohort_a["cohort"] == "phase5-cohort-a"
    assert tuple(item["ticker"] for item in cohort_a["companies"]) == (
        "TFC",
        "WFC",
        "PFSI",
        "RKT",
    )


def test_invalid_cli_input_is_safe_structured_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unsafe_input = "UNREGISTERED_RAW_FILING_SECRET"
    assert main(["discover", "--company", unsafe_input]) == 2
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert captured.out == ""
    assert error["code"] == "invalid_company_selection"
    assert unsafe_input not in captured.err

    assert main(["sync", "--all", "--company", "TFC"]) == 2
    usage_error = json.loads(capsys.readouterr().err)
    assert usage_error["code"] == "invalid_arguments"

    assert main(["ingest", "--phase3", "--phase5-cohort-b"]) == 2
    mixed_mode_error = json.loads(capsys.readouterr().err)
    assert mixed_mode_error["code"] == "invalid_arguments"


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
    unsafe_since = "raw-filing-secret-123"
    assert main(["sync", "--company", "TFC", "--since", unsafe_since]) == 2
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["code"] == "invalid_since"
    assert unsafe_since not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [["discover", "--live"], ["ingest", "--live", "--database-url", "sqlite:///blocked.db"]],
)
def test_live_aliases_require_identity_before_adapter_or_database(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsAdapter.from_config",
        lambda *_args, **_kwargs: pytest.fail("adapter construction must follow identity"),
    )
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.create_database_engine",
        lambda *_args, **_kwargs: pytest.fail("database construction must follow identity"),
    )

    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["code"] == "edgar_identity_required"
    assert "EDGAR_IDENTITY" in error["error"]


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


@pytest.mark.parametrize(
    ("arguments", "mode"),
    [
        (["sync", "--all", "--dry-run"], "sync"),
        (["discover", "--live"], "discover-live"),
    ],
)
def test_public_discovery_commands_have_safe_bounded_ordered_output(
    arguments: list[str],
    mode: str,
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
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not seed legacy data"),
    )

    _StubPipeline.state = EdgarToolsSyncState.DISCOVERED
    assert main([*arguments, "--runtime-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["provider"] == "PUBLIC_EDGARTOOLS"
    assert payload["mode"] == mode
    assert payload["cohort"] == "phase5-cohort-b"
    assert payload["network_mode"] == "explicit_live"
    assert tuple(item.ticker for item in _StubPipeline.companies) == _PHASE5_B_TICKERS
    assert _StubPipeline.options == [
        {"since": None, "dry_run": True, "known_accessions": frozenset()} for _ in _PHASE5_B_TICKERS
    ]
    assert _StubPipeline.events == [f"prepare:{company_id}" for company_id in _PHASE5_B_IDS]
    assert payload["results"][0]["published_count"] == 0
    assert identity not in captured.out
    assert _StubPipeline.persistences[-1] is None

    if arguments[0] == "sync":
        _StubPipeline.state = EdgarToolsSyncState.MISMATCH
        assert main(["sync", "--company", "TFC", "--dry-run"]) == 1
        assert json.loads(capsys.readouterr().out)["results"][0]["terminal_state"] == ("MISMATCH")


@pytest.mark.parametrize("runtime_name", [None, "custom-state"])
def test_phase5_runtime_dir_is_one_state_root_before_lazy_import(
    runtime_name: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "edgar", raising=False)
    monkeypatch.setenv("EDGAR_IDENTITY", "Synthetic Operator operator@example.test")
    captured: dict[str, object] = {}

    def evidence_store(root: Path) -> object:
        captured["evidence_root"] = root
        return object()

    def adapter_from_config(config: object, *, evidence_store: object) -> object:
        captured["bootstrap"] = config
        captured["evidence_store"] = evidence_store
        return object()

    real_manifest_loader = cli_module._load_golden_manifest

    def manifest_loader(path: Path) -> dict[str, object]:
        captured["manifest_path"] = path
        return real_manifest_loader(path)

    real_registry_loader = FinancialFieldRegistry.from_yaml

    def registry_loader(_cls: type[FinancialFieldRegistry], path: Path) -> FinancialFieldRegistry:
        captured["registry_path"] = path
        return real_registry_loader(path)

    monkeypatch.setattr(cli_module, "GeneralEvidenceStore", evidence_store)
    monkeypatch.setattr(EdgarToolsAdapter, "from_config", adapter_from_config)
    monkeypatch.setattr(cli_module, "_load_golden_manifest", manifest_loader)
    monkeypatch.setattr(
        FinancialFieldRegistry,
        "from_yaml",
        classmethod(registry_loader),
    )
    monkeypatch.setattr(cli_module, "EdgarToolsSyncPipeline", _StubPipeline)

    runtime = tmp_path / runtime_name if runtime_name is not None else tmp_path / ".msi"
    arguments = ["sync", "--phase5-cohort-b", "--company", "TFC", "--dry-run"]
    if runtime_name is not None:
        arguments.extend(("--runtime-dir", str(runtime)))
    assert main(arguments) == 0
    assert capsys.readouterr().err == ""

    bootstrap = captured["bootstrap"]
    assert isinstance(bootstrap, EdgarBootstrapConfig)
    assert bootstrap.runtime_root == runtime.resolve()
    assert bootstrap.local_data_root == (runtime.resolve() / "edgartools" / "data")
    assert captured["evidence_root"] == runtime.resolve() / "evidence" / "edgartools"
    assert Path(str(captured["manifest_path"])).is_absolute()
    assert Path(str(captured["manifest_path"])).name == "cohort-b-sources.v1.yaml"
    assert Path(str(captured["registry_path"])).is_absolute()
    assert Path(str(captured["registry_path"])).name == "financial_fields.v1.yaml"
    assert ".msi\\.msi" not in str(captured)
    assert default_database_url(runtime) == (
        f"sqlite:///{(runtime.resolve() / 'msi.db').as_posix()}"
    )
    assert "edgar" not in sys.modules


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
    error = json.loads(captured.err)
    assert error["code"] == "runtime_storage_failed"
    assert "synthetic retention failure" not in captured.err


def test_filing_validation_error_never_echoes_raw_filing_or_identity(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identity = "Synthetic Operator operator@example.test"
    raw_filing = "RAW_FILING_VALUE_THAT_MUST_NOT_BE_PRINTED"
    monkeypatch.setenv("EDGAR_IDENTITY", identity)
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsAdapter.from_config",
        lambda *_args, **_kwargs: object(),
    )

    def fail_pipeline(**_options: object) -> object:
        raise ValueError(raw_filing)

    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.EdgarToolsSyncPipeline",
        fail_pipeline,
    )

    assert main(["sync", "--company", "TFC", "--dry-run", "--runtime-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert captured.out == ""
    assert error["code"] == "filing_validation_failed"
    assert raw_filing not in captured.err
    assert identity not in captured.err


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


@pytest.mark.parametrize(
    ("arguments", "mode"),
    [(["sync", "--all"], "sync"), (["ingest", "--live"], "ingest-live")],
)
def test_public_ingestion_commands_prepare_phase5_b_before_one_commit(
    arguments: list[str],
    mode: str,
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
        def __init__(
            self,
            actual_engine: object,
            *,
            companies: dict[str, object],
            registry: FinancialFieldRegistry,
        ) -> None:
            assert actual_engine is engine
            assert tuple(companies) == _PHASE5_B_IDS
            assert registry.version == "financial-fields-phase5-v1"

        def known_accessions(self, company_id: str) -> frozenset[str]:
            return frozenset({f"known-{company_id}"})

    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.AtomicEdgarToolsRepository",
        FakeRepository,
    )
    _StubPipeline.state = EdgarToolsSyncState.VALIDATED
    database_url = f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}"

    assert main([*arguments, "--database-url", database_url]) == 0

    assert _StubPipeline.options == [
        {
            "since": None,
            "dry_run": False,
            "known_accessions": frozenset({f"known-{company_id}"}),
        }
        for company_id in _PHASE5_B_IDS
    ]
    assert _StubPipeline.events == [
        *(f"prepare:{company_id}" for company_id in _PHASE5_B_IDS),
        "persist",
    ]
    assert len(_StubPipeline.committed_batches) == 1
    assert engine.disposed is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "PUBLIC_EDGARTOOLS"
    assert payload["mode"] == mode
    assert payload["cohort"] == "phase5-cohort-b"


def test_validate_current_empty_database_is_uncapped_and_never_mutates(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("validate must not seed legacy data"),
    )
    database_url = _current_database_url(tmp_path, "empty.db")
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.database.initialize_schema",
        lambda *_args, **_kwargs: pytest.fail("validate must not initialize a schema"),
    )

    assert main(["validate", "--database-url", database_url]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["companies"] == 0
    assert payload["metrics"] == 0
    assert payload["observation_count"] == 0
    assert payload["observations"] == 0
    assert payload["latest_period_end"] is None


def test_calendar_and_serve_current_database_never_invoke_mutation_paths(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.seed_stage_a",
        lambda *_args, **_kwargs: pytest.fail("read commands must not seed legacy data"),
    )
    calendar_url = _current_database_url(tmp_path, "empty-calendar.db")
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.database.initialize_schema",
        lambda *_args, **_kwargs: pytest.fail("read commands must not initialize a schema"),
    )
    assert main(["calendar", "--database-url", calendar_url]) == 0
    assert "calendar" in json.loads(capsys.readouterr().out)

    calls: list[tuple[str, int]] = []
    app_arguments: dict[str, object] = {}

    def create_app(**options: object) -> object:
        app_arguments.update(options)
        return object()

    monkeypatch.setattr("mortgage_servicing_dashboard.api.create_app", create_app)
    monkeypatch.setattr(
        "mortgage_servicing_dashboard.cli.uvicorn.run",
        lambda _app, *, host, port: calls.append((host, port)),
    )
    runtime_root = tmp_path / "bounded-runtime"
    assert (
        main(
            [
                "serve",
                "--database-url",
                calendar_url,
                "--runtime-dir",
                str(runtime_root),
            ]
        )
        == 0
    )
    assert calls == [("127.0.0.1", 8000)]
    assert app_arguments["runtime_root"] == runtime_root.resolve()
    assert app_arguments["evidence_root"] == runtime_root.resolve() / "evidence" / "edgartools"
    assert "repository" in app_arguments


@pytest.mark.parametrize(
    "arguments",
    [
        ["calendar"],
        ["validate"],
        ["coverage"],
        ["evidence", "--evidence-id", "evidence:test"],
        ["serve"],
    ],
)
def test_read_commands_require_a_database_without_creating_default_state(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MSI_DATABASE_URL", raising=False)

    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "database_url_required"
    assert not (tmp_path / ".msi").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["calendar"],
        ["validate"],
        ["coverage"],
        ["evidence", "--evidence-id", "evidence:test"],
        ["serve"],
    ],
)
def test_read_commands_reject_missing_sqlite_without_creating_it(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "must-not-exist.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    assert main([*arguments, "--database-url", database_url]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "database_not_found"
    assert not database_path.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["calendar"],
        ["validate"],
        ["coverage"],
        ["evidence", "--evidence-id", "evidence:test"],
        ["serve"],
    ],
)
def test_read_commands_fail_safely_on_old_schema_without_migrating(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "old.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('0001_public_intelligence_schema')")
    database_url = f"sqlite:///{database_path.as_posix()}"

    assert main([*arguments, "--database-url", database_url]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["code"] == "database_schema_not_current"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0001_public_intelligence_schema",
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall() == [("alembic_version",)]


def test_database_errors_are_structured_and_sanitized(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "present.db"
    database_path.touch()
    secret = "postgresql://operator:TOP_SECRET@example.invalid/private"

    def fail_engine(_database_url: str) -> object:
        raise SQLAlchemyError(secret)

    monkeypatch.setattr(cli_module, "create_database_engine", fail_engine)
    assert main(["validate", "--database-url", f"sqlite:///{database_path.as_posix()}"]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert captured.out == ""
    assert error["code"] == "database_read_failed"
    assert secret not in captured.err
    assert "TOP_SECRET" not in captured.err


def test_coverage_and_evidence_commands_emit_exact_bounded_json(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'stage-a.db').as_posix()}"
    assert main(["ingest", "--stage-a", "--database-url", database_url]) == 0
    capsys.readouterr()

    assert main(["coverage", "--database-url", database_url, "--limit", "1"]) == 0
    coverage = json.loads(capsys.readouterr().out)
    assert set(coverage) == {"coverage", "limit", "offset", "returned_count", "total_count"}
    assert coverage["limit"] == 1
    assert coverage["offset"] == 0
    assert coverage["returned_count"] <= 1
    assert coverage["total_count"] >= coverage["returned_count"]
    if coverage["coverage"]:
        assert set(coverage["coverage"][0]) == {
            "company_id",
            "missing",
            "period_end",
            "reported",
            "source_not_checked",
            "total",
        }

    engine = create_database_engine(database_url)
    with Session(engine) as session:
        evidence_id = session.scalar(select(SourceEvidence.id).order_by(SourceEvidence.id))
    engine.dispose()
    assert isinstance(evidence_id, str)
    assert (
        main(
            [
                "evidence",
                "--database-url",
                database_url,
                "--evidence-id",
                evidence_id,
            ]
        )
        == 0
    )
    evidence_payload = json.loads(capsys.readouterr().out)
    assert set(evidence_payload) == {"evidence"}
    assert set(evidence_payload["evidence"]) == {
        "accession_or_identifier",
        "byte_length",
        "capture_method",
        "content_sha256",
        "etag",
        "id",
        "last_modified",
        "media_type",
        "original_url",
        "parser_version",
        "published_at",
        "representation",
        "response_status",
        "retention_location",
        "retrieved_at",
        "source_class",
    }
    assert evidence_payload["evidence"]["id"] == evidence_id
    assert "bounded_excerpt" not in evidence_payload["evidence"]
    assert "raw_value" not in evidence_payload["evidence"]


def test_checkout_phase5_replay_publishes_160_then_is_unchanged_and_validates_599(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'combined.db').as_posix()}"
    runtime_root = tmp_path / "replay-runtime"
    assert main(["ingest", "--phase3", "--database-url", database_url]) == 0
    capsys.readouterr()

    command = [
        "ingest",
        "--phase5-cohort-b",
        "--database-url",
        database_url,
        "--runtime-dir",
        str(runtime_root),
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    first_filings = [filing for result in first["results"] for filing in result["filings"]]
    assert first["network_mode"] == "offline_replay"
    assert len(first_filings) == 160
    assert {item["state"] for item in first_filings} == {"PUBLISHED"}

    engine = create_database_engine(database_url)
    with Session(engine) as session:
        retained_evidence = tuple(
            session.scalars(
                select(SourceEvidence)
                .where(
                    SourceEvidence.retention_location.like("content-sha256://%"),
                    SourceEvidence.representation == "BOUNDED_DERIVED_REPLAY_EXCERPT",
                )
                .order_by(SourceEvidence.content_sha256)
            )
        )
    engine.dispose()
    assert len(retained_evidence) == 80
    retained_root = runtime_root / "evidence" / "edgartools"
    assert len(tuple(retained_root.rglob("*.bin"))) == 80
    assert not tuple(retained_root.rglob("*.tmp"))
    for evidence in retained_evidence:
        retained_path = (
            retained_root / evidence.content_sha256[:2] / f"{evidence.content_sha256}.bin"
        )
        payload = retained_path.read_bytes()
        assert len(payload) == evidence.byte_length
        assert hashlib.sha256(payload).hexdigest() == evidence.content_sha256
        assert b"BOUNDED DERIVED REPLAY EXCERPT; NOT ORIGINAL SEC DOCUMENT BYTES;" in payload[:256]

    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)
    second_filings = [filing for result in second["results"] for filing in result["filings"]]
    assert len(second_filings) == 160
    assert {item["state"] for item in second_filings} == {"UNCHANGED"}

    assert main(["validate", "--database-url", database_url]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["observation_count"] == 599
    assert validation["observations"] == 599


def test_replay_verification_error_creates_neither_runtime_nor_database(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "verification-error.db"
    runtime_root = tmp_path / "verification-error-runtime"

    def fail_check(_root: Path) -> None:
        message = "synthetic governed replay mismatch"
        raise ValueError(message)

    monkeypatch.setattr(phase5_replay_module, "check_outputs", fail_check)
    assert (
        main(
            [
                "ingest",
                "--phase5-cohort-b",
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--runtime-dir",
                str(runtime_root),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "phase5_replay_failed"
    assert not database_path.exists()
    assert not runtime_root.exists()


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_doctor_fails_closed_for_missing_or_corrupt_phase5_manifests(
    failure: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_root = tmp_path / failure
    phase5 = config_root / "phase5"
    phase5.mkdir(parents=True)
    filenames = (
        "cohort-a-sources.v1.yaml",
        "cohort-a-universe.v1.yaml",
        "cohort-b-sources.v1.yaml",
        "cohort-b-universe.v1.yaml",
        "financial_fields.v1.yaml",
    )
    for filename in filenames:
        shutil.copyfile(_ROOT / "config" / "phase5" / filename, phase5 / filename)
    if failure == "missing":
        (phase5 / "cohort-a-sources.v1.yaml").unlink()
    else:
        manifest = phase5 / "cohort-b-sources.v1.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "mapping_version: financial-fields-phase5-v1",
                "mapping_version: incompatible-version",
                1,
            ),
            encoding="utf-8",
        )

    assert main(["doctor", "--json", "--config-dir", str(config_root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "phase5_configuration_unavailable"
