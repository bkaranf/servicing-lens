"""Tests for LangChain `create_agent` integration."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from mortgage_servicing_dashboard.agent import (
    AgentConfigurationError,
    AgentProtocolError,
    ModelInvocationDisabledError,
    create_dashboard_agent,
)
from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import create_database_engine
from mortgage_servicing_dashboard.privacy import DataClassification, SensitiveContentError
from mortgage_servicing_dashboard.repository import IntelligenceRepository, seed_stage_a
from tests.unit_tests.fake_models import RecordingToolModel

_READ_TOOL_NAMES = {
    "list_companies",
    "get_company_profile",
    "list_metric_definitions",
    "get_metric_series",
    "list_observations",
    "compare_metric",
    "get_observation_provenance",
    "get_evidence",
    "get_disclosure_coverage",
    "list_earnings_events",
    "get_pipeline_freshness",
}


def _settings(*, enabled: bool = True) -> AppSettings:
    return AppSettings(
        environment="test",
        model="test:foundation",
        enable_model_calls=enabled,
        max_prompt_chars=500,
    )


@pytest.fixture
def repository(tmp_path: Path) -> IntelligenceRepository:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'agent.db').as_posix()}")
    seed_stage_a(engine)
    return IntelligenceRepository(engine)


def test_create_agent_runs_tool_loop_with_safe_tools_only(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_companies",
                        "args": {},
                        "id": "safe-call",
                    }
                ],
            ),
            AIMessage(content="The selected-company universe is ready."),
        ]
    )
    agent = create_dashboard_agent(_settings(), model=model, repository=repository)
    prompt = agent.approve_prompt(
        "Report the synthetic foundation status.",
        classification=DataClassification.SYNTHETIC,
    )

    result = agent.invoke(prompt)

    assert result.text == "The selected-company universe is ready."
    assert len(result.request_id) == 32
    assert model.bound_tool_names
    assert set(model.bound_tool_names[0]) == _READ_TOOL_NAMES


def test_agent_blocks_sensitive_model_output(repository: IntelligenceRepository) -> None:
    model = RecordingToolModel(
        responses=[AIMessage(content="SYNTHETIC reserved email fixture@example.test")]
    )
    agent = create_dashboard_agent(_settings(), model=model, repository=repository)
    prompt = agent.approve_prompt(
        "Return a synthetic status.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(SensitiveContentError, match="email"):
        agent.invoke(prompt)


def test_agent_invocation_is_disabled_by_default(repository: IntelligenceRepository) -> None:
    model = RecordingToolModel(responses=[AIMessage(content="unused")])
    agent = create_dashboard_agent(
        _settings(enabled=False),
        model=model,
        repository=repository,
    )
    prompt = agent.approve_prompt(
        "Synthetic status.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(ModelInvocationDisabledError):
        agent.invoke(prompt)
    assert not model.received_messages


def test_agent_requires_a_model(repository: IntelligenceRepository) -> None:
    with pytest.raises(AgentConfigurationError):
        create_dashboard_agent(
            AppSettings(model=None, enable_model_calls=False),
            repository=repository,
        )


def test_agent_requires_authoritative_repository() -> None:
    model = RecordingToolModel(responses=[AIMessage(content="unused")])
    with pytest.raises(AgentConfigurationError, match="repository"):
        create_dashboard_agent(_settings(), model=model)


def test_agent_rejects_numeric_claim_absent_from_tool_results(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(responses=[AIMessage(content="Servicing revenue is 999.")])
    agent = create_dashboard_agent(_settings(), model=model, repository=repository)
    prompt = agent.approve_prompt(
        "Summarize the public evidence.",
        classification=DataClassification.PUBLIC,
    )
    with pytest.raises(AgentProtocolError, match="absent"):
        agent.invoke(prompt)
