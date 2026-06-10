# AI Agent Governance & Skills Guide

**Mandatory onboarding for every AI agent that reads, writes, generates, or deploys
code in this repository.** Read this file first, then the [`skills/`](skills/)
directory, *before* you touch a protocol, config, or script.

This is a hardware-in-the-loop laboratory robotics project. Generated code drives a
**physical Opentrons OT-2** handling **20 mL glass scintillation vials** and
delicate paper substrates. A wrong number here is not a failing test — it is a tip
driven into glass, a fractured vial, or a crashed Z-axis motor. Treat every change
with that gravity.

---

## The three rules (non-negotiable)

### Rule 1 — `skills/` is the single source of truth. Read it first.

Before editing or generating any code, consult the [`skills/`](skills/) directory.
It is the authoritative knowledge base for what this system can do and how. Do not
infer capabilities from memory, from training priors, or from a quick skim of one
file.

| Skill | Read it when you… |
|-------|-------------------|
| [`skills/vial-dilution-print/`](skills/vial-dilution-print/SKILL.md) | Touch the vial-dilution-print pipeline — the YAML, the protocol, the build/validate/vision scripts, or any of its parameters. Start here for this project's flagship workflow. |
| [`skills/ot2-protocols/`](skills/ot2-protocols/SKILL.md) | Build, validate, generate, or simulate any OT-2 workflow protocol. |
| [`skills/ot2-labware/`](skills/ot2-labware/SKILL.md) | Create or modify custom labware definitions (plates, racks, reservoirs). |
| [`skills/ot2-robot-control/`](skills/ot2-robot-control/SKILL.md) | Deploy to or run on the physical robot. **Lab laptop only.** |

Sub-references inside `skills/vial-dilution-print/`:
[TOOLS.md](skills/vial-dilution-print/TOOLS.md) (CLI utilities),
[PROTOCOL_MECHANICS.md](skills/vial-dilution-print/PROTOCOL_MECHANICS.md) (deck +
motion), [PARAMETERS.md](skills/vial-dilution-print/PARAMETERS.md) (every YAML key).

**If you change behaviour, update the relevant skill file in the same change.** A
skill that lies is worse than no skill.

### Rule 2 — Map every parameter back to the validated dictionary. Never go out of bounds.

Each workflow has a parameter dictionary defining each key's **type, units,
physical deck impact, and safe operational boundaries** (for the flagship pipeline:
[PARAMETERS.md](skills/vial-dilution-print/PARAMETERS.md)). Before you write or
change a value:

1. Look it up. Confirm type and **units** (mm vs µL vs s — a 30 that should be 3
   can crash the head).
2. Confirm it stays inside the stated safe bounds.
3. Confirm dependent invariants still hold — e.g.
   `print_block_column ∉ single_tip_columns`; `stock = total/fold ≤ pipette max`;
   `total_volume_ul ≤ plate well max`; tip columns supply `≥ 1 + len(factors)` tips.

The bounds are enforced twice — by the builder's `validate()` and the protocol's
on-robot `_preflight()` — but **enforcement is a backstop, not a license to guess.**
Generate values that are already in bounds; let validation catch mistakes, not
design.

Geometry constants (vial diameter 28 mm, depth 55 mm, row/col spacing 34/31 mm)
live in the labware JSON and the `safety:` block. The protocol loads labware with
explicit `namespace`/`version` and pre-flight cross-checks the **loaded** geometry
to ±0.5 mm before any motion. **Never** weaken that check, widen the tolerance, or
switch to fallback/default labware to make something "work."

### Rule 3 — Maintain structural invariant assertions in the tests. Don't hardcode statics.

The test suites assert **structure and invariants**, not frozen literals, so the
pipeline stays modular and resilient to config changes:

- Test expectations are **derived from the YAML** (well names, fold labels, droplet
  counts) — see `validate_vial_print.py` and `verify_print_droplets.py`. Changing
  `destination_column` or the factor list must never silently break a gate.
- Regression guards assert invariants — e.g. row order is derived from the loaded
  labware (`rows_by_name()`), so `_ROWS = "ABCDEFGH"` must **not** appear in the
  protocol source.

When you add behaviour, add an **invariant assertion** in
[`tests/test_vial_print.py`](tests/test_vial_print.py) (or the relevant suite), not
a hardcoded expected string that a benign config edit would invalidate. Keep
derivations data-driven.

---

## The mandatory pipeline order

Never hand the robot a protocol that has not passed both gates, in this order:

```
edit YAML  →  build (embeds CONFIG + simulates)  →  validate (run-mode matrix)
           →  CV verify (droplet QC)             →  deploy + execute (lab laptop)
```

```bash
python scripts/build_vial_dilution_print.py        # → SIMULATION OK
python scripts/validate_vial_print.py              # → ALL CASES PASSED
python vision_tests/scripts/verify_print_droplets.py --mock --expect 8
```

Steps 1–3 are safe on any machine (no robot connection). Deployment/execution is
**lab laptop only** — see [ot2-robot-control](skills/ot2-robot-control/SKILL.md).

You can also drive this entire pipeline **conversationally**: the
`src/agents/vial_print_agent.py` LangChain agent maps natural-language requests
("5 dilutions, 20 µL droplets, 3 replicates") onto the YAML and runs
build → validate → CV for you. It **wraps** these gates rather than bypassing them,
and edits a *user* YAML copy — the rules below still apply in full. See
[skills/vial-dilution-print/TOOLS.md](skills/vial-dilution-print/TOOLS.md) §5.

> **A green exit code is not proof.** `opentrons.simulate` exits 0 even when a
> protocol raises at runtime. Trust the text-scan verdicts (`SIMULATION OK`,
> `ALL CASES PASSED`) — not the exit code alone.

## Engineering invariants every agent must uphold

- **Edit the YAML, never the generated file.** The robot can't read the repo; the
  builder embeds the YAML as `CONFIG` between the `# >>> CONFIG START >>>` /
  `# <<< CONFIG END <<<` markers and writes a self-contained copy to
  `src/protocols/generated/`. Edits to generated files are overwritten and never
  reach hardware correctly.
- **`pathlib.Path`, relative to repo root.** All shared file I/O must be OS-agnostic
  (the robot runs Linux, the laptops run Windows). No hardcoded `C:\…` or rigid
  `/home/…` paths in shared logic. The only absolute path allowed is the robot-side
  `camera.robot_image_dir` — a config-driven POSIX path used solely on the robot,
  behind an `is_simulating()` guard. `scripts/audit_paths.py` enforces this.
- **Zero magic values in execution logic.** Well names, row letters, volumes, and
  operational constants come from config or are derived from loaded labware — never
  hardcoded in the motion code.
- **Respect the two-machine split.** This dev/code laptop is git + simulation only.
  The lab laptop has the live `.env`, SSH key, and the physical OT-2. Don't write
  code that assumes a robot connection exists on the dev laptop.

## Before you finish a change — self-audit

1. Did you read the relevant `skills/` file first? (Rule 1)
2. Is every value you touched within its documented bounds and units? (Rule 2)
3. Did you add/keep **invariant** assertions rather than hardcoded statics? (Rule 3)
4. Does `build → validate → CV` all pass on the dev laptop?
5. Did you update the affected skill doc(s) in the same change?
6. No hardcoded OS paths, no edits to generated files, no weakened safety checks?

If you cannot answer yes to all six, do not deploy.
