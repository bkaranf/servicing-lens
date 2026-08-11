"""Tests for LangChain `create_agent` integration."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from mortgage_servicing_dashboard.agent import (
    AgentConfigurationError,
    ModelInvocationDisabledError,
    create_dashboard_agent,
)
from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.privacy import DataClassification, SensitiveContentError
from tests.unit_tests.fake_models import RecordingToolModel


def _settings(*, enabled: bool = True) -> AppSettings:
    return AppSettings(
        environment="test",
        model="test:foundation",
        enable_model_calls=enabled,
        max_prompt_chars=500,
    )


def test_create_agent_runs_tool_loop_with_safe_tools_only() -> None:
    model = RecordingToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_foundation_capabilities",
                        "args": {},
                        "id": "safe-call",
                    }
                ],
            ),
            AIMessage(content="Foundation capabilities are ready."),
        ]
    )
    agent = create_dashboard_agent(_settings(), model=model)
    prompt = agent.approve_prompt(
        "Report the synthetic foundation status.",
        classification=DataClassification.SYNTHETIC,
    )

    result = agent.invoke(prompt)

    assert result.text == "Foundation capabilities are ready."
    assert len(result.request_id) == 32
    assert model.bound_tool_names
    assert set(model.bound_tool_names[0]) == {
        "get_foundation_capabilities",
        "get_foundation_guardrails",
    }


def test_agent_blocks_sensitive_model_output() -> None:
    model = RecordingToolModel(
        responses=[AIMessage(content="SYNTHETIC reserved email fixture@example.test")]
    )
    agent = create_dashboard_agent(_settings(), model=model)
    prompt = agent.approve_prompt(
        "Return a synthetic status.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(SensitiveContentError, match="email"):
        agent.invoke(prompt)


def test_agent_invocation_is_disabled_by_default() -> None:
    model = RecordingToolModel(responses=[AIMessage(content="unused")])
    agent = create_dashboard_agent(_settings(enabled=False), model=model)
    prompt = agent.approve_prompt(
        "Synthetic status.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(ModelInvocationDisabledError):
        agent.invoke(prompt)
    assert not model.received_messages


def test_agent_requires_a_model() -> None:
    with pytest.raises(AgentConfigurationError):
        create_dashboard_agent(AppSettings(model=None, enable_model_calls=False))
