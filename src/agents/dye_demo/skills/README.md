# Skills

`__init__.py` is the existing loader moved here without changing its behavior.
`src.agents.dye_demo.skills` remains the import path. Runtime Markdown stays in
the repository's shared [skills directory](../../../../skills/README.md);
there is no duplicate set of instructions or new discovery system.

| Capability | Existing skill |
| --- | --- |
| Custom stock dilutions and dilution-factor series | [nanodrop-dilutions](../../../../skills/nanodrop-dilutions/SKILL.md) |
| Paper patterns, logical sources, droplets and replicates | [nanodrop-printing](../../../../skills/nanodrop-printing/SKILL.md) |
| Labware, materials and plan parameters | [nanodrop-plan-settings](../../../../skills/nanodrop-plan-settings/SKILL.md) |
| Revisions and conversational changes | [nanodrop-edit-semantics](../../../../skills/nanodrop-edit-semantics/SKILL.md) |
| Protocol generation and simulation procedure | [ot2-protocols](../../../../skills/ot2-protocols/SKILL.md) |

The four NanoDrop Markdown skills are loaded by the router. The protocol skill
is an operator/development procedure, not another runtime prompt. NanoDrop's
factor series prepares each dilution from stock; it does not implement serial
well-to-well dilution. The older dilution generator remains in
`src/protocols/dilution.py` with `configs/workflows/defaults/dilution.yaml`;
it contains a fixed transfer example and is not a complete serial workflow.
