"""Deterministic LangGraph ingestion workflow with an auditable review interrupt."""

from __future__ import annotations

import operator
from collections.abc import Callable
from itertools import pairwise
from typing import Annotated, Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

INGESTION_NODES = (
    "discover_sources",
    "acquire_source",
    "hash_and_store",
    "parse_document",
    "extract_xbrl_facts",
    "extract_bank_regulatory_facts",
    "resolve_entity_and_scope",
    "resolve_fiscal_period",
    "map_metric",
    "normalize_value_and_units",
    "apply_effective_dated_rules",
    "reconcile_and_validate",
    "deduplicate_and_supersede",
    "quarantine_ambiguous_candidates",
    "request_human_review",
    "publish_approved_observations",
    "refresh_comparability_and_materializations",
    "emit_audit_events",
)


class IngestionState(TypedDict, total=False):
    """Serializable state kept free of secrets and private/customer data."""

    thread_id: str
    source_keys: list[str]
    visited: Annotated[list[str], operator.add]
    ambiguous_candidates: list[dict[str, Any]]
    review_decision: Literal["approve", "reject", "pending"]
    published_count: int
    audit_events: list[str]


def _visit(name: str) -> Callable[[IngestionState], dict[str, Any]]:
    def node(_: IngestionState) -> dict[str, Any]:
        return {"visited": [name]}

    node.__name__ = name
    return node


def _quarantine(state: IngestionState) -> dict[str, Any]:
    candidates = state.get("ambiguous_candidates", [])
    return {"visited": ["quarantine_ambiguous_candidates"], "ambiguous_candidates": candidates}


def _review_route(state: IngestionState) -> Literal["request_human_review", "publish"]:
    return "request_human_review" if state.get("ambiguous_candidates") else "publish"


def _request_review(state: IngestionState) -> dict[str, Any]:
    candidates = state.get("ambiguous_candidates", [])
    decision = interrupt(
        {
            "kind": "metric_candidate_review",
            "candidate_ids": [str(candidate.get("id", "unknown")) for candidate in candidates],
            "allowed_decisions": ["approve", "reject"],
        }
    )
    if not isinstance(decision, dict) or decision.get("decision") not in {"approve", "reject"}:
        msg = "human review resume payload must contain approve or reject"
        raise ValueError(msg)
    return {
        "visited": ["request_human_review"],
        "review_decision": decision["decision"],
    }


def _publish(state: IngestionState) -> dict[str, Any]:
    rejected = state.get("review_decision") == "reject"
    return {
        "visited": ["publish_approved_observations"],
        "published_count": 0 if rejected else len(state.get("source_keys", [])),
    }


def _audit(state: IngestionState) -> dict[str, Any]:
    return {
        "visited": ["emit_audit_events"],
        "audit_events": [
            f"ingestion_completed:published={state.get('published_count', 0)}",
            f"review={state.get('review_decision', 'not_required')}",
        ],
    }


def create_ingestion_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the required replayable ingestion stages."""
    builder = StateGraph(IngestionState)
    simple_nodes = INGESTION_NODES[:13]
    for name in simple_nodes:
        builder.add_node(name, cast("Any", _visit(name)))
    builder.add_node("quarantine_ambiguous_candidates", _quarantine)
    builder.add_node("request_human_review", _request_review)
    builder.add_node("publish_approved_observations", _publish)
    builder.add_node(
        "refresh_comparability_and_materializations",
        cast("Any", _visit("refresh_comparability_and_materializations")),
    )
    builder.add_node("emit_audit_events", _audit)
    builder.add_edge(START, INGESTION_NODES[0])
    for left, right in pairwise(simple_nodes):
        builder.add_edge(left, right)
    builder.add_edge(simple_nodes[-1], "quarantine_ambiguous_candidates")
    builder.add_conditional_edges(
        "quarantine_ambiguous_candidates",
        _review_route,
        {
            "request_human_review": "request_human_review",
            "publish": "publish_approved_observations",
        },
    )
    builder.add_edge("request_human_review", "publish_approved_observations")
    builder.add_edge("publish_approved_observations", "refresh_comparability_and_materializations")
    builder.add_edge("refresh_comparability_and_materializations", "emit_audit_events")
    builder.add_edge("emit_audit_events", END)
    return builder.compile(checkpointer=checkpointer, name="public_servicing_ingestion_v1")


def resume_review(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    thread_id: str,
    decision: Literal["approve", "reject"],
) -> dict[str, Any]:
    """Resume a paused review on the same durable thread identifier."""
    config = RunnableConfig(configurable={"thread_id": thread_id})
    result = graph.invoke(Command[Any](resume={"decision": decision}), config=config)
    return dict(result)
