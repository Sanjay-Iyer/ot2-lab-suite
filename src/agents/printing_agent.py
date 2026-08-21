"""Bounded agent for selecting validated paper-printing capabilities.

The agent interprets scientific intent. Skills contain procedural guidance; tools,
schemas, registries, and validators own deterministic laboratory behavior. Its tool
surface intentionally stops at local simulation.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from src.agents.printing_tools import (
    PRINT_EXPERIMENT_TOOLS,
    PRINT_JOB_TOOLS,
    PRINTING_EXPERIMENT_TOOLS,
    STANDARD_PRINT_EXPERIMENT_TOOLS,
)
from src.core.config import Config
from src.printing.schemas import PrintingFamily
from src.printing.skills import (
    load_printing_skill_content,
    printing_skill_index,
    select_printing_experiment_skills,
    select_printing_skills,
    select_standard_experiment_skills,
)


class PrintingExecutionMode(str, Enum):
    INSPECT = "inspect"
    CONSTRUCT = "construct"
    MODIFY = "modify"
    REPORT = "report"
    APPROVE = "approve"
    REJECT = "reject"


class PrintingAgentPlan(BaseModel):
    """Auditable capability routing before a model invokes a tool."""

    model_config = ConfigDict(extra="forbid")

    user_intent: str
    family: PrintingFamily | None = None
    design_name: str | None = None
    skill_names: list[str] = Field(default_factory=list)
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution_mode: PrintingExecutionMode
    validation_outcome: Literal["not_run"] = "not_run"
    needs_clarification: list[str] = Field(default_factory=list)


class LoadPrintingSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1)
    reference: str | None = None


@tool(args_schema=LoadPrintingSkillInput)
def load_printing_skill(skill_name: str, reference: str | None = None) -> str:
    """Load one selected printing procedure or its declared local reference."""
    return load_printing_skill_content(skill_name, reference)


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def plan_printing_intent(user_intent: str) -> PrintingAgentPlan:
    """Route family/skill/tool without performing scientific interpretation."""
    text = user_intent.lower()
    normalized = _normalized(user_intent)
    design_name = "four_clover" if "clover" in text else None
    if design_name is not None:
        family = PrintingFamily.DESIGN
    elif re.search(r"\b[a-h]\s*(?:[1-9]|1[0-2])\b", text) or any(
        token in text
        for token in (
            "standard", "row", "rows", "cols", "columns", "well",
            "drop", "drops", "triplicate", "nanoparticle",
        )
    ):
        family = PrintingFamily.STANDARD
    else:
        family = None

    if any(
        phrase in text
        for phrase in (
            "what can",
            "capabilities",
            "available printing",
            "list printing",
        )
    ):
        return PrintingAgentPlan(
            user_intent=user_intent,
            tool_name="list_printing_capabilities",
            execution_mode=PrintingExecutionMode.INSPECT,
        )

    clarification: list[str] = []
    if any(phrase in text for phrase in ("don't run", "do not run", "reject")):
        tool_name = "reject_printing_experiment"
        mode = PrintingExecutionMode.REJECT
    elif any(phrase in text for phrase in ("approve", "approved", "yes, run", "yes run")):
        tool_name = "approve_printing_experiment"
        mode = PrintingExecutionMode.APPROVE
    elif "ring" in normalized:
        clarification.append(
            "Ring geometry is outside the two registered V1 printing families."
        )
        tool_name = "report_printing_request_issue"
        mode = PrintingExecutionMode.REPORT
    elif family == PrintingFamily.DESIGN and re.search(r"\bsome\s+clovers?\b", text):
        clarification.extend(
            [
                "Specify droplet volume.",
                "Specify the number of clover replicates or explicit centers.",
            ]
        )
        tool_name = "report_printing_request_issue"
        mode = PrintingExecutionMode.REPORT
    elif any(
        phrase in text
        for phrase in (
            "same experiment",
            "same clover",
            "change",
            "instead",
            "keep everything",
        )
    ):
        tool_name = "revise_printing_experiment"
        mode = PrintingExecutionMode.MODIFY
    elif family is not None:
        tool_name = "draft_printing_experiment"
        mode = PrintingExecutionMode.CONSTRUCT
    else:
        tool_name = "list_printing_capabilities"
        mode = PrintingExecutionMode.INSPECT
        clarification.append(
            "Identify a well-selection or four-clover printing intent."
        )

    skills = (
        list(select_printing_skills(family, design_name=design_name))
        if family is not None
        else []
    )
    return PrintingAgentPlan(
        user_intent=user_intent,
        family=family,
        design_name=design_name,
        skill_names=skills,
        tool_name=tool_name,
        parameters={},
        execution_mode=mode,
        needs_clarification=clarification,
    )


PRINTING_AGENT_PROMPT = f"""You are the bounded OT-2 Printing Agent.

Interpret scientific printing intent and submit it as a persistent experiment YAML.
Runtime skills provide family-specific procedure. Python serializes and validates
the YAML through PrintJobV1; the model never writes raw YAML or robot code.

Runtime skill index (name and description only):
{printing_skill_index()}

Rules:
1. Load the selected runtime skill before constructing or modifying a job.
2. Use draft_printing_experiment for a new experiment and revise_printing_experiment
   for a requested change. Every revision creates a child YAML version.
3. Supply scientific intent only. Never calculate coordinates, deck positions,
   source wells, air handling, piston volumes, pipette settings, or plan internals.
4. Never invent config hashes, job_id, plan_id, labware hashes, namespace, or version. Registered
   references and deterministic code populate them.
5. Use report_printing_request_issue for missing scientific information or an
   unsupported pattern. Do not invent ring, line, or arbitrary-point printing.
6. Tool validation and compilation results are authoritative. Clearly distinguish
   interpretation, schema, reference, compiler, and physical-validation failures.
7. Present config_summary and the YAML path from the tool result. A new or revised
   config must stop in AWAITING_APPROVAL.
8. Call approve_printing_experiment only after an explicit user approval that refers
   to the displayed config. Then call prepare_approved_printing_experiment to resolve,
   build, and locally simulate that exact sealed SHA.
9. Rejection ends in PLAN_REJECTED. Never resolve, build, or simulate a rejected or
   merely displayed config.
10. This surface cannot deploy or execute live OT-2 motion. Its terminal state is
    READY_FOR_EXECUTION after local simulation.
11. The older create_and_compile_print_job and modify_and_compile_print_job tools are
    compatibility-only. Do not use them for a new conversational experiment; they
    do not create the mandatory YAML approval artifact.
"""


PRINTING_AGENT_TOOLS = [
    *PRINT_EXPERIMENT_TOOLS,
    load_printing_skill,
]
LEGACY_PRINTING_AGENT_TOOLS = [
    *PRINTING_AGENT_TOOLS,
    *[
        item
        for item in PRINT_JOB_TOOLS
        if item.name in {"create_and_compile_print_job", "modify_and_compile_print_job"}
    ],
]

STANDARD_EXPERIMENT_AGENT_TOOLS = [
    *STANDARD_PRINT_EXPERIMENT_TOOLS,
]

STANDARD_EXPERIMENT_AGENT_PROMPT = """You are the Standard Printing Experiment Agent.
Interpret scientific intent and produce explicit print-experiment-job/v1 configuration.
Use the preloaded standard-printing-experiment skill, then use only high-level
generalized tools. The scientist owns liquids, preparation, layout, controls, repeats, mixing,
and delays. Registered profiles and deterministic code own hardware and motions.
Never write OT-2 Python, action lists, calibration, or live-run commands. Present the
validated YAML, hashes, and scientist review, then stop for external user approval.
"""


def standard_experiment_agent_prompt(state: dict[str, Any]) -> list[Any]:
    messages = list(state.get("messages", []))
    selected = select_standard_experiment_skills()
    skill_context = "\n\n".join(
        f"## {name}\n{load_printing_skill_content(name)}" for name in selected
    )
    return [
        SystemMessage(
            content=f"{STANDARD_EXPERIMENT_AGENT_PROMPT}\n\n{skill_context}"
        ),
        *messages,
    ]


def _message_text(message: BaseMessage | tuple[str, str] | Any) -> str:
    if isinstance(message, tuple) and len(message) >= 2:
        return str(message[1])
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def printing_agent_prompt(state: dict[str, Any]) -> list[Any]:
    """Build the actual model context with deterministic, scoped skill loading."""
    messages = list(state.get("messages", []))
    user_intent = next(
        (
            _message_text(message)
            for message in reversed(messages)
            if getattr(message, "type", None) in {"human", "user"}
            or (
                isinstance(message, tuple)
                and message
                and message[0] in {"human", "user"}
            )
        ),
        "",
    )
    if not user_intent:
        return [SystemMessage(content=PRINTING_AGENT_PROMPT), *messages]
    plan = plan_printing_intent(user_intent)
    loaded = [
        f"## {name}\n{load_printing_skill_content(name)}" for name in plan.skill_names
    ]
    routing = plan.model_dump_json(indent=2)
    selected_context = (
        "\n\n".join(loaded) if loaded else "(No printing skill selected yet.)"
    )
    dynamic = (
        f"{PRINTING_AGENT_PROMPT}\n\n"
        "Deterministic intent routing for this request:\n"
        f"{routing}\n\n"
        "Selected runtime skill content (loaded from SKILL.md before tool choice):\n"
        f"{selected_context}"
    )
    return [SystemMessage(content=dynamic), *messages]


def create_printing_agent(
    model: Any | None = None, *, include_legacy_compatibility: bool = False
):
    """Create the LLM-backed Printing Agent with a simulation-only tool surface."""
    from langgraph.prebuilt import create_react_agent

    llm = model or Config.get_llm(temperature=0)
    return create_react_agent(
        model=llm,
        tools=(
            LEGACY_PRINTING_AGENT_TOOLS
            if include_legacy_compatibility
            else PRINTING_AGENT_TOOLS
        ),
        prompt=printing_agent_prompt,
    )


def create_standard_experiment_agent(model: Any | None = None):
    """Create the generalized configuration agent; no legacy or approval bypasses."""
    from langgraph.prebuilt import create_react_agent

    llm = model or Config.get_llm(temperature=0)
    return create_react_agent(
        model=llm,
        tools=STANDARD_EXPERIMENT_AGENT_TOOLS,
        prompt=standard_experiment_agent_prompt,
    )


# --------------------------------------------------------------------------- #
# One Printing Agent, two workflow families
#
# The request selects the family; the family selects the template, the skill, the
# tools, and the deterministic executor. There is no second agent, and the model
# never writes YAML text or OT-2 Python in either branch.
# --------------------------------------------------------------------------- #

#: Words that only ever mean the four-droplet clover pattern.
_CLOVER_TERMS = (
    "clover",
    "cloverleaf",
    "four droplets around",
    "four-droplet pattern",
    "coffee ring overlap",
    "coffee-ring overlap",
    "ring overlap",
)


class PrintingWorkflowFamily(str, Enum):
    """Which generalized printing workflow a request belongs to."""

    STANDARD = "standard"
    FOUR_CLOVER = "four_clover"


def select_printing_workflow_family(user_intent: str) -> PrintingWorkflowFamily:
    """Deterministic family routing, done before the model chooses any tool.

    Clover wording is decisive because the clover executor is the only one that
    prints a pattern around a centre point. Everything else -- wells, columns,
    dilution series, replicates, controls -- is standard printing, which is also
    the safe default: the standard schema will reject a request it cannot express
    rather than silently approximate it.
    """
    text = user_intent.lower()
    if any(term in text for term in _CLOVER_TERMS):
        return PrintingWorkflowFamily.FOUR_CLOVER
    return PrintingWorkflowFamily.STANDARD


PRINTING_EXPERIMENT_AGENT_PROMPT = """You are the Printing Experiment Agent.

You convert a scientist's natural-language request into a validated experiment
CONFIGURATION. You never write OT-2 Python, never write raw YAML text, never
compute coordinates or volumes the tools can compute, and never invent hardware
values. Deterministic code renders and validates the configuration; a frozen
executor performs the physical run.

Two workflow families exist. The request selects one:

  standard      Printing onto positions of the 96-position paper grid, with
                optional transfers, serial or direct dilution, mixing, repeated
                deposition, delays, replicates, and controls. Multiple liquids.
                Tools: list_standard_printing_experiment_capabilities,
                validate_standard_printing_experiment,
                resolve_standard_printing_experiment,
                inspect_standard_printing_layout,
                create_standard_printing_experiment_config.

  four_clover   Printing groups of four droplets around a centre point so their
                dried rings overlap. Exactly one liquid, no dilution, no mixing.
                Tools: list_four_clover_experiment_capabilities,
                validate_four_clover_experiment,
                preview_four_clover_experiment,
                create_four_clover_experiment_config,
                simulate_four_clover_experiment.

Never mix the two tool sets in one experiment. If a request needs dilution AND a
clover pattern, that is not currently supported: say so with
report_printing_request_issue rather than approximating it.

Procedure, in order:
1. Call the capability tool for the selected family before constructing anything.
2. Build the configuration from the loaded skill and the request alone. Record any
   value you had to decide rather than read in metadata.notes.
3. Validate. Then preview or inspect the layout, and present the tool's own
   review to the scientist. Never substitute a summary you wrote yourself.
4. Persist with the family's create_*_config tool. Generated configurations are
   written under configs/generated/ and never over a hand-validated ground truth
   in configs/experiments/.
5. Stop for the scientist's decision. Standard simulation requires an externally
   sealed approval. Clover simulation is local and read-only and reaches no
   execution-ready state.

Tool validation results are authoritative. If a tool rejects the configuration,
fix the science; never work around it by changing a machine profile, a resolver,
or an executor. This surface cannot deploy or execute live OT-2 motion.
"""


PRINTING_EXPERIMENT_AGENT_TOOLS = [*PRINTING_EXPERIMENT_TOOLS]


def printing_experiment_agent_prompt(state: dict[str, Any]) -> list[Any]:
    """Route the family deterministically, then load only that family's skill."""
    messages = list(state.get("messages", []))
    user_intent = next(
        (
            _message_text(message)
            for message in reversed(messages)
            if getattr(message, "type", None) in {"human", "user"}
            or (
                isinstance(message, tuple)
                and message
                and message[0] in {"human", "user"}
            )
        ),
        "",
    )
    family = select_printing_workflow_family(user_intent)
    skill_names = select_printing_experiment_skills(family.value)
    skill_context = "\n\n".join(
        f"## {name}\n{load_printing_skill_content(name)}" for name in skill_names
    )
    routing = (
        "Deterministic workflow routing for this request:\n"
        f"  family : {family.value}\n"
        f"  skill  : {', '.join(skill_names)}\n"
    )
    return [
        SystemMessage(
            content=(
                f"{PRINTING_EXPERIMENT_AGENT_PROMPT}\n\n{routing}\n\n{skill_context}"
            )
        ),
        *messages,
    ]


def create_printing_experiment_agent(model: Any | None = None):
    """One Printing Agent covering both generalized workflow families."""
    from langgraph.prebuilt import create_react_agent

    llm = model or Config.get_llm(temperature=0)
    return create_react_agent(
        model=llm,
        tools=PRINTING_EXPERIMENT_AGENT_TOOLS,
        prompt=printing_experiment_agent_prompt,
    )
