"""Safe, static tools and ports for the foundation-only agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from langchain_core.tools import BaseTool, StructuredTool

from mortgage_servicing_dashboard.repository import IntelligenceRepository


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Non-customer capability metadata safe to expose to a model."""

    phase: Literal["stage_a"]
    status: Literal["ready"]
    available: tuple[str, ...]
    unavailable: tuple[str, ...]

    def as_payload(self) -> dict[str, str | list[str]]:
        """Convert the snapshot to a JSON-compatible tool payload.

        Returns:
            Capability fields containing no customer or operational data.
        """
        return {
            "phase": self.phase,
            "status": self.status,
            "available": list(self.available),
            "unavailable": list(self.unavailable),
        }


@dataclass(frozen=True, slots=True)
class GuardrailSnapshot:
    """Static policy metadata safe to expose to a model."""

    accepted_data: tuple[str, ...]
    customer_data_access: Literal["disabled"]
    operational_actions: Literal["disabled"]
    mortgage_calculations: Literal["not_implemented"]

    def as_payload(self) -> dict[str, str | list[str]]:
        """Convert the guardrails to a JSON-compatible tool payload.

        Returns:
            Policy fields containing no prompt or customer content.
        """
        return {
            "accepted_data": list(self.accepted_data),
            "customer_data_access": self.customer_data_access,
            "operational_actions": self.operational_actions,
            "mortgage_calculations": self.mortgage_calculations,
        }


class FoundationInformationPort(Protocol):
    """Interface for non-sensitive application metadata exposed to tools."""

    def capabilities(self) -> CapabilitySnapshot:
        """Return implemented and deliberately unavailable capabilities."""

    def guardrails(self) -> GuardrailSnapshot:
        """Return the foundation's fixed safety boundary."""


class StaticFoundationInformation:
    """Provide deterministic metadata without network or data-store access."""

    def capabilities(self) -> CapabilitySnapshot:
        """Return baseline-only capabilities.

        Returns:
            A deterministic readiness snapshot.
        """
        return CapabilitySnapshot(
            phase="stage_a",
            status="ready",
            available=(
                "validated configuration",
                "privacy boundary",
                "LangChain agent wiring",
                "LangGraph human-review orchestration boundary",
                "Deep Agents research-draft worker boundary",
                "versioned two-company metric catalog",
                "immutable evidence and bitemporal observations",
                "read-only API and accessible dashboard",
                "typed public-intelligence read tools",
                "interruptible human-review ingestion graph",
            ),
            unavailable=(
                "customer or loan data access",
                "mortgage calculations",
                "servicing decisions",
                "operational actions",
            ),
        )

    def guardrails(self) -> GuardrailSnapshot:
        """Return fixed data and action restrictions.

        Returns:
            A deterministic guardrail snapshot.
        """
        return GuardrailSnapshot(
            accepted_data=(
                "public_filing",
                "public_regulatory",
                "issuer_public",
                "synthetic_test",
            ),
            customer_data_access="disabled",
            operational_actions="disabled",
            mortgage_calculations="not_implemented",
        )


def build_foundation_tools(
    information: FoundationInformationPort | None = None,
) -> tuple[BaseTool, ...]:
    """Build read-only tools backed by static, non-customer metadata.

    Args:
        information: Optional metadata port, primarily for dependency injection.

    Returns:
        Tools that cannot calculate, decide, mutate state, or access customer data.
    """
    active_information = information or StaticFoundationInformation()

    def get_foundation_capabilities() -> dict[str, str | list[str]]:
        """Report implemented foundation features and explicit omissions."""
        return active_information.capabilities().as_payload()

    def get_foundation_guardrails() -> dict[str, str | list[str]]:
        """Report the fixed data, calculation, and action restrictions."""
        return active_information.guardrails().as_payload()

    return (
        StructuredTool.from_function(
            func=get_foundation_capabilities,
            name="get_foundation_capabilities",
            description=(
                "Return static application-foundation capabilities. This tool has no "
                "customer data and performs no mortgage calculations or actions."
            ),
        ),
        StructuredTool.from_function(
            func=get_foundation_guardrails,
            name="get_foundation_guardrails",
            description=(
                "Return static privacy and operational guardrails. This tool has no "
                "external-system access."
            ),
        ),
    )


def build_intelligence_tools(  # noqa: C901
    repository: IntelligenceRepository,
) -> tuple[BaseTool, ...]:
    """Build typed, bounded, read-only tools over published public observations."""

    def list_companies() -> list[dict[str, object]]:
        """Return the versioned selected-company universe."""
        return repository.companies()

    def get_company_profile(company_id: Literal["tfc", "pfsi"]) -> dict[str, object]:
        """Return one selected company and its published observation count."""
        company = next(item for item in repository.companies() if item["id"] == company_id)
        return {
            **company,
            "observation_count": len(repository.observations(company_id=company_id)),
        }

    def list_metric_definitions() -> list[dict[str, object]]:
        """Return all versioned metric semantic contracts."""
        return repository.metrics()

    def get_metric_series(
        company_id: Literal["tfc", "pfsi"],
        metric_id: str,
    ) -> list[dict[str, object]]:
        """Return a metric series for one selected issuer, including explicit missing rows."""
        return [
            item.as_dict()
            for item in repository.observations(company_id=company_id, metric_id=metric_id)
        ]

    def list_observations(
        company_id: Literal["tfc", "pfsi"] | None = None,
        metric_id: str | None = None,
        period_end: str | None = None,
    ) -> list[dict[str, object]]:
        """Read bounded observations using optional issuer, metric, and period filters."""
        parsed_period = date.fromisoformat(period_end) if period_end is not None else None
        return [
            item.as_dict()
            for item in repository.observations(
                company_id=company_id,
                metric_id=metric_id,
                period_end=parsed_period,
            )
        ]

    def compare_metric(metric_id: str, period_end: str) -> dict[str, object]:
        """Assess TFC and PFSI pairwise comparability for one metric and period."""
        result = repository.compare(metric_id=metric_id, period_end=date.fromisoformat(period_end))
        return {"status": "insufficient_information"} if result is None else result.as_dict()

    def get_observation_provenance(observation_id: str) -> dict[str, object]:
        """Return the bounded source and semantic context for one observation identifier."""
        result = repository.observation(observation_id)
        return {"status": "not_found"} if result is None else result.as_dict()

    def get_evidence(evidence_id: str) -> dict[str, object]:
        """Return immutable public evidence metadata by content-addressed identifier."""
        result = repository.evidence(evidence_id)
        return {"status": "not_found"} if result is None else result

    def get_disclosure_coverage() -> list[dict[str, object]]:
        """Return reported-versus-not-disclosed counts by issuer and quarter."""
        return repository.coverage()

    def list_earnings_events() -> list[dict[str, object]]:
        """Return the selected issuers' public disclosure events."""
        return repository.earnings_events()

    def get_pipeline_freshness() -> dict[str, object]:
        """Return recorded dataset and knowledge-time freshness metadata."""
        return repository.freshness()

    return (
        StructuredTool.from_function(
            list_companies,
            name="list_companies",
            description="Read the versioned TFC/PFSI selected universe.",
        ),
        StructuredTool.from_function(
            get_company_profile,
            name="get_company_profile",
            description="Read one selected company's public-data profile.",
        ),
        StructuredTool.from_function(
            list_metric_definitions,
            name="list_metric_definitions",
            description="Read versioned servicing metric definitions and rules.",
        ),
        StructuredTool.from_function(
            get_metric_series,
            name="get_metric_series",
            description="Read one versioned public metric series for TFC or PFSI.",
        ),
        StructuredTool.from_function(
            list_observations,
            name="list_observations",
            description="Read observations with bounded issuer, metric, or period filters.",
        ),
        StructuredTool.from_function(
            compare_metric,
            name="compare_metric",
            description="Read a deterministic pairwise comparability assessment.",
        ),
        StructuredTool.from_function(
            get_observation_provenance,
            name="get_observation_provenance",
            description="Read source evidence and semantic metadata for one observation.",
        ),
        StructuredTool.from_function(
            get_evidence,
            name="get_evidence",
            description="Read immutable evidence metadata and its authoritative locator.",
        ),
        StructuredTool.from_function(
            get_disclosure_coverage,
            name="get_disclosure_coverage",
            description="Read explicit disclosure and missingness coverage.",
        ),
        StructuredTool.from_function(
            list_earnings_events,
            name="list_earnings_events",
            description="Read selected-company public earnings disclosure events.",
        ),
        StructuredTool.from_function(
            get_pipeline_freshness,
            name="get_pipeline_freshness",
            description="Read materialized dataset and knowledge-time freshness.",
        ),
    )
