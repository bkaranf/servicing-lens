"""LangChain `create_agent` wiring behind a privacy-safe invocation API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    PIIDetectionError,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from mortgage_servicing_dashboard.config import AppSettings
from mortgage_servicing_dashboard.privacy import (
    ApprovedPrompt,
    DataClassification,
    PromptBoundary,
    SensitiveContentError,
    assert_remote_tracing_disabled,
    build_privacy_middleware,
)
from mortgage_servicing_dashboard.repository import IntelligenceRepository
from mortgage_servicing_dashboard.state import DashboardAgentState, DashboardContext
from mortgage_servicing_dashboard.tools import FoundationInformationPort, build_intelligence_tools

_SYSTEM_PROMPT = """You are the public mortgage servicing intelligence assistant.
Operate only on public or synthetic, de-identified text. Never request customer, borrower,
loan, payment, authentication, or other sensitive data. Use only the supplied typed,
read-only tools. Never invent a value, source, scope, comparison, mortgage calculation,
servicing decision, recommendation, or account action. Every number in your answer must
appear in a tool result from this invocation. Comparability comes only from the compare
tool. Explain missing and incomparable results plainly, cite observation/evidence IDs, and
retain source caveats."""

_MAX_OUTPUT_CHARS = 12_000
_MAX_MODEL_CALLS = 6
_MAX_TOOL_CALLS = 8
# LangGraph counts every middleware hook as a graph step. Ten privacy guards plus
# the two call-limit guards consume far more than one step per model turn; the
# model/tool counters below are the actual execution bounds.
_MAX_RECURSION = 192
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?(?:[$€£])?\d[\d,]*(?:\.\d+)?%?")


class AgentConfigurationError(RuntimeError):
    """Report that an agent cannot be constructed from safe settings."""


class ModelInvocationDisabledError(RuntimeError):
    """Report that the fail-closed model-call switch is disabled."""


class AgentProtocolError(RuntimeError):
    """Report a model result that does not contain a final AI message."""


@dataclass(frozen=True, slots=True)
class AgentInvocationResult:
    """Minimal result surface returned without exposing full graph state."""

    request_id: str
    text: str


class _AgentGraph(Protocol):
    """Narrow graph interface used by the application wrapper."""

    def invoke(
        self,
        state: DashboardAgentState,
        config: RunnableConfig | None = None,
        *,
        context: DashboardContext | None = None,
    ) -> dict[str, Any]:
        """Invoke a compiled graph with typed state and context."""


def _normalized_numbers(text: str) -> set[str]:
    return {token.replace(",", "").lstrip("$€£+") for token in _NUMBER_PATTERN.findall(text)}


def ensure_grounded_numeric_output(
    messages: list[object],
    text: str,
    *,
    max_chars: int = _MAX_OUTPUT_CHARS,
) -> None:
    """Reject oversized drafts or numbers absent from invocation tool results."""
    if len(text) > max_chars:
        msg = "Agent output exceeded the bounded result size"
        raise AgentProtocolError(msg)
    returned_numbers: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            returned_numbers.update(_normalized_numbers(str(message.content)))
    if not _normalized_numbers(text) <= returned_numbers:
        msg = "Agent output contained a number absent from read-tool results"
        raise AgentProtocolError(msg)


class DashboardAgent:
    """Expose approved invocation while keeping the raw graph private."""

    def __init__(
        self,
        *,
        graph: _AgentGraph,
        settings: AppSettings,
        prompt_boundary: PromptBoundary,
    ) -> None:
        """Initialize the guarded application agent.

        Args:
            graph: Compiled LangChain agent graph.
            settings: Validated application settings.
            prompt_boundary: Boundary used to approve model input.
        """
        self._graph = graph
        self._settings = settings
        self._prompt_boundary = prompt_boundary

    def approve_prompt(
        self,
        text: str,
        *,
        classification: DataClassification,
    ) -> ApprovedPrompt:
        """Screen de-identified text before model invocation.

        Args:
            text: Public or synthetic text already de-identified by the caller.
            classification: Explicit permitted classification.

        Returns:
            An approved prompt accepted by `invoke`.

        Raises:
            SensitiveContentError: If deterministic screening rejects the text.
        """
        return self._prompt_boundary.approve(text, classification=classification)

    def invoke(self, prompt: ApprovedPrompt) -> AgentInvocationResult:
        """Invoke the agent without persistence, tracing, or content logging.

        Args:
            prompt: Text approved by this application's prompt boundary.

        Returns:
            Correlation metadata and the final screened assistant text.

        Raises:
            ModelInvocationDisabledError: If live model calls are disabled.
            SensitiveContentError: If LangChain middleware detects sensitive content.
            AgentProtocolError: If the graph does not return a final AI message.
        """
        if not self._settings.enable_model_calls:
            msg = "Model invocation is disabled by MSD_ENABLE_MODEL_CALLS"
            raise ModelInvocationDisabledError(msg)
        assert_remote_tracing_disabled()

        request_id = uuid4().hex
        classification: Literal["public", "synthetic"] = (
            "synthetic" if prompt.classification is DataClassification.SYNTHETIC else "public"
        )
        state = DashboardAgentState(
            messages=[HumanMessage(content=prompt.text)],
            request_id=request_id,
            data_classification=classification,
            interaction_mode="foundation",
        )
        context = DashboardContext(
            request_id=request_id,
            data_classification=classification,
            interaction_mode="foundation",
        )
        config = RunnableConfig(
            recursion_limit=_MAX_RECURSION,
            tags=["msd-foundation"],
            metadata={
                "application": "mortgage-servicing-dashboard-foundation",
                "request_id": request_id,
                "data_classification": classification,
            },
            callbacks=[],
        )

        try:
            result = self._graph.invoke(state, config=config, context=context)
        except PIIDetectionError as error:
            raise SensitiveContentError(error.pii_type) from None

        messages = list(result.get("messages", []))
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                text = str(message.text)
                ensure_grounded_numeric_output(messages, text)
                return AgentInvocationResult(request_id=request_id, text=text)
        msg = "Agent completed without a final AI message"
        raise AgentProtocolError(msg)


def create_dashboard_agent(
    settings: AppSettings,
    *,
    model: str | BaseChatModel | None = None,
    information: FoundationInformationPort | None = None,
    repository: IntelligenceRepository | None = None,
    prompt_boundary: PromptBoundary | None = None,
) -> DashboardAgent:
    """Compile the provider-neutral foundation agent with safe defaults.

    Args:
        settings: Validated application settings.
        model: Optional injected chat model. Tests use a local fake model.
        information: Optional static foundation-information port.
        repository: Optional authoritative public-intelligence read repository.
        prompt_boundary: Optional input boundary for dependency injection.

    Returns:
        A guarded application agent around LangChain's compiled graph.

    Raises:
        AgentConfigurationError: If neither an injected nor configured model exists.
    """
    resolved_model = model or settings.model
    if resolved_model is None:
        msg = "A model must be configured or injected before creating the agent"
        raise AgentConfigurationError(msg)
    if repository is None:
        msg = "An authoritative read repository is required for the product agent"
        raise AgentConfigurationError(msg)

    middleware = cast(
        "tuple[AgentMiddleware[DashboardAgentState, DashboardContext, Any], ...]",
        (
            *build_privacy_middleware(),
            ModelCallLimitMiddleware(run_limit=_MAX_MODEL_CALLS, exit_behavior="error"),
            ToolCallLimitMiddleware(run_limit=_MAX_TOOL_CALLS, exit_behavior="error"),
        ),
    )
    del information
    tools = build_intelligence_tools(repository)
    graph = create_agent(
        model=resolved_model,
        tools=list(tools),
        system_prompt=_SYSTEM_PROMPT,
        middleware=middleware,
        state_schema=DashboardAgentState,
        context_schema=DashboardContext,
        checkpointer=None,
        store=None,
        debug=False,
        name="public_mortgage_servicing_intelligence",
    )
    active_boundary = prompt_boundary or PromptBoundary(max_chars=settings.max_prompt_chars)
    return DashboardAgent(
        graph=cast("_AgentGraph", graph),
        settings=settings,
        prompt_boundary=active_boundary,
    )
