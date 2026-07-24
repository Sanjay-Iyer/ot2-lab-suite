"""
Sample dilution -> paper print, protocol v6 (OT-2 Python Protocol API 2.15).

The WHOLE workflow on the single-channel P20 — no P300. One sample is diluted in
one solvent across a series of fold factors in a plate column, and each dilution is
printed onto paper at several volumes. Before every print aspiration the P20 mixes
the source well (2 up-down cycles by default) so the sample stays suspended.

Materials carry a ROLE flag so future configs can swap what is being diluted and
what it is diluted in without touching this code:
  * role "solvent"  -> the diluent (here: water)
  * role "sample"   -> the thing being diluted (here: the nanoparticle stock)

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML (configs/printing/*_v6.yaml), not a generated protocol.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "Sample Dilution to Paper Print v6 (P20-only, OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "P20-only: dilute one sample in one solvent across a fold series, mix, and "
        "print each dilution onto paper at 20/15/10/5 uL."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = True
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
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'materials': { 'water': {'role': 'solvent', 'vial': 'A1', 'aspirate_height_mm': 4.0},
                 'nanoparticle': {'role': 'sample', 'vial': 'A2', 'aspirate_height_mm': 4.0}},
  'dilution': { 'enabled': True,
                'plate_column': '12',
                'total_volume_ul': 150.0,
                'factors': [1, 2, 5, 8, 10, 15, 20, 50],
                'max_transfer_ul': 20.0,
                'solvent_dispense_from_top_mm': -2.0,
                'sample_dispense_from_top_mm': -1.0},
  'mixing': {'reps': 2, 'volume_ul': 15.0, 'height_mm': 2.0},
  'print': { 'z_mm': 4.5,
             'aspirate_height_mm': 1.0,
             'blow_out': True,
             'post_dispense_delay_s': 0.3,
             'paper_columns': 12,
             'groups': [ { 'volume_ul': 20.0,
                           'replicates': 1,
                           'droplets_per_spot': 1,
                           'air_gap_ul': 2.0},
                         { 'volume_ul': 15.0,
                           'replicates': 1,
                           'droplets_per_spot': 1,
                           'air_gap_ul': 0.0},
                         { 'volume_ul': 10.0,
                           'replicates': 1,
                           'droplets_per_spot': 1,
                           'air_gap_ul': 0.0},
                         { 'volume_ul': 5.0,
                           'replicates': 1,
                           'droplets_per_spot': 1,
                           'air_gap_ul': 0.0},
                         { 'volume_ul': 5.0,
                           'replicates': 1,
                           'droplets_per_spot': 3,
                           'paper_column': 6}]},
  'tips': { 'return_tips': True,
            'p20': { 'solvent_setup': 'A1',
                     'sample_setup': 'A2',
                     'print_by_row': { 'A': 'A3',
                                       'B': 'A4',
                                       'C': 'A5',
                                       'D': 'A6',
                                       'E': 'A7',
                                       'F': 'A8',
                                       'G': 'A9',
                                       'H': 'A10'}}},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': { 'expected_tuberack_load_name': 'tuberack_3dprint_20ml_8vials_v2',
              'expected_well_count': 8,
              'p20_max_volume_ul': 20.0,
              'max_well_fill_ul': 340.0},
  'protocol_version': 6}
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


def _resolve_factors(dilution):
    return [float(value) for value in dilution["factors"]]


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
    """Assign paper columns to print groups.

    A group may pin itself with `paper_column: N` (it then occupies N, N+1, ... for
    its replicates). Groups without it float and fill the lowest still-free columns,
    left to right, in group order — so the common case needs no column bookkeeping,
    but a specific column can be targeted when you want one.

    Returns (placed, skipped, conflicts): `placed` spots carry a concrete 1-based
    `column` and fit on the paper, `skipped` spots fall past the paper's width (the
    caller prints what fits and reports the rest), and `conflicts` lists columns
    claimed by more than one group (a config error).
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
    tips = CONFIG["tips"]["p20"]
    pr = CONFIG["print"]
    mixing = CONFIG["mixing"]

    for role, expected in (("tuberack", 7), ("plate", 4), ("paper", 5), ("tiprack_p20", 9)):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")

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

    factors = _resolve_factors(dilution)
    if len(factors) != len(ROWS):
        errors.append(f"v6 expects {len(ROWS)} dilution factors, got {len(factors)}")
    total = float(dilution["total_volume_ul"])
    if total > float(safety["max_well_fill_ul"]):
        errors.append(
            f"total volume {total:.2f} uL exceeds safe well fill "
            f"{safety['max_well_fill_ul']:.2f} uL"
        )
    max_transfer = float(dilution["max_transfer_ul"])
    if max_transfer > float(safety["p20_max_volume_ul"]):
        errors.append("dilution.max_transfer_ul exceeds the P20's 20 uL")
    for factor in factors:
        if factor <= 0:
            errors.append("dilution factors must be positive")
            continue
        sample = total / factor
        solvent = total - sample
        if any(chunk > 20.0 for chunk in _split_volume(sample, max_transfer)):
            errors.append(f"sample transfer chunk exceeds 20 uL at {factor:g}x")
        if any(chunk > 20.0 for chunk in _split_volume(solvent, max_transfer)):
            errors.append(f"solvent transfer chunk exceeds 20 uL at {factor:g}x")

    if not (0 < float(mixing["volume_ul"]) <= float(safety["p20_max_volume_ul"])):
        errors.append("mixing.volume_ul must be in (0, 20]")

    # Print groups: volumes within the P20, positive replicate/droplet counts.
    p20_max = float(safety["p20_max_volume_ul"])
    for index, group in enumerate(pr["groups"], start=1):
        volume = float(group["volume_ul"])
        air_gap = float(group.get("air_gap_ul", 0.0))
        if volume > p20_max:
            errors.append(f"print group {index} volume {volume:g} exceeds 20 uL")
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

    # Tip plan: solvent + sample setup tips, plus one print tip per dilution row.
    assigned = [tips["solvent_setup"], tips["sample_setup"]]
    if set(tips["print_by_row"]) != set(ROWS):
        errors.append("tips.p20.print_by_row must map rows A through H")
    assigned.extend(tips["print_by_row"][row] for row in ROWS)
    if len(assigned) != len(set(assigned)):
        errors.append(f"P20 tip assignments must be unique, got {assigned}")
    rack_names = labware["tiprack_p20"].wells_by_name()
    for tip_name in assigned:
        if tip_name not in rack_names:
            errors.append(f"P20 tip {tip_name} is outside the slot-9 tip rack")

    # Paper layout: assign columns and confirm every PLACED spot exists. Spots that
    # overflow the paper are NOT errors — they are printed as far as they fit and
    # reported at run time (graceful degradation).
    placed, skipped, conflicts = _plan_paper_layout(len(labware["paper"].columns()))
    if conflicts:
        errors.append(
            f"paper column(s) {conflicts} are claimed by more than one print group; "
            f"change a group's paper_column or its replicates"
        )
    column = str(dilution["plate_column"])
    plate_names = labware["plate"].wells_by_name()
    paper_names = labware["paper"].wells_by_name()
    for row in ROWS:
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
            f"WARNING: print plan needs {len(placed) + len(skipped)} paper columns but "
            f"only {len(placed)} fit; {len(skipped)} spot(s) will be skipped."
        )
    per_well_draw = sum(s["volume_ul"] * s["droplets"] for s in placed)
    if per_well_draw > total:
        protocol.comment(
            f"WARNING: printing draws ~{per_well_draw:g} uL per dilution well but each "
            f"holds only {total:g} uL; later spots on a well may run dry."
        )
    return factors, placed, skipped


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _transfer(protocol, p20, volume_ul, source, destination, *, label):
    """One P20 transfer, split into <=20 uL chunks. Vial/well -> well, no blow-out."""
    for index, chunk in enumerate(
        _split_volume(volume_ul, CONFIG["dilution"]["max_transfer_ul"]), start=1
    ):
        protocol.comment(f"P20 {label}: chunk {index} of {chunk:.2f} uL.")
        p20.aspirate(chunk, source)
        p20.dispense(chunk, destination)


def _prepare_dilutions(protocol, labware, p20, factors):
    dilution = CONFIG["dilution"]
    tips = CONFIG["tips"]["p20"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    total = float(dilution["total_volume_ul"])
    column = str(dilution["plate_column"])
    wells = [labware["plate"][f"{row}{column}"] for row in ROWS]

    solvent_name, solvent = _material_by_role("solvent")
    sample_name, sample = _material_by_role("sample")
    solvent_vial = labware["tuberack"][solvent["vial"]].bottom(
        float(solvent["aspirate_height_mm"]))
    sample_vial = labware["tuberack"][sample["vial"]].bottom(
        float(sample["aspirate_height_mm"]))

    # Solvent into every well first (shared tip: it only ever goes solvent -> well).
    p20.pick_up_tip(labware["tiprack_p20"][tips["solvent_setup"]])
    protocol.comment(f"Solvent ({solvent_name}) setup tip {tips['solvent_setup']} picked.")
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
    p20.pick_up_tip(labware["tiprack_p20"][tips["sample_setup"]])
    protocol.comment(f"Sample ({sample_name}) setup tip {tips['sample_setup']} picked.")
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


def _print_paper(protocol, labware, p20, placed, skipped):
    dilution = CONFIG["dilution"]
    mixing = CONFIG["mixing"]
    pr = CONFIG["print"]
    tips = CONFIG["tips"]["p20"]["print_by_row"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    column = str(dilution["plate_column"])
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
            f"{len(placed)} spot(s) that fit and skipping {len(skipped)} "
            "(raise print.paper_columns, lower replicates, or use a wider paper)."
        )

    for row in ROWS:
        tip_name = tips[row]
        p20.pick_up_tip(labware["tiprack_p20"][tip_name])
        protocol.comment(f"P20 print row {row} tip {tip_name} picked.")
        source = labware["plate"][f"{row}{column}"]
        for spot in placed:
            volume = spot["volume_ul"]
            drops = spot["droplets"]
            air_gap = spot["air_gap"]
            paper_well = labware["paper"][f"{row}{spot['column']}"]
            # One printing step = mix the source, then lay down `drops` drop(s) on the
            # same paper spot (droplets_per_spot). Default drops=1 is a single drop.
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

    printed = len(ROWS) * len(placed)
    tail = f" ({len(skipped)} spot/row skipped — paper full)" if skipped else ""
    protocol.comment(
        f"Paper print complete: {printed} spot(s) across {len(placed)} column(s), "
        f"{len(ROWS)} dilution rows{tail}."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("tuberack", "plate", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    factors, placed, skipped = _preflight(protocol, labware, p20)
    solvent_name, solvent = _material_by_role("solvent")
    sample_name, sample = _material_by_role("sample")

    protocol.comment("=== Sample Dilution -> Paper Print V6 Started ===")
    protocol.comment(
        f"Flags: dry_run={DEFAULT_DRY_RUN}, do_dilution={DEFAULT_DO_DILUTION}, "
        f"do_print={DEFAULT_DO_PRINT}"
    )
    protocol.comment(
        f"Materials: solvent={solvent_name} (vial {solvent['vial']}), "
        f"sample={sample_name} (vial {sample['vial']})."
    )
    protocol.comment(
        "Dilutions "
        + ", ".join(f"{f:g}x" for f in factors)
        + f" in plate column {CONFIG['dilution']['plate_column']}."
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
        protocol.comment("=== Sample Dilution -> Paper Print V6 Completed (dry run) ===")
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_DILUTION and CONFIG["dilution"].get("enabled", True):
        _prepare_dilutions(protocol, labware, p20, factors)
    if DEFAULT_DO_PRINT:
        _print_paper(protocol, labware, p20, placed, skipped)
    protocol.comment("=== Sample Dilution -> Paper Print V6 Completed ===")
