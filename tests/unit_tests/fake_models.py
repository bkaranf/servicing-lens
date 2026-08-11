"""No-network chat-model test doubles."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field


def _tool_name(tool: dict[str, Any] | type | Callable[..., Any] | BaseTool) -> str:
    """Extract a stable name from a tool definition."""
    if isinstance(tool, BaseTool):
        return tool.name
    if isinstance(tool, dict):
        name = tool.get("name")
        if isinstance(name, str):
            return name
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return str(function["name"])
        return "unknown-dict-tool"
    return getattr(tool, "__name__", "unknown-callable-tool")


class RecordingToolModel(BaseChatModel):
    """Return scripted messages and record the final model-visible tools."""

    model_name: str = "foundation"
    provider_name: str = "test"
    responses: list[AIMessage]
    response_index: int = 0
    bound_tool_names: list[tuple[str, ...]] = Field(default_factory=list)
    received_messages: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return self.provider_name

    def _get_ls_params(
        self,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LangSmithParams:
        del stop, kwargs
        return {
            "ls_provider": self.provider_name,
            "ls_model_name": self.model_name,
            "ls_model_type": "chat",
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.received_messages.append(messages)
        if self.response_index >= len(self.responses):
            msg = "Scripted fake model has no response remaining"
            raise RuntimeError(msg)
        response = self.responses[self.response_index]
        self.response_index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tool_choice, kwargs
        self.bound_tool_names.append(tuple(sorted(_tool_name(tool) for tool in tools)))
        return self
