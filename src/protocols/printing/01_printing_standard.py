"""Trusted deterministic executor for standard paper-printing experiments.

DO NOT UPLOAD THIS FILE TO THE ROBOT. The PLAN block below is a one-drop
placeholder, not an experiment. Build the real thing and upload that instead:

    python scripts/run_printing_workflow.py standard

        configs/experiments/01_printing_standard.yaml   <- edit this
             |  validation + deterministic resolution
             v
        this file (engine, never edited by hand)
             |
             v
        src/protocols/generated/01_printing_standard_latest.py  <- upload this

That path is the MANUAL FALLBACK WORKFLOW 1: it needs no agent, no LLM, no
runtime skill, no natural-language request and no approval workflow.

VALIDATED PAPER GEOMETRY. The paper surface sits at well z = 6.0 mm and the
registered machine profile prints 0.5 mm above it, so droplets are released from
an absolute 6.5 mm with a 0.5 mm standoff. Changing that is a laboratory decision
requiring physical revalidation, and it lives in
configs/machines/ot2_standard_printing_p20_v1.yaml, never here.

This protocol is the only thing that moves the OT-2 for this workflow, and it is
experiment-independent. It contains no dilution factors, no column layout, no
liquid names, no row counts, and no experiment branches. It executes exactly the
canonical action list it is given and nothing else.

The action list is produced upstream by
``src.printing.standard.resolver.resolve_experiment_job`` from a validated
``PrintExperimentJobV1``. A build step replaces the ``PLAN`` block below with the
approved ``ResolvedExperimentPlanV1``; the placeholder plan committed here is an
unrelated single-drop smoke test so the base file remains simulatable on its own.

Supported actions:

    LOAD_LABWARE   place one definition in one deck slot
    LOAD_PIPETTE   mount the pipette and set its flow rates
    TRANSFER       one aspirate/dispense pair, already split to pipette size
    MIX            in-well mixing at a resolved height
    PRINT          droplet release using the validated air-handling profile
    DELAY          a configured rest, with the tip dropped first

Nothing here infers, rounds, re-splits, or reorders. If the plan is internally
inconsistent the protocol refuses to run rather than improvising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentrons import protocol_api


metadata = {
    "protocolName": "Standard paper printing (configuration-driven)",
    "author": "Laboratory automation",
    "description": "Executes one resolved standard printing plan; no experiment logic.",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


SUPPORTED_ACTIONS = (
    "LOAD_LABWARE",
    "LOAD_PIPETTE",
    "TRANSFER",
    "MIX",
    "PRINT",
    "DELAY",
)


# >>> PLAN START >>> (auto-generated from an approved ResolvedExperimentPlanV1)
PLAN = {
    "schema_version": "resolved-experiment-plan/v1",
    "plan_id": "0" * 64,
    "job_id": "0" * 64,
    "experiment_id": "placeholder_smoke_test",
    "initial_liquids": [
        {
            "liquid_id": "placeholder_liquid",
            "display_name": "Placeholder liquid",
            "location": {
                "labware": "source_plate",
                "well": "A1",
                "reference": "bottom",
                "z_mm": 0.2,
            },
            "loaded_volume_ul": 200.0,
            "minimum_remaining_ul": 0.0,
            "scientific_allocation_ul": 4.0,
        }
    ],
    "preparation_math": [],
    "actions": [
        {
            "sequence_index": 1,
            "action": "LOAD_LABWARE",
            "role": "source_plate",
            "slot": 4,
            "load_name": "corning_96_wellplate_360ul_custom",
            "namespace": "custom_beta",
            "version": 1,
        },
        {
            "sequence_index": 2,
            "action": "LOAD_LABWARE",
            "role": "substrate",
            "slot": 5,
            "load_name": "paper_print_96_flat",
            "namespace": "custom_beta",
            "version": 1,
        },
        {
            "sequence_index": 3,
            "action": "LOAD_LABWARE",
            "role": "tiprack",
            "slot": 9,
            "load_name": "opentrons_96_tiprack_20ul",
            "namespace": "opentrons",
            "version": 1,
        },
        {
            "sequence_index": 4,
            "action": "LOAD_PIPETTE",
            "pipette": "p20_single_gen2",
            "mount": "left",
            "tiprack_role": "tiprack",
            "minimum_volume_ul": 1.0,
            "maximum_volume_ul": 20.0,
            "flow_rates": {"aspirate_ul_s": 3.0, "dispense_ul_s": 3.0},
        },
        {
            "sequence_index": 5,
            "action": "PRINT",
            "operation_id": "placeholder_print",
            "condition_id": "substrate_A1",
            "liquid_id": "placeholder_liquid",
            "source": {
                "labware": "source_plate",
                "well": "A1",
                "reference": "bottom",
                "z_mm": 0.2,
            },
            "destination": {
                "labware": "substrate",
                "well": "A1",
                "reference": "bottom",
                "z_mm": 0.5,
            },
            "volume_ul": 4.0,
            "air_gap_ul": 1.5,
            "air_gap_height_mm": 5.0,
            "piston_dispense_ul": 5.5,
            "push_out_ul": 3.0,
            "blow_out": True,
            "post_dispense_delay_s": 2.0,
            "pipette": "p20_single_gen2",
            "drop_index": 1,
            "repeat_count": 1,
            "tip_group": "placeholder",
        },
    ],
    "totals": {
        "action_count": 5,
        "transfer_count": 0,
        "mix_count": 0,
        "print_count": 1,
        "delay_count": 0,
        "configured_experimental_delay_s": 0.0,
        "printed_liquid_ul": 4.0,
        "tip_count": 1,
    },
    "source_accessibility": {
        "status": "PASS",
        "checked_aspirate_or_mix_actions": 1,
        "minimum_nominal_submerged_margin_ul": 188.6,
        "ending_volumes_ul": {"source_plate:A1": 196.0},
        "geometry": {
            "source_plate_well_diameter_mm": 6.86,
            "source_plate_aspirate_height_mm": 0.2,
            "substrate_dispense_height_mm": 0.5,
        },
    },
}
# <<< PLAN END <<<


DEFAULT_DRY_RUN = False


def verify_plan(plan: dict[str, Any]) -> None:
    """Fail closed on a plan this executor cannot faithfully perform.

    Full schema validation happens upstream. These checks exist because the
    generated file is what actually reaches the robot: they confirm that the
    embedded plan is still internally consistent and that every action is one
    this protocol knows how to execute.
    """
    if plan.get("schema_version") != "resolved-experiment-plan/v1":
        raise ValueError(
            f"unsupported plan schema_version {plan.get('schema_version')!r}"
        )
    actions = plan.get("actions") or []
    if not actions:
        raise ValueError("the resolved plan contains no actions")

    for expected, action in enumerate(actions, start=1):
        if action.get("sequence_index") != expected:
            raise ValueError(
                f"plan actions must be numbered 1..n; found "
                f"{action.get('sequence_index')!r} at position {expected}"
            )
        if action.get("action") not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported action {action.get('action')!r}")

    totals = plan.get("totals") or {}
    if totals.get("action_count") != len(actions):
        raise ValueError("plan totals do not describe the embedded action list")
    for name in ("TRANSFER", "MIX", "PRINT", "DELAY"):
        counted = sum(1 for action in actions if action["action"] == name)
        declared = totals.get(f"{name.lower()}_count")
        if declared is not None and declared != counted:
            raise ValueError(
                f"plan totals claim {declared} {name} actions but hold {counted}"
            )

    loaded = next(
        (action for action in actions if action["action"] == "LOAD_PIPETTE"), None
    )
    if loaded is None:
        raise ValueError("the resolved plan never loads a pipette")
    minimum = float(loaded["minimum_volume_ul"])
    maximum = float(loaded["maximum_volume_ul"])
    roles = {
        action["role"] for action in actions if action["action"] == "LOAD_LABWARE"
    }
    if loaded["tiprack_role"] not in roles:
        raise ValueError("the pipette references a tip rack that is never loaded")

    for action in actions:
        kind = action["action"]
        if kind in {"TRANSFER", "MIX"}:
            volume = float(action["volume_ul"])
            if not minimum <= volume <= maximum:
                raise ValueError(
                    f"action {action['sequence_index']} moves {volume:g} uL, outside the "
                    f"loaded pipette range [{minimum:g}, {maximum:g}] uL"
                )
        elif kind == "PRINT":
            piston = float(action["piston_dispense_ul"])
            if float(action["volume_ul"]) < minimum or piston > maximum:
                raise ValueError(
                    f"action {action['sequence_index']} cannot be released within the "
                    f"loaded pipette range [{minimum:g}, {maximum:g}] uL"
                )
        elif kind == "DELAY":
            if float(action["duration_s"]) < 0:
                raise ValueError(
                    f"action {action['sequence_index']} requests a negative delay"
                )
        for key in ("source", "destination", "location"):
            location = action.get(key)
            if location is None:
                continue
            if location["labware"] not in roles:
                raise ValueError(
                    f"action {action['sequence_index']} uses labware role "
                    f"{location['labware']!r}, which is never loaded"
                )
            if location["reference"] not in {"bottom", "top"}:
                raise ValueError(
                    f"action {action['sequence_index']} uses unknown reference "
                    f"{location['reference']!r}"
                )


def _load_labware(protocol: protocol_api.ProtocolContext, action: dict[str, Any]):
    load_name = action["load_name"]
    slot = str(action["slot"])
    if action["namespace"] == "opentrons":
        return protocol.load_labware(load_name, slot)
    try:
        return protocol.load_labware(
            load_name,
            slot,
            namespace=action["namespace"],
            version=action["version"],
        )
    except Exception:
        repository = Path(__file__).resolve().parents[3]
        definition = json.loads(
            (repository / "labware" / f"{load_name}.json").read_text(encoding="utf-8")
        )
        return protocol.load_labware_from_definition(definition, slot)


def _point(labware_by_role: dict[str, Any], spec: dict[str, Any]):
    well = labware_by_role[spec["labware"]][spec["well"]]
    if spec["reference"] == "bottom":
        return well.bottom(float(spec["z_mm"]))
    return well.top(float(spec["z_mm"]))


def run(protocol: protocol_api.ProtocolContext) -> None:
    """Execute the embedded resolved plan exactly as written."""
    plan = PLAN
    verify_plan(plan)
    actions = plan["actions"]

    labware_by_role: dict[str, Any] = {}
    for action in actions:
        if action["action"] == "LOAD_LABWARE":
            labware_by_role[action["role"]] = _load_labware(protocol, action)

    tiprack_role = next(
        action["tiprack_role"]
        for action in actions
        if action["action"] == "LOAD_PIPETTE"
    )
    required_tips = int(plan.get("totals", {}).get("tip_count", 0))
    tip_capacity = len(labware_by_role[tiprack_role].wells())
    if required_tips > tip_capacity:
        raise RuntimeError(
            f"resolved plan requires {required_tips} tips but the loaded rack provides "
            f"only {tip_capacity}"
        )

    for source in plan.get("initial_liquids", []):
        liquid = protocol.define_liquid(
            name=source["liquid_id"],
            description=source["display_name"],
            display_color="#7f8c8d",
        )
        location = source["location"]
        labware_by_role[location["labware"]][location["well"]].load_liquid(
            liquid=liquid, volume=float(source["loaded_volume_ul"])
        )

    pipette_action = next(
        action for action in actions if action["action"] == "LOAD_PIPETTE"
    )
    pipette = protocol.load_instrument(
        pipette_action["pipette"],
        pipette_action["mount"],
        tip_racks=[labware_by_role[pipette_action["tiprack_role"]]],
    )
    pipette.flow_rate.aspirate = float(pipette_action["flow_rates"]["aspirate_ul_s"])
    pipette.flow_rate.dispense = float(pipette_action["flow_rates"]["dispense_ul_s"])

    available_tips = iter(labware_by_role[pipette_action["tiprack_role"]].wells())
    active_tip_group: str | None = None

    def release_tip() -> None:
        nonlocal active_tip_group
        if pipette.has_tip:
            pipette.drop_tip()
        active_tip_group = None

    def ensure_tip(group: str) -> None:
        nonlocal active_tip_group
        if active_tip_group == group and pipette.has_tip:
            return
        release_tip()
        try:
            tip = next(available_tips)
        except StopIteration as exc:
            raise RuntimeError(
                "the resolved plan needs more tips than the loaded rack provides"
            ) from exc
        pipette.pick_up_tip(tip)
        active_tip_group = group

    totals = plan.get("totals", {})
    protocol.comment(
        f"=== standard printing plan {plan['plan_id'][:12]} "
        f"(experiment {plan['experiment_id']}) ==="
    )
    protocol.comment(
        f"planned actions: {totals.get('action_count')}, "
        f"prints: {totals.get('print_count')}, "
        f"tips: {totals.get('tip_count')}"
    )

    for action in actions:
        kind = action["action"]
        if kind in {"LOAD_LABWARE", "LOAD_PIPETTE"}:
            continue

        if kind == "DELAY":
            release_tip()
            protocol.comment(action["reason"])
            protocol.delay(seconds=float(action["duration_s"]))
            continue

        ensure_tip(action["tip_group"])

        if kind == "TRANSFER":
            pipette.aspirate(
                float(action["volume_ul"]), _point(labware_by_role, action["source"])
            )
            pipette.dispense(
                float(action["volume_ul"]),
                _point(labware_by_role, action["destination"]),
            )
        elif kind == "MIX":
            pipette.mix(
                int(action["cycles"]),
                float(action["volume_ul"]),
                _point(labware_by_role, action["location"]),
            )
        elif kind == "PRINT":
            destination = _point(labware_by_role, action["destination"])
            pipette.aspirate(
                float(action["volume_ul"]), _point(labware_by_role, action["source"])
            )
            if float(action["air_gap_ul"]) > 0:
                pipette.air_gap(
                    float(action["air_gap_ul"]),
                    height=float(action["air_gap_height_mm"]),
                )
            try:
                pipette.dispense(
                    float(action["piston_dispense_ul"]),
                    destination,
                    push_out=float(action["push_out_ul"]),
                )
            except TypeError:
                pipette.dispense(float(action["piston_dispense_ul"]), destination)
            if action["blow_out"]:
                pipette.blow_out(destination)
            if float(action["post_dispense_delay_s"]) > 0:
                protocol.delay(seconds=float(action["post_dispense_delay_s"]))
        else:  # pragma: no cover - verify_plan rejects anything else
            raise ValueError(f"unsupported action {kind!r}")

    release_tip()
    protocol.comment(
        f"standard printing complete: {totals.get('print_count')} droplets, "
        f"{totals.get('printed_liquid_ul')} uL printed."
    )
