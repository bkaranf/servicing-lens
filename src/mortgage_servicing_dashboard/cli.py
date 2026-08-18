"""Operational CLI for deterministic public-servicing data and local serving."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import uvicorn
import yaml
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.capabilities import StaticCapabilities
from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import (
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
    default_database_url,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncPipeline,
    EdgarToolsSyncState,
)
from mortgage_servicing_dashboard.edgartools_adapter import (
    AcquiredContent,
    Attachment,
    AttachmentAcquisition,
    Company,
    ContentRepresentation,
    EdgarBootstrapConfig,
    EdgarToolsAdapter,
    Filing,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import EdgarToolsAdapterError
from mortgage_servicing_dashboard.edgartools_adapter.retention import GeneralEvidenceStore
from mortgage_servicing_dashboard.financial_discovery import FinancialFieldRegistry
from mortgage_servicing_dashboard.ingestion import (
    run_cli_review_resume,
)
from mortgage_servicing_dashboard.repository import (
    AtomicEdgarToolsRepository,
    EdgarToolsCompanyIdentity,
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    prepare_stage_a,
    seed_phase3,
    seed_stage_a,
)


class _CLIUsageError(ValueError):
    """Represent an argparse usage error without printing non-JSON output."""


class _CLICommandError(RuntimeError):
    """Represent one safe operational failure without retaining its cause text."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_action: str,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.next_action = next_action
        self.exit_code = exit_code


def _fail_command(
    code: str,
    message: str,
    *,
    next_action: str,
    exit_code: int = 1,
) -> NoReturn:
    raise _CLICommandError(
        code,
        message,
        next_action=next_action,
        exit_code=exit_code,
    )


_CURRENT_SCHEMA_REVISION = "0005_edgartools_acquisition_lineage"
_EVIDENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_MAX_COVERAGE_PAGE = 100
_PHASE5_MAPPING_VERSION = "financial-fields-phase5-v1"


class _StructuredArgumentParser(argparse.ArgumentParser):
    """Raise usage failures so the CLI can serialize them consistently."""

    def error(self, message: str) -> NoReturn:
        """Raise a structured usage error instead of writing argparse prose."""
        raise _CLIUsageError(message)


@dataclass(frozen=True, slots=True)
class _Phase5CompanyRecord:
    """Validated public company fields loaded from a Phase 5 universe registry."""

    company_id: str
    legal_name: str
    ticker: str
    cik: str
    classification: Literal["bank", "nonbank"]
    reporting_entity_id: str
    current_sec_status: str
    onboarding_status: str

    def as_edgar_company(self) -> EdgarToolsCompany:
        """Return the bounded acquisition identity used by the live pipeline."""
        return EdgarToolsCompany(self.company_id, self.ticker, self.cik)

    def as_repository_identity(self) -> EdgarToolsCompanyIdentity:
        """Return the exact legal identity used by deterministic persistence."""
        return EdgarToolsCompanyIdentity(
            self.legal_name,
            self.ticker,
            self.classification,
            self.cik,
            self.reporting_entity_id,
        )

    def as_discovery_payload(self) -> dict[str, str]:
        """Return safe allow-listed company discovery metadata."""
        return {
            "company_id": self.company_id,
            "legal_name": self.legal_name,
            "ticker": self.ticker,
            "cik": self.cik,
            "classification": self.classification,
            "reporting_entity_id": self.reporting_entity_id,
            "current_sec_status": self.current_sec_status,
            "onboarding_status": self.onboarding_status,
        }


def _print_error(code: str, message: str, *, next_action: str | None = None) -> None:
    """Write one stable JSON error without secrets or filing content."""
    payload = {"code": code, "error": message}
    if next_action is not None:
        payload["next_action"] = next_action
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


def _add_phase5_cohort_arguments(parser: argparse.ArgumentParser) -> None:
    cohort = parser.add_mutually_exclusive_group()
    cohort.add_argument(
        "--phase5-cohort-a",
        action="store_true",
        help="Select the exact two-bank/two-nonbank Phase 5 cohort A.",
    )
    cohort.add_argument(
        "--phase5-cohort-b",
        action="store_true",
        help="Select the default five-bank/five-nonbank Phase 5 cohort B explicitly.",
    )


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    """Build the non-interactive public-intelligence CLI."""
    parser = _StructuredArgumentParser(
        prog="msi",
        description=(
            "Operate the local, read-only public mortgage-servicing intelligence dataset. "
            "Commands are network-free unless --live is supplied or sync is invoked."
        ),
        epilog=(
            "Workflows:\n"
            "  readiness                 msi doctor --json\n"
            "  registered companies      msi discover\n"
            "  live filing discovery     msi discover --live --company <ticker>\n"
            "  offline legacy ingestion  msi ingest --stage-a | msi ingest --phase3\n"
            "  offline Phase 5 replay    msi ingest --phase5-cohort-b --database-url <url>\n"
            "  live Phase 5 ingestion    msi ingest --live --database-url <isolated-url>\n"
            "  validate local data        msi validate --database-url <url>\n"
            "  coverage and evidence      msi coverage --database-url <url> | msi evidence ...\n"
            "  serve the read API         msi serve --database-url <url>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    doctor = subparsers.add_parser(
        "doctor",
        help="Report local readiness and the registered Phase 5 scope without network access.",
        description=(
            "Validate packaged runtime configuration and report bounded local readiness. "
            "This command never contacts the SEC or opens a database."
        ),
    )
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--config-dir", type=Path)
    for command, command_help in (
        ("init-db", "Initialize and seed the legacy Stage A compatibility dataset."),
        ("seed", "Seed the legacy Stage A compatibility dataset idempotently."),
    ):
        child = subparsers.add_parser(command, help=command_help)
        child.add_argument("--database-url")
        child.add_argument("--config-dir", type=Path)
    phase3 = subparsers.add_parser(
        "seed-phase3",
        help="Load the checkout-only two-issuer retained Phase 3 compatibility dataset.",
    )
    phase3.add_argument("--database-url")
    phase3.add_argument("--config-dir", type=Path)
    calendar = subparsers.add_parser(
        "calendar",
        help="Read actual reports and separately labeled inferred filing windows.",
        description=(
            "Read an existing current database without creating, migrating, or seeding it. "
            "Provide --database-url or MSI_DATABASE_URL."
        ),
    )
    calendar.add_argument("--database-url", help="Existing current database; never created.")
    calendar.add_argument("--config-dir", type=Path)
    calendar.add_argument("--as-of", help="Optional ISO knowledge-time cutoff.")
    coverage = subparsers.add_parser(
        "coverage",
        help="Read a bounded page of reported, missing, and unchecked coverage as JSON.",
        description=(
            "Inspect an existing current database without creating, migrating, or seeding it."
        ),
    )
    coverage.add_argument("--database-url", help="Existing current database; never created.")
    coverage.add_argument("--as-of", help="Optional ISO knowledge-time cutoff.")
    coverage.add_argument("--limit", type=int, default=50, help="Page size from 1 through 100.")
    coverage.add_argument("--offset", type=int, default=0, help="Zero-based page offset.")
    evidence = subparsers.add_parser(
        "evidence",
        help="Read safe metadata for one evidence ID as JSON; filing bytes are never emitted.",
        description=(
            "Inspect one evidence record in an existing current database. Raw filing content "
            "and bounded replay excerpts are excluded."
        ),
    )
    evidence.add_argument("--database-url", help="Existing current database; never created.")
    evidence.add_argument("--evidence-id", required=True, help="Exact bounded evidence ID.")
    discover = subparsers.add_parser(
        "discover",
        help="List registered companies offline, or explicitly discover live SEC filings.",
        description=(
            "Without --live, list allow-listed company registry metadata without network access. "
            "With --live, query eligible SEC filings without opening a database."
        ),
    )
    discover.add_argument("--company", metavar="TICKER", help="Filter to one registered ticker.")
    discover.add_argument("--config-dir", type=Path)
    discover.add_argument(
        "--live",
        action="store_true",
        help="Explicitly query SEC filings through edgartools; no database is opened.",
    )
    discover.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(".msi"),
        help="Application state root for edgartools cache and retained evidence.",
    )
    _add_phase5_cohort_arguments(discover)
    ingest = subparsers.add_parser(
        "ingest",
        help=(
            "Load retained data or a checkout-only Phase 5 replay offline, or explicitly "
            "acquire registered filings with --live."
        ),
        description=(
            "Offline default: load the legacy Stage A retained dataset. Use --phase3 for the "
            "checkout-only Phase 3 dataset. A Phase 5 cohort selector without --live replays "
            "tracked bounded fixtures into an explicit isolated database. --live is the only "
            "mode that opens SEC sockets."
        ),
        epilog=(
            "The bounded Phase 5 replay requires a source checkout; installed wheels contain "
            "runtime manifests but intentionally omit replay fixtures."
        ),
    )
    ingest.add_argument("--database-url")
    ingest.add_argument("--config-dir", type=Path)
    ingest_mode = ingest.add_mutually_exclusive_group()
    ingest_mode.add_argument(
        "--stage-a",
        action="store_true",
        help="Explicitly load the legacy two-issuer Stage A retained dataset offline.",
    )
    ingest_mode.add_argument(
        "--phase3",
        action="store_true",
        help="Load the checkout-only two-issuer retained Phase 3 dataset offline.",
    )
    ingest_mode.add_argument(
        "--live",
        action="store_true",
        help="Explicitly acquire official SEC filings and publish them atomically.",
    )
    ingest.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(".msi"),
        help=(
            "Application state root for live cache or offline replay content-addressed evidence."
        ),
    )
    _add_phase5_cohort_arguments(ingest)
    validate = subparsers.add_parser(
        "validate",
        help="Inspect uncapped database counts and latest published period without mutations.",
        description=(
            "Read an existing current database without creating, migrating, or seeding it. "
            "Provide --database-url or MSI_DATABASE_URL."
        ),
    )
    validate.add_argument("--database-url", help="Existing current database; never created.")
    validate.add_argument("--config-dir", type=Path)
    sync = subparsers.add_parser(
        "sync",
        help="Explicitly query live SEC filings and optionally publish Phase 5 results.",
        description=(
            "This command always performs live SEC access through edgartools. --dry-run "
            "suppresses database writes; it does not make the command offline."
        ),
    )
    sync_target = sync.add_mutually_exclusive_group(required=True)
    sync_target.add_argument("--company", metavar="TICKER", help="One registered ticker.")
    sync_target.add_argument(
        "--all",
        action="store_true",
        dest="all_companies",
        help="Process every company in the selected cohort in registry order.",
    )
    sync.add_argument("--since", help="Inclusive ISO filing date with a seven-day overlap.")
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and validate live filings without opening or writing a database.",
    )
    sync.add_argument("--database-url", help="Explicit isolated destination for non-dry sync.")
    sync.add_argument("--config-dir", type=Path)
    sync.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(".msi"),
        help="Application state root for edgartools cache and retained evidence.",
    )
    _add_phase5_cohort_arguments(sync)
    review = subparsers.add_parser("review", help="List or decide quarantined candidates.")
    review.add_argument("action", choices=("list", "approve", "reject"))
    review.add_argument("--database-url")
    review.add_argument("--candidate-id")
    review.add_argument("--reviewer", default="local-reviewer")
    review.add_argument("--rationale", default="reviewed against public evidence")
    review.add_argument("--thread-id")
    review.add_argument("--config-dir", type=Path)
    serve = subparsers.add_parser(
        "serve",
        help=(
            "Serve the dashboard and read-only API, including /api/v1/coverage and "
            "/api/v1/evidence/{evidence_id}."
        ),
        description=(
            "Serve an existing current database without creating, migrating, or seeding it. "
            "Inspect /api/v1/coverage and /api/v1/evidence/{evidence_id}."
        ),
    )
    serve.add_argument("--database-url", help="Existing current database; never created.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--config-dir", type=Path)
    serve.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(".msi"),
        help="Bounded root for runtime metadata and retained evidence links.",
    )
    return parser


def _selected_phase5_cohort(
    *,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> Literal["a", "b"]:
    """Resolve selectors, with the five-bank/five-nonbank cohort as the default."""
    if phase5_cohort_a and phase5_cohort_b:
        message = "Phase 5 cohort selectors are mutually exclusive"
        raise ValueError(message)
    return "a" if phase5_cohort_a else "b"


def _required_registry_string(
    record: dict[str, Any],
    key: str,
    *,
    location: str,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"{location}.{key} must be a non-empty string"
        raise TypeError(message)
    return value


def _phase5_company_records(
    *,
    config_dir: Path | None = None,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> tuple[_Phase5CompanyRecord, ...]:
    """Load and validate exact acquisition identities from a Phase 5 registry."""
    cohort = _selected_phase5_cohort(
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    path = config_directory(config_dir) / "phase5" / f"cohort-{cohort}-universe.v1.yaml"
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        message = "Phase 5 universe registry root must be a mapping"
        raise TypeError(message)
    raw_companies = loaded.get("companies")
    if not isinstance(raw_companies, list):
        message = "Phase 5 universe registry companies must be a list"
        raise TypeError(message)

    records: list[_Phase5CompanyRecord] = []
    for index, raw_company in enumerate(raw_companies):
        location = f"companies[{index}]"
        if not isinstance(raw_company, dict):
            message = f"{location} must be a mapping"
            raise TypeError(message)
        company = cast("dict[str, Any]", raw_company)
        classification = _required_registry_string(
            company,
            "classification",
            location=location,
        )
        if classification not in {"bank", "nonbank"}:
            message = f"{location}.classification must be bank or nonbank"
            raise ValueError(message)
        records.append(
            _Phase5CompanyRecord(
                company_id=_required_registry_string(company, "id", location=location),
                legal_name=_required_registry_string(company, "legal_name", location=location),
                ticker=_required_registry_string(company, "ticker", location=location),
                cik=_required_registry_string(company, "cik", location=location),
                classification=cast('Literal["bank", "nonbank"]', classification),
                reporting_entity_id=_required_registry_string(
                    company,
                    "reporting_entity_id",
                    location=location,
                ),
                current_sec_status=_required_registry_string(
                    company,
                    "current_sec_status",
                    location=location,
                ),
                onboarding_status=_required_registry_string(
                    company,
                    "onboarding_status",
                    location=location,
                ),
            )
        )

    expected_count = loaded.get("issuer_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
        message = "Phase 5 universe registry issuer_count must be an integer"
        raise TypeError(message)
    if len(records) != expected_count:
        message = "Phase 5 universe registry issuer_count does not match companies"
        raise ValueError(message)
    identities = {(item.company_id, item.ticker, item.cik) for item in records}
    if len(identities) != len(records):
        message = "Phase 5 universe registry contains duplicate acquisition identities"
        raise ValueError(message)
    bank_count = sum(item.classification == "bank" for item in records)
    nonbank_count = sum(item.classification == "nonbank" for item in records)
    if loaded.get("bank_count") != bank_count or loaded.get("nonbank_count") != nonbank_count:
        message = "Phase 5 universe registry classification counts do not match companies"
        raise ValueError(message)
    return tuple(records)


def _validated_phase5_runtime_configuration(
    config_dir: Path | None,
) -> dict[str, object]:
    """Validate both packaged source manifests against the financial registry."""
    config_root = config_directory(config_dir)
    registry_path = config_root / "phase5" / "financial_fields.v1.yaml"
    registry = FinancialFieldRegistry.from_yaml(registry_path)
    if registry.version != _PHASE5_MAPPING_VERSION:
        message = "Phase 5 financial registry version is not supported"
        raise ValueError(message)
    manifests: dict[str, str] = {}
    case_counts: dict[str, int] = {}
    for cohort in ("a", "b"):
        manifest = _load_golden_manifest(
            config_root / "phase5" / f"cohort-{cohort}-sources.v1.yaml"
        )
        # Construction validates the complete manifest, every mapping ID, and the
        # exact registry version without invoking any adapter operation.
        pipeline = EdgarToolsSyncPipeline(
            adapter=cast("EdgarToolsAdapter", object()),
            registry=registry,
            golden_manifest=manifest,
        )
        del pipeline
        version = manifest.get("manifest_version")
        cases = manifest.get("cases")
        if version != f"phase5-cohort-{cohort}-v1" or not isinstance(cases, list):
            message = "Phase 5 source manifest metadata is invalid"
            raise TypeError(message)
        manifests[f"cohort_{cohort}"] = version
        case_counts[f"cohort_{cohort}"] = len(cases)
        _phase5_company_records(
            config_dir=config_dir,
            phase5_cohort_a=cohort == "a",
            phase5_cohort_b=cohort == "b",
        )
    return {
        "financial_mapping_version": registry.version,
        "source_manifest_versions": manifests,
        "source_case_counts": case_counts,
    }


def doctor_payload(
    settings: AppSettings,
    *,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic, allow-listed readiness payload."""
    information = StaticCapabilities()
    configuration = settings.safe_summary()
    runtime_configuration = _validated_phase5_runtime_configuration(config_dir)
    companies = _phase5_company_records(config_dir=config_dir, phase5_cohort_b=True)
    bank_count = sum(item.classification == "bank" for item in companies)
    nonbank_count = sum(item.classification == "nonbank" for item in companies)
    return {
        "application": "public-mortgage-servicing-intelligence",
        # The public key remains stable while its value describes the current capability.
        "stage": "expanded_comparison",
        # Retained for compatibility with the separately packaged Phase 3 data workflow.
        "stage_role": "legacy_retained_dataset_compatibility",
        "readiness": {
            "status": "ready_for_local_read_only_workflows",
            "network_contacted": False,
            "phase5_runtime_configuration": "available",
            "checked": [
                "packaged_phase5_configuration",
                "cohort_a_source_manifest",
                "cohort_b_source_manifest",
                "financial_field_mapping_version",
                "registered_company_scope",
                "network_default",
            ],
            "not_checked": [
                "database_contents",
                "live_sec_connectivity",
                "production_readiness",
            ],
        },
        "registered_scope": {
            "cohort": "phase5-cohort-b",
            "company_count": len(companies),
            "bank_count": bank_count,
            "nonbank_count": nonbank_count,
            "default_for_live_commands": True,
        },
        "universe": [item.ticker for item in companies],
        "phase5_runtime": runtime_configuration,
        "configuration": configuration,
        "capabilities": information.capabilities().as_payload(),
        "guardrails": information.guardrails().as_payload(),
    }


def _format_text(payload: dict[str, Any]) -> str:
    configuration = payload["configuration"]
    capabilities = payload["capabilities"]
    registered_scope = payload["registered_scope"]
    return "\n".join(
        (
            f"application: {payload['application']}",
            f"comparison capability: {capabilities['phase']}",
            f"legacy Phase 3 role: {capabilities['phase_role']}",
            f"status: {payload['readiness']['status']}",
            f"environment: {configuration['environment']}",
            (
                "registered scope: Phase 5 cohort B "
                f"({registered_scope['bank_count']} banks + "
                f"{registered_scope['nonbank_count']} nonbanks)"
            ),
            f"registered issuers: {', '.join(payload['universe'])}",
            "network default: disabled; --live or sync is explicit SEC access",
            "not checked: database contents, live SEC connectivity, production readiness",
            "customer data access: disabled",
            "operational actions: disabled",
            "next: msi discover",
        )
    )


def _database_url(explicit: str | None) -> str:
    return explicit or os.environ.get("MSI_DATABASE_URL") or default_database_url()


def _require_read_database_url(explicit: str | None) -> str:
    """Resolve a read target and reject SQLite targets that do not already exist."""
    database_url = explicit or os.environ.get("MSI_DATABASE_URL")
    if database_url is None or not database_url.strip():
        _fail_command(
            "database_url_required",
            "This read command requires an existing current database.",
            next_action="Pass --database-url <existing-url> or set MSI_DATABASE_URL.",
            exit_code=2,
        )
    try:
        parsed = make_url(database_url)
    except (TypeError, ValueError, SQLAlchemyError):
        _fail_command(
            "database_url_invalid",
            "The database URL is invalid.",
            next_action="Pass a valid URL for an existing current database.",
            exit_code=2,
        )
    if parsed.get_backend_name() != "sqlite":
        return database_url
    database = parsed.database
    if database is None or database in {"", ":memory:"} or database.startswith("file:"):
        _fail_command(
            "database_path_required",
            "Read commands require an existing on-disk SQLite database.",
            next_action="Pass sqlite:///path/to/an/existing/current.db.",
            exit_code=2,
        )
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        _fail_command(
            "database_not_found",
            "The requested SQLite database does not exist; it was not created.",
            next_action="Initialize or ingest an explicit database, then retry this read command.",
        )
    return database_url


def _assert_current_schema(engine: Engine) -> None:
    """Fail closed when a read target is absent, old, or otherwise not current."""
    try:
        with engine.connect() as connection:
            revisions = tuple(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
    except SQLAlchemyError:
        _fail_command(
            "database_schema_not_current",
            "The database schema is missing or is not readable as the current schema.",
            next_action=(
                "Run an explicit initialization or ingestion command against an isolated "
                "database, then retry."
            ),
        )
    if revisions != (_CURRENT_SCHEMA_REVISION,):
        _fail_command(
            "database_schema_not_current",
            "The database schema is not at the required current revision.",
            next_action=(
                "Migrate the database explicitly outside this read command, or use a current "
                "isolated database."
            ),
        )


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        _fail_command(
            "invalid_as_of",
            "The --as-of value must be an ISO date or timestamp.",
            next_action="Use a value such as 2026-08-18 or omit --as-of.",
            exit_code=2,
        )


def _edgar_companies(
    *,
    config_dir: Path | None = None,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> tuple[EdgarToolsCompany, ...]:
    """Return registry-defined companies in the required deterministic order."""
    records = _phase5_company_records(
        config_dir=config_dir,
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    return tuple(item.as_edgar_company() for item in records)


def _phase5_company_identities(
    *,
    config_dir: Path | None = None,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> dict[str, EdgarToolsCompanyIdentity]:
    """Return exact legal identities governed by the selected Phase 5 cohort."""
    records = _phase5_company_records(
        config_dir=config_dir,
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    return {item.company_id: item.as_repository_identity() for item in records}


def _selected_edgar_companies(
    company: str | None,
    *,
    config_dir: Path | None = None,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> tuple[EdgarToolsCompany, ...]:
    companies = _edgar_companies(
        config_dir=config_dir,
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    if company is None:
        return companies
    selected = tuple(item for item in companies if item.ticker == company.upper())
    if len(selected) != 1:
        message = "unsupported --company; run `msi discover` for registered tickers"
        raise ValueError(message)
    return selected


def _edgartools_config_paths(
    config_root: Path,
    *,
    phase5_cohort_a: bool,
    phase5_cohort_b: bool,
) -> tuple[Path, Path]:
    cohort = _selected_phase5_cohort(
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    return (
        (config_root / "phase5" / f"cohort-{cohort}-sources.v1.yaml").resolve(),
        (config_root / "phase5" / "financial_fields.v1.yaml").resolve(),
    )


class _Phase5ReplayAdapter(EdgarToolsAdapter):
    """Expose verified checkout replay excerpts through the production adapter seam."""

    def __init__(
        self,
        *,
        root: Path,
        manifest: dict[str, object],
        records: tuple[_Phase5CompanyRecord, ...],
    ) -> None:
        selected_ids = {record.company_id for record in records}
        self._companies: dict[str, Company] = {}
        for record in records:
            company = Company(record.cik, record.legal_name, (record.ticker,))
            self._companies[record.cik] = company
            self._companies[record.ticker] = company
        self._filings_by_cik: dict[str, list[Filing]] = {}
        self._attachments_by_accession: dict[str, tuple[Attachment, ...]] = {}
        self._payload_by_source: dict[tuple[str, str], bytes] = {}
        self._source_by_key: dict[tuple[str, str], dict[str, object]] = {}
        self._evidence_store: GeneralEvidenceStore | None = None
        raw_cases = manifest.get("cases")
        if not isinstance(raw_cases, list):
            message = "Phase 5 replay cases must be a list"
            raise TypeError(message)
        cases = tuple(
            cast("dict[str, object]", item)
            for item in raw_cases
            if isinstance(item, dict) and str(item.get("issuer_id")) in selected_ids
        )
        by_accession: dict[str, dict[str, object]] = {}
        for case in cases:
            by_accession.setdefault(str(case["accession"]), case)
        fixture_root = (root / "tests" / "fixtures" / "phase5" / "replay").resolve()
        records_by_id = {record.company_id: record for record in records}
        for accession, case in by_accession.items():
            record = records_by_id[str(case["issuer_id"])]
            source = cast("dict[str, object]", case["edgartools_source"])
            source_document = str(case["source_document"])
            fixture_path = (root / str(source["fixture_path"])).resolve()
            if not fixture_path.is_relative_to(fixture_root):
                message = "Phase 5 replay fixture escapes its governed root"
                raise ValueError(message)
            payload = fixture_path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != source.get("sha256") or len(payload) != source.get("byte_length"):
                message = "Phase 5 replay fixture identity differs from its manifest"
                raise ValueError(message)
            retrieved_at = datetime.fromisoformat(str(source["retrieved_at"]))
            if retrieved_at.tzinfo is None:
                retrieved_at = retrieved_at.replace(tzinfo=UTC)
            source_key = (accession, source_document)
            self._payload_by_source[source_key] = payload
            self._source_by_key[source_key] = source
            filing = Filing(
                cik=record.cik,
                accession_number=accession,
                company_name=record.legal_name,
                form=str(case["form"]),
                filing_date=date.fromisoformat(str(case["filing_date"])),
                acceptance_timestamp=retrieved_at,
                report_period=date.fromisoformat(str(case["period_end"])),
                primary_document=str(case["primary_document"]),
                amendment=bool(case["amendment"]),
                is_xbrl=True,
                is_inline_xbrl=True,
                size=None,
                homepage_url=f"https://www.sec.gov/Archives/{accession}",
                text_url=f"https://www.sec.gov/Archives/{accession}.txt",
            )
            self._filings_by_cik.setdefault(record.cik, []).append(filing)
            primary = Attachment(
                cik=record.cik,
                accession_number=accession,
                document=str(case["primary_document"]),
                sequence=str(case["primary_sequence"]),
                description=str(case["primary_description"]),
                attachment_type=str(case["primary_document_type"]),
                size=None,
                source_url=str(case["primary_source_url"]),
                is_primary=True,
                is_binary=False,
            )
            attachments = [primary]
            if source_document != primary.document:
                attachments.append(
                    Attachment(
                        cik=record.cik,
                        accession_number=accession,
                        document=source_document,
                        sequence=str(case["source_sequence"]),
                        description=str(case["source_description"]),
                        attachment_type=str(case["source_document_type"]),
                        size=len(payload),
                        source_url=str(case["source_url"]),
                        is_primary=False,
                        is_binary=False,
                    )
                )
            self._attachments_by_accession[accession] = tuple(attachments)

    def bind_evidence_root(self, evidence_root: Path) -> None:
        """Bind the application evidence store after every fixture has verified."""
        if self._evidence_store is not None:
            message = "Phase 5 replay evidence root is already bound"
            raise RuntimeError(message)
        self._evidence_store = GeneralEvidenceStore(evidence_root)

    def company(self, cik_or_ticker: str) -> Company:
        return self._companies[cik_or_ticker.upper()]

    def filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = (),
        filing_date: date | tuple[date, date] | None = None,
        include_amendments: bool = True,
    ) -> tuple[Filing, ...]:
        del forms, filing_date, include_amendments
        return tuple(self._filings_by_cik[cik])

    def attachments(
        self,
        accession: str,
        *,
        expected_cik: str | None = None,
    ) -> tuple[Attachment, ...]:
        del expected_cik
        return self._attachments_by_accession[accession]

    def acquire_attachment(
        self,
        accession: str,
        document: str,
        *,
        expected_cik: str | None = None,
        retain: bool = True,
    ) -> AttachmentAcquisition:
        del expected_cik
        attachment = next(
            item for item in self._attachments_by_accession[accession] if item.document == document
        )
        payload = self._payload_by_source[(accession, document)]
        source = self._source_by_key[(accession, document)]
        digest = hashlib.sha256(payload).hexdigest()
        retrieved_at = datetime.fromisoformat(str(source["retrieved_at"]))
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        content = AcquiredContent(
            cik=attachment.cik,
            accession_number=accession,
            document=document,
            source_url=attachment.source_url,
            content=payload,
            media_type="application/xml",
            representation=ContentRepresentation.BOUNDED_REPLAY_EXCERPT,
            capture_method="offline_bounded_xbrl_replay_excerpt",
            sha256=digest,
            retrieved_at=retrieved_at,
        )
        store = self._evidence_store
        if not retain:
            retained = None
        elif store is None:
            message = "Phase 5 replay evidence root is not bound"
            raise RuntimeError(message)
        else:
            retained = store.retain(content)
        return AttachmentAcquisition(attachment, content, retained)


def _run_phase5_replay(args: argparse.Namespace) -> int:
    """Publish the governed bounded replay from a source checkout, without sockets."""
    if not args.database_url:
        _print_error(
            "database_url_required",
            "Offline Phase 5 replay requires an explicit isolated --database-url.",
            next_action="Repeat with --database-url <isolated-database-url>.",
        )
        return 2
    root = Path(__file__).resolve().parents[2]
    replay_path = root / "config" / "phase5" / "cohort-b-replay.v1.yaml"
    required_assets = (
        root / "scripts" / "phase5_replay.py",
        replay_path,
        root / "tests" / "fixtures" / "phase5" / "replay-index.v1.yaml",
    )
    if not all(path.is_file() for path in required_assets):
        _print_error(
            "phase5_replay_unavailable",
            "Bounded Phase 5 replay assets are unavailable in this installation.",
            next_action="Run this offline replay from a source checkout; wheels omit fixtures.",
        )
        return 1
    cohort = _selected_phase5_cohort(
        phase5_cohort_a=bool(args.phase5_cohort_a),
        phase5_cohort_b=bool(args.phase5_cohort_b),
    )
    engine: Engine | None = None
    try:
        from scripts.phase5_replay import check_outputs  # noqa: PLC0415

        check_outputs(root)
        replay_manifest = _load_golden_manifest(replay_path)
        records = _phase5_company_records(
            config_dir=root / "config",
            phase5_cohort_a=cohort == "a",
            phase5_cohort_b=cohort == "b",
        )
        companies = tuple(record.as_edgar_company() for record in records)
        identities = {record.company_id: record.as_repository_identity() for record in records}
        registry = FinancialFieldRegistry.from_yaml(
            root / "config" / "phase5" / "financial_fields.v1.yaml"
        )
        adapter = _Phase5ReplayAdapter(root=root, manifest=replay_manifest, records=records)
        engine = create_database_engine(cast("str", args.database_url))
        persistence = AtomicEdgarToolsRepository(engine, companies=identities, registry=registry)
        known_accessions = {
            company.company_id: persistence.known_accessions(company.company_id)
            for company in companies
        }
        pipeline = EdgarToolsSyncPipeline(
            adapter=adapter,
            registry=registry,
            golden_manifest=replay_manifest,
            persistence=persistence,
        )
        state_root = args.runtime_dir.resolve()
        adapter.bind_evidence_root(state_root / "evidence" / "edgartools")
        prepared = tuple(
            pipeline.prepare_company(
                company,
                dry_run=False,
                known_accessions=known_accessions[company.company_id],
            )
            for company in companies
        )
        summaries = pipeline.persist_prepared_batch(prepared)
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError, yaml.YAMLError):
        _print_error(
            "phase5_replay_failed",
            "The governed Phase 5 replay failed closed without emitting filing content.",
            next_action="Verify the checkout with `python -m scripts.phase5_replay --check`.",
        )
        return 1
    except SQLAlchemyError:
        _print_error(
            "database_operation_failed",
            "The isolated replay database operation failed closed.",
            next_action="Verify the explicit --database-url and retry with an isolated database.",
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    _print_public_edgartools_results(
        summaries,
        mode="ingest-replay",
        cohort=cohort,
        network_mode="offline_replay",
    )
    return 0


def _run_public_edgartools(  # noqa: C901, PLR0911, PLR0913
    *,
    settings: AppSettings,
    selected: tuple[EdgarToolsCompany, ...],
    dry_run: bool,
    database_url: str | None,
    config_dir: Path | None,
    runtime_dir: Path,
    since: date | None = None,
    require_explicit_database: bool = False,
    phase5_cohort_a: bool = False,
    phase5_cohort_b: bool = False,
) -> tuple[int, tuple[Any, ...]]:
    """Run one shared public-adapter preparation/persistence path."""
    try:
        identity = settings.require_edgar_identity()
    except ValueError:
        _print_error(
            "edgar_identity_required",
            "Live SEC access requires a valid EDGAR_IDENTITY application name and contact email.",
            next_action="Set EDGAR_IDENTITY locally, then repeat the explicit live command.",
        )
        return 2, ()

    if not dry_run and (require_explicit_database and not database_url):
        _print_error(
            "database_url_required",
            "Live ingestion requires an explicit isolated --database-url.",
            next_action="Repeat the command with --database-url <isolated-database-url>.",
        )
        return 2, ()
    if not selected:
        _print_error(
            "company_selection_required",
            "No registered Phase 5 company was selected.",
            next_action="Run `msi discover` to list registered tickers.",
        )
        return 2, ()
    if not dry_run and database_url is None:
        _print_error(
            "database_url_required",
            "A non-dry live SEC operation requires an explicit isolated --database-url.",
        )
        return 2, ()

    try:
        config_root = config_directory(config_dir)
        manifest_path, registry_path = _edgartools_config_paths(
            config_root,
            phase5_cohort_a=phase5_cohort_a,
            phase5_cohort_b=phase5_cohort_b,
        )
        registry = FinancialFieldRegistry.from_yaml(registry_path)
        manifest = _load_golden_manifest(manifest_path)
        company_identities = _phase5_company_identities(
            config_dir=config_dir,
            phase5_cohort_a=phase5_cohort_a,
            phase5_cohort_b=phase5_cohort_b,
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        _print_error(
            "phase5_configuration_unavailable",
            "The selected Phase 5 runtime configuration is missing or invalid.",
            next_action="Verify the installed package or pass a valid --config-dir.",
        )
        return 1, ()

    engine = None
    try:
        state_root = runtime_dir.resolve()
        evidence_root = state_root / "evidence" / "edgartools"
        bootstrap = EdgarBootstrapConfig(identity=identity, runtime_root=state_root)
        adapter = EdgarToolsAdapter.from_config(
            bootstrap,
            evidence_store=GeneralEvidenceStore(evidence_root),
        )
        persistence = None
        known_accessions: dict[str, frozenset[str]] = {}
        if not dry_run:
            engine = create_database_engine(cast("str", database_url))
            persistence = AtomicEdgarToolsRepository(
                engine,
                companies=company_identities,
                registry=registry,
            )
            known_accessions = {
                company.company_id: persistence.known_accessions(company.company_id)
                for company in selected
            }
        pipeline = EdgarToolsSyncPipeline(
            adapter=adapter,
            registry=registry,
            golden_manifest=manifest,
            persistence=persistence,
        )
        prepared = tuple(
            pipeline.prepare_company(
                company,
                since=since,
                dry_run=dry_run,
                known_accessions=known_accessions.get(company.company_id, frozenset()),
            )
            for company in selected
        )
        summaries = (
            tuple(item.summary for item in prepared)
            if dry_run
            else pipeline.persist_prepared_batch(prepared)
        )
    except EdgarToolsAdapterError:
        _print_error(
            "live_sec_pipeline_failed",
            "The live SEC filing operation failed closed; no filing content was written to output.",
            next_action="Retry later or inspect local retained evidence and application logs.",
        )
        return 1, ()
    except OSError:
        _print_error(
            "runtime_storage_failed",
            "The runtime cache or retained-evidence location could not be used.",
            next_action="Verify --runtime-dir permissions and available space.",
        )
        return 1, ()
    except SQLAlchemyError:
        _print_error(
            "database_operation_failed",
            "The isolated database operation failed closed.",
            next_action="Verify --database-url and validate the destination database.",
        )
        return 1, ()
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        _print_error(
            "filing_validation_failed",
            "A live filing did not satisfy the deterministic Phase 5 validation contract.",
            next_action=(
                "Inspect retained evidence locally; filing content is not echoed by the CLI."
            ),
        )
        return 1, ()
    finally:
        if engine is not None:
            engine.dispose()
    return 0, summaries


def _print_public_edgartools_results(
    summaries: tuple[Any, ...],
    *,
    mode: str,
    cohort: Literal["a", "b"],
    network_mode: str = "explicit_live",
) -> None:
    print(
        json.dumps(
            {
                "provider": "PUBLIC_EDGARTOOLS",
                "mode": mode,
                "cohort": f"phase5-cohort-{cohort}",
                "network_mode": network_mode,
                "results": [summary.as_payload() for summary in summaries],
            },
            indent=2,
            sort_keys=True,
        )
    )


def _discover_live(args: argparse.Namespace, settings: AppSettings) -> int:
    try:
        selected = _selected_edgar_companies(
            args.company,
            config_dir=args.config_dir,
            phase5_cohort_a=bool(args.phase5_cohort_a),
            phase5_cohort_b=bool(args.phase5_cohort_b),
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        _print_error(
            "invalid_company_selection",
            "The requested company is not registered in the selected Phase 5 cohort.",
            next_action="Run `msi discover` with the same cohort selector.",
        )
        return 2
    exit_code, summaries = _run_public_edgartools(
        settings=settings,
        selected=selected,
        dry_run=True,
        database_url=None,
        config_dir=args.config_dir,
        runtime_dir=args.runtime_dir,
        phase5_cohort_a=bool(args.phase5_cohort_a),
        phase5_cohort_b=bool(args.phase5_cohort_b),
    )
    if exit_code == 0:
        _print_public_edgartools_results(
            summaries,
            mode="discover-live",
            cohort=_selected_phase5_cohort(
                phase5_cohort_a=bool(args.phase5_cohort_a),
                phase5_cohort_b=bool(args.phase5_cohort_b),
            ),
        )
    return exit_code


def _ingest_live(args: argparse.Namespace, settings: AppSettings) -> int:
    try:
        selected = _edgar_companies(
            config_dir=args.config_dir,
            phase5_cohort_a=bool(args.phase5_cohort_a),
            phase5_cohort_b=bool(args.phase5_cohort_b),
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        _print_error(
            "phase5_configuration_unavailable",
            "The selected Phase 5 company registry is missing or invalid.",
            next_action="Verify the installed package or pass a valid --config-dir.",
        )
        return 1
    exit_code, summaries = _run_public_edgartools(
        settings=settings,
        selected=selected,
        dry_run=False,
        database_url=args.database_url,
        config_dir=args.config_dir,
        runtime_dir=args.runtime_dir,
        require_explicit_database=True,
        phase5_cohort_a=bool(args.phase5_cohort_a),
        phase5_cohort_b=bool(args.phase5_cohort_b),
    )
    if exit_code == 0:
        _print_public_edgartools_results(
            summaries,
            mode="ingest-live",
            cohort=_selected_phase5_cohort(
                phase5_cohort_a=bool(args.phase5_cohort_a),
                phase5_cohort_b=bool(args.phase5_cohort_b),
            ),
        )
    return exit_code


def _edgar_tools_sync(args: argparse.Namespace, settings: AppSettings) -> int:
    """Run bounded public-edgartools validation and optional atomic publication."""
    try:
        since = date.fromisoformat(args.since) if args.since else None
    except ValueError:
        _print_error(
            "invalid_since",
            "Invalid --since value; expected an ISO date in YYYY-MM-DD form.",
            next_action="Use a calendar date such as 2026-01-01.",
        )
        return 2

    phase5_cohort_a = bool(args.phase5_cohort_a)
    phase5_cohort_b = bool(args.phase5_cohort_b)
    try:
        selected = (
            _edgar_companies(
                config_dir=args.config_dir,
                phase5_cohort_a=phase5_cohort_a,
                phase5_cohort_b=phase5_cohort_b,
            )
            if args.all_companies
            else _selected_edgar_companies(
                args.company,
                config_dir=args.config_dir,
                phase5_cohort_a=phase5_cohort_a,
                phase5_cohort_b=phase5_cohort_b,
            )
        )
    except ValueError:
        _print_error(
            "invalid_company_selection",
            "The requested company is not registered in the selected Phase 5 cohort.",
            next_action="Run `msi discover` with the same cohort selector.",
        )
        return 2
    except (OSError, TypeError, yaml.YAMLError):
        _print_error(
            "phase5_configuration_unavailable",
            "The selected Phase 5 company registry is missing or invalid.",
            next_action="Verify the installed package or pass a valid --config-dir.",
        )
        return 1
    database_url = args.database_url
    if not args.dry_run and database_url is None:
        _print_error(
            "database_url_required",
            "Non-dry sync requires an explicit isolated --database-url.",
            next_action="Repeat the command with --database-url <isolated-database-url>.",
        )
        return 2
    exit_code, summaries = _run_public_edgartools(
        settings=settings,
        selected=selected,
        dry_run=args.dry_run,
        database_url=database_url,
        config_dir=args.config_dir,
        runtime_dir=args.runtime_dir,
        since=since,
        phase5_cohort_a=phase5_cohort_a,
        phase5_cohort_b=phase5_cohort_b,
    )
    if exit_code == 0:
        _print_public_edgartools_results(
            summaries,
            mode="sync",
            cohort=_selected_phase5_cohort(
                phase5_cohort_a=phase5_cohort_a,
                phase5_cohort_b=phase5_cohort_b,
            ),
        )
        successful_states = {
            EdgarToolsSyncState.VALIDATED,
            EdgarToolsSyncState.PUBLISHED,
            EdgarToolsSyncState.LINKED,
            EdgarToolsSyncState.UNCHANGED,
            EdgarToolsSyncState.DISCOVERED,
        }
        return 0 if all(summary.terminal_state in successful_states for summary in summaries) else 1
    return exit_code


def _load_golden_manifest(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        message = "golden manifest root must be a mapping"
        raise TypeError(message)
    return cast("dict[str, object]", loaded)


def _calendar_command(repository: IntelligenceRepository, args: argparse.Namespace) -> int:
    as_of = _parse_as_of(args.as_of)
    calendar_payload = repository.calendar(as_of=as_of, config_dir=args.config_dir)
    print(json.dumps({"calendar": calendar_payload}, indent=2, sort_keys=True))
    return 0


def _coverage_command(repository: IntelligenceRepository, args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= _MAX_COVERAGE_PAGE or args.offset < 0:
        _fail_command(
            "invalid_pagination",
            "Coverage pagination requires limit 1 through 100 and a nonnegative offset.",
            next_action="Adjust --limit and --offset, then retry.",
            exit_code=2,
        )
    rows = repository.coverage(as_of=_parse_as_of(args.as_of))
    page = rows[args.offset : args.offset + args.limit]
    print(
        json.dumps(
            {
                "coverage": page,
                "limit": args.limit,
                "offset": args.offset,
                "returned_count": len(page),
                "total_count": len(rows),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _evidence_command(repository: IntelligenceRepository, args: argparse.Namespace) -> int:
    if _EVIDENCE_ID.fullmatch(args.evidence_id) is None:
        _fail_command(
            "invalid_evidence_id",
            "The evidence ID has an invalid bounded format.",
            next_action="Use an evidence ID returned by a local observation or coverage view.",
            exit_code=2,
        )
    evidence = repository.evidence(args.evidence_id)
    if evidence is None:
        _fail_command(
            "evidence_not_found",
            "No evidence metadata was found for that ID.",
            next_action="Inspect a local observation for an available evidence ID.",
        )
    safe_fields = (
        "id",
        "source_class",
        "original_url",
        "retrieved_at",
        "published_at",
        "accession_or_identifier",
        "content_sha256",
        "byte_length",
        "media_type",
        "representation",
        "capture_method",
        "parser_version",
        "retention_location",
        "response_status",
        "etag",
        "last_modified",
    )
    print(
        json.dumps(
            {"evidence": {field: evidence.get(field) for field in safe_fields}},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _validate_command(repository: IntelligenceRepository) -> int:
    observation_count = repository.observation_count()
    latest_period_end = repository.latest_period_end()
    payload = {
        "status": "valid",
        "companies": len(repository.companies()),
        "metrics": len(repository.metrics()),
        "observation_count": observation_count,
        "observations": observation_count,
        "latest_period_end": (
            latest_period_end.isoformat() if latest_period_end is not None else None
        ),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _serve_command(
    repository: IntelligenceRepository,
    args: argparse.Namespace,
) -> int:
    from mortgage_servicing_dashboard.api import create_app  # noqa: PLC0415

    runtime_root = args.runtime_dir.resolve()
    evidence_root = runtime_root / "evidence" / "edgartools"
    app = create_app(
        repository=repository,
        runtime_root=runtime_root,
        evidence_root=evidence_root,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _run_read_command(args: argparse.Namespace) -> int:  # noqa: PLR0911
    """Run one mutation-free database command with preflight and guaranteed disposal."""
    engine: Engine | None = None
    try:
        database_url = _require_read_database_url(args.database_url)
        engine = create_database_engine(database_url)
        _assert_current_schema(engine)
        repository = IntelligenceRepository(engine)
        if args.command == "calendar":
            return _calendar_command(repository, args)
        if args.command == "coverage":
            return _coverage_command(repository, args)
        if args.command == "evidence":
            return _evidence_command(repository, args)
        if args.command == "validate":
            return _validate_command(repository)
        if args.command == "serve":
            return _serve_command(repository, args)
        _fail_command(
            "unsupported_command",
            "The requested read command is not implemented.",
            next_action="Run `msi --help`.",
            exit_code=2,
        )
    except _CLICommandError as error:
        _print_error(error.code, error.safe_message, next_action=error.next_action)
        return error.exit_code
    except (KeyError, OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
        _print_error(
            "database_read_failed",
            "The read-only database operation failed closed.",
            next_action="Verify the existing database and command arguments, then retry.",
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def _review_candidate(engine: Any, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """List candidates or record one auditable decision."""
    with Session(engine) as session:
        if args.action == "list":
            candidates = session.execute(
                select(QuarantineCandidate, PipelineRun.thread_id)
                .join(PipelineRun, QuarantineCandidate.pipeline_run_id == PipelineRun.id)
                .order_by(QuarantineCandidate.id)
            )
            return 0, {
                "candidates": [
                    {
                        "id": item.id,
                        "metric_id": item.proposed_metric_id,
                        "status": item.status,
                        "confidence": str(item.confidence),
                        "thread_id": thread_id,
                    }
                    for item, thread_id in candidates
                ]
            }
        if not args.candidate_id:
            return 2, {
                "code": "invalid_arguments",
                "error": "--candidate-id is required for approve or reject",
            }
    if not args.thread_id:
        return 2, {
            "code": "invalid_arguments",
            "error": "--thread-id is required for approve or reject",
        }
    try:
        result = run_cli_review_resume(
            engine=engine,
            candidate_id=args.candidate_id,
            decision=args.action,
            reviewer=args.reviewer,
            rationale=args.rationale,
            thread_id=args.thread_id,
            config_dir=args.config_dir,
        )
    except KeyError:
        return 3, {"code": "candidate_not_found", "error": "candidate not found"}
    except ValueError:
        return 4, {
            "code": "review_validation_failed",
            "error": "The review decision failed deterministic validation.",
        }
    return 0, {
        "candidate_id": result["candidate_id"],
        "status": result["status"],
        "decision": result["decision"],
        "thread_id": result["thread_id"],
        "terminal_status": result["terminal_status"],
        "terminal_outcomes": result["terminal_outcomes"],
    }


def main(argv: Sequence[str] | None = None) -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Dispatch deterministic CLI operations."""
    try:
        args = build_parser().parse_args(argv)
    except _CLIUsageError:
        _print_error(
            "invalid_arguments",
            "The command arguments are invalid.",
            next_action="Run `msi --help` or `msi <command> --help`.",
        )
        return 2
    command = args.command or "doctor"
    try:
        settings = AppSettings()
    except ValidationError:
        _print_error(
            "invalid_configuration",
            "Configuration is invalid; review the non-secret MSD_* settings.",
            next_action=(
                "Correct the local environment without placing secrets on the command line."
            ),
        )
        return 2

    if command == "doctor":
        try:
            payload = doctor_payload(
                settings,
                config_dir=getattr(args, "config_dir", None),
            )
        except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
            _print_error(
                "phase5_configuration_unavailable",
                "Readiness failed because the Phase 5 runtime configuration is unavailable.",
                next_action="Verify the installed package or pass a valid --config-dir.",
            )
            return 1
        if getattr(args, "as_json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(_format_text(payload))
        return 0

    if command == "discover":
        if args.live:
            return _discover_live(args, settings)
        try:
            phase5_cohort_a = bool(args.phase5_cohort_a)
            phase5_cohort_b = bool(args.phase5_cohort_b)
            cohort = _selected_phase5_cohort(
                phase5_cohort_a=phase5_cohort_a,
                phase5_cohort_b=phase5_cohort_b,
            )
            companies = _phase5_company_records(
                config_dir=args.config_dir,
                phase5_cohort_a=phase5_cohort_a,
                phase5_cohort_b=phase5_cohort_b,
            )
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            _print_error(
                "phase5_configuration_unavailable",
                "Company discovery could not load the registered Phase 5 scope.",
                next_action="Verify the installed package or pass a valid --config-dir.",
            )
            return 1
        try:
            _, _, legacy_data = load_stage_a_configuration(args.config_dir)
            legacy_sources = legacy_data["sources"]
        except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
            legacy_sources = {}
        ticker = args.company.upper() if args.company else None
        selected_companies = tuple(
            item for item in companies if ticker is None or item.ticker == ticker
        )
        if not selected_companies:
            _print_error(
                "invalid_company_selection",
                "The requested company is not registered in the selected Phase 5 cohort.",
                next_action="Run `msi discover` with no company filter.",
            )
            return 2
        company_ids = {item.company_id for item in selected_companies}
        sources = [
            {
                "key": key,
                "company_id": value["company_id"],
                "source_class": value["source_class"],
                "accession": value["accession"],
                "url": value["url"],
                "published_at": value["published_at"],
                "period_end": value["period_end"],
                "representation": value["representation"],
                "locator": value["locator"],
            }
            for key, value in legacy_sources.items()
            if value["company_id"] in company_ids
        ]
        print(
            json.dumps(
                {
                    "mode": "registered-companies",
                    "cohort": f"phase5-cohort-{cohort}",
                    "companies": [item.as_discovery_payload() for item in selected_companies],
                    "legacy_stage_a_sources": sources,
                    "sources": sources,
                    "sources_scope": (
                        "legacy Stage A retained-source compatibility metadata; "
                        "the sources key is retained for compatibility"
                    ),
                    "network_contacted": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if command == "ingest" and args.live:
        return _ingest_live(args, settings)

    if command == "ingest" and (args.phase5_cohort_a or args.phase5_cohort_b):
        if args.stage_a or args.phase3:
            _print_error(
                "invalid_arguments",
                "A Phase 5 replay selector cannot be combined with a legacy ingest mode.",
                next_action="Choose one Phase 5 cohort replay, --stage-a, or --phase3.",
            )
            return 2
        return _run_phase5_replay(args)

    if command == "sync":
        return _edgar_tools_sync(args, settings)

    if command in {"calendar", "coverage", "evidence", "validate", "serve"}:
        return _run_read_command(args)

    database_url = _database_url(getattr(args, "database_url", None))
    if getattr(args, "config_dir", None) is not None:
        os.environ["MSI_CONFIG_DIR"] = str(args.config_dir.resolve())
    engine: Engine | None = None
    try:
        engine = create_database_engine(database_url)
        if command == "seed-phase3" or (command == "ingest" and args.phase3):
            counts = seed_phase3(engine, config_dir=getattr(args, "config_dir", None))
            print(
                json.dumps(
                    {"database": "ready", "mode": "phase3", "inserted": counts},
                    sort_keys=True,
                )
            )
            return 0
        if command == "review":
            prepare_stage_a(engine, config_dir=getattr(args, "config_dir", None))
            exit_code, payload = _review_candidate(engine, args)
            print(json.dumps(payload, sort_keys=True))
            return exit_code
        if command in {"init-db", "seed", "ingest"}:
            counts = seed_stage_a(engine, config_dir=getattr(args, "config_dir", None))
            print(json.dumps({"database": "ready", "inserted": counts}, sort_keys=True))
            return 0
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ):
        _print_error(
            "database_operation_failed",
            "The local database operation failed closed.",
            next_action="Verify the database URL, configuration, and local permissions.",
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    _print_error(
        "unsupported_command",
        "The requested command is not implemented.",
        next_action="Run `msi --help`.",
    )
    return 2
