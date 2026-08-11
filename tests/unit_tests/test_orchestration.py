"""Tests for deterministic LangGraph orchestration."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.orchestration import (
    WorkflowPersistenceDisabledError,
    _route_to_human_review,
    _validate_request,
    create_foundation_workflow,
)
from mortgage_servicing_dashboard.privacy import (
    DataClassification,
    UnsafeTracingConfigurationError,
)


def test_langgraph_runs_to_human_review_without_model() -> None:
    workflow = create_foundation_workflow()

    result = workflow.run(classification=DataClassification.SYNTHETIC)

    assert result.stage == "awaiting_human_review"
    assert result.requires_human_review is True
    assert len(result.request_id) == 32


def test_langgraph_rejects_remote_tracing_before_graph_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = create_foundation_workflow()
    graph_invoke = Mock()
    monkeypatch.setattr(workflow._graph, "invoke", graph_invoke)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(UnsafeTracingConfigurationError, match="Remote tracing"):
        workflow.run(classification=DataClassification.SYNTHETIC)

    graph_invoke.assert_not_called()


def test_langgraph_accepts_checkpoint_saver() -> None:
    checkpointer = InMemorySaver()
    settings = AppSettings(enable_langgraph_persistence=True)
    workflow = create_foundation_workflow(settings=settings, checkpointer=checkpointer)

    result = workflow.run(classification=DataClassification.PUBLIC)
    checkpoint = checkpointer.get_tuple(
        RunnableConfig(configurable={"thread_id": result.request_id})
    )

    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["stage"] == "awaiting_human_review"


def test_langgraph_rejects_checkpointer_when_persistence_is_disabled() -> None:
    with pytest.raises(WorkflowPersistenceDisabledError, match="disabled"):
        create_foundation_workflow(checkpointer=InMemorySaver())


def test_orchestration_nodes_fail_closed_on_invalid_state() -> None:
    with pytest.raises(ValueError, match="request_id"):
        _validate_request(
            {
                "request_id": "",
                "data_classification": "synthetic",
                "stage": "received",
                "requires_human_review": False,
            }
        )

    with pytest.raises(ValueError, match="public or synthetic"):
        _validate_request(
            {
                "request_id": "safe-correlation-id",
                "data_classification": "restricted",  # type: ignore[typeddict-item]
                "stage": "received",
                "requires_human_review": False,
            }
        )

    with pytest.raises(ValueError, match="before validation"):
        _route_to_human_review(
            {
                "request_id": "safe-correlation-id",
                "data_classification": "public",
                "stage": "received",
                "requires_human_review": False,
            }
        )
