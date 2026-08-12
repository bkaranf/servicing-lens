"""Restricted Deep Agents boundary for future research and analysis drafts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import PIIDetectionError
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from mortgage_servicing_dashboard.agent import (
    AgentConfigurationError,
    AgentProtocolError,
    ModelInvocationDisabledError,
)
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
from mortgage_servicing_dashboard.state import DashboardContext, ResearchWorkerState
from mortgage_servicing_dashboard.tools import (
    FoundationInformationPort,
    build_foundation_tools,
    build_intelligence_tools,
)

_SYSTEM_PROMPT = """You are a restricted research and analysis worker for a future mortgage
servicing dashboard. Work only with public or synthetic, de-identified material that has
already crossed the application privacy boundary. Produce drafts for human review only.
Do not access files, networks, customer systems, accounts, or credentials. Do not delegate
work. Never perform or invent mortgage calculations, servicing decisions, recommendations,
approvals, or operational actions. State plainly when evidence or capability is unavailable."""

_DEEP_AGENT_BUILTIN_TOOLS = frozenset(
    {
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
)


class UnsafeResearchToolError(RuntimeError):
    """Report an attempted tool call outside the read-only worker allowlist."""


class DeepAgentInvocationDisabledError(RuntimeError):
    """Report that the independent Deep Agents kill switch is disabled."""


def _tool_name(tool: object) -> str | None:
    """Extract a model-visible tool name without serializing its arguments."""
    if isinstance(tool, dict):
        name = tool.get("name")
        if isinstance(name, str):
            return name
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return cast("str", function["name"])
        return None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class _ResearchToolBoundary(AgentMiddleware[AgentState[Any], DashboardContext, Any]):
    """Filter model tools and block execution outside a static allowlist."""

    def __init__(self, *, allowed_names: frozenset[str]) -> None:
        self._allowed_names = allowed_names

    def _filter_request(
        self,
        request: ModelRequest[DashboardContext],
    ) -> ModelRequest[DashboardContext]:
        filtered_tools = [tool for tool in request.tools if _tool_name(tool) in self._allowed_names]
        return request.override(tools=filtered_tools)

    def wrap_model_call(
        self,
        request: ModelRequest[DashboardContext],
        handler: Callable[[ModelRequest[DashboardContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Expose only the explicitly approved tools to the model."""
        return handler(self._filter_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[DashboardContext],
        handler: Callable[[ModelRequest[DashboardContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Expose only the explicitly approved tools to an async model call."""
        return await handler(self._filter_request(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Block fabricated or stale calls to non-allow-listed tools."""
        if request.tool_call.get("name") not in self._allowed_names:
            msg = "Research worker tool execution blocked by the application boundary"
            raise UnsafeResearchToolError(msg)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Block non-allow-listed tools in asynchronous execution."""
        if request.tool_call.get("name") not in self._allowed_names:
            msg = "Research worker tool execution blocked by the application boundary"
            raise UnsafeResearchToolError(msg)
        return await handler(request)


class _ResearchGraph(Protocol):
    """Narrow Deep Agents graph surface used by the worker wrapper."""

    def invoke(
        self,
        state: ResearchWorkerState,
        config: RunnableConfig | None = None,
        *,
        context: DashboardContext | None = None,
    ) -> dict[str, Any]:
        """Invoke the compiled research graph."""


@dataclass(frozen=True, slots=True)
class ResearchDraft:
    """Model-authored draft that can never represent an operational decision."""

    request_id: str
    text: str
    requires_human_review: Literal[True] = True


class ResearchAnalysisWorker:
    """Expose a privacy-screened draft interface around a restricted deep agent."""

    def __init__(
        self,
        *,
        graph: _ResearchGraph,
        settings: AppSettings,
        prompt_boundary: PromptBoundary,
    ) -> None:
        """Initialize the restricted worker.

        Args:
            graph: Compiled Deep Agents graph.
            settings: Validated application settings.
            prompt_boundary: Boundary for public or synthetic input.
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
        """Screen de-identified research text.

        Args:
            text: Public or synthetic research request.
            classification: Explicit permitted classification.

        Returns:
            An approved prompt accepted by `analyze`.
        """
        return self._prompt_boundary.approve(text, classification=classification)

    def analyze(self, prompt: ApprovedPrompt) -> ResearchDraft:
        """Produce a non-operational draft that requires human review.

        Args:
            prompt: Text approved by the application privacy boundary.

        Returns:
            A draft explicitly marked for human review.

        Raises:
            DeepAgentInvocationDisabledError: If Deep Agents execution is disabled.
            ModelInvocationDisabledError: If live model calls are disabled.
            SensitiveContentError: If middleware detects sensitive content.
            UnsafeResearchToolError: If a non-allow-listed tool call is attempted.
            AgentProtocolError: If no final AI message is returned.
        """
        if not self._settings.enable_deep_agent:
            msg = "Deep Agents execution is disabled by MSD_ENABLE_DEEP_AGENT"
            raise DeepAgentInvocationDisabledError(msg)
        if not self._settings.enable_model_calls:
            msg = "Model invocation is disabled by MSD_ENABLE_MODEL_CALLS"
            raise ModelInvocationDisabledError(msg)
        assert_remote_tracing_disabled()

        request_id = uuid4().hex
        classification: Literal["public", "synthetic"] = (
            "synthetic" if prompt.classification is DataClassification.SYNTHETIC else "public"
        )
        state = ResearchWorkerState(
            messages=[HumanMessage(content=prompt.text)],
            request_id=request_id,
            data_classification=classification,
            interaction_mode="research_draft",
        )
        context = DashboardContext(
            request_id=request_id,
            data_classification=classification,
            interaction_mode="foundation",
        )
        config = RunnableConfig(
            tags=["msd-foundation", "deep-agents-research-worker"],
            metadata={
                "application": "mortgage-servicing-dashboard-foundation",
                "request_id": request_id,
                "data_classification": classification,
                "output_status": "draft_requires_human_review",
            },
            callbacks=[],
        )
        try:
            result = self._graph.invoke(state, config=config, context=context)
        except PIIDetectionError as error:
            raise SensitiveContentError(error.pii_type) from None

        for message in reversed(result.get("messages", [])):
            if isinstance(message, AIMessage):
                return ResearchDraft(request_id=request_id, text=str(message.text))
        msg = "Research worker completed without a final AI message"
        raise AgentProtocolError(msg)


def _assert_profile_key_matches(model: str | BaseChatModel, profile_key: str) -> None:
    """Fail closed if an injected model cannot activate the registered profile."""
    if isinstance(model, str):
        if model != profile_key:
            msg = "Deep Agents profile key must exactly match the model identifier"
            raise AgentConfigurationError(msg)
        return

    model_identifier = getattr(model, "model_name", None) or getattr(model, "model", None)
    try:
        provider = model._get_ls_params().get("ls_provider")  # noqa: SLF001
    except (AttributeError, TypeError, NotImplementedError):
        provider = None
    expected_key = (
        f"{provider}:{model_identifier}"
        if isinstance(provider, str) and isinstance(model_identifier, str)
        else None
    )
    if expected_key != profile_key:
        msg = "Injected model cannot be matched safely to the Deep Agents harness profile"
        raise AgentConfigurationError(msg)


def create_research_worker(  # noqa: PLR0913
    settings: AppSettings,
    *,
    model: str | BaseChatModel | None = None,
    profile_key: str | None = None,
    information: FoundationInformationPort | None = None,
    repository: IntelligenceRepository | None = None,
    prompt_boundary: PromptBoundary | None = None,
) -> ResearchAnalysisWorker:
    """Construct a Deep Agents worker with delegation and filesystem access disabled.

    Args:
        settings: Validated application settings.
        model: Optional injected chat model for tests or controlled deployments.
        profile_key: Exact `provider:model` key for an injected model.
        information: Optional static foundation-information port.
        repository: Optional authoritative public-intelligence read repository.
        prompt_boundary: Optional input boundary for dependency injection.

    Returns:
        A restricted research-draft worker.

    Raises:
        AgentConfigurationError: If model or profile selection cannot fail closed.
    """
    resolved_model = model or settings.model
    if resolved_model is None:
        msg = "A model must be configured or injected before creating the research worker"
        raise AgentConfigurationError(msg)
    resolved_profile_key = profile_key or settings.model
    if resolved_profile_key is None:
        msg = "An exact provider:model profile key is required for Deep Agents"
        raise AgentConfigurationError(msg)
    _assert_profile_key_matches(resolved_model, resolved_profile_key)

    register_harness_profile(
        resolved_profile_key,
        HarnessProfile(
            excluded_tools=_DEEP_AGENT_BUILTIN_TOOLS,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )

    tools = list(
        build_intelligence_tools(repository)
        if repository is not None
        else build_foundation_tools(information)
    )
    allowed_tool_names = frozenset(tool.name for tool in tools)
    privacy_middleware = cast(
        "tuple[AgentMiddleware[AgentState[Any], DashboardContext, Any], ...]",
        build_privacy_middleware(),
    )
    middleware: list[AgentMiddleware[AgentState[Any], DashboardContext, Any]] = [
        *privacy_middleware,
        _ResearchToolBoundary(allowed_names=allowed_tool_names),
    ]
    graph = create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=_SYSTEM_PROMPT,
        middleware=middleware,
        subagents=[],
        skills=None,
        memory=None,
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            )
        ],
        interrupt_on=None,
        state_schema=ResearchWorkerState,
        context_schema=DashboardContext,
        checkpointer=None,
        store=None,
        debug=False,
        name="mortgage_servicing_research_worker",
    )
    active_boundary = prompt_boundary or PromptBoundary(max_chars=settings.max_prompt_chars)
    return ResearchAnalysisWorker(
        graph=cast("_ResearchGraph", graph),
        settings=settings,
        prompt_boundary=active_boundary,
    )
