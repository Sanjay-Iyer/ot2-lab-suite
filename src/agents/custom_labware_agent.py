"""
src/agents/custom_labware_agent.py
==================================
Conversational AI driver for **custom Opentrons labware definitions**.

Describe a physical part in plain English; the agent turns that into validated
structured parameters and hands them to the deterministic generator in
``src/labware/``, which computes every coordinate and validates the result.

    "Make another 96-position paper labware called paper_test_01"
    "Same as paper_print_96_flat but 8.5 mm X spacing, call it paper_wide"
    "What custom labware families can you create?"

Run (from the repo root; `conda activate ai` on the simulation laptop):

    python -m src.agents.custom_labware_agent
    python -m src.agents.custom_labware_agent "make the paper plate with 8 mm spacing"

Flags:
    --rate-limit    Enable the 60 s rolling-window guard (Gemini free tier).

Scope: this agent creates labware DEFINITIONS only. It has no protocol,
deck, or robot tools and cannot move hardware. Generation is safe on either
laptop — it needs no robot connection.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from src.utils.paths import AGENT_LOG_DIR, ensure_project_dirs

# Run as a module from the repo root.
if __name__ == "__main__" and not __package__:
    print("ERROR: run as a module from the project root:")
    print("       python -m src.agents.custom_labware_agent")
    sys.exit(1)

ensure_project_dirs()

from src.agents.labware_tools import (
    generate_registered_labware,
    list_labware_families,
    list_registered_labware_templates,
    load_registered_labware_template,
    validate_labware_definition,
)
from src.labware.families import match_agent_family
from src.labware.skills import (
    labware_skill_index,
    load_labware_skill_content,
    select_labware_skills,
)

LABWARE_TOOLS = [
    list_labware_families,
    list_registered_labware_templates,
    load_registered_labware_template,
    generate_registered_labware,
    validate_labware_definition,
]


class LoadLabwareSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1)
    reference: str | None = None


@tool(args_schema=LoadLabwareSkillInput)
def load_labware_skill(skill_name: str, reference: str | None = None) -> str:
    """Load one selected Labware Specialist procedure at runtime."""
    return load_labware_skill_content(skill_name, reference)


LABWARE_TOOLS.append(load_labware_skill)


class LabwareAgentPlan(BaseModel):
    """Auditable deterministic routing context supplied before model reasoning."""

    model_config = ConfigDict(extra="forbid")

    user_intent: str
    family: str | None = None
    skill_names: list[str] = Field(default_factory=list)
    template_name: str | None = None
    next_tool: str
    needs_clarification: list[str] = Field(default_factory=list)


def plan_labware_intent(user_intent: str) -> LabwareAgentPlan:
    text = user_intent.lower().replace("×", "x")
    registered = match_agent_family(user_intent)
    if registered is None:
        return LabwareAgentPlan(
            user_intent=user_intent,
            next_tool="list_labware_families",
            needs_clarification=["Select the implemented regular 96-well family."],
        )
    template = None
    if registered.example_configs and any(token in text for token in ("paper", "same", "standard")):
        template = Path(registered.example_configs[0]).name
    clarification: list[str] = []
    if any(token in text for token in ("a1 4", "rest of the wells", "nonuniform", "uneven")):
        clarification.append(
            "The request is nonuniform and is outside the V1 family; do not generate an artifact."
        )
    elif template is None:
        clarification.append(
            "A new plate requires all measured footprint, well, depth/Z, spacing, offset, and volume fields."
        )
    return LabwareAgentPlan(
        user_intent=user_intent,
        family=registered.name,
        skill_names=list(select_labware_skills(registered.name)),
        template_name=template,
        next_tool="load_registered_labware_template" if template else "generate_registered_labware",
        needs_clarification=clarification,
    )


SYSTEM_PROMPT = f"""\
You are a labware definition specialist for the Opentrons OT-2. Your only job is
to turn a description of a physical part into a validated custom labware JSON
definition. You do not write protocols, plan experiments, or run the robot.

Runtime skill index (names and descriptions only):
{labware_skill_index()}

WHAT YOU DECIDE vs WHAT THE CODE DECIDES
You interpret: whether the request is the regular 96-well family, whether a
measured template applies, and which explicitly requested parameters changed.
The code computes: every single position coordinate, the ordering array, the
JSON document, and whether the result is valid. You must never write x/y values
yourself, and never try to enumerate 96 wells — call a tool and let it calculate.

HOW TO HANDLE A REQUEST
1. Inspect list_labware_families; only families returned there are public.
2. Load the selected runtime procedure with load_labware_skill when needed.
3. For the standard paper plate or "same plate", load
   paper_print_96_flat_v1 and alter only the explicitly requested fields.
4. For a new part, require the complete WellPlate96SpecV1 measurements.
5. Call generate_registered_labware. Its family-discriminated schema and
   validation are authoritative.

THE ONE RULE YOU MUST NOT BREAK
Never invent a physical dimension. Well diameter, depth, spacing, offsets and
the outer footprint must come from the user or from a named measured template.
If something is missing and nothing supplies it, say precisely which
values you need and ask for them. Do not pick a plausible number to make a call
succeed. These definitions drive a real pipette against real glass and paper; a
fabricated dimension is a crash, not a failing test.

If a user says only "make me a 96-well plate", that is underspecified. Offer
the measured template or ask for the missing values named by the runtime skill.

V1 rejects nonuniform wells, arbitrary row/column counts, uneven spacing, and
all other labware families. Explain the boundary; do not route around it with a
generic JSON writer or the internal legacy rectangular-grid interface.

NAMING
load_name: lowercase letters, digits, '.' and '_' only — no spaces, hyphens, or
capitals. display_name: any readable string. The output filename always follows
load_name, and load_name must be unique — reusing one would collide with the
existing definition.

OUTPUT SAFETY
Never pass overwrite=True unless the user has explicitly asked to replace a
specific existing definition. The default refusal exists because labware JSON in
labware/ is calibrated against physical hardware.

REPORTING RESULTS
Report the validation line verbatim: schema / geometry / json / opentrons. If a
layer FAILED, no file was written — fix the parameters rather than retrying the
same call. Surface any WARNING to the user; warnings mean the definition is
legal JSON but the object may be physically questionable.

Local validation is NOT physical verification. It proves the JSON is well formed
and that Opentrons accepts it. It does not prove the definition matches the real
object on the deck. Never tell the user labware has been verified on the OT-2 —
that requires a measured check on the machine, which you cannot perform.
"""


def get_labware_tools() -> List[Any]:
    """The tool set this agent exposes. No robot or protocol tools, by design."""
    return list(LABWARE_TOOLS)


def _message_text(message: BaseMessage | tuple[str, str] | Any) -> str:
    if isinstance(message, tuple) and len(message) >= 2:
        return str(message[1])
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def labware_agent_prompt(state: dict[str, Any]) -> list[Any]:
    """Inject routing plus the selected SKILL.md body into actual model context."""
    messages = list(state.get("messages", []))
    user_intent = next(
        (
            _message_text(message)
            for message in reversed(messages)
            if getattr(message, "type", None) in {"human", "user"}
            or (isinstance(message, tuple) and message and message[0] in {"human", "user"})
        ),
        "",
    )
    if not user_intent:
        return [SystemMessage(content=SYSTEM_PROMPT), *messages]
    plan = plan_labware_intent(user_intent)
    loaded = [
        f"## {name}\n{load_labware_skill_content(name)}"
        for name in plan.skill_names
    ]
    context = "\n\n".join(loaded) if loaded else "(No labware skill selected.)"
    dynamic = (
        f"{SYSTEM_PROMPT}\n\nDeterministic intent routing:\n"
        f"{plan.model_dump_json(indent=2)}\n\n"
        "Selected runtime skill content loaded before tool choice:\n"
        f"{context}"
    )
    return [SystemMessage(content=dynamic), *messages]


def create_custom_labware_agent(model: Any | None = None):
    """Build the LangGraph ReAct agent for custom labware generation."""
    from langgraph.prebuilt import create_react_agent

    from src.core.config import Config

    llm = model or Config.get_llm(temperature=0)
    return create_react_agent(model=llm, tools=get_labware_tools(), prompt=labware_agent_prompt)


def _repl(initial_input: str | None, rate_limited: bool) -> None:
    from src.core.config import Config
    from src.utils.limits_per_minute import RateLimitGuard

    rate_guard = RateLimitGuard(enabled=rate_limited)
    executor = create_custom_labware_agent()
    chat_history: list = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = AGENT_LOG_DIR / f"custom_labware_session_{timestamp}.log"

    print("--- Custom Labware AI Agent ---")
    print(Config.describe_llm_auth())
    print(f"Logging to: {log_file}")
    print("Try: 'make the paper plate with 8 mm spacing' or 'what labware families can you create?'")

    while True:
        try:
            user_input = initial_input if initial_input else input("\n[USER]: ")
            initial_input = None
        except (KeyboardInterrupt, EOFError):
            break

        if user_input.lower() in ("exit", "quit", "q"):
            break

        chat_history.append(("user", user_input))
        result = rate_guard.invoke_with_limit(executor, {"messages": chat_history})

        final_msg = result["messages"][-1]
        chat_history.append(final_msg)

        if isinstance(final_msg.content, list):
            text = "".join(p.get("text", "") for p in final_msg.content if isinstance(p, dict))
        else:
            text = final_msg.content

        print(f"\n[AGENT]: {text}")
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now().strftime('%H:%M:%S')}] USER: {user_input}\n")
            fh.write(f"[{datetime.now().strftime('%H:%M:%S')}] AGENT: {text}\n")


def main() -> int:
    rate_limited = "--rate-limit" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    _repl(" ".join(args) if args else None, rate_limited=rate_limited)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
