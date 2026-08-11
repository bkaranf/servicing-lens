"""Tests for safe static foundation tools."""

from __future__ import annotations

from mortgage_servicing_dashboard.tools import (
    StaticFoundationInformation,
    build_foundation_tools,
)


def test_static_information_has_no_business_capabilities() -> None:
    information = StaticFoundationInformation()

    capabilities = information.capabilities().as_payload()
    guardrails = information.guardrails().as_payload()

    assert capabilities["status"] == "ready"
    assert "mortgage calculations" in capabilities["unavailable"]
    assert guardrails["customer_data_access"] == "disabled"
    assert guardrails["operational_actions"] == "disabled"


def test_tools_are_network_free_and_read_only() -> None:
    tools = build_foundation_tools()
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "get_foundation_capabilities",
        "get_foundation_guardrails",
    }
    assert by_name["get_foundation_capabilities"].invoke({})["phase"] == "foundation"
    assert by_name["get_foundation_guardrails"].invoke({})["operational_actions"] == "disabled"
