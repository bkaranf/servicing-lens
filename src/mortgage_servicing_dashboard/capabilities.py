"""Small deterministic capability metadata used by local readiness output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Allow-listed application capabilities with no model or customer state."""

    phase: Literal["phase_3_metric_deepening"]
    phase_role: Literal["legacy_retained_dataset_compatibility"]
    status: Literal["ready"]
    readiness_scope: Literal["local_read_only_workflows"]
    registered_scope: Literal["phase_5_cohort_b"]
    default_network_access: Literal["disabled"]
    available: tuple[str, ...]
    unavailable: tuple[str, ...]
    safe_next_actions: tuple[str, ...]

    def as_payload(self) -> dict[str, str | list[str]]:
        """Return JSON-compatible capability metadata."""
        return {
            "phase": self.phase,
            "phase_role": self.phase_role,
            "status": self.status,
            "readiness_scope": self.readiness_scope,
            "registered_scope": self.registered_scope,
            "default_network_access": self.default_network_access,
            "available": list(self.available),
            "unavailable": list(self.unavailable),
            "safe_next_actions": list(self.safe_next_actions),
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
            phase_role="legacy_retained_dataset_compatibility",
            status="ready",
            readiness_scope="local_read_only_workflows",
            registered_scope="phase_5_cohort_b",
            default_network_access="disabled",
            available=(
                "validated configuration",
                "declarative Phase 5 cohort A and cohort B company registries",
                "default five-bank/five-nonbank Phase 5 filing discovery and sync scope",
                "packaged Phase 5 live-sync manifests and financial-field registry",
                "checkout-only Phase 5 replay with bounded content-addressed evidence",
                "immutable evidence and bitemporal observations",
                "read-only API and accessible dashboard",
                "SEC filing calendar",
                "filing-specific SEC XBRL adapter",
                "legacy two-issuer Stage A and Phase 3 retained-data compatibility workflows",
                "exact derived-observation input lineage",
                "versioned delinquency, MSR economics, and portfolio-mix semantics",
                "deterministic 16-stage ingestion and durable human review",
            ),
            unavailable=(
                "bundled Phase 5 retained filing bytes or bounded replay fixtures",
                "published Phase 5 expansion companies beyond cohort B",
                "comprehensive issuer coverage or industry ranking",
                "customer or loan data access",
                "mortgage calculations",
                "servicing decisions",
                "operational actions",
                "model-authored values or summaries",
            ),
            safe_next_actions=(
                "msi discover",
                (
                    "msi ingest --phase5-cohort-b --database-url <isolated-database-url> "
                    "--runtime-dir <runtime-dir>"
                ),
                "msi discover --live --company <ticker>",
                "msi ingest --live --database-url <isolated-database-url>",
                "msi validate --database-url <database-url>",
                "msi coverage --database-url <database-url>",
                "msi evidence --evidence-id <id> --database-url <database-url>",
                "msi serve --database-url <database-url>",
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
