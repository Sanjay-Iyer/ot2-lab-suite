"""
AI agent demo: dilution series -> paper print, protocol v19 (OT-2 API 2.15).

The whole workflow on the single-channel P20 — the same instrument and tip logic
as 06_vial_dilution_paper_print_v6_p20only.py, with the physically validated
print-release cycle from 11_standard_print.py / the ot2_standard_printing_p20_v1
machine profile.

What it does, in order:
  1. dilute one SAMPLE (dye stock) in one SOLVENT (water) across a fold series,
     one dilution per plate row, all in a single plate column;
  2. print every one of those dilutions onto paper, one paper row per dilution,
     one paper column per print volume x replicate.

Everything a demo audience changes by talking — deck slots (or OFF_DECK), how
many dilutions, which plate column, which paper column, drop volume, replicates,
drops per spot, starting tip, tip policy — is configuration, not code.

LIQUID HANDLING (docs/ai_dye_demo/liquid_handling_parameters.md):
  dilution transfer  aspirate at vial bottom + aspirate_height_mm; dispense below
                     the well top (solvent/sample_dispense_from_top_mm) so a shared
                     tip never touches the liquid; then BLOW OUT at the same place.
                     The P20 GEN2's default push-out is 0 uL, so without the
                     blow-out the end of every transfer can stay in the tip.
                     (blow_out_after_dispense, added 2026-09-10.)
  volume splitting   at most max_transfer_ul per transfer; a remainder below the
                     P20 minimum is rebalanced with the previous transfer
                     (20 + 0.63 uL -> 10.31 + 10.31 uL).
  print step         mix the source well, then for each drop: aspirate at well
                     bottom + aspirate_height_mm, trailing air gap, dispense liquid
                     + gap with push-out at paper bottom + z_mm, blow out, dwell.
                     Unchanged from the validated cycle; 11_standard_print.py
                     explains blow-out inside a loop at API 2.15.

TIPS are taken in rack order from tips.start_tip, one pick-up per tip group:
  per_liquid (default)     all water transfers share a tip, all dye transfers
                           share a tip, and each dilution gets its own print tip.
  new_tip_every_transfer   a fresh tip for every transfer and paper position.
A step that does not run takes no tips.

Labware whose slot is OFF_DECK is not loaded; the pre-flight refuses a run that
needs it. Release geometry and air handling are laboratory-owned and come from
configs/machines/ot2_standard_printing_p20_v1.yaml.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML, not a generated protocol.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "AI Agent Dilution + Paper Print Demo (P20-only, OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "P20-only: dilute one sample in one solvent across a fold series, then print "
        "each dilution onto paper at the configured droplet volume."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = True
DEFAULT_DO_DILUTION = True
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>>
CONFIG = {
    "protocol_version": 19,
    "deck": {
        "tuberack": {
            "slot": 7,
            "load_name": "tuberack_3dprint_20ml_8vials_v2",
            "namespace": "custom_beta",
            "version": 1,
        },
        "plate": {
            "slot": 4,
            "load_name": "corning_96_wellplate_360ul_custom",
            "namespace": "custom_beta",
            "version": 1,
        },
        "paper": {
            "slot": 5,
            "load_name": "paper_print_96_flat",
            "namespace": "custom_beta",
            "version": 1,
        },
        "tiprack": {"slot": 9, "load_name": "opentrons_96_tiprack_20ul"},
    },
    "pipette": {"name": "p20_single_gen2", "mount": "left"},
    # role: "solvent" = diluent, "sample" = the material being diluted.
    "materials": {
        "water": {"role": "solvent", "vial": "A1", "aspirate_height_mm": 4.0},
        "dye": {"role": "sample", "vial": "A2", "aspirate_height_mm": 4.0},
    },
    "dilution": {
        "enabled": True,
        "plate_column": "11",
        "start_row": "A",
        "factors": [1, 2, 3, 4, 6, 8, 12, 16],
        "total_volume_ul": 150.0,
        "max_transfer_ul": 20.0,
        "solvent_dispense_from_top_mm": -2.0,
        "sample_dispense_from_top_mm": -1.0,
        "blow_out_after_dispense": True,
    },
    "mixing": {"reps": 2, "volume_ul": 15.0, "height_mm": 2.0},
    "print": {
        "enabled": True,
        "droplet_volume_ul": 5.0,
        "droplets_per_spot": 1,
        "replicates": 1,
        "paper_start_column": 1,
        "paper_columns": 12,
        # --- laboratory-owned release geometry; see the machine profile ---------
        "z_mm": 1.1,
        "aspirate_height_mm": 1.0,
        "air_gap_ul": 1.5,
        "air_gap_height_mm": 5.0,
        "push_out_ul": 3.0,
        "blow_out": True,
        "post_dispense_delay_s": 2.0,
    },
    "tips": {"start_tip": "A1", "return_tips": False, "policy": "per_liquid"},
    "flow_rates": {"aspirate": 3.0, "dispense": 3.0},
    "safety": {
        "expected_tuberack_load_name": "tuberack_3dprint_20ml_8vials_v2",
        "expected_well_count": 8,
        "p20_max_volume_ul": 20.0,
        "p20_min_volume_ul": 1.0,
        "max_well_fill_ul": 340.0,
    },
}
# <<< CONFIG END <<<


ROWS = tuple("ABCDEFGH")
EPSILON_UL = 0.01
OFF_DECK = "OFF_DECK"
LABWARE_ROLES = ("tuberack", "plate", "paper", "tiprack")
TIP_POLICIES = ("per_liquid", "new_tip_every_transfer")
TIP_ORDER = tuple(f"{row}{column}" for column in range(1, 13) for row in ROWS)


def _on_deck(spec):
    slot = spec.get("slot")
    return not (isinstance(slot, str) and slot.strip().upper() == OFF_DECK)


def _load_labware(protocol, spec):
    kwargs = {}
    if spec.get("namespace"):
        kwargs["namespace"] = spec["namespace"]
    if spec.get("version") is not None:
        kwargs["version"] = int(spec["version"])
    return protocol.load_labware(spec["load_name"], str(spec["slot"]), **kwargs)


def _factors():
    return [float(value) for value in CONFIG["dilution"]["factors"]]


def _droplet_volumes():
    """print.droplet_volume_ul as a list, whether it was written as one or not."""
    raw = CONFIG["print"]["droplet_volume_ul"]
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return [float(value) for value in values]


def _steps():
    do_dilution = bool(DEFAULT_DO_DILUTION and CONFIG["dilution"].get("enabled", True))
    do_print = bool(DEFAULT_DO_PRINT and CONFIG["print"].get("enabled", True))
    return do_dilution, do_print


def _required_roles(do_dilution, do_print):
    roles = set()
    if do_dilution or do_print:
        roles.update(("plate", "tiprack"))
    if do_dilution:
        roles.add("tuberack")
    if do_print:
        roles.add("paper")
    return roles


def _deck_errors(do_dilution, do_print):
    """Deck problems that must stop the run before any labware is loaded."""
    errors = []
    if not do_dilution and not do_print:
        errors.append("the dilution and print steps are both off; nothing to run")
    required = _required_roles(do_dilution, do_print)
    slots = {}
    for role in LABWARE_ROLES:
        spec = CONFIG["deck"][role]
        if not _on_deck(spec):
            if role in required:
                errors.append(f"deck.{role} is OFF_DECK, but this run needs it")
            continue
        try:
            slot = int(spec["slot"])
        except (TypeError, ValueError):
            errors.append(f"deck.{role}.slot must be 1-11 or OFF_DECK, got {spec.get('slot')!r}")
            continue
        if not 1 <= slot <= 11:
            errors.append(f"deck.{role}.slot must be 1-11 (12 is the trash), got {slot}")
        if slot in slots:
            errors.append(f"deck slot {slot} holds both {slots[slot]} and {role}")
        slots[slot] = role
    return errors


def _split_volume(total_ul, max_transfer_ul, minimum_ul):
    """P20-sized transfers; a sub-minimum remainder is rebalanced with the previous one."""
    remaining = float(total_ul)
    chunks = []
    while remaining > EPSILON_UL:
        chunk = min(float(max_transfer_ul), remaining)
        chunks.append(round(chunk, 2))
        remaining = round(remaining - chunk, 6)
    if len(chunks) >= 2 and chunks[-1] < float(minimum_ul):
        pair = chunks[-2] + chunks[-1]
        first = round(pair / 2.0, 2)
        chunks[-2:] = [first, round(pair - first, 2)]
    return chunks


def _material_by_role(role):
    for name, spec in CONFIG["materials"].items():
        if spec.get("role") == role:
            return str(spec.get("label") or name), spec
    raise RuntimeError(f"no material with role {role!r} in CONFIG['materials']")


def _plan_paper_layout(paper_columns_available):
    """One paper column per (droplet volume x replicate), left to right.

    Columns start at print.paper_start_column and run consecutively. Spots past the
    paper's width are reported and skipped rather than aborting the run.
    """
    pr = CONFIG["print"]
    budget = min(int(pr.get("paper_columns", paper_columns_available)),
                 int(paper_columns_available))
    start = int(pr.get("paper_start_column", 1))
    replicates = int(pr.get("replicates", 1))
    droplets = int(pr.get("droplets_per_spot", 1))

    spots = []
    column = start
    for volume in _droplet_volumes():
        for replicate in range(1, replicates + 1):
            spots.append({"column": column, "volume_ul": float(volume),
                          "droplets": droplets, "replicate": replicate})
            column += 1
    placed = [spot for spot in spots if 1 <= spot["column"] <= budget]
    skipped = [spot for spot in spots if not 1 <= spot["column"] <= budget]
    return placed, skipped


def _build_operations(rows, factors, placed, do_dilution, do_print):
    """Every tip-bearing operation in execution order, each with its tip group.

    Mirrors src/agents/dye_demo/plan.py::build_operations, which the operator's
    summary is rendered from.
    """
    dilution = CONFIG["dilution"]
    policy = str(CONFIG["tips"].get("policy", "per_liquid"))
    minimum = float(CONFIG["safety"].get("p20_min_volume_ul", 1.0))
    max_transfer = float(dilution["max_transfer_ul"])
    column = str(dilution["plate_column"])
    total = float(dilution["total_volume_ul"])
    operations = []
    if do_dilution:
        for role, height_key in (("solvent", "solvent_dispense_from_top_mm"),
                                 ("sample", "sample_dispense_from_top_mm")):
            for row, factor in zip(rows, factors):
                sample_ul = total / factor
                volume = total - sample_ul if role == "solvent" else sample_ul
                if volume <= EPSILON_UL:
                    continue
                well = f"{row}{column}"
                chunks = _split_volume(volume, max_transfer, minimum)
                for index, chunk in enumerate(chunks, start=1):
                    operations.append({
                        "kind": "transfer",
                        "group": role if policy == "per_liquid" else f"{role}:{well}:{index}",
                        "role": role, "well": well, "factor": factor, "total_ul": volume,
                        "volume_ul": chunk, "chunk": index, "chunks": len(chunks),
                        "from_top_mm": float(dilution[height_key]),
                    })
    if do_print:
        for row, factor in zip(rows, factors):
            for spot in placed:
                paper_well = f"{row}{spot['column']}"
                operations.append({
                    "kind": "print",
                    "group": f"print:{row}" if policy == "per_liquid" else f"print:{paper_well}",
                    "row": row, "source": f"{row}{column}", "paper_well": paper_well,
                    "factor": factor, "column": spot["column"],
                    "volume_ul": spot["volume_ul"], "droplets": spot["droplets"],
                })
    return operations


def _tip_groups(operations):
    groups = []
    for operation in operations:
        if not groups or groups[-1] != operation["group"]:
            groups.append(operation["group"])
    return groups


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _preflight(protocol, labware, p20, do_dilution, do_print):
    errors = []
    dilution = CONFIG["dilution"]
    safety = CONFIG["safety"]
    pr = CONFIG["print"]
    mixing = CONFIG["mixing"]
    p20_max = float(safety["p20_max_volume_ul"])
    p20_min = float(safety.get("p20_min_volume_ul", 1.0))

    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    for role in ("solvent", "sample"):
        matches = [n for n, s in CONFIG["materials"].items() if s.get("role") == role]
        if len(matches) != 1:
            errors.append(f"exactly one material must have role {role!r}, got {matches}")

    if "tuberack" in labware:
        tuberack = labware["tuberack"]
        if tuberack.load_name != safety["expected_tuberack_load_name"]:
            errors.append(
                f"tuberack is {tuberack.load_name!r}; expected "
                f"{safety['expected_tuberack_load_name']!r}"
            )
        if len(tuberack.wells()) != int(safety["expected_well_count"]):
            errors.append(
                f"tuberack has {len(tuberack.wells())} wells; expected "
                f"{safety['expected_well_count']}"
            )
        for name, spec in CONFIG["materials"].items():
            if spec["vial"] not in tuberack.wells_by_name():
                errors.append(f"{name} vial {spec['vial']} is absent from the rack")

    factors = _factors()
    if not 1 <= len(factors) <= len(ROWS):
        errors.append(f"1 to {len(ROWS)} dilutions are possible, got {len(factors)}")
    if any(factor < 1 for factor in factors):
        errors.append("dilution factors must be 1x or greater")
    start_row = str(dilution.get("start_row", "A")).upper()
    rows = []
    if start_row not in ROWS:
        errors.append(f"dilution.start_row must be one of {''.join(ROWS)}, got {start_row!r}")
    elif ROWS.index(start_row) + len(factors) > len(ROWS):
        errors.append(f"{len(factors)} dilutions starting at row {start_row} run past row H")
    else:
        rows = list(ROWS[ROWS.index(start_row):ROWS.index(start_row) + len(factors)])

    total = float(dilution["total_volume_ul"])
    if total > float(safety["max_well_fill_ul"]):
        errors.append(f"total volume {total:.2f} uL exceeds safe well fill {safety['max_well_fill_ul']:.2f} uL")
    max_transfer = float(dilution["max_transfer_ul"])
    if not 0 < max_transfer <= p20_max:
        errors.append(f"dilution.max_transfer_ul must be in (0, {p20_max:g}]")
    if do_dilution:
        for factor in factors:
            if factor < 1:
                continue
            sample, solvent = total / factor, total - total / factor
            if sample < p20_min:
                errors.append(f"{factor:g}x needs {sample:.2f} uL of sample, below the P20's {p20_min:g} uL minimum")
            if EPSILON_UL < solvent < p20_min:
                errors.append(f"{factor:g}x needs {solvent:.2f} uL of solvent, below the P20's {p20_min:g} uL minimum")

    if str(CONFIG["tips"].get("policy", "per_liquid")) not in TIP_POLICIES:
        errors.append(f"tips.policy must be one of {TIP_POLICIES}")

    placed, skipped = [], []
    if do_print:
        if not 0 < float(mixing["volume_ul"]) <= p20_max:
            errors.append(f"mixing.volume_ul must be in (0, {p20_max:g}]")
        air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
        if air_gap < 0:
            errors.append("print.air_gap_ul must be >= 0")
        for volume in _droplet_volumes():
            if volume < p20_min:
                errors.append(f"droplet volume {volume:g} uL is below the P20's {p20_min:g} uL minimum")
            if volume + air_gap > p20_max:
                errors.append(f"droplet {volume:g} uL + air gap {air_gap:g} uL exceeds the P20's {p20_max:g} uL")
        if int(pr.get("replicates", 1)) < 1:
            errors.append("print.replicates must be >= 1")
        if int(pr.get("droplets_per_spot", 1)) < 1:
            errors.append("print.droplets_per_spot must be >= 1")
        if int(pr.get("paper_start_column", 1)) < 1:
            errors.append("print.paper_start_column must be >= 1")
        placed, skipped = _plan_paper_layout(len(labware["paper"].columns()))

    operations = _build_operations(rows, factors, placed, do_dilution, do_print)
    groups = _tip_groups(operations)
    start_tip = str(CONFIG["tips"].get("start_tip", "A1")).upper()
    tip_names = []
    if start_tip not in TIP_ORDER:
        errors.append(f"tips.start_tip {start_tip!r} is not a 96-rack position")
    else:
        first = TIP_ORDER.index(start_tip)
        tip_names = list(TIP_ORDER[first:first + len(groups)])
        if len(tip_names) < len(groups):
            errors.append(
                f"this plan needs {len(groups)} tips but only {len(tip_names)} remain from "
                f"{start_tip}; use an earlier start_tip"
            )
    rack_names = labware["tiprack"].wells_by_name()
    for tip_name in tip_names:
        if tip_name not in rack_names:
            errors.append(f"P20 tip {tip_name} is outside the tip rack")

    column = str(dilution["plate_column"])
    plate_names = labware["plate"].wells_by_name()
    paper_names = labware["paper"].wells_by_name() if "paper" in labware else {}
    for row in rows:
        if f"{row}{column}" not in plate_names:
            errors.append(f"plate well {row}{column} does not exist")
        for spot in placed:
            if f"{row}{spot['column']}" not in paper_names:
                errors.append(f"paper well {row}{spot['column']} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")

    # Soft warnings (do not abort): paper overflow, per-well liquid budget, depth.
    if skipped:
        protocol.comment(
            f"WARNING: the print plan needs {len(placed) + len(skipped)} paper columns "
            f"but only {len(placed)} fit; {len(skipped)} will be skipped."
        )
    _liquid_warnings(protocol, labware, placed, do_dilution, do_print)
    return rows, factors, placed, skipped, operations, tip_names


def _liquid_warnings(protocol, labware, placed, do_dilution, do_print):
    """Comment when a dilution well would get too shallow for the mix or aspirate height."""
    if not do_print or not placed or "plate" not in labware:
        return
    diameter = getattr(labware["plate"].wells()[0], "diameter", None)
    if not diameter:
        return
    area = math.pi * (float(diameter) / 2.0) ** 2
    prepared = CONFIG["dilution"].get("prepared_volume_ul")
    volume = (float(CONFIG["dilution"]["total_volume_ul"]) if do_dilution or prepared in (None, "")
              else float(prepared))
    if sum(spot["volume_ul"] * spot["droplets"] for spot in placed) > volume:
        protocol.comment("WARNING: printing draws more than each dilution well holds; later spots may run dry.")
    mix_ul = float(CONFIG["mixing"]["volume_ul"])
    mix_mm = float(CONFIG["mixing"].get("height_mm", 2.0))
    aspirate_mm = float(CONFIG["print"]["aspirate_height_mm"])
    for spot in placed:
        if volume < mix_ul + mix_mm * area:
            protocol.comment(f"WARNING: mixing at {mix_mm:g} mm may draw air before paper column "
                             f"{spot['column']} (~{volume:.0f} uL left per well).")
            return
        for _ in range(int(spot["droplets"])):
            if volume < float(spot["volume_ul"]) + aspirate_mm * area:
                protocol.comment(f"WARNING: aspirating at {aspirate_mm:g} mm may draw air at paper column "
                                 f"{spot['column']} (~{volume:.0f} uL left per well).")
                return
            volume -= float(spot["volume_ul"])


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _group_description(operation):
    if operation["kind"] == "transfer":
        name, _ = _material_by_role(operation["role"])
        return f"{name} -> {operation['well']}"
    return f"printing {operation['source']} -> paper {operation['paper_well']}"


def _execute(protocol, labware, p20, operations, tip_names):
    dilution, mixing, pr = CONFIG["dilution"], CONFIG["mixing"], CONFIG["print"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    blow_out_dilution = bool(dilution.get("blow_out_after_dispense", True))
    materials = {role: _material_by_role(role) for role in ("solvent", "sample")}
    z = float(pr["z_mm"])
    asp_h = float(pr["aspirate_height_mm"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
    mix_reps = int(mixing["reps"])
    mix_vol = float(mixing["volume_ul"])
    mix_h = float(mixing["height_mm"])

    group = None
    tip_index = -1
    printed_drops = 0
    for index, op in enumerate(operations):
        if op["group"] != group:
            _release_tip(p20, return_tips)
            tip_index += 1
            p20.pick_up_tip(labware["tiprack"][tip_names[tip_index]])
            protocol.comment(f"P20 tip {tip_names[tip_index]} picked for {_group_description(op)}.")
            group = op["group"]

        if op["kind"] == "transfer":
            name, material = materials[op["role"]]
            if op["role"] == "sample" and op["chunk"] == 1:
                protocol.comment(f"Diluting {op['well']} to {op['factor']:g}x ({name} {op['total_ul']:.2f} uL).")
            vial = labware["tuberack"][material["vial"]].bottom(float(material["aspirate_height_mm"]))
            destination = labware["plate"][op["well"]].top(op["from_top_mm"])
            protocol.comment(f"P20 {name} -> {op['well']}: transfer {op['chunk']} of {op['chunks']}, "
                             f"{op['volume_ul']:.2f} uL.")
            p20.aspirate(op["volume_ul"], vial)
            p20.dispense(op["volume_ul"], destination)
            if blow_out_dilution:
                # Dispensed in air above the liquid: blow out right there so the whole
                # transfer leaves the tip (default push-out on the P20 GEN2 is 0 uL).
                p20.blow_out(destination)
            following = operations[index + 1] if index + 1 < len(operations) else None
            if following is None or following["kind"] != "transfer":
                protocol.comment(f"Dilution series ready in column {dilution['plate_column']}.")
            continue

        source = labware["plate"][op["source"]]
        paper_well = labware["paper"][op["paper_well"]]
        protocol.comment(
            f"Row {op['row']} -> paper {op['paper_well']}: mix {mix_reps}x, then "
            f"{op['droplets']} x {op['volume_ul']:g} uL drop(s)."
        )
        p20.mix(mix_reps, mix_vol, source.bottom(mix_h))
        for layer in range(1, op["droplets"] + 1):
            # The physically validated print cycle (11_standard_print.py):
            # aspirate, trailing air gap, dispense everything with push-out,
            # blow out, then dwell so the drop separates from the tip.
            destination = paper_well.bottom(z)
            p20.aspirate(op["volume_ul"], source.bottom(asp_h))
            if air_gap > 0:
                p20.air_gap(air_gap, height=air_gap_height)
            piston = op["volume_ul"] + air_gap
            if push_out > 0:
                p20.dispense(piston, destination, push_out=push_out)
            else:
                p20.dispense(piston, destination)
            if blow_out:
                p20.blow_out(destination)
            if dwell > 0:
                protocol.delay(seconds=dwell)
            printed_drops += 1
            protocol.comment(f"  drop {layer}/{op['droplets']} on {op['paper_well']}")
    _release_tip(p20, return_tips)
    return printed_drops


def run(protocol: protocol_api.ProtocolContext):
    do_dilution, do_print = _steps()
    deck_errors = _deck_errors(do_dilution, do_print)
    if deck_errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(deck_errors))
    labware = {
        role: _load_labware(protocol, CONFIG["deck"][role])
        for role in LABWARE_ROLES
        if _on_deck(CONFIG["deck"][role])
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack"]]
    )

    rows, factors, placed, skipped, operations, tip_names = _preflight(
        protocol, labware, p20, do_dilution, do_print
    )
    solvent_name, solvent = _material_by_role("solvent")
    sample_name, sample = _material_by_role("sample")
    session = CONFIG.get("session") or {}

    protocol.comment("=== AI Agent Dilution -> Paper Print Demo Started ===")
    if session.get("operator"):
        protocol.comment(
            f"Operator: {session['operator']} | Session: {session.get('session_label', '')} | "
            f"Revision: {session.get('revision', '')}"
        )
    protocol.comment(
        f"Flags: dry_run={DEFAULT_DRY_RUN}, do_dilution={do_dilution}, do_print={do_print}"
    )
    off_deck = [role for role in LABWARE_ROLES if not _on_deck(CONFIG["deck"][role])]
    if off_deck:
        protocol.comment("Off deck (not loaded): " + ", ".join(off_deck))
    protocol.comment(
        f"Materials: solvent={solvent_name} (vial {solvent['vial']}), "
        f"sample={sample_name} (vial {sample['vial']})."
    )
    protocol.comment(
        "Series: "
        + ", ".join(f"{row}={factor:g}x" for row, factor in zip(rows, factors))
        + f" in plate column {CONFIG['dilution']['plate_column']}"
        + ("." if do_dilution else " (already prepared; not diluted in this run).")
    )
    if do_print:
        protocol.comment(
            "Print plan: "
            + ", ".join(
                f"col {spot['column']}={spot['volume_ul']:g} uL"
                + (f" x{spot['droplets']} drops" if spot["droplets"] > 1 else "")
                for spot in placed
            )
            + f"; mix {CONFIG['mixing']['reps']}x before each."
        )
    policy = CONFIG["tips"].get("policy", "per_liquid")
    protocol.comment(
        f"Tips: {len(tip_names)} ({policy})"
        + (f", {tip_names[0]}-{tip_names[-1]}" if tip_names else "")
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment(
            "=== AI Agent Dilution -> Paper Print Demo Completed (dry run) ==="
        )
        return

    _set_flow_rates(p20)
    printed = _execute(protocol, labware, p20, operations, tip_names)
    if do_print:
        tail = f" ({len(skipped)} column(s) skipped - paper full)" if skipped else ""
        protocol.comment(
            f"Paper print complete: {printed} drop(s) across {len(placed)} column(s) and "
            f"{len(rows)} dilution row(s){tail}."
        )
    protocol.comment("=== AI Agent Dilution -> Paper Print Demo Completed ===")
