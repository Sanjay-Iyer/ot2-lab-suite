#!/usr/bin/env python3
"""Conversational printing agent: plain language -> YAML config -> real OT-2 run.

    python scripts/print_agent.py

The agent NEVER writes or edits deterministic Python. It only produces a YAML
configuration for one of the existing printing workflows, shows you the
interpreted plan, and -- after you confirm -- hands off to the existing runner
(scripts/run_printing_experiment_robot.py), which rebuilds the protocol fresh
from that YAML and uploads/plays/monitors it over the existing HTTP path.

    you: Print from vial A1. One drop at A1, B1, C1. Three drops at A2, B2, C2.
         Wait five seconds between layers.
    ->  agent writes configs/generated/agent_print.yaml, shows the plan,
        asks to confirm, then runs the real robot.

Type 'quit' to exit.
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

from src.printing.source_config import SOURCE_TYPES

GENERATED_DIR = REPO / "configs" / "generated"
STANDARD_OUT = GENERATED_DIR / "agent_print.yaml"
CLOVER_OUT = GENERATED_DIR / "agent_clover.yaml"
RUNNER = REPO / "scripts" / "run_printing_experiment_robot.py"
MACHINE_PROFILE = "configs/machines/ot2_standard_printing_p20_v1.yaml"

SYSTEM_PROMPT = f"""You turn a scientist's plain-language printing request into ONE JSON
object describing an OT-2 paper-printing experiment. You never write Python.

Return ONLY a JSON object (no prose, no code fence) with these keys:

  "pattern":        "standard" or "clover"
  "source_type":    one of {sorted(SOURCE_TYPES)}
                    - "vial_rack"     = the 20 mL vial rack (slot 7)
                    - "corning_plate" = the existing/normal/regular 96-well plate (slot 4)
                    - "well_plate"    = the BRAND (Ref. 781662) 96-well plate (slot 1)
  "source_wells":   list of source wells, e.g. ["A1"] or ["B3"]
  "droplet_volume_ul": number, default 5.0
  "inter_layer_delay_s": number, seconds to dry between layers, default 5.0
  "print_groups":   ONLY for pattern "standard". List of
                    {{"source_well": "A1", "targets": ["A1","B1","C1"], "droplets": 1}}
                    Each group's targets all get the same number of droplets.
  "clover_wells":   ONLY for pattern "clover". List of paper wells to center a
                    clover on, e.g. ["B2","B5","B8"]. If the user asks for "three
                    clovers" without naming wells, use ["B2","B5","B8"].
  "pipette_tip_reuse": true or false. DEFAULT true. Set it to false ONLY when the
                    user explicitly asks not to reuse tips -- e.g. "do not reuse
                    pipette tips", "use a fresh tip for every print", "new tip
                    each droplet", "clean tip per deposit". Otherwise always true.

Rules:
- "column 1 rows A-C" means targets ["A1","B1","C1"]. Column N is the number,
  row is the letter. "column 2 rows A to C" -> ["A2","B2","C2"].
- If the user says "one drop" for a group, droplets = 1; "three drops" -> 3.
- If the user mentions the BRAND plate use "well_plate"; a "normal"/"regular"/
  "existing" 96-well plate means "corning_plate"; a vial means "vial_rack".
- If a source well is not stated, use ["A1"].
- Every print group's source_well must appear in source_wells.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in model reply:\n{text[:500]}")
    return json.loads(text[start : end + 1])


def _standard_yaml(spec: dict) -> dict:
    source_type = spec.get("source_type", "vial_rack")
    wells = [str(w).upper() for w in (spec.get("source_wells") or ["A1"])]
    default_slot = SOURCE_TYPES[source_type]["default_slot"]
    is_vial = source_type == "vial_rack"
    return {
        "machine_profile": MACHINE_PROFILE,
        "protocol_label": "agent_standard_print",
        "run_modes": {"dry_run": False},
        "source": {
            "type": source_type,
            "slot": default_slot,
            "wells": wells,
            "material": spec.get("material", "agent-specified liquid"),
            "loaded_volume_ul": 5000.0 if is_vial else 300.0,
            "minimum_remaining_ul": 100.0 if is_vial else 20.0,
        },
        "printing": {
            "droplet_volume_ul": float(spec.get("droplet_volume_ul", 5.0)),
            "inter_drop_delay_s": 0.0,
            "inter_layer_delay_s": float(spec.get("inter_layer_delay_s", 5.0)),
        },
        "print_groups": [
            {
                "source_well": str(g.get("source_well", wells[0])).upper(),
                "targets": [str(t).upper() for t in g["targets"]],
                "droplets": int(g.get("droplets", 1)),
            }
            for g in spec["print_groups"]
        ],
        "tips": {
            "pipette_tip_reuse": bool(spec.get("pipette_tip_reuse", True)),
            "print_tip": "A1",
            "return_tips": True,
        },
    }


def _clover_yaml(spec: dict) -> dict:
    """Reuse the committed clover config, swapping only source + clover centers."""
    base_path = REPO / "configs" / "experiments" / "02_printing_four_clover.yaml"
    config = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    source_type = spec.get("source_type", "vial_rack")
    wells = [str(w).upper() for w in (spec.get("source_wells") or ["A1"])]
    is_vial = source_type == "vial_rack"
    config["protocol_label"] = "agent_clover_print"
    config["run_modes"] = {"dry_run": False, "do_dilution": False, "do_print": True}
    config["source"] = {
        "type": source_type,
        "slot": SOURCE_TYPES[source_type]["default_slot"],
        "wells": wells,
        "material": spec.get("material", "agent-specified liquid"),
        "loaded_volume_ul": 5000.0 if is_vial else 300.0,
        "minimum_remaining_ul": 100.0 if is_vial else 20.0,
    }
    config.setdefault("tips", {})["pipette_tip_reuse"] = bool(
        spec.get("pipette_tip_reuse", True)
    )
    clover_wells = [str(w).upper() for w in (spec.get("clover_wells") or ["B2", "B5", "B8"])]
    geometry = config["destination"].get("default_clover_geometry", {
        "half_width_mm": 2.0, "half_height_mm": 2.0})
    config["destination"]["clover_grid"] = {"enabled": False}
    config["destination"]["manual_clover_centers"] = [
        {
            "name": f"clover_{index + 1:02d}",
            "reference_well": well,
            "geometry": dict(geometry),
        }
        for index, well in enumerate(clover_wells)
    ]
    return config


def _describe(spec: dict, config: dict, pattern: str) -> str:
    lines = [f"  pattern      : {pattern}"]
    src = config["source"]
    lines.append(
        f"  source       : {src['type']} slot {src['slot']}, well(s) "
        f"{', '.join(src['wells'])}"
    )
    if pattern == "standard":
        pr = config["printing"]
        max_layers = max(g["droplets"] for g in config["print_groups"])
        lines.append(f"  droplet      : {pr['droplet_volume_ul']:g} uL")
        for index, group in enumerate(config["print_groups"], start=1):
            lines.append(
                f"  group {index}      : {', '.join(group['targets'])} x "
                f"{group['droplets']} drop(s) from {group['source_well']}"
            )
        lines.append(
            f"  layers       : {max_layers}, {pr['inter_layer_delay_s']:g} s drying between"
        )
        lines.append("  order        : layer-major (all targets get layer 1, then wait)")
        if config["tips"]["pipette_tip_reuse"]:
            tips = len({g["source_well"] for g in config["print_groups"]})
            lines.append(f"  tips         : {tips} (reuse on, one per source well)")
        else:
            tips = sum(len(g["targets"]) * g["droplets"] for g in config["print_groups"])
            lines.append(f"  tips         : {tips} (reuse OFF, fresh tip per deposit)")
    else:
        centers = config["destination"]["manual_clover_centers"]
        lines.append(f"  clovers      : {len(centers)} at "
                     f"{', '.join(c['reference_well'] for c in centers)}")
        lines.append("  geometry     : existing validated four-droplet clover")
        if config.get("tips", {}).get("pipette_tip_reuse", True):
            lines.append("  tips         : 1 (reuse on, single source well)")
        else:
            lines.append(
                f"  tips         : {len(centers) * 4} (reuse OFF, fresh tip per droplet)"
            )
    return "\n".join(lines)


def main() -> int:
    from src.core.config import Config

    print(__doc__)
    llm = Config.get_llm(temperature=0)
    history: list = []

    while True:
        try:
            request = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not request:
            continue
        if request.lower() in {"quit", "exit", "q"}:
            return 0

        messages = [("system", SYSTEM_PROMPT), *history, ("human", request)]
        try:
            reply = llm.invoke(messages)
            spec = _extract_json(getattr(reply, "content", str(reply)))
        except Exception as exc:  # noqa: BLE001
            print(f"agent> could not interpret that: {exc}")
            continue

        pattern = str(spec.get("pattern", "standard")).lower()
        try:
            if pattern == "clover":
                config, out_path, workflow = _clover_yaml(spec), CLOVER_OUT, "clover"
            else:
                config, out_path, workflow = _standard_yaml(spec), STANDARD_OUT, "print-from-vial"
        except (KeyError, TypeError, ValueError) as exc:
            print(f"agent> the request is missing something: {exc}")
            continue

        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            "# AI-GENERATED - review before the physical run.\n"
            + yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        print("\nagent> interpreted plan:")
        print(_describe(spec, config, pattern))
        print(f"\n  config written: {out_path.relative_to(REPO)}")
        print("  This will run on the REAL OT-2 and move liquid.")

        answer = input("\n  Run it? (yes / no) > ").strip().lower()
        if answer not in {"y", "yes"}:
            print("agent> not running. Edit the YAML or rephrase your request.")
            history.extend([("human", request), ("ai", json.dumps(spec))])
            continue

        cmd = [
            sys.executable, str(RUNNER), workflow,
            "--config", str(out_path.relative_to(REPO)).replace("\\", "/"),
        ]
        print(f"\nagent> {' '.join(cmd)}\n")
        result = subprocess.run(cmd, cwd=str(REPO))
        print(f"\nagent> runner exit code {result.returncode}")
        history.extend([("human", request), ("ai", json.dumps(spec))])


if __name__ == "__main__":
    raise SystemExit(main())
