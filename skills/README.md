# OT-2 Lab Suite — Skills

This directory holds **Agent Skills** for the OT-2 Lab Suite. Each skill is a
self-contained playbook (`SKILL.md` + frontmatter) describing one capability
area, with the exact commands, file paths, and safety gates needed to perform it.

> **Environment note:** Skills that touch the physical robot (SCP, SSH,
> `opentrons_execute`) are written for the **lab laptop** — the machine with the
> real OT-2 connection, the live `.env`, and the SSH private key. The dev/code
> laptop cannot reach the robot; on it, only the config/generate/simulate steps
> work. See [project_dev_setup](../../../.claude memory) for the two-machine split.

## Skills

| Skill | Directory | What it covers |
|-------|-----------|----------------|
| **ot2-labware** | [`ot2-labware/`](ot2-labware/SKILL.md) | Create custom Opentrons labware definitions (YAML config → JSON) via the agent tools or the CLI generator |
| **ot2-protocols** | [`ot2-protocols/`](ot2-protocols/SKILL.md) | Build, validate, generate, and simulate OT-2 workflow protocols (dilution, printing) |
| **ot2-robot-control** | [`ot2-robot-control/`](ot2-robot-control/SKILL.md) | Interact with the live OT-2 from the lab laptop: connectivity checks, SCP deploy, SSH, and running workflows with `opentrons_execute` |
| **vial-dilution-print** | [`vial-dilution-print/`](vial-dilution-print/SKILL.md) | The flagship 20 mL vial → 96-well dilution → 8-channel paper-print demo: build/validate/simulate/CV tools, protocol mechanics, and the full `vial_dilution_print.yaml` parameter dictionary |

## How these relate to the AI agent

The agent in [`src/agents/main.py`](../src/agents/main.py) exposes 18 LangChain
tools that already orchestrate most of these steps conversationally. These skill
files document **both** paths:

1. **Agent path** — what to say to the running agent (`python -m src.agents.main`)
2. **Manual path** — the underlying CLI commands / scripts, for when you want to
   do it by hand or debug a failure

## Related docs

- [docs/AI_Agent_Overview.md](../docs/AI_Agent_Overview.md) — full file/tool map
- [docs/SOP_AI_Agent_Lab_Laptop.md](../docs/SOP_AI_Agent_Lab_Laptop.md) — operator SOP
