# Agent NanoDrop

The primary agent and NiceGUI demonstration. Existing imports remain under
`src.agents.dye_demo`; protocol implementations and laboratory settings stay in
their established locations.

| Responsibility | Implementation |
| --- | --- |
| Agent: interpret instructions and propose edits | `conversation.py`, `llm.py`, `patches.py`, `session.py` |
| Tools: load, construct and compile workflows; generate protocols | `tools.py`, `model.py`, `plan.py`, `compiler.py` |
| Skills: reusable experimental instructions | [skills/](skills/README.md), loading the existing root `skills/nanodrop-*/SKILL.md` |
| Memory: operator, experiment IDs, dates and saved configurations | [memory/](memory/README.md) |
| NiceGUI: chat, deck, workflow preview and run controls | `gui/adapter.py`, `gui/app.py` |
| Existing physical checks and edit controls | `validation.py`, `limits.py`, `state.py`; unchanged by this organization |
| Manual YAML operation | `manual.py`, called by `scripts/run_dilution.py` and `scripts/run_paper_printing.py` |

Both the session and manual tools use the existing `compiler.compile_plan`.
The existing protocol builder embeds its resolved configuration into standalone
v19 Python. The GUI continues to use `DemoSession` and `SubprocessExecutor`.
No new agent framework, tool registry, approval system or execution gate is added.

See [architecture](../../../docs/ARCHITECTURE.md) and
[commands](../../../docs/COMMANDS.md).
