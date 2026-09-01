"""
AI agent demo: dilution series -> paper print, protocol v19 (OT-2 API 2.15).

The whole workflow on the single-channel P20 — the same instrument, tip logic and
liquid handling as 06_vial_dilution_paper_print_v6_p20only.py, with the physically
validated print-release cycle from 11_standard_print.py / the
ot2_standard_printing_p20_v1 machine profile.

What it does, in order:
  1. dilute one SAMPLE (dye stock) in one SOLVENT (water) across a fold series,
     one dilution per plate row, all in a single plate column;
  2. print every one of those dilutions onto paper, one paper row per dilution,
     one paper column per print volume x replicate.

The difference from v6 is that everything a demo audience wants to change by
talking — deck slots, how many dilutions, which plate column, which paper column,
drop volume, replicates, drops per spot — is configuration, not code. v6 hard-wired
slots 7/4/5/9 and exactly 8 dilutions; this asks only that the numbers are
physically possible.

Print-release geometry and air handling are laboratory-owned: they come from
configs/machines/ot2_standard_printing_p20_v1.yaml and must not be edited to make a
demo look better. Paper dispense height 1.1 mm and a 1.5 uL trailing air gap with a
3.0 uL push-out are the current values there.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML, not a generated protocol.
"""
from __future__ import annotations

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


DEFAULT_DRY_RUN     = False
DEFAULT_DO_DILUTION = True
DEFAULT_DO_PRINT    = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'deck': { 'tuberack': { 'slot': 7,
                          'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                          'namespace': 'custom_beta',
                          'version': 1},
            'plate': { 'slot': 4,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'materials': { 'water': {'role': 'solvent', 'vial': 'A1', 'aspirate_height_mm': 4.0},
                 'dye': {'role': 'sample', 'vial': 'A2', 'aspirate_height_mm': 4.0}},
  'dilution': { 'enabled': True,
                'plate_column': '11',
                'start_row': 'A',
                'factors': [1, 2, 3, 4, 6, 8, 12, 16],
                'total_volume_ul': 150.0,
                'max_transfer_ul': 20.0,
                'solvent_dispense_from_top_mm': -2.0,
                'sample_dispense_from_top_mm': -1.0},
  'mixing': {'reps': 2, 'volume_ul': 15.0, 'height_mm': 2.0},
  'print': { 'enabled': True,
             'droplet_volume_ul': 5.0,
             'droplets_per_spot': 1,
             'replicates': 1,
             'paper_start_column': 1,
             'paper_columns': 12,
             'z_mm': 1.1,
             'aspirate_height_mm': 1.0,
             'air_gap_ul': 1.5,
             'air_gap_height_mm': 5.0,
             'push_out_ul': 3.0,
             'blow_out': True,
             'post_dispense_delay_s': 2.0},
  'tips': {'start_tip': 'A1', 'return_tips': False},
  'flow_rates': {'aspirate': 3.0, 'dispense': 3.0},
  'safety': { 'expected_tuberack_load_name': 'tuberack_3dprint_20ml_8vials_v2',
              'expected_well_count': 8,
              'p20_max_volume_ul': 20.0,
              'p20_min_volume_ul': 1.0,
              'max_well_fill_ul': 340.0},
  'protocol_version': 19}
# <<< CONFIG END <<<


ROWS = tuple("ABCDEFGH")
EPSILON_UL = 0.01


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


def _tip_names(count):
    """`count` tip positions in rack order (A1..H1, A2..) from tips.start_tip."""
    order = [f"{row}{column}" for column in range(1, 13) for row in ROWS]
    start = str(CONFIG["tips"].get("start_tip", "A1")).upper()
    if start not in order:
        raise RuntimeError(f"tips.start_tip {start!r} is not a 96-rack position")
    return order[order.index(start) : order.index(start) + count]


def _split_volume(total_ul, max_transfer_ul):
    """Split a volume into positive P20-sized transfers without rounding drift."""
    remaining = float(total_ul)
    chunks = []
    while remaining > EPSILON_UL:
        chunk = min(float(max_transfer_ul), remaining)
        chunks.append(round(chunk, 2))
        remaining = round(remaining - chunk, 6)
    return chunks


def _material_by_role(role):
    for name, spec in CONFIG["materials"].items():
        if spec.get("role") == role:
            return name, spec
    raise RuntimeError(f"no material with role {role!r} in CONFIG['materials']")


def _plan_paper_layout(paper_columns_available):
    """One paper column per (droplet volume x replicate), left to right.

    Columns start at print.paper_start_column and run consecutively, so the whole
    layout moves with a single number. Spots past the paper's width are reported and
    skipped rather than aborting the run.
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
            spots.append({
                "column": column,
                "volume_ul": float(volume),
                "droplets": droplets,
                "replicate": replicate,
            })
            column += 1
    placed = [spot for spot in spots if 1 <= spot["column"] <= budget]
    skipped = [spot for spot in spots if not 1 <= spot["column"] <= budget]
    return placed, skipped


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    dilution = CONFIG["dilution"]
    safety = CONFIG["safety"]
    pr = CONFIG["print"]
    mixing = CONFIG["mixing"]
    p20_max = float(safety["p20_max_volume_ul"])
    p20_min = float(safety.get("p20_min_volume_ul", 1.0))

    # Deck: any addressable slot, as long as nothing is stacked on anything else.
    slots = {}
    for role in ("tuberack", "plate", "paper", "tiprack"):
        slot = int(deck[role]["slot"])
        if not 1 <= slot <= 11:
            errors.append(f"deck.{role}.slot must be 1-11 (12 is the trash), got {slot}")
        if slot in slots:
            errors.append(f"deck slot {slot} holds both {slots[slot]} and {role}")
        slots[slot] = role

    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    # Exactly one solvent and one sample must be declared.
    for role in ("solvent", "sample"):
        matches = [n for n, s in CONFIG["materials"].items() if s.get("role") == role]
        if len(matches) != 1:
            errors.append(f"exactly one material must have role {role!r}, got {matches}")

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

    # Dilution series: 1..8 rows, fitting on the plate from start_row down.
    factors = _factors()
    if not 1 <= len(factors) <= len(ROWS):
        errors.append(f"1 to {len(ROWS)} dilutions are possible, got {len(factors)}")
    if any(factor < 1 for factor in factors):
        errors.append(
            "dilution factors must be 1x or greater; below 1x would need more sample "
            "than the well holds"
        )
    start_row = str(dilution.get("start_row", "A")).upper()
    if start_row not in ROWS:
        errors.append(f"dilution.start_row must be one of {''.join(ROWS)}, got {start_row!r}")
    elif ROWS.index(start_row) + len(factors) > len(ROWS):
        errors.append(
            f"{len(factors)} dilutions starting at row {start_row} run past row H; "
            f"start higher or ask for fewer"
        )

    total = float(dilution["total_volume_ul"])
    if total > float(safety["max_well_fill_ul"]):
        errors.append(
            f"total volume {total:.2f} uL exceeds safe well fill "
            f"{safety['max_well_fill_ul']:.2f} uL"
        )
    max_transfer = float(dilution["max_transfer_ul"])
    if not 0 < max_transfer <= p20_max:
        errors.append(f"dilution.max_transfer_ul must be in (0, {p20_max:g}]")
    for factor in factors:
        if factor < 1:
            continue
        sample = total / factor
        if 0 < sample < p20_min:
            errors.append(
                f"{factor:g}x needs {sample:.2f} uL of sample, below the P20's "
                f"{p20_min:g} uL minimum; lower the fold factor or raise total_volume_ul"
            )

    if not 0 < float(mixing["volume_ul"]) <= p20_max:
        errors.append(f"mixing.volume_ul must be in (0, {p20_max:g}]")

    # Print: every droplet, plus its air gap, must fit the P20.
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    if air_gap < 0:
        errors.append("print.air_gap_ul must be >= 0")
    for volume in _droplet_volumes():
        if volume < p20_min:
            errors.append(
                f"droplet volume {volume:g} uL is below the P20's {p20_min:g} uL minimum"
            )
        if volume + air_gap > p20_max:
            errors.append(
                f"droplet {volume:g} uL + air gap {air_gap:g} uL = {volume + air_gap:g} uL "
                f"exceeds the P20's {p20_max:g} uL"
            )
    if int(pr.get("replicates", 1)) < 1:
        errors.append("print.replicates must be >= 1")
    if int(pr.get("droplets_per_spot", 1)) < 1:
        errors.append("print.droplets_per_spot must be >= 1")
    if int(pr.get("paper_start_column", 1)) < 1:
        errors.append("print.paper_start_column must be >= 1")

    # Tips: solvent + sample setup, then one print tip per dilution row.
    tip_count = 2 + len(factors)
    try:
        tip_names = _tip_names(tip_count)
    except RuntimeError as exc:
        errors.append(str(exc))
        tip_names = []
    if tip_names and len(tip_names) < tip_count:
        errors.append(
            f"this plan needs {tip_count} tips but only {len(tip_names)} remain from "
            f"{CONFIG['tips'].get('start_tip', 'A1')}; use an earlier start_tip"
        )
    rack_names = labware["tiprack"].wells_by_name()
    for tip_name in tip_names:
        if tip_name not in rack_names:
            errors.append(f"P20 tip {tip_name} is outside the tip rack")

    # Wells: every plate and paper well the plan touches must exist.
    placed, skipped = _plan_paper_layout(len(labware["paper"].columns()))
    column = str(dilution["plate_column"])
    plate_names = labware["plate"].wells_by_name()
    paper_names = labware["paper"].wells_by_name()
    if start_row in ROWS:
        rows = list(ROWS[ROWS.index(start_row) : ROWS.index(start_row) + len(factors)])
    else:
        rows = []
    for row in rows:
        if f"{row}{column}" not in plate_names:
            errors.append(f"plate well {row}{column} does not exist")
        for spot in placed:
            well = f"{row}{spot['column']}"
            if well not in paper_names:
                errors.append(f"paper well {well} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")

    # Soft warnings (do not abort): paper overflow, and per-well liquid budget.
    if skipped:
        protocol.comment(
            f"WARNING: the print plan needs {len(placed) + len(skipped)} paper columns "
            f"but only {len(placed)} fit; {len(skipped)} will be skipped."
        )
    per_well_draw = sum(spot["volume_ul"] * spot["droplets"] for spot in placed)
    if per_well_draw > total:
        protocol.comment(
            f"WARNING: printing draws ~{per_well_draw:g} uL per dilution well but each "
            f"holds only {total:g} uL; later spots on a well may run dry."
        )
    return rows, factors, tip_names, placed, skipped


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _transfer(protocol, p20, volume_ul, source, destination, *, label):
    """One P20 transfer, split into <=20 uL chunks. Vial -> well, no blow-out."""
    for index, chunk in enumerate(
        _split_volume(volume_ul, CONFIG["dilution"]["max_transfer_ul"]), start=1
    ):
        protocol.comment(f"P20 {label}: chunk {index} of {chunk:.2f} uL.")
        p20.aspirate(chunk, source)
        p20.dispense(chunk, destination)


def _prepare_dilutions(protocol, labware, p20, rows, factors, tip_names):
    dilution = CONFIG["dilution"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    total = float(dilution["total_volume_ul"])
    column = str(dilution["plate_column"])
    wells = [labware["plate"][f"{row}{column}"] for row in rows]

    solvent_name, solvent = _material_by_role("solvent")
    sample_name, sample = _material_by_role("sample")
    solvent_vial = labware["tuberack"][solvent["vial"]].bottom(
        float(solvent["aspirate_height_mm"]))
    sample_vial = labware["tuberack"][sample["vial"]].bottom(
        float(sample["aspirate_height_mm"]))

    # Solvent into every well first (shared tip: it only ever goes solvent -> well).
    p20.pick_up_tip(labware["tiprack"][tip_names[0]])
    protocol.comment(f"Solvent ({solvent_name}) setup tip {tip_names[0]} picked.")
    for well, factor in zip(wells, factors):
        solvent_vol = total - (total / factor)
        if solvent_vol <= EPSILON_UL:
            continue
        _transfer(
            protocol, p20, solvent_vol, solvent_vial,
            well.top(float(dilution["solvent_dispense_from_top_mm"])),
            label=f"{solvent_name} -> {well.well_name}",
        )
    _release_tip(p20, return_tips)
    protocol.comment("Solvent transfers done.")

    # Sample into every well (shared tip: only ever sample vial -> well, dispensed
    # from above the liquid so nothing carries back into the sample vial).
    p20.pick_up_tip(labware["tiprack"][tip_names[1]])
    protocol.comment(f"Sample ({sample_name}) setup tip {tip_names[1]} picked.")
    for well, factor in zip(wells, factors):
        sample_vol = total / factor
        if sample_vol <= EPSILON_UL:
            continue
        protocol.comment(
            f"Diluting {well.well_name} to {factor:g}x "
            f"({sample_name} {sample_vol:.2f} uL)."
        )
        _transfer(
            protocol, p20, sample_vol, sample_vial,
            well.top(float(dilution["sample_dispense_from_top_mm"])),
            label=f"{sample_name} -> {well.well_name}",
        )
    _release_tip(p20, return_tips)
    protocol.comment(f"Sample transfers done. Dilution series ready in column {column}.")


def _print_paper(protocol, labware, p20, rows, tip_names, placed, skipped):
    dilution = CONFIG["dilution"]
    mixing = CONFIG["mixing"]
    pr = CONFIG["print"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    column = str(dilution["plate_column"])
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

    if skipped:
        protocol.comment(
            "UNABLE TO FINISH PRINT JOB: paper is full. Printing the "
            f"{len(placed)} spot(s) that fit and skipping {len(skipped)} "
            "(lower print.paper_start_column or replicates, or use wider paper)."
        )

    # One fresh tip per dilution row: two concentrations must never share a tip.
    for index, row in enumerate(rows):
        tip_name = tip_names[2 + index]
        p20.pick_up_tip(labware["tiprack"][tip_name])
        protocol.comment(f"P20 print row {row} tip {tip_name} picked.")
        source = labware["plate"][f"{row}{column}"]
        for spot in placed:
            volume = spot["volume_ul"]
            paper_well = labware["paper"][f"{row}{spot['column']}"]
            protocol.comment(
                f"Row {row} -> paper column {spot['column']}: mix {mix_reps}x, then "
                f"{spot['droplets']} x {volume:g} uL drop(s)."
            )
            p20.mix(mix_reps, mix_vol, source.bottom(mix_h))
            for layer in range(1, spot["droplets"] + 1):
                # The physically validated print cycle (11_standard_print.py):
                # aspirate, trailing air gap, dispense everything with push-out,
                # blow out, then dwell so the drop separates from the tip.
                destination = paper_well.bottom(z)
                p20.aspirate(volume, source.bottom(asp_h))
                if air_gap > 0:
                    p20.air_gap(air_gap, height=air_gap_height)
                piston = volume + air_gap
                if push_out > 0:
                    p20.dispense(piston, destination, push_out=push_out)
                else:
                    p20.dispense(piston, destination)
                if blow_out:
                    p20.blow_out(destination)
                if dwell > 0:
                    protocol.delay(seconds=dwell)
                protocol.comment(
                    f"  drop {layer}/{spot['droplets']} on {paper_well.well_name}"
                )
        _release_tip(p20, return_tips)

    printed = len(rows) * sum(spot["droplets"] for spot in placed)
    tail = f" ({len(skipped)} column(s) skipped - paper full)" if skipped else ""
    protocol.comment(
        f"Paper print complete: {printed} drop(s) across {len(placed)} column(s) and "
        f"{len(rows)} dilution row(s){tail}."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("tuberack", "plate", "paper", "tiprack")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack"]]
    )

    rows, factors, tip_names, placed, skipped = _preflight(protocol, labware, p20)
    solvent_name, solvent = _material_by_role("solvent")
    sample_name, sample = _material_by_role("sample")

    protocol.comment("=== AI Agent Dilution -> Paper Print Demo Started ===")
    protocol.comment(
        f"Flags: dry_run={DEFAULT_DRY_RUN}, do_dilution={DEFAULT_DO_DILUTION}, "
        f"do_print={DEFAULT_DO_PRINT}"
    )
    protocol.comment(
        f"Materials: solvent={solvent_name} (vial {solvent['vial']}), "
        f"sample={sample_name} (vial {sample['vial']})."
    )
    protocol.comment(
        "Series: "
        + ", ".join(f"{row}={factor:g}x" for row, factor in zip(rows, factors))
        + f" in plate column {CONFIG['dilution']['plate_column']}."
    )
    protocol.comment(
        "Print plan: "
        + ", ".join(
            f"col {spot['column']}={spot['volume_ul']:g} uL"
            + (f" x{spot['droplets']} drops" if spot["droplets"] > 1 else "")
            for spot in placed
        )
        + f"; mix {CONFIG['mixing']['reps']}x before each."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment(
            "=== AI Agent Dilution -> Paper Print Demo Completed (dry run) ==="
        )
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_DILUTION and CONFIG["dilution"].get("enabled", True):
        _prepare_dilutions(protocol, labware, p20, rows, factors, tip_names)
    if DEFAULT_DO_PRINT and CONFIG["print"].get("enabled", True):
        _print_paper(protocol, labware, p20, rows, tip_names, placed, skipped)
    protocol.comment("=== AI Agent Dilution -> Paper Print Demo Completed ===")
