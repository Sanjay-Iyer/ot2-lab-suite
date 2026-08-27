"""A scripted chat model for testing the agent graph without an LLM provider.

Tool-calling behaviour is the part of the agent worth testing deterministically:
whether the graph routes to the right node, whether approvals gate correctly,
and whether an edit invalidates a simulation.  None of that should depend on a
network call or a model's mood.
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed list of assistant turns, one per invocation.

    Each scripted turn is either a plain string (a final reply) or a list of
    ``{"name": ..., "args": {...}}`` tool calls.
    """

    script: list[Any] = []
    calls_made: list[Any] = []

    def __init__(self, script: Sequence[Any], **kwargs: Any) -> None:
        super().__init__(script=list(script), calls_made=[], **kwargs)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self.calls_made.append([getattr(item, "name", str(item)) for item in tools])
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any
    ) -> ChatResult:
        if not self.script:
            message = AIMessage(content="(script exhausted)")
        else:
            turn = self.script.pop(0)
            if isinstance(turn, str):
                message = AIMessage(content=turn)
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": call["name"],
                            "args": call.get("args", {}),
                            "id": f"call_{index}_{call['name']}",
                        }
                        for index, call in enumerate(turn)
                    ],
                )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:  # pragma: no cover
        raise NotImplementedError
