"""Deterministic LangGraph orchestration ending at a human-review boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.privacy import (
    DataClassification,
    assert_remote_tracing_disabled,
)
from mortgage_servicing_dashboard.state import (
    FoundationWorkflowState,
    FoundationWorkflowUpdate,
)


@dataclass(frozen=True, slots=True)
class FoundationWorkflowResult:
    """Non-operational workflow result that always requires human review."""

    request_id: str
    stage: Literal["awaiting_human_review"]
    requires_human_review: Literal[True]


class WorkflowPersistenceDisabledError(RuntimeError):
    """Report that a checkpointer was supplied while persistence is disabled."""


def _validate_request(state: FoundationWorkflowState) -> FoundationWorkflowUpdate:
    """Validate the non-customer correlation state deterministically."""
    if not state["request_id"]:
        msg = "request_id must not be empty"
        raise ValueError(msg)
    if state["data_classification"] not in {"public", "synthetic"}:
        msg = "workflow accepts only public or synthetic classification"
        raise ValueError(msg)
    return {"stage": "validated"}


def _route_to_human_review(state: FoundationWorkflowState) -> FoundationWorkflowUpdate:
    """Stop the baseline before any regulated or operational action."""
    if state["stage"] != "validated":
        msg = "workflow cannot request review before validation"
        raise ValueError(msg)
    return {"stage": "awaiting_human_review", "requires_human_review": True}


class FoundationWorkflow:
    """Run a compiled LangGraph without prompts, models, or business logic."""

    def __init__(
        self,
        graph: CompiledStateGraph[
            FoundationWorkflowState,
            None,
            FoundationWorkflowState,
            FoundationWorkflowState,
        ],
    ) -> None:
        """Store the compiled checkpoint-ready graph.

        Args:
            graph: Deterministic compiled LangGraph.
        """
        self._graph = graph

    def run(self, *, classification: DataClassification) -> FoundationWorkflowResult:
        """Advance a metadata-only request to mandatory human review.

        Args:
            classification: Public or synthetic classification for the future request.

        Returns:
            A result that cannot represent automatic approval or execution.
        """
        assert_remote_tracing_disabled()
        if classification is DataClassification.RESTRICTED_PRIVATE:
            msg = "workflow rejects restricted/private classification"
            raise ValueError(msg)
        request_id = uuid4().hex
        initial_state = FoundationWorkflowState(
            request_id=request_id,
            data_classification=(
                "synthetic" if classification is DataClassification.SYNTHETIC else "public"
            ),
            stage="received",
            requires_human_review=False,
        )
        config = RunnableConfig(
            configurable={"thread_id": request_id},
            tags=["msd-foundation", "deterministic-orchestration"],
            callbacks=[],
        )
        result = self._graph.invoke(initial_state, config=config)
        if result["stage"] != "awaiting_human_review" or not result["requires_human_review"]:
            msg = "foundation workflow did not stop at the human-review boundary"
            raise RuntimeError(msg)
        return FoundationWorkflowResult(
            request_id=request_id,
            stage="awaiting_human_review",
            requires_human_review=True,
        )


def create_foundation_workflow(
    *,
    settings: AppSettings | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> FoundationWorkflow:
    """Compile the deterministic state graph with an optional checkpointer.

    Args:
        settings: Optional settings carrying the independent persistence switch.
        checkpointer: Optional LangGraph saver supplied by the deployment layer.

    Returns:
        A workflow that is runnable in memory and ready for durable checkpointing.

    Raises:
        WorkflowPersistenceDisabledError: If a saver is supplied while persistence is off.
    """
    persistence_enabled = settings.enable_langgraph_persistence if settings is not None else False
    if checkpointer is not None and not persistence_enabled:
        msg = "LangGraph persistence is disabled by MSD_ENABLE_LANGGRAPH_PERSISTENCE"
        raise WorkflowPersistenceDisabledError(msg)

    builder = StateGraph(FoundationWorkflowState)
    builder.add_node("validate_request", _validate_request)
    builder.add_node("require_human_review", _route_to_human_review)
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "require_human_review")
    builder.add_edge("require_human_review", END)
    graph = builder.compile(
        checkpointer=checkpointer,
        debug=False,
        name="mortgage_servicing_foundation_workflow",
    )
    return FoundationWorkflow(graph)
