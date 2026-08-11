"""Typed, domain-neutral state shared by the foundation agent."""

from __future__ import annotations

from typing import Any, Literal, NotRequired

from deepagents import DeepAgentState
from langchain.agents import AgentState
from typing_extensions import TypedDict


class DashboardAgentState(AgentState[Any]):
    """Agent state containing correlation metadata but no customer identifiers."""

    request_id: NotRequired[str]
    data_classification: NotRequired[Literal["public", "synthetic"]]
    interaction_mode: NotRequired[Literal["foundation"]]


class DashboardContext(TypedDict):
    """Immutable-by-convention runtime context supplied for one invocation."""

    request_id: str
    data_classification: Literal["public", "synthetic"]
    interaction_mode: Literal["foundation"]


class ResearchWorkerState(DeepAgentState):
    """Deep Agents worker state containing no customer or operational fields."""

    request_id: NotRequired[str]
    data_classification: NotRequired[Literal["public", "synthetic"]]
    interaction_mode: NotRequired[Literal["research_draft"]]


class FoundationWorkflowState(TypedDict):
    """Deterministic LangGraph state for a human-review handoff."""

    request_id: str
    data_classification: Literal["public", "synthetic"]
    stage: Literal["received", "validated", "awaiting_human_review"]
    requires_human_review: bool


class FoundationWorkflowUpdate(TypedDict, total=False):
    """Partial deterministic state update emitted by workflow nodes."""

    stage: Literal["validated", "awaiting_human_review"]
    requires_human_review: bool
