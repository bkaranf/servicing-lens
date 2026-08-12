"""Tests for the restricted Deep Agents research worker."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from mortgage_servicing_dashboard.agent import (
    AgentConfigurationError,
    AgentProtocolError,
)
from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.database import create_database_engine
from mortgage_servicing_dashboard.deep_worker import (
    DeepAgentInvocationDisabledError,
    UnsafeResearchToolError,
    _assert_profile_key_matches,
    _tool_name,
    create_research_worker,
)
from mortgage_servicing_dashboard.privacy import DataClassification, SensitiveContentError
from mortgage_servicing_dashboard.repository import IntelligenceRepository, seed_stage_a
from tests.unit_tests.fake_models import RecordingToolModel

_FORBIDDEN_DEEP_AGENT_TOOLS = {
    "delete",
    "edit_file",
    "execute",
    "glob",
    "grep",
    "ls",
    "read_file",
    "task",
    "write_file",
    "write_todos",
}

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


def _settings() -> AppSettings:
    return AppSettings(
        environment="test",
        model="test:foundation",
        enable_model_calls=True,
        enable_deep_agent=True,
        max_prompt_chars=500,
    )


@pytest.fixture
def repository(tmp_path: Path) -> IntelligenceRepository:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'deep-worker.db').as_posix()}")
    seed_stage_a(engine)
    return IntelligenceRepository(engine)


def test_deep_agent_constructs_and_invokes_without_external_credentials(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(responses=[AIMessage(content="Draft for review.")])
    worker = create_research_worker(
        _settings(),
        model=model,
        profile_key="test:foundation",
        repository=repository,
    )
    prompt = worker.approve_prompt(
        "Analyze a synthetic public-policy excerpt.",
        classification=DataClassification.SYNTHETIC,
    )

    result = worker.analyze(prompt)

    assert result.text == "Draft for review."
    assert result.requires_human_review is True
    assert model.bound_tool_names
    visible_tools = set(model.bound_tool_names[0])
    assert visible_tools == _READ_TOOL_NAMES
    assert visible_tools.isdisjoint(_FORBIDDEN_DEEP_AGENT_TOOLS)


def test_deep_agent_blocks_fabricated_filesystem_tool_call(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/forbidden.txt", "content": "blocked"},
                        "id": "unsafe-call",
                    }
                ],
            )
        ]
    )
    worker = create_research_worker(
        _settings(),
        model=model,
        profile_key="test:foundation",
        repository=repository,
    )
    prompt = worker.approve_prompt(
        "Prepare a synthetic analysis draft.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(UnsafeResearchToolError, match="blocked"):
        worker.analyze(prompt)


def test_injected_model_must_match_active_harness_profile(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(responses=[AIMessage(content="unused")])

    with pytest.raises(AgentConfigurationError, match="profile"):
        create_research_worker(
            _settings(),
            model=model,
            profile_key="other:model",
            repository=repository,
        )


def test_deep_agent_invocation_respects_disabled_switch(
    repository: IntelligenceRepository,
) -> None:
    settings = AppSettings(
        environment="test",
        model="test:foundation",
        enable_model_calls=True,
        enable_deep_agent=False,
    )
    model = RecordingToolModel(responses=[AIMessage(content="unused")])
    worker = create_research_worker(
        settings,
        model=model,
        profile_key="test:foundation",
        repository=repository,
    )
    prompt = worker.approve_prompt(
        "Prepare a synthetic draft.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(DeepAgentInvocationDisabledError):
        worker.analyze(prompt)
    assert not model.received_messages


def test_deep_agent_blocks_sensitive_output(repository: IntelligenceRepository) -> None:
    model = RecordingToolModel(
        responses=[AIMessage(content="SYNTHETIC reserved email fixture@example.test")]
    )
    worker = create_research_worker(
        _settings(),
        model=model,
        profile_key="test:foundation",
        repository=repository,
    )
    prompt = worker.approve_prompt(
        "Prepare a synthetic draft.",
        classification=DataClassification.SYNTHETIC,
    )

    with pytest.raises(SensitiveContentError, match="email"):
        worker.analyze(prompt)


def test_deep_agent_requires_model_and_exact_profile() -> None:
    unconfigured = AppSettings(model=None, enable_model_calls=False)
    with pytest.raises(AgentConfigurationError, match="model"):
        create_research_worker(unconfigured)

    injected = RecordingToolModel(responses=[AIMessage(content="unused")])
    with pytest.raises(AgentConfigurationError, match="profile key"):
        create_research_worker(unconfigured, model=injected)


def test_deep_agent_requires_authoritative_repository() -> None:
    model = RecordingToolModel(responses=[AIMessage(content="unused")])
    with pytest.raises(AgentConfigurationError, match="repository"):
        create_research_worker(
            _settings(),
            model=model,
            profile_key="test:foundation",
        )


def test_profile_string_matching_fails_closed() -> None:
    _assert_profile_key_matches("test:foundation", "test:foundation")
    with pytest.raises(AgentConfigurationError, match="exactly match"):
        _assert_profile_key_matches("test:foundation", "other:model")


def test_tool_name_parser_does_not_read_arguments() -> None:
    assert _tool_name({"name": "top_level", "args": {"secret": "ignored"}}) == "top_level"
    assert _tool_name({"function": {"name": "nested", "arguments": "ignored"}}) == "nested"
    assert _tool_name({"function": {"arguments": "ignored"}}) is None


def test_deep_agent_rejects_numeric_claim_absent_from_tool_results(
    repository: IntelligenceRepository,
) -> None:
    model = RecordingToolModel(responses=[AIMessage(content="Portfolio value is 999.")])
    worker = create_research_worker(
        _settings(),
        model=model,
        profile_key="test:foundation",
        repository=repository,
    )
    prompt = worker.approve_prompt(
        "Prepare a public evidence draft.",
        classification=DataClassification.PUBLIC,
    )
    with pytest.raises(AgentProtocolError, match="absent"):
        worker.analyze(prompt)
