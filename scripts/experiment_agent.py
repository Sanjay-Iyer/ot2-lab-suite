#!/usr/bin/env python3
"""Conversational experiment agent: plain language -> YAML -> real OT-2.

    python scripts/experiment_agent.py

One agent, three deterministic workflows:

    dilution        general single or serial dilution   (no printing)
    standard-print  droplets onto configured paper wells
    clover-print    the validated four-droplet clover pattern

The agent NEVER writes or edits deterministic Python. It starts from the
templates in configs/templates/, applies what you ask for across the whole
conversation, writes configs/generated/current_*.yaml, shows the resolved plan,
and only after you type "yes" hands off to the existing robot runner
(scripts/run_printing_experiment_robot.py), which rebuilds the protocol fresh
from that YAML and uploads/plays/monitors it.

Uses the repository's existing LLM connection (src.core.config.Config.get_llm),
which on the work laptop is the already-configured Vertex AI / gcloud path.

Commands: 'show' prints the current config, 'run' re-runs it, 'quit' exits.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml

TEMPLATES = REPO / "configs" / "templates"
GENERATED = REPO / "configs" / "generated"
RUNNER = REPO / "scripts" / "run_printing_experiment_robot.py"

WORKFLOWS = {
    "dilution": {
        "template": TEMPLATES / "dilution_template.yaml",
        "output": GENERATED / "current_dilution.yaml",
        "runner": "dilution",
    },
    "standard-print": {
        "template": TEMPLATES / "standard_print_template.yaml",
        "output": GENERATED / "current_standard_print.yaml",
        "runner": "standard-print",
    },
    "clover-print": {
        "template": TEMPLATES / "clover_print_template.yaml",
        "output": GENERATED / "current_clover_print.yaml",
        "runner": "clover-print",
    },
}

LABWARE = {
    "vial_rack": ("tuberack_3dprint_20ml_8vials_v2", 7),
    "corning_plate": ("corning_96_wellplate_360ul_custom", 4),
    "well_plate": ("brand_96_wellplate_350ul_flat_781662", 1),
}

SYSTEM_PROMPT = """You turn a scientist's plain-language request into ONE JSON object
describing an OT-2 experiment. You never write Python.

Return ONLY a JSON object (no prose, no code fence):

  "workflow": "dilution" | "standard-print" | "clover-print"

  Source labware, used by all three:
  "source_type": "vial_rack" | "corning_plate" | "well_plate"
      vial_rack     = the 20 mL vial rack, slot 7
      corning_plate = the existing/normal/regular 96-well plate, slot 4
      well_plate    = the BRAND (Ref. 781662) 96-well plate, slot 1
  "source_slot": integer, omit to use that labware's usual slot
  "source_wells": list, e.g. ["A1"] or ["C11"]

  "paper_slot": 5 or 11 (printing workflows). Default 11.
  "droplet_volume_ul": number, default 5
  "pipette_tip_reuse": true or false. DEFAULT true. Only false when the user
      explicitly asks for fresh/clean tips per droplet.

  standard-print only:
  "print_groups": [{"targets": ["A1","B1","C1"], "droplets": 1}, ...]
      "column 1 rows A-C" -> ["A1","B1","C1"]; "all 8 rows of column 2" ->
      ["A2","B2","C2","D2","E2","F2","G2","H2"]. Column is the number, row the
      letter.
  "inter_layer_delay_s": seconds of drying between stacked droplets, default 5

  clover-print only:
  "clover_wells": list of paper wells to centre a clover on, e.g. ["B3"]
  "half_width_mm" / "half_height_mm": offsets FROM CENTRE. The user states the
      SEPARATION, which is TWICE these. "2 mm spacing" -> 1.0. "5 mm" -> 2.5.

  dilution only:
  "dilution_mode": "single" or "series"
  "diluent_type"/"diluent_slot"/"diluent_well": where the water is; default the
      20 mL vial rack slot 7 well A2
  "destination_type"/"destination_slot"/"destination_wells": where the dilution
      is made
  "stock_volume_ul", "diluent_volume_ul", "transfer_volume_ul",
  "mix_cycles", "mix_volume_ul"
      A "5x dilution" of final volume V is stock V/5 and diluent 4V/5.
      e.g. 5x in 100 uL -> stock 20, diluent 80.

Only include keys the user actually determined or that you can infer with
confidence. Omit anything unknown; the template default will be used.
"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in reply:\n{text[:400]}")
    return json.loads(text[start : end + 1])


def _apply(config: dict, spec: dict, workflow: str) -> dict:
    """Apply the agent's spec onto a loaded template config."""
    reuse = spec.get("pipette_tip_reuse")
    source_type = spec.get("source_type")
    wells = [str(w).upper() for w in (spec.get("source_wells") or [])]

    if workflow == "dilution":
        if source_type:
            load_name, slot = LABWARE[source_type]
            config["source"]["labware"] = load_name
            config["source"]["slot"] = int(spec.get("source_slot", slot))
        if wells:
            config["source"]["well"] = wells[0]
        if spec.get("diluent_type"):
            load_name, slot = LABWARE[spec["diluent_type"]]
            config["diluent"]["labware"] = load_name
            config["diluent"]["slot"] = int(spec.get("diluent_slot", slot))
        if spec.get("diluent_well"):
            config["diluent"]["well"] = str(spec["diluent_well"]).upper()
        if spec.get("destination_type"):
            load_name, slot = LABWARE[spec["destination_type"]]
            config["destination"]["labware"] = load_name
            config["destination"]["slot"] = int(spec.get("destination_slot", slot))
        if spec.get("destination_wells"):
            config["destination"]["wells"] = [
                str(w).upper() for w in spec["destination_wells"]
            ]
        for key in ("stock_volume_ul", "diluent_volume_ul", "transfer_volume_ul",
                    "mix_cycles", "mix_volume_ul"):
            if spec.get(key) is not None:
                config["dilution"][key] = spec[key]
        if spec.get("dilution_mode"):
            config["dilution"]["mode"] = spec["dilution_mode"]
        if reuse is not None:
            config["pipette_tip_reuse"] = bool(reuse)
        return config

    # ---- printing workflows ----
    if workflow == "standard-print":
        if source_type:
            config["source"]["type"] = source_type
            config["source"]["slot"] = int(
                spec.get("source_slot", LABWARE[source_type][1])
            )
            is_vial = source_type == "vial_rack"
            config["source"]["loaded_volume_ul"] = 5000.0 if is_vial else 300.0
            config["source"]["minimum_remaining_ul"] = 100.0 if is_vial else 20.0
            config["source"]["aspirate_height_mm"] = 4.0 if is_vial else 1.0
        if wells:
            config["source"]["wells"] = wells
        if spec.get("paper_slot"):
            config["substrate"]["slot"] = int(spec["paper_slot"])
        if spec.get("droplet_volume_ul") is not None:
            config["printing"]["droplet_volume_ul"] = spec["droplet_volume_ul"]
        if spec.get("inter_layer_delay_s") is not None:
            config["printing"]["inter_layer_delay_s"] = spec["inter_layer_delay_s"]
        if spec.get("print_groups"):
            default_source = (config["source"].get("wells") or ["A1"])[0]
            config["print_groups"] = [
                {
                    "source_well": str(g.get("source_well", default_source)).upper(),
                    "targets": [str(t).upper() for t in g["targets"]],
                    "droplets": int(g.get("droplets", 1)),
                }
                for g in spec["print_groups"]
            ]
        if reuse is not None:
            config["tips"]["pipette_tip_reuse"] = bool(reuse)
        return config

    # clover-print
    if source_type:
        config["source"]["type"] = source_type
        config["source"]["slot"] = int(spec.get("source_slot", LABWARE[source_type][1]))
        is_vial = source_type == "vial_rack"
        config["source"]["loaded_volume_ul"] = 5000.0 if is_vial else 300.0
        config["source"]["minimum_remaining_ul"] = 100.0 if is_vial else 20.0
    if wells:
        config["source"]["wells"] = wells
    if spec.get("paper_slot"):
        config["deck"]["paper"]["slot"] = int(spec["paper_slot"])
    if spec.get("droplet_volume_ul") is not None:
        config["printing"]["droplet_volume_ul"] = spec["droplet_volume_ul"]
    if spec.get("clover_wells"):
        half_w = spec.get("half_width_mm", 1.0)
        half_h = spec.get("half_height_mm", half_w)
        config["destination"]["manual_clover_centers"] = [
            {
                "name": f"clover_{i + 1:02d}",
                "reference_well": str(w).upper(),
                "geometry": {"half_width_mm": half_w, "half_height_mm": half_h},
            }
            for i, w in enumerate(spec["clover_wells"])
        ]
    elif spec.get("half_width_mm") is not None:
        half_w = spec["half_width_mm"]
        half_h = spec.get("half_height_mm", half_w)
        for centre in config["destination"]["manual_clover_centers"]:
            centre["geometry"] = {"half_width_mm": half_w, "half_height_mm": half_h}
    if reuse is not None:
        config["tips"]["pipette_tip_reuse"] = bool(reuse)
    return config


def _plan(config: dict, workflow: str) -> str:
    lines = [f"\n{workflow.upper().replace('-', ' ')}", "=" * 40]
    if workflow == "dilution":
        d = config["dilution"]
        lines += [
            "Stock", "-----",
            f"{config['source']['labware']}  slot {config['source']['slot']}  "
            f"{config['source']['well']}", "",
            "Diluent", "-------",
            f"{config['diluent']['labware']}  slot {config['diluent']['slot']}  "
            f"{config['diluent']['well']}", "",
            "Destination", "-----------",
            f"{config['destination']['labware']}  slot {config['destination']['slot']}  "
            f"{', '.join(config['destination']['wells'])}", "",
            "Recipe", "------", f"mode {d['mode']}",
        ]
        if d["mode"] == "single":
            total = d["stock_volume_ul"] + d["diluent_volume_ul"]
            factor = total / d["stock_volume_ul"] if d["stock_volume_ul"] else 0
            lines.append(
                f"{d['stock_volume_ul']:g} uL stock + {d['diluent_volume_ul']:g} uL "
                f"diluent = {total:g} uL  ({factor:g}x)"
            )
        else:
            lines.append(
                f"{d['diluent_volume_ul']:g} uL diluent per well, "
                f"{d['stock_volume_ul']:g} uL stock into the first, then "
                f"{d['transfer_volume_ul']:g} uL carried down"
            )
        lines += [f"mix {d['mix_cycles']} x {d['mix_volume_ul']:g} uL", "",
                  "Tip reuse", "---------", str(config.get("pipette_tip_reuse", True))]
    elif workflow == "standard-print":
        s, p = config["source"], config["printing"]
        load_name = LABWARE[s["type"]][0]
        max_layers = max(g["droplets"] for g in config["print_groups"])
        lines += [
            "Source", "------", f"{load_name}  slot {s['slot']}  "
            f"{', '.join(s['wells'])}", "",
            "Paper", "-----", f"slot {config['substrate']['slot']}", "",
            "Targets", "-------",
        ]
        for g in config["print_groups"]:
            lines.append(
                f"{', '.join(g['targets'])}   x{g['droplets']} drop(s)  "
                f"from {g['source_well']}"
            )
        lines += ["", "Volume", "------", f"{p['droplet_volume_ul']:g} uL each", ""]
        if max_layers > 1:
            lines += ["Layers", "------",
                      f"{max_layers}, {p['inter_layer_delay_s']:g} s drying between", ""]
        lines += ["Tip reuse", "---------", str(config["tips"]["pipette_tip_reuse"])]
    else:
        s = config["source"]
        load_name = LABWARE[s["type"]][0]
        centres = config["destination"]["manual_clover_centers"]
        geometry = centres[0]["geometry"]
        lines += [
            "Source", "------", f"{load_name}  slot {s['slot']}  "
            f"{', '.join(s['wells'])}", "",
            "Paper", "-----", f"slot {config['deck']['paper']['slot']}", "",
            "Clovers", "-------",
            f"{len(centres)} at {', '.join(c['reference_well'] for c in centres)}",
            f"separation {2 * geometry['half_width_mm']:g} mm horizontal / "
            f"{2 * geometry['half_height_mm']:g} mm vertical", "",
            "Volume", "------",
            f"{config['printing']['droplet_volume_ul']:g} uL per droplet "
            f"({len(centres) * 4} droplets)", "",
            "Tip reuse", "---------", str(config["tips"]["pipette_tip_reuse"]),
        ]
    return "\n".join(lines)


def main() -> int:
    from src.core.config import Config

    print(__doc__)
    print("What would you like to do?\n")
    print("  1. Dilution")
    print("  2. Standard print")
    print("  3. Clover print\n")
    print("Describe what you want in your own words, or say 'default standard print',")
    print("'default clover' or 'default dilution' to start from the shipped defaults.\n")

    llm = Config.get_llm(temperature=0)
    state: dict = {"workflow": None, "config": None}
    history: list = []

    while True:
        try:
            request = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not request:
            continue
        low = request.lower()
        if low in {"quit", "exit", "q"}:
            return 0
        if low == "show":
            if state["config"] is None:
                print("agent> nothing configured yet.")
            else:
                print(yaml.safe_dump(state["config"], sort_keys=False))
            continue
        if low == "run":
            if state["config"] is None:
                print("agent> nothing configured yet.")
                continue
            spec_workflow = state["workflow"]
        else:
            messages = [("system", SYSTEM_PROMPT), *history, ("human", request)]
            try:
                reply = llm.invoke(messages)
                spec = _extract_json(getattr(reply, "content", str(reply)))
            except Exception as exc:  # noqa: BLE001
                print(f"agent> could not interpret that: {exc}")
                continue

            spec_workflow = spec.get("workflow") or state["workflow"]
            if spec_workflow not in WORKFLOWS:
                print("agent> which workflow: dilution, standard-print or clover-print?")
                continue

            # Start from the template on a new workflow; otherwise keep refining.
            if state["workflow"] != spec_workflow or state["config"] is None:
                template = WORKFLOWS[spec_workflow]["template"]
                state["config"] = yaml.safe_load(template.read_text(encoding="utf-8"))
                state["workflow"] = spec_workflow
            try:
                state["config"] = _apply(state["config"], spec, spec_workflow)
            except (KeyError, TypeError, ValueError) as exc:
                print(f"agent> something was missing: {exc}")
                continue
            history.extend([("human", request), ("ai", json.dumps(spec))])

        out = WORKFLOWS[spec_workflow]["output"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "# AI-GENERATED - review before the physical run.\n"
            + yaml.safe_dump(state["config"], sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        print(_plan(state["config"], spec_workflow))
        print(f"\n  config written: {out.relative_to(REPO)}")
        print("  This runs on the REAL OT-2 and moves liquid.")

        answer = input("\n  Run this on the OT-2? (yes/no) > ").strip().lower()
        if answer not in {"y", "yes"}:
            print("agent> not running. Keep describing changes, or 'run' when ready.")
            continue

        cmd = [
            sys.executable, str(RUNNER), WORKFLOWS[spec_workflow]["runner"],
            "--config", str(out.relative_to(REPO)).replace("\\", "/"),
        ]
        print(f"\nagent> {' '.join(cmd)}\n")
        result = subprocess.run(cmd, cwd=str(REPO))
        print(f"\nagent> runner exit code {result.returncode}")


if __name__ == "__main__":
    raise SystemExit(main())
