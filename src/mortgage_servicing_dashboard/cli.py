"""Operational CLI for deterministic Stage A setup, validation, and serving."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import (
    QuarantineCandidate,
    create_database_engine,
    default_database_url,
)
from mortgage_servicing_dashboard.repository import (
    IntelligenceRepository,
    load_stage_a_configuration,
    seed_stage_a,
)
from mortgage_servicing_dashboard.tools import StaticFoundationInformation


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive public-intelligence CLI."""
    parser = argparse.ArgumentParser(
        prog="msi",
        description="Operate the public mortgage-servicing intelligence Stage A slice.",
    )
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run deterministic readiness checks.")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    for command in ("init-db", "seed"):
        child = subparsers.add_parser(command, help=f"{command} the Stage A database.")
        child.add_argument("--database-url")
        child.add_argument("--config-dir", type=Path)
    discover = subparsers.add_parser("discover", help="List configured authoritative sources.")
    discover.add_argument("--company", choices=("TFC", "PFSI"))
    discover.add_argument("--config-dir", type=Path)
    for command in ("ingest", "validate"):
        child = subparsers.add_parser(command, help=f"{command} recorded Stage A evidence.")
        child.add_argument("--database-url")
        child.add_argument("--company", choices=("TFC", "PFSI"))
        child.add_argument("--config-dir", type=Path)
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
    return {
        "application": "public-mortgage-servicing-intelligence",
        "stage": "A",
        "universe": ["TFC", "PFSI"],
        "configuration": settings.safe_summary(),
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


def _review_candidate(engine: Any, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """List candidates or record one auditable decision."""
    with Session(engine) as session:
        if args.action == "list":
            candidates = session.scalars(
                select(QuarantineCandidate).order_by(QuarantineCandidate.id)
            )
            return 0, {
                "candidates": [
                    {
                        "id": item.id,
                        "metric_id": item.proposed_metric_id,
                        "status": item.status,
                        "confidence": str(item.confidence),
                    }
                    for item in candidates
                ]
            }
        if not args.candidate_id:
            return 2, {"error": "--candidate-id is required for approve or reject"}
    if not args.thread_id:
        return 2, {"error": "--thread-id is required for approve or reject"}
    repository = IntelligenceRepository(engine)
    try:
        result = repository.record_review_decision(
            candidate_id=args.candidate_id,
            decision=args.action,
            reviewer=args.reviewer,
            rationale=args.rationale,
            thread_id=args.thread_id,
        )
    except KeyError:
        return 3, {"error": "candidate not found"}
    except ValueError as error:
        return 4, {"error": str(error)}
    return 0, {
        "candidate_id": result["candidate_id"],
        "status": result["status"],
    }


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0911
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
        _, _, data = load_stage_a_configuration(getattr(args, "config_dir", None))
        company_id = args.company.lower() if args.company else None
        sources = [
            {"key": key, **value}
            for key, value in data["sources"].items()
            if company_id is None or value["company_id"] == company_id
        ]
        print(json.dumps({"sources": sources}, indent=2, sort_keys=True))
        return 0

    database_url = _database_url(getattr(args, "database_url", None))
    if getattr(args, "config_dir", None) is not None:
        os.environ["MSI_CONFIG_DIR"] = str(args.config_dir.resolve())
    engine = create_database_engine(database_url)
    counts = seed_stage_a(engine, config_dir=getattr(args, "config_dir", None))
    if command in {"init-db", "seed", "ingest"}:
        print(json.dumps({"database": "ready", "inserted": counts}, sort_keys=True))
        engine.dispose()
        return 0
    if command == "validate":
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
    if command == "review":
        exit_code, payload = _review_candidate(engine, args)
        print(json.dumps(payload, sort_keys=True))
        engine.dispose()
        return exit_code
    if command == "serve":
        repository = IntelligenceRepository(engine)
        from mortgage_servicing_dashboard.api import create_app  # noqa: PLC0415

        uvicorn.run(create_app(repository=repository), host=args.host, port=args.port)
        engine.dispose()
        return 0
    return 2
