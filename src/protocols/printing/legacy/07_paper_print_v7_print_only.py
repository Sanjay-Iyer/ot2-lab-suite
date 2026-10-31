"""
Paper print only, protocol v7 (OT-2 Python Protocol API 2.15).

PRINT-ONLY. It assumes the dilution plate column is ALREADY prepared — nothing is
diluted, no vials are touched, no solvent or sample is transferred. The P20 mixes
each source well and prints it onto the paper.

Use this to re-print from a plate you already made (e.g. by a v6 run or by hand)
without rebuilding the dilution series.

Layout: each source row of the plate column prints to the same row of the paper.
Print columns come from `print.groups`, which use the same vocabulary as v6:
  * replicates        - side-by-side paper columns for that volume
  * droplets_per_spot - drops stacked on one spot
  * paper_column      - optional, pins a group to a specific column

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML (configs/printing/*_v7.yaml), not a generated protocol.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "Paper Print Only v7 (P20, OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Print from an already-prepared 96-well plate column onto paper with the "
        "P20. No dilution, no vials."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = True
DEFAULT_DO_DILUTION = False  # v7 never dilutes; kept so build flag substitution is uniform
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>>
CONFIG = {
    "protocol_version": 7,
    "deck": {
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
        "tiprack_p20": {"slot": 9, "load_name": "opentrons_96_tiprack_20ul"},
        # Optional: declared only so the robot knows the tall rack is on the deck for
        # travel clearance. Delete this entry if the rack is not physically present.
        "tuberack": {
            "slot": 7,
            "load_name": "tuberack_3dprint_20ml_8vials_v2",
            "namespace": "custom_beta",
            "version": 1,
        },
    },
    "pipette": {"name": "p20_single_gen2", "mount": "left"},
    # The already-prepared plate column and which of its rows to print.
    "source": {
        "plate_column": "11",
        "rows": ["A", "B", "C", "D", "E", "F", "G", "H"],
    },
    "mixing": {"reps": 2, "volume_ul": 15.0, "height_mm": 2.0},
    "print": {
        "z_mm": 4.5,
        "aspirate_height_mm": 1.0,
        "blow_out": True,
        "post_dispense_delay_s": 0.3,
        "paper_columns": 12,
        "groups": [
            {"volume_ul": 5.0, "replicates": 1, "droplets_per_spot": 1,
             "paper_column": 6},
        ],
    },
    "tips": {
        "return_tips": True,
        "p20": {
            "print_by_row": {
                "A": "A1", "B": "A2", "C": "A3", "D": "A4",
                "E": "A5", "F": "A6", "G": "A7", "H": "A8",
            },
        },
    },
    "flow_rates": {"p20": {"aspirate": 3.0, "dispense": 3.0}},
    "safety": {"p20_max_volume_ul": 20.0},
}
# <<< CONFIG END <<<


def _load_labware(protocol, spec):
    kwargs = {}
    if spec.get("namespace"):
        kwargs["namespace"] = spec["namespace"]
    if spec.get("version") is not None:
        kwargs["version"] = int(spec["version"])
    return protocol.load_labware(spec["load_name"], str(spec["slot"]), **kwargs)


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _plan_paper_layout(paper_columns_available):
    """Assign paper columns to print groups (same rules as v6).

    A group with `paper_column: N` is pinned there; groups without one fill the
    lowest free columns left to right. Returns (placed, skipped, conflicts).
    """
    pr = CONFIG["print"]
    budget = min(int(pr.get("paper_columns", paper_columns_available)),
                 int(paper_columns_available))

    pinned, floating = [], []
    for index, group in enumerate(pr["groups"], start=1):
        start = group.get("paper_column")
        for replicate in range(1, int(group.get("replicates", 1)) + 1):
            spot = {
                "volume_ul": float(group["volume_ul"]),
                "droplets": int(group.get("droplets_per_spot", 1)),
                "air_gap": float(group.get("air_gap_ul", 0.0)),
                "group": index,
                "replicate": replicate,
            }
            if start is None:
                floating.append(spot)
            else:
                spot["column"] = int(start) + replicate - 1
                pinned.append(spot)

    taken, conflicts = {}, []
    for spot in pinned:
        if spot["column"] in taken:
            conflicts.append(spot["column"])
        taken[spot["column"]] = spot
    next_column = 1
    for spot in floating:
        while next_column in taken:
            next_column += 1
        spot["column"] = next_column
        taken[next_column] = spot
        next_column += 1

    ordered = sorted(taken.values(), key=lambda s: s["column"])
    placed = [s for s in ordered if s["column"] <= budget]
    skipped = [s for s in ordered if s["column"] > budget]
    return placed, skipped, sorted(set(conflicts))


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["print"]
    mixing = CONFIG["mixing"]
    safety = CONFIG["safety"]
    tip_map = CONFIG["tips"]["p20"]["print_by_row"]
    rows = list(src["rows"])

    for role, expected in (("plate", 4), ("paper", 5), ("tiprack_p20", 9)):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    if not rows:
        errors.append("source.rows must list at least one plate row")
    p20_max = float(safety["p20_max_volume_ul"])
    if not (0 < float(mixing["volume_ul"]) <= p20_max):
        errors.append("mixing.volume_ul must be in (0, 20]")
    for index, group in enumerate(pr["groups"], start=1):
        volume = float(group["volume_ul"])
        air_gap = float(group.get("air_gap_ul", 0.0))
        if not (0 < volume <= p20_max):
            errors.append(f"print group {index} volume must be in (0, {p20_max:g}]")
        if air_gap < 0:
            errors.append(f"print group {index} air_gap_ul must be >= 0")
        # The tip holds liquid + air gap at once, so the pair must fit the pipette.
        if volume + air_gap > p20_max:
            errors.append(
                f"print group {index}: volume {volume:g} + air gap {air_gap:g} = "
                f"{volume + air_gap:g} uL exceeds the P20's {p20_max:g} uL"
            )
        if int(group.get("replicates", 1)) < 1:
            errors.append(f"print group {index} replicates must be >= 1")
        if int(group.get("droplets_per_spot", 1)) < 1:
            errors.append(f"print group {index} droplets_per_spot must be >= 1")

    # One print tip per source row; each must exist and be unique.
    missing = [row for row in rows if row not in tip_map]
    if missing:
        errors.append(f"tips.p20.print_by_row is missing row(s) {missing}")
    assigned = [tip_map[row] for row in rows if row in tip_map]
    if len(assigned) != len(set(assigned)):
        errors.append(f"P20 print tips must be unique, got {assigned}")
    rack_names = labware["tiprack_p20"].wells_by_name()
    for tip_name in assigned:
        if tip_name not in rack_names:
            errors.append(f"P20 tip {tip_name} is outside the slot-9 tip rack")

    placed, skipped, conflicts = _plan_paper_layout(len(labware["paper"].columns()))
    if conflicts:
        errors.append(
            f"paper column(s) {conflicts} are claimed by more than one print group"
        )
    column = str(src["plate_column"])
    plate_names = labware["plate"].wells_by_name()
    paper_names = labware["paper"].wells_by_name()
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
    if skipped:
        protocol.comment(
            f"WARNING: print plan needs {len(placed) + len(skipped)} paper columns but "
            f"only {len(placed)} fit; {len(skipped)} spot(s) will be skipped."
        )
    return rows, placed, skipped


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _print_paper(protocol, labware, p20, rows, placed, skipped):
    src = CONFIG["source"]
    mixing = CONFIG["mixing"]
    pr = CONFIG["print"]
    tip_map = CONFIG["tips"]["p20"]["print_by_row"]
    return_tips = bool(CONFIG["tips"].get("return_tips", True))
    column = str(src["plate_column"])
    z = float(pr["z_mm"])
    asp_h = float(pr["aspirate_height_mm"])
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
    mix_reps = int(mixing["reps"])
    mix_vol = float(mixing["volume_ul"])
    mix_h = float(mixing["height_mm"])

    if skipped:
        protocol.comment(
            "UNABLE TO FINISH PRINT JOB: paper is full. Printing the "
            f"{len(placed)} spot(s) that fit and skipping {len(skipped)}."
        )

    for row in rows:
        tip_name = tip_map[row]
        p20.pick_up_tip(labware["tiprack_p20"][tip_name])
        protocol.comment(f"P20 print row {row} tip {tip_name} picked.")
        source = labware["plate"][f"{row}{column}"]
        for spot in placed:
            volume = spot["volume_ul"]
            drops = spot["droplets"]
            air_gap = spot["air_gap"]
            paper_well = labware["paper"][f"{row}{spot['column']}"]
            protocol.comment(
                f"Row {row} -> paper column {spot['column']}: mix {mix_reps}x, then "
                f"{drops} x {volume:g} uL drop(s)"
                + (f" with a {air_gap:g} uL air gap." if air_gap > 0 else ".")
            )
            p20.mix(mix_reps, mix_vol, source.bottom(mix_h))
            for _drop in range(drops):
                p20.aspirate(volume, source.bottom(asp_h))
                # Anti-drip: pull an air gap above the liquid for the trip to the
                # paper. air_gap() moves to the well top first, so the plunger is
                # also re-prepared out of the liquid.
                if air_gap > 0:
                    p20.air_gap(air_gap)
                # Push the air gap and the liquid back out together.
                p20.dispense(volume + air_gap, paper_well.bottom(z))
                if blow_out:
                    p20.blow_out(paper_well.bottom(z))
                if dwell > 0:
                    protocol.delay(seconds=dwell)
        _release_tip(p20, return_tips)

    tail = f" ({len(skipped)} spot/row skipped — paper full)" if skipped else ""
    protocol.comment(
        f"Paper print complete: {len(rows) * len(placed)} spot(s) across "
        f"{len(placed)} column(s), {len(rows)} source row(s){tail}."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {}
    for role in ("plate", "paper", "tiprack_p20"):
        labware[role] = _load_labware(protocol, deck[role])
    # Optional, unused: declared only so its height is known for travel clearance.
    if deck.get("tuberack"):
        labware["tuberack"] = _load_labware(protocol, deck["tuberack"])

    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    rows, placed, skipped = _preflight(protocol, labware, p20)

    protocol.comment("=== Paper Print Only V7 Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")
    protocol.comment(
        f"Source: plate column {CONFIG['source']['plate_column']} "
        f"(ALREADY PREPARED — nothing is diluted), rows {', '.join(rows)}."
    )
    protocol.comment(
        "Print plan: "
        + ", ".join(
            f"col {s['column']}={s['volume_ul']:g} uL"
            + (f" x{s['droplets']} drops" if s["droplets"] > 1 else "")
            for s in placed
        )
        + f"; mix {CONFIG['mixing']['reps']}x before each."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Paper Print Only V7 Completed (dry run) ===")
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_paper(protocol, labware, p20, rows, placed, skipped)
    protocol.comment("=== Paper Print Only V7 Completed ===")
