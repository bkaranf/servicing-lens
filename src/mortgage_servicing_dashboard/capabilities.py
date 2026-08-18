"""Small deterministic capability metadata used by local readiness output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Allow-listed application capabilities with no model or customer state."""

    phase: Literal["phase_3_metric_deepening"]
    status: Literal["ready"]
    available: tuple[str, ...]
    unavailable: tuple[str, ...]

    def as_payload(self) -> dict[str, str | list[str]]:
        """Return JSON-compatible capability metadata."""
        return {
            "phase": self.phase,
            "status": self.status,
            "available": list(self.available),
            "unavailable": list(self.unavailable),
        }


@dataclass(frozen=True, slots=True)
class GuardrailSnapshot:
    """Static product boundaries safe to expose in readiness diagnostics."""

    accepted_data: tuple[str, ...]
    customer_data_access: Literal["disabled"]
    operational_actions: Literal["disabled"]
    mortgage_calculations: Literal["not_implemented"]

    def as_payload(self) -> dict[str, str | list[str]]:
        """Return JSON-compatible guardrail metadata."""
        return {
            "accepted_data": list(self.accepted_data),
            "customer_data_access": self.customer_data_access,
            "operational_actions": self.operational_actions,
            "mortgage_calculations": self.mortgage_calculations,
        }


class StaticCapabilities:
    """Return the deterministic local application's supported surfaces."""

    def capabilities(self) -> CapabilitySnapshot:
        """Return implemented and deliberately unavailable capabilities."""
        return CapabilitySnapshot(
            phase="phase_3_metric_deepening",
            status="ready",
            available=(
                "validated configuration",
                "versioned two-company metric catalog",
                "immutable evidence and bitemporal observations",
                "read-only API and accessible dashboard",
                "SEC filing calendar",
                "filing-specific SEC XBRL adapter",
                "two-issuer profitability and expense metric deepening",
                "exact derived-observation input lineage",
                "versioned delinquency, MSR economics, and portfolio-mix semantics",
                "deterministic 16-stage ingestion and durable human review",
            ),
            unavailable=(
                "customer or loan data access",
                "mortgage calculations",
                "servicing decisions",
                "operational actions",
                "model-authored values or summaries",
            ),
        )

    def guardrails(self) -> GuardrailSnapshot:
        """Return fixed data and action restrictions."""
        return GuardrailSnapshot(
            accepted_data=(
                "public_filing",
                "synthetic_test",
            ),
            customer_data_access="disabled",
            operational_actions="disabled",
            mortgage_calculations="not_implemented",
        )
