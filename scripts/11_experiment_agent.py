#!/usr/bin/env python3
"""Version 11 conversational experiment agent.

    python scripts/11_experiment_agent.py                 (work laptop: Vertex)
    python scripts/11_experiment_agent.py --offline       (home laptop: no cloud)
    python scripts/11_experiment_agent.py --dry-run       (never call the runner)

One agent, three deterministic Version 11 workflows:

    dilution        general single or serial dilution   (no printing)
    standard_print  droplets onto configured paper wells
    clover_print    the validated four-droplet clover pattern

Architecture:

    you -> LLM adapter -> structured intent -> ExperimentState (pure Python)
        -> 11_ YAML -> V11 loader validation -> plan -> your confirmation
        -> existing robot runner -> OT-2

The agent only ever edits YAML. It never writes or edits deterministic Python.
On the work laptop the adapter is VertexAdapter, which reuses the repository's
existing GCloud/Vertex connection (Config.get_llm). --offline swaps in the
rule-based parser so the same agent logic can be driven with no credentials.

Commands: 'show' prints the YAML, 'plan' re-renders the plan, 'run' executes,
'reset' starts over, 'quit' exits.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml

from src.printing.v11.agent_core import WORKFLOWS, AgentError, ExperimentState
from src.printing.v11.labware import LABWARE, V11ConfigError

RUNNER = REPO / "scripts" / "run_printing_experiment_robot.py"

BANNER = """
============================================================
 VERSION 11 EXPERIMENT AGENT
============================================================
What would you like to do?

  1. Dilution        (single or serial, any labware -> any plate)
  2. Standard print  (droplets onto paper wells)
  3. Clover print    (four-droplet pattern)

Describe what you want in your own words, or say
"default standard print" / "default clover" / "default dilution".

Registered labware:
"""


def _rule(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def render_plan(workflow: str, resolved: dict[str, Any]) -> str:
    """Human-readable resolved plan; every number comes from the loader."""
    lines: list[str] = []
    deck = resolved.get("deck", {})
    tips = resolved.get("tips", {})

    if workflow == "dilution":
        dilution = resolved.get("dilution", {})
        stock, diluent = resolved.get("stock", {}), resolved.get("diluent", {})
        destination = resolved.get("destination", {})
        mix = resolved.get("mix", {})
        transfer = resolved.get("transfer", {})
        lines.append("DILUTION")
        lines.append(_rule("Stock source"))
        lines.append(f"  {deck.get('stock', {}).get('load_name')}  "
                     f"slot {deck.get('stock', {}).get('slot')}  "
                     f"well {stock.get('well')}")
        lines.append(f"  aspirate height {stock.get('aspirate_height_mm')} mm")
        lines.append(_rule("Diluent source"))
        lines.append(f"  {deck.get('diluent', {}).get('load_name')}  "
                     f"slot {deck.get('diluent', {}).get('slot')}  "
                     f"well {diluent.get('well')}")
        lines.append(_rule("Destination"))
        lines.append(f"  {deck.get('destination', {}).get('load_name')}  "
                     f"slot {deck.get('destination', {}).get('slot')}")
        lines.append(f"  wells: {', '.join(destination.get('wells', []))}")
        lines.append(_rule("Recipe"))
        mode = dilution.get("mode")
        lines.append(f"  mode: {mode}")
        stock_ul = dilution.get("stock_volume_ul")
        diluent_ul = dilution.get("diluent_volume_ul")
        if mode == "single":
            total = (stock_ul or 0) + (diluent_ul or 0)
            factor = total / stock_ul if stock_ul else 0
            lines.append(f"  stock volume:   {stock_ul} uL")
            lines.append(f"  diluent volume: {diluent_ul} uL")
            lines.append(f"  final volume:   {total} uL")
            lines.append(f"  dilution factor: {factor:g}x")
        else:
            lines.append(f"  diluent per well: {diluent_ul} uL")
            lines.append(f"  stock into first: {stock_ul} uL")
            lines.append(f"  carried down:     {dilution.get('transfer_volume_ul')} uL")
        lines.append(_rule("Transfer chunking"))
        lines.append(f"  max chunk {transfer.get('max_chunk_ul')} uL, "
                     f"air gap {transfer.get('air_gap_ul')} uL")
        lines.append(_rule("Mixing"))
        if mix.get("enabled"):
            lines.append(f"  {mix.get('cycles')} cycles x {mix.get('volume_ul')} uL")
        else:
            lines.append("  disabled")
    else:
        source = resolved.get("source", {})
        paper = resolved.get("paper", {})
        printing = resolved.get("printing", {})
        pipetting = resolved.get("pipetting", {})
        timing = resolved.get("timing", {})
        lines.append("CLOVER PRINT" if workflow == "clover_print" else "STANDARD PRINT")
        lines.append(_rule("Source"))
        lines.append(f"  {deck.get('source', {}).get('load_name')}  "
                     f"slot {deck.get('source', {}).get('slot')}  "
                     f"well(s) {', '.join(source.get('wells', []))}")
        lines.append(f"  aspirate height {source.get('aspirate_height_mm')} mm")
        lines.append(_rule("Paper"))
        lines.append(f"  slot {deck.get('paper', {}).get('slot', paper.get('slot'))}")
        lines.append(f"  print height {paper.get('print_height_mm')} mm above the surface")
        lines.append(_rule("Volume"))
        lines.append(f"  {printing.get('droplet_volume_ul')} uL per droplet")
        lines.append(f"  air gap {pipetting.get('air_gap_ul')} uL per cycle")

        if workflow == "standard_print":
            groups = resolved.get("print_groups") or resolved.get("groups") or []
            lines.append(_rule("Targets"))
            deposits = 0
            for group in groups:
                targets = group.get("targets", [])
                drops = group.get("droplets", 1)
                deposits += len(targets) * drops
                lines.append(f"  {', '.join(targets)}")
                lines.append(f"      x{drops} drop(s) from {group.get('source_well')}")
            layers = max((g.get("droplets", 1) for g in groups), default=1)
            lines.append(_rule("Totals"))
            lines.append(f"  {deposits} deposits, "
                         f"{deposits * float(printing.get('droplet_volume_ul', 0)):g} uL")
            lines.append(f"  order: {printing.get('order')}, {layers} layer(s)")
        else:
            clovers = resolved.get("clovers") or []
            lines.append(_rule("Clovers"))
            for clover in clovers:
                geometry = clover.get("geometry", {})
                lines.append(f"  {clover.get('reference')}  from "
                             f"{clover.get('source_well')}  "
                             f"layers {clover.get('layers', 1)}")
                lines.append(
                    f"      separation {geometry.get('separation_x_mm')} mm horizontal / "
                    f"{geometry.get('separation_y_mm')} mm vertical"
                    + (f", rotated {geometry.get('rotation_deg')} deg"
                       if geometry.get("rotation_deg") else "")
                )
            deposits = sum(4 * c.get("layers", 1) for c in clovers)
            lines.append(_rule("Totals"))
            lines.append(f"  {len(clovers)} clover(s), {deposits} droplets, "
                         f"{deposits * float(printing.get('droplet_volume_ul', 0)):g} uL")

        lines.append(_rule("Timing"))
        for key, label in (("inter_drop_delay_s", "between drops"),
                           ("inter_layer_delay_s", "between layers (drying)"),
                           ("inter_target_delay_s", "between targets"),
                           ("inter_clover_delay_s", "between clovers")):
            if key in timing:
                lines.append(f"  {label}: {timing[key]} s")

    lines.append(_rule("Tips"))
    lines.append(f"  reuse: {tips.get('pipette_tip_reuse')}")
    lines.append(f"  start tip {tips.get('start_tip')}, "
                 f"{'return to rack' if tips.get('return_tips') else 'drop in trash'}")
    totals = resolved.get("totals") or {}
    if totals.get("tips") or totals.get("tip_count"):
        lines.append(f"  estimated tips: {totals.get('tips') or totals.get('tip_count')}")

    dry_run = (resolved.get("run_modes") or {}).get("dry_run")
    if dry_run is not None:
        lines.append(_rule("Execution"))
        lines.append("  DRY RUN - the arm will not move" if dry_run
                     else "  LIVE - this will move liquid on the real OT-2")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="use the rule-based parser instead of Vertex (no GCloud)")
    parser.add_argument("--dry-run", action="store_true",
                        help="never invoke the robot runner, whatever you confirm")
    args = parser.parse_args(argv)

    from src.printing.v11.llm_adapter import RuleBasedAdapter

    if args.offline:
        adapter: Any = RuleBasedAdapter()
        print("(offline mode: rule-based parser, no cloud)")
    else:
        try:
            from src.printing.v11.llm_adapter import VertexAdapter

            adapter = VertexAdapter()
        except Exception as exc:  # noqa: BLE001
            print(f"Could not reach the configured LLM ({exc}).")
            print("Falling back to --offline rule-based parsing.")
            adapter = RuleBasedAdapter()

    print(BANNER)
    for key, spec in LABWARE.items():
        print(f"  {key:14s} {spec['load_name']}  (usual slot {spec['usual_slot']})")

    state = ExperimentState()
    history: list = []

    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        low = text.lower()
        if low in {"quit", "exit", "q"}:
            return 0
        if low == "reset":
            state = ExperimentState()
            history = []
            print("agent> starting over.")
            continue
        if low == "show":
            if state.config is None:
                print("agent> nothing configured yet.")
            else:
                print(yaml.safe_dump(state.config, sort_keys=False))
            continue

        if low not in {"plan", "run"}:
            try:
                intent = adapter.interpret(text, history)
            except Exception as exc:  # noqa: BLE001
                print(f"agent> could not interpret that: {exc}")
                continue
            if not intent:
                print("agent> I did not catch a change in that. Try naming the "
                      "labware, slot, well, volume or targets.")
                continue
            try:
                state.apply(intent)
            except (AgentError, V11ConfigError) as exc:
                print(f"agent> {exc}")
                continue
            history.append(("human", text))
            if intent.get("needs"):
                print(f"agent> I still need: {', '.join(intent['needs'])}")

        if state.workflow is None:
            print("agent> which workflow - dilution, standard print or clover print?")
            continue

        ok, message, resolved = state.validate()
        if not ok:
            print(f"agent> that configuration is not valid yet:\n        {message}")
            continue

        out = state.write()
        print(render_plan(state.workflow, resolved))
        print(f"\n  config written: {out.relative_to(REPO)}")

        # Only the explicit 'run' command opens the confirmation gate. Asking
        # after every turn would swallow the user's next instruction as the
        # answer and make multi-turn refinement impossible.
        if low != "run":
            print("\n  Keep describing changes, or type 'run' to execute.")
            continue

        answer = input("\n  Run this on the OT-2? (yes/no) > ").strip().lower()
        if answer not in {"y", "yes"}:
            print("agent> not running.")
            continue

        if args.dry_run:
            print("agent> --dry-run set; not calling the runner.")
            continue

        command = [
            sys.executable, str(RUNNER), WORKFLOWS[state.workflow]["runner"],
            "--config", str(out.relative_to(REPO)).replace("\\", "/"),
        ]
        print(f"\nagent> {' '.join(command)}\n")
        result = subprocess.run(command, cwd=str(REPO))
        print(f"\nagent> runner exit code {result.returncode}")


if __name__ == "__main__":
    raise SystemExit(main())
