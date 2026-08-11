"""Safe, static tools and ports for the foundation-only agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.tools import BaseTool, StructuredTool


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Non-customer capability metadata safe to expose to a model."""

    phase: Literal["foundation"]
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

    accepted_data: tuple[Literal["public", "synthetic"], ...]
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
            phase="foundation",
            status="ready",
            available=(
                "validated configuration",
                "privacy boundary",
                "LangChain agent wiring",
                "LangGraph human-review orchestration boundary",
                "Deep Agents research-draft worker boundary",
                "static foundation tools",
            ),
            unavailable=(
                "dashboard UI",
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
            accepted_data=("public", "synthetic"),
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
