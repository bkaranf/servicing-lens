"""Operational CLI for deterministic public-servicing data and local serving."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import uvicorn
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import (
    PipelineRun,
    QuarantineCandidate,
    create_database_engine,
    default_database_url,
    initialize_schema,
)
from mortgage_servicing_dashboard.edgar_tools_pipeline import (
    EdgarToolsCompany,
    EdgarToolsSyncPipeline,
    EdgarToolsSyncState,
)
from mortgage_servicing_dashboard.edgartools_adapter import (
    EdgarBootstrapConfig,
    EdgarToolsAdapter,
)
from mortgage_servicing_dashboard.edgartools_adapter.errors import EdgarToolsAdapterError
from mortgage_servicing_dashboard.edgartools_adapter.retention import GeneralEvidenceStore
from mortgage_servicing_dashboard.financial_discovery import FinancialFieldRegistry
from mortgage_servicing_dashboard.ingestion import (
    discover_live_sec_filings,
    run_cli_review_resume,
    run_live_sec_ingestion,
)
from mortgage_servicing_dashboard.repository import (
    AtomicEdgarToolsRepository,
    IntelligenceRepository,
    config_directory,
    load_stage_a_configuration,
    prepare_stage_a,
    seed_phase3,
    seed_stage_a,
)
from mortgage_servicing_dashboard.sources import PublicSourceError
from mortgage_servicing_dashboard.tools import StaticFoundationInformation


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive public-intelligence CLI."""
    parser = argparse.ArgumentParser(
        prog="msi",
        description="Operate the governed public mortgage-servicing intelligence dataset.",
    )
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run deterministic readiness checks.")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    for command in ("init-db", "seed"):
        child = subparsers.add_parser(command, help=f"{command} the governed local database.")
        child.add_argument("--database-url")
        child.add_argument("--config-dir", type=Path)
    phase3 = subparsers.add_parser(
        "seed-phase3",
        help="Publish the governed retained Phase 3 profitability dataset.",
    )
    phase3.add_argument("--database-url")
    phase3.add_argument("--config-dir", type=Path)
    calendar = subparsers.add_parser(
        "calendar",
        help="Show actual reports and separately labeled inferred filing windows.",
    )
    calendar.add_argument("--database-url")
    calendar.add_argument("--config-dir", type=Path)
    calendar.add_argument("--as-of", help="Optional ISO knowledge-time cutoff.")
    discover = subparsers.add_parser("discover", help="List configured authoritative sources.")
    discover.add_argument("--company", choices=("TFC", "PFSI"))
    discover.add_argument("--config-dir", type=Path)
    discover.add_argument("--live", action="store_true", help="Query official SEC submissions.")
    for command in ("ingest", "validate"):
        child = subparsers.add_parser(command, help=f"{command} retained governed evidence.")
        child.add_argument("--database-url")
        child.add_argument("--config-dir", type=Path)
        if command == "ingest":
            child.add_argument(
                "--live",
                action="store_true",
                help="Acquire official SEC responses before deterministic publication.",
            )
            child.add_argument(
                "--phase3",
                action="store_true",
                help="Publish the governed retained Phase 3 dataset without network access.",
            )
    sync = subparsers.add_parser(
        "sync",
        help="Validate selected filing evidence through the public edgartools library.",
    )
    sync_target = sync.add_mutually_exclusive_group(required=True)
    sync_target.add_argument("--company", choices=("TFC", "PFSI"))
    sync_target.add_argument("--all", action="store_true", dest="all_companies")
    sync.add_argument("--since", help="Inclusive ISO filing date with a seven-day overlap.")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--database-url")
    sync.add_argument("--config-dir", type=Path)
    sync.add_argument("--runtime-dir", type=Path, default=Path(".msi"))
    review = subparsers.add_parser("review", help="List or decide quarantined candidates.")
    review.add_argument("action", choices=("list", "approve", "reject"))
    review.add_argument("--database-url")
    review.add_argument("--candidate-id")
    review.add_argument("--reviewer", default="local-reviewer")
    review.add_argument("--rationale", default="reviewed against public evidence")
    review.add_argument("--thread-id")
    review.add_argument("--config-dir", type=Path)
    serve = subparsers.add_parser("serve", help="Serve the dashboard and read-only API.")
    serve.add_argument("--database-url")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--config-dir", type=Path)
    return parser


def doctor_payload(settings: AppSettings) -> dict[str, Any]:
    """Build a deterministic, allow-listed readiness payload."""
    information = StaticFoundationInformation()
    configuration = settings.safe_summary()
    configuration.pop("edgar_api_key_configured", None)
    configuration.pop("edgar_api_base_url", None)
    return {
        "application": "public-mortgage-servicing-intelligence",
        "stage": "phase_3_metric_deepening",
        "universe": ["TFC", "PFSI"],
        "configuration": configuration,
        "capabilities": information.capabilities().as_payload(),
        "guardrails": information.guardrails().as_payload(),
    }


def _format_text(payload: dict[str, Any]) -> str:
    configuration = payload["configuration"]
    capabilities = payload["capabilities"]
    return "\n".join(
        (
            f"application: {payload['application']}",
            f"phase: {capabilities['phase']}",
            f"status: {capabilities['status']}",
            f"environment: {configuration['environment']}",
            f"model configured: {str(configuration['model_configured']).lower()}",
            f"model calls enabled: {str(configuration['model_calls_enabled']).lower()}",
            f"Deep Agents enabled: {str(configuration['deep_agent_enabled']).lower()}",
            (
                "LangGraph persistence enabled: "
                f"{str(configuration['langgraph_persistence_enabled']).lower()}"
            ),
            "selected universe: TFC, PFSI",
            "customer data access: disabled",
            "operational actions: disabled",
        )
    )


def _database_url(explicit: str | None) -> str:
    return explicit or os.environ.get("MSI_DATABASE_URL") or default_database_url()


def _live_sec_identity(settings: AppSettings) -> tuple[int, str | None]:
    try:
        return 0, settings.require_sec_user_agent()
    except ValueError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2, None


def _discover_live(args: argparse.Namespace, settings: AppSettings) -> int:
    exit_code, user_agent = _live_sec_identity(settings)
    if user_agent is None:
        return exit_code
    try:
        filings = discover_live_sec_filings(
            user_agent=user_agent,
            company=args.company,
            config_dir=args.config_dir,
        )
    except (PublicSourceError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "mode": "live",
                "filings": [filing.as_payload() for filing in filings],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _ingest_live(args: argparse.Namespace, settings: AppSettings) -> int:
    exit_code, user_agent = _live_sec_identity(settings)
    if user_agent is None:
        return exit_code
    try:
        acquisitions = run_live_sec_ingestion(
            user_agent=user_agent,
            config_dir=args.config_dir,
        )
    except (PublicSourceError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    engine = create_database_engine(_database_url(args.database_url))
    try:
        from mortgage_servicing_dashboard.repository import (  # noqa: PLC0415
            ingest_live_sec_acquisitions,
        )

        inserted = ingest_live_sec_acquisitions(
            engine,
            acquisitions,
            config_dir=args.config_dir,
        )
    except (PublicSourceError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "database": "ready",
                "mode": "live",
                "acquired": len(acquisitions),
                "evidence": [item.as_payload() for item in acquisitions],
                "inserted": inserted,
            },
            sort_keys=True,
        )
    )
    return 0


def _edgar_tools_sync(args: argparse.Namespace, settings: AppSettings) -> int:
    """Run bounded public-edgartools validation and optional atomic publication."""
    try:
        identity = settings.require_edgar_identity()
        since = date.fromisoformat(args.since) if args.since else None
    except ValueError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    database_url = args.database_url or os.environ.get("MSI_DATABASE_URL")
    if not args.dry_run and not database_url:
        print(
            json.dumps(
                {"error": "non-dry sync requires an explicit isolated --database-url"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    companies = (
        EdgarToolsCompany("tfc", "TFC", "0000092230"),
        EdgarToolsCompany("pfsi", "PFSI", "0001745916"),
    )
    selected = (
        companies
        if args.all_companies
        else tuple(company for company in companies if company.ticker == args.company)
    )
    repository_root = Path.cwd().resolve()
    config_root = config_directory(args.config_dir)
    manifest_path = config_root / "golden-sources.v1.yaml"
    if args.config_dir is None and not manifest_path.is_file():
        manifest_path = (
            repository_root / "tests" / "fixtures" / "edgartools" / "golden-sources.v1.yaml"
        )
    engine = None
    try:
        registry = FinancialFieldRegistry.from_yaml(config_root / "financial_fields.v1.yaml")
        manifest = _load_golden_manifest(manifest_path)
        bootstrap = EdgarBootstrapConfig(identity=identity, repository_root=repository_root)
        adapter = EdgarToolsAdapter.from_config(
            bootstrap,
            evidence_store=GeneralEvidenceStore(
                (args.runtime_dir / "evidence" / "edgartools").resolve()
            ),
        )
        persistence = None
        known_accessions: dict[str, frozenset[str]] = {}
        if not args.dry_run:
            engine = create_database_engine(str(database_url))
            persistence = AtomicEdgarToolsRepository(engine)
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
                dry_run=args.dry_run,
                known_accessions=known_accessions.get(company.company_id, frozenset()),
            )
            for company in selected
        )
        summaries = (
            tuple(item.summary for item in prepared)
            if args.dry_run
            else pipeline.persist_prepared_batch(prepared)
        )
    except (EdgarToolsAdapterError, OSError, TypeError, ValueError, yaml.YAMLError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(
        json.dumps(
            {
                "provider": "PUBLIC_EDGARTOOLS",
                "results": [summary.as_payload() for summary in summaries],
            },
            indent=2,
            sort_keys=True,
        )
    )
    successful_states = {
        EdgarToolsSyncState.VALIDATED,
        EdgarToolsSyncState.PUBLISHED,
        EdgarToolsSyncState.LINKED,
        EdgarToolsSyncState.UNCHANGED,
        EdgarToolsSyncState.DISCOVERED,
    }
    return 0 if all(summary.terminal_state in successful_states for summary in summaries) else 1


def _load_golden_manifest(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        message = "golden manifest root must be a mapping"
        raise TypeError(message)
    return cast("dict[str, object]", loaded)


def _calendar_command(engine: Any, args: argparse.Namespace) -> int:
    try:
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        calendar_payload = IntelligenceRepository(engine).calendar(
            as_of=as_of,
            config_dir=args.config_dir,
        )
    except ValueError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        engine.dispose()
        return 2
    print(json.dumps({"calendar": calendar_payload}, indent=2, sort_keys=True))
    engine.dispose()
    return 0


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
            return 2, {"error": "--candidate-id is required for approve or reject"}
    if not args.thread_id:
        return 2, {"error": "--thread-id is required for approve or reject"}
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
        return 3, {"error": "candidate not found"}
    except ValueError as error:
        return 4, {"error": str(error)}
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
    args = build_parser().parse_args(argv)
    command = args.command or "doctor"
    try:
        settings = AppSettings()
    except ValidationError:
        print(
            "Configuration is invalid; review the non-secret MSD_* settings.",
            file=sys.stderr,
        )
        return 2

    if command == "doctor":
        payload = doctor_payload(settings)
        if getattr(args, "as_json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(_format_text(payload))
        return 0

    if command == "discover":
        if args.live:
            return _discover_live(args, settings)
        _, _, data = load_stage_a_configuration(getattr(args, "config_dir", None))
        company_id = args.company.lower() if args.company else None
        sources = [
            {"key": key, **value}
            for key, value in data["sources"].items()
            if company_id is None or value["company_id"] == company_id
        ]
        print(json.dumps({"sources": sources}, indent=2, sort_keys=True))
        return 0

    if command == "ingest" and args.live:
        if args.phase3:
            print(
                json.dumps({"error": "--live and --phase3 are mutually exclusive"}),
                file=sys.stderr,
            )
            return 2
        return _ingest_live(args, settings)

    if command == "sync":
        return _edgar_tools_sync(args, settings)

    database_url = _database_url(getattr(args, "database_url", None))
    if getattr(args, "config_dir", None) is not None:
        os.environ["MSI_CONFIG_DIR"] = str(args.config_dir.resolve())
    engine = create_database_engine(database_url)
    if command == "seed-phase3" or (command == "ingest" and args.phase3):
        counts = seed_phase3(engine, config_dir=getattr(args, "config_dir", None))
        print(
            json.dumps(
                {"database": "ready", "mode": "phase3", "inserted": counts},
                sort_keys=True,
            )
        )
        engine.dispose()
        return 0
    if command == "review":
        prepare_stage_a(engine, config_dir=getattr(args, "config_dir", None))
        exit_code, payload = _review_candidate(engine, args)
        print(json.dumps(payload, sort_keys=True))
        engine.dispose()
        return exit_code
    if command == "calendar":
        initialize_schema(engine)
        return _calendar_command(engine, args)
    if command in {"init-db", "seed", "ingest"}:
        counts = seed_stage_a(engine, config_dir=getattr(args, "config_dir", None))
        print(json.dumps({"database": "ready", "inserted": counts}, sort_keys=True))
        engine.dispose()
        return 0
    if command == "validate":
        initialize_schema(engine)
        repository = IntelligenceRepository(engine)
        payload = {
            "status": "valid",
            "companies": len(repository.companies()),
            "metrics": len(repository.metrics()),
            "observations": len(repository.observations()),
            "latest_period_end": str(repository.latest_period_end()),
        }
        print(json.dumps(payload, sort_keys=True))
        engine.dispose()
        return 0
    if command == "serve":
        initialize_schema(engine)
        repository = IntelligenceRepository(engine)
        from mortgage_servicing_dashboard.api import create_app  # noqa: PLC0415

        uvicorn.run(create_app(repository=repository), host=args.host, port=args.port)
        engine.dispose()
        return 0
    return 2
