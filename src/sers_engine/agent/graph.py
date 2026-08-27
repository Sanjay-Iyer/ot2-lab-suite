"""LangGraph orchestration for the SERS experiment agent.

One conversational orchestrator, modular deterministic tools underneath it.
There is no swarm: dilution, printing, and robot control are capabilities, not
agents, because they are deterministic Python and nothing about them benefits
from a second language model in the loop.

The graph deliberately splits tools in two so that anything that can move the
robot passes through a node the graph interrupts before.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from ..agent_tools import CONFIG_TOOLS, ROBOT_TOOLS
from ..provenance import active_session, tool_schema_provenance
from ..state import REGISTRY
from .prompts import SYSTEM_PROMPT
from .state import AgentState, blank_state

# Tools that can start physical motion or authorize it. The graph stops before
# running any of these so a human sees the exact call first.
GATED_TOOL_NAMES = {"approve_live_execution", "execute_experiment"}


def _session_mirror() -> dict[str, Any]:
    """Reflect the deterministic session into graph state."""
    try:
        session = REGISTRY.get()
    except Exception:
        return blank_state()
    return session.snapshot().model_dump(mode="json")


def build_agent_graph(
    llm: Any,
    tools: Iterable[Any] | None = None,
    checkpointer: Any | None = None,
    interrupt_before_robot: bool = True,
) -> Any:
    """Compile the agent graph around a tool-calling chat model."""
    config_tools = list(CONFIG_TOOLS)
    robot_tools = list(ROBOT_TOOLS)
    if tools is not None:
        chosen = list(tools)
        names = {getattr(item, "name", None) for item in chosen}
        config_tools = [item for item in chosen if item.name not in GATED_TOOL_NAMES]
        robot_tools = [item for item in chosen if item.name in GATED_TOOL_NAMES]
        del names
    every_tool = config_tools + robot_tools
    model = llm.bind_tools(every_tool)

    def agent_node(state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = model.invoke(messages)
        return {"messages": [response], **_session_mirror()}

    def sync_node(state: AgentState) -> dict[str, Any]:
        return _session_mirror()

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls:
            return END
        if any(call["name"] in GATED_TOOL_NAMES for call in calls):
            return "robot_tools"
        return "config_tools"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("config_tools", ToolNode(config_tools))
    graph.add_node("robot_tools", ToolNode(robot_tools))
    graph.add_node("sync", sync_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", route, {"config_tools": "config_tools", "robot_tools": "robot_tools", END: END}
    )
    graph.add_edge("config_tools", "sync")
    graph.add_edge("robot_tools", "sync")
    graph.add_edge("sync", "agent")

    return graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=["robot_tools"] if interrupt_before_robot else None,
    )


class SERSExperimentAgent:
    """A single conversational orchestrator over the deterministic engine."""

    def __init__(
        self,
        llm: Any,
        thread_id: str = "sers-session",
        allow_robot_tools: bool = True,
        interrupt_before_robot: bool = True,
        provenance: Any | None = None,
    ) -> None:
        tools = list(CONFIG_TOOLS) + (list(ROBOT_TOOLS) if allow_robot_tools else [])
        self.graph = build_agent_graph(
            llm, tools=tools, interrupt_before_robot=interrupt_before_robot
        )
        self.config = {"configurable": {"thread_id": thread_id}}
        self.allow_robot_tools = allow_robot_tools
        # The checkpointer is runtime state that dies with the process. The
        # provenance recorder is the permanent record, so it is told what the
        # model is and exactly which tool contract it was given.
        self.provenance = provenance if provenance is not None else active_session()
        if self.provenance is not None:
            self.provenance.thread_id = thread_id
            self.provenance.describe_model(llm)
            self.provenance.describe(thread_id=thread_id, **tool_schema_provenance(tools))

    # ---- conversation ------------------------------------------------------
    def send(self, message: str) -> dict[str, Any]:
        """Send one user turn and run until the graph stops or interrupts."""
        # The turn is given an id and recorded before the model is called, so a
        # provider failure cannot lose what the researcher asked for.
        turn = HumanMessage(content=message, id=f"user-{uuid.uuid4().hex[:12]}")
        if self.provenance is not None:
            self.provenance.log_message("user", message, message_id=turn.id)
        return self._run({"messages": [turn], **_session_mirror()})

    def resume(self) -> dict[str, Any]:
        """Continue past a robot-tool interrupt after the human approved it."""
        return self._run(None)

    def refuse_pending_tool(self, reason: str = "the operator declined") -> dict[str, Any]:
        """Answer a pending robot tool call with a refusal instead of running it."""
        snapshot = self.graph.get_state(self.config)
        pending = snapshot.values["messages"][-1]
        self._record_refusal(pending, reason)
        refusals = [
            ToolMessage(
                content=f"REFUSED: {reason}. The robot was not touched.",
                tool_call_id=call["id"],
                name=call["name"],
            )
            for call in getattr(pending, "tool_calls", []) or []
        ]
        if not refusals:
            return {"messages": [], "interrupted": False}
        self.graph.update_state(self.config, {"messages": refusals}, as_node="robot_tools")
        return self._run(None)

    def _record_refusal(self, pending: Any, reason: str) -> None:
        """A refused robot call is part of the record, not an absence of one."""
        if self.provenance is None:
            return
        calls = [
            {"name": call["name"], "args": call["args"]}
            for call in getattr(pending, "tool_calls", []) or []
        ]
        if not calls:
            return
        try:
            session = REGISTRY.get()
        except Exception:
            session = None
        self.provenance.record_live_refusal(session, reason, calls)

    def _record_turn(self, snapshot: Any, interrupted: bool) -> None:
        """Persist every message the graph produced, plus any pending approval."""
        if self.provenance is None:
            return
        messages = snapshot.values.get("messages", [])
        self.provenance.record_messages(messages)
        if interrupted:
            try:
                session = REGISTRY.get()
            except Exception:
                session = None
            if session is not None:
                self.provenance.record_live_approval_requested(
                    session, self.pending_tools(snapshot)
                )
        self.provenance.write_transcript()

    def _run(self, payload: Any) -> dict[str, Any]:
        self.graph.invoke(payload, self.config)
        snapshot = self.graph.get_state(self.config)
        interrupted = bool(snapshot.next)
        self._record_turn(snapshot, interrupted)
        return {
            "reply": self.last_reply(snapshot.values["messages"]),
            "interrupted": interrupted,
            "pending_tools": self.pending_tools(snapshot) if interrupted else [],
            "state": {
                key: value for key, value in snapshot.values.items() if key != "messages"
            },
        }

    # ---- inspection --------------------------------------------------------
    @staticmethod
    def last_reply(messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.content:
                content = message.content
                if isinstance(content, list):
                    return "".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    ).strip()
                return str(content).strip()
        return ""

    @staticmethod
    def pending_tools(snapshot: Any) -> list[dict[str, Any]]:
        last = snapshot.values["messages"][-1]
        return [
            {"name": call["name"], "args": call["args"]}
            for call in getattr(last, "tool_calls", []) or []
        ]

    def tool_transcript(self) -> list[dict[str, Any]]:
        """Every tool result so far, for showing plans verbatim in a CLI."""
        snapshot = self.graph.get_state(self.config)
        return [
            {"name": message.name, "content": message.content}
            for message in snapshot.values.get("messages", [])
            if isinstance(message, ToolMessage)
        ]


def make_default_agent(
    thread_id: str = "sers-session",
    allow_robot_tools: bool = True,
    llm_factory: Callable[[], Any] | None = None,
    provenance: Any | None = None,
) -> SERSExperimentAgent:
    """Build the agent on the repository's configured LLM auth path."""
    if llm_factory is None:
        from src.core.config import Config

        llm_factory = lambda: Config.get_llm(temperature=0)  # noqa: E731
    return SERSExperimentAgent(
        llm_factory(),
        thread_id=thread_id,
        allow_robot_tools=allow_robot_tools,
        provenance=provenance,
    )
