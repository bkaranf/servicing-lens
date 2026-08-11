"""Network-free command-line diagnostics for the application foundation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.tools import StaticFoundationInformation


def build_parser() -> argparse.ArgumentParser:
    """Build the small, non-interactive foundation CLI.

    Returns:
        An argument parser with a network-free `doctor` command.
    """
    parser = argparse.ArgumentParser(
        prog="msd-foundation",
        description="Inspect the mortgage servicing dashboard foundation safely.",
    )
    parser.add_argument("command", choices=("doctor",), nargs="?", default="doctor")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the allow-listed readiness summary as JSON.",
    )
    return parser


def doctor_payload(settings: AppSettings) -> dict[str, Any]:
    """Build a deterministic, allow-listed readiness payload.

    Args:
        settings: Validated non-secret settings.

    Returns:
        Foundation capabilities, guardrails, and safe configuration metadata.
    """
    information = StaticFoundationInformation()
    return {
        "application": "mortgage-servicing-dashboard-foundation",
        "configuration": settings.safe_summary(),
        "capabilities": information.capabilities().as_payload(),
        "guardrails": information.guardrails().as_payload(),
    }


def _format_text(payload: dict[str, Any]) -> str:
    """Format allow-listed readiness fields for people.

    Args:
        payload: Output from `doctor_payload`.

    Returns:
        A compact content-free status report.
    """
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
            "customer data access: disabled",
            "mortgage calculations: not implemented",
            "operational actions: disabled",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run network-free diagnostics without displaying raw configuration values.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        Process exit code (`0` for readiness, `2` for invalid configuration).
    """
    args = build_parser().parse_args(argv)
    try:
        settings = AppSettings()
    except ValidationError:
        print(
            "Configuration is invalid; review the non-secret MSD_* settings.",
            file=sys.stderr,
        )
        return 2

    payload = doctor_payload(settings)
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_text(payload))
    return 0
