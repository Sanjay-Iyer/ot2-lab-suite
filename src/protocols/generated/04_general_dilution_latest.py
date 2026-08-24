"""General dilution - deterministic executor (P20, API 2.15).

Dilution ONLY. This never prints and never touches the paper: run a printing
workflow separately afterwards if you want to print what you just made.

    configs/generated/current_dilution.yaml   <- or any dilution config
         |  build (validate + flatten with the referenced machine profile)
         v
    this file (CONFIG block regenerated in place)
         |
         v
    src/protocols/generated/04_general_dilution_latest.py  <- uploaded

    python scripts/run_printing_experiment_robot.py dilution

Two modes, both driven entirely by YAML:

  mode: single
      stock_volume_ul from the stock well + diluent_volume_ul from the diluent
      well into EACH destination well, then mix.

  mode: series
      every destination well gets diluent_volume_ul; the first also gets
      stock_volume_ul from the stock well; then transfer_volume_ul is carried
      from each well into the next, mixing at every step.

Every transfer larger than the P20 is split into chunks of transfer_chunk_ul.
The trailing air gap is trimmed to whatever still fits alongside the liquid, so
a full 20 uL transfer simply carries no gap rather than overfilling the pipette.

Tips: pipette_tip_reuse true keeps one tip per distinct source well; false takes
a fresh tip for every transfer and every mix.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "General Dilution (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Config-driven single or serial dilution between any registered source "
        "and destination labware. No printing."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'add_dye',
  'deck': { 'stock': { 'slot': 7,
                       'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                       'namespace': 'custom_beta',
                       'version': 1},
            'diluent': { 'slot': 7,
                         'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                         'namespace': 'custom_beta',
                         'version': 1},
            'destination': { 'slot': 1,
                             'load_name': 'brand_96_wellplate_350ul_flat_781662',
                             'namespace': 'custom_beta',
                             'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'stock': {'well': 'A2', 'material': 'concentrated dye', 'aspirate_height_mm': 4.0},
  'diluent': {'well': 'A1', 'material': 'water (unused, 0 uL)', 'aspirate_height_mm': 4.0},
  'destination': {'wells': ['A11'], 'dispense_height_mm': 2.0, 'aspirate_height_mm': 0.5},
  'dilution': { 'mode': 'single',
                'stock_volume_ul': 200.0,
                'diluent_volume_ul': 0.0,
                'transfer_volume_ul': 20.0,
                'transfer_chunk_ul': 18.0,
                'mix_cycles': 0,
                'mix_volume_ul': 15.0},
  'liquid_handling': {'air_gap_ul': 1.5, 'air_gap_height_mm': 5.0},
  'tips': {'start_tip': 'A1', 'return_tips': True, 'pipette_tip_reuse': True},
  'flow_rates': {'p20': {'aspirate_ul_s': 3.0, 'dispense_ul_s': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0}}
# <<< CONFIG END <<<


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _load_deck(protocol):
    """Load each distinct deck slot once; roles may share one labware."""
    by_slot = {}
    labware = {}
    for role, spec in CONFIG["deck"].items():
        slot = str(spec["slot"])
        if slot not in by_slot:
            kwargs = {}
            if spec.get("namespace"):
                kwargs["namespace"] = spec["namespace"]
            if spec.get("version") is not None:
                kwargs["version"] = int(spec["version"])
            by_slot[slot] = protocol.load_labware(
                spec["load_name"], slot, **kwargs
            )
        labware[role] = by_slot[slot]
    return labware


def _preflight(protocol, labware, p20):
    errors = []
    dil = CONFIG["dilution"]
    dest = CONFIG["destination"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])

    if int(CONFIG["deck"]["tiprack_p20"]["slot"]) != 9:
        errors.append("deck.tiprack_p20 must be slot 9")
    for role in ("stock", "diluent", "destination"):
        slot = int(CONFIG["deck"][role]["slot"])
        if not (1 <= slot <= 11):
            errors.append(f"deck.{role} slot must be 1-11, got {slot}")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    mode = str(dil.get("mode", "single")).lower()
    if mode not in ("single", "series"):
        errors.append(f"dilution.mode must be 'single' or 'series', got {mode!r}")

    stock_well = str(CONFIG["stock"]["well"]).upper()
    diluent_well = str(CONFIG["diluent"]["well"]).upper()
    if stock_well not in labware["stock"].wells_by_name():
        errors.append(f"stock.well {stock_well} does not exist")
    if diluent_well not in labware["diluent"].wells_by_name():
        errors.append(f"diluent.well {diluent_well} does not exist")

    dest_names = labware["destination"].wells_by_name()
    wells = [str(w).upper() for w in (dest.get("wells") or [])]
    if not wells:
        errors.append("destination.wells must list at least one well")
    missing = sorted({w for w in wells if w not in dest_names})
    if missing:
        errors.append(f"destination wells not on the labware: {', '.join(missing)}")
    if mode == "series" and len(wells) < 2:
        errors.append("dilution.mode series needs at least two destination wells")

    chunk = float(dil.get("transfer_chunk_ul", p20_max))
    if not (0 < chunk <= p20_max):
        errors.append(f"dilution.transfer_chunk_ul must be in (0, {p20_max:g}]")
    stock_volume = float(dil.get("stock_volume_ul", 0.0) or 0.0)
    diluent_volume = float(dil.get("diluent_volume_ul", 0.0) or 0.0)
    transfer_volume = float(dil.get("transfer_volume_ul", 0.0) or 0.0)
    if stock_volume < 0 or diluent_volume < 0 or transfer_volume < 0:
        errors.append("dilution volumes must be >= 0")
    if mode == "single" and stock_volume <= 0:
        errors.append("dilution.stock_volume_ul must be > 0 for mode single")
    if mode == "series" and transfer_volume <= 0:
        errors.append("dilution.transfer_volume_ul must be > 0 for mode series")

    mix_cycles = dil.get("mix_cycles", 0)
    mix_volume = float(dil.get("mix_volume_ul", 0.0) or 0.0)
    if isinstance(mix_cycles, bool) or not isinstance(mix_cycles, int) or mix_cycles < 0:
        errors.append(f"dilution.mix_cycles must be an integer >= 0, got {mix_cycles!r}")
    if mix_volume > p20_max + 1e-9:
        errors.append(f"dilution.mix_volume_ul must be <= {p20_max:g}")

    # Capacity of each destination well.
    if wells and not missing:
        if mode == "single":
            per_well = stock_volume + diluent_volume
        else:
            per_well = diluent_volume + max(stock_volume, transfer_volume)
        capacity = dest_names[wells[0]].max_volume
        if per_well > capacity + 1e-9:
            errors.append(
                f"each destination well would hold {per_well:g} uL, over its "
                f"{capacity:g} uL capacity"
            )

    tip_names = list(labware["tiprack_p20"].wells_by_name())
    start_tip = str(CONFIG["tips"].get("start_tip", "A1")).upper()
    if start_tip not in tip_names:
        errors.append(f"tips.start_tip {start_tip} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed.")
    return {
        "mode": mode,
        "stock_well": stock_well,
        "diluent_well": diluent_well,
        "wells": wells,
        "chunk": chunk,
        "stock_volume": stock_volume,
        "diluent_volume": diluent_volume,
        "transfer_volume": transfer_volume,
        "mix_cycles": int(mix_cycles),
        "mix_volume": mix_volume,
    }


def _report_plan(protocol, resolved):
    protocol.comment("=== GENERAL DILUTION PLAN ===")
    protocol.comment(f"Mode: {resolved['mode']}")
    protocol.comment(
        f"Stock:   {CONFIG['deck']['stock']['load_name']} slot "
        f"{CONFIG['deck']['stock']['slot']} well {resolved['stock_well']} "
        f"({CONFIG['stock'].get('material', 'stock')})"
    )
    protocol.comment(
        f"Diluent: {CONFIG['deck']['diluent']['load_name']} slot "
        f"{CONFIG['deck']['diluent']['slot']} well {resolved['diluent_well']} "
        f"({CONFIG['diluent'].get('material', 'diluent')})"
    )
    protocol.comment(
        f"Destination: {CONFIG['deck']['destination']['load_name']} slot "
        f"{CONFIG['deck']['destination']['slot']} wells "
        f"{', '.join(resolved['wells'])}"
    )
    if resolved["mode"] == "single":
        protocol.comment(
            f"Each well: {resolved['stock_volume']:g} uL stock + "
            f"{resolved['diluent_volume']:g} uL diluent = "
            f"{resolved['stock_volume'] + resolved['diluent_volume']:g} uL"
        )
    else:
        protocol.comment(
            f"Series: {resolved['diluent_volume']:g} uL diluent in every well, "
            f"{resolved['stock_volume']:g} uL stock into {resolved['wells'][0]}, "
            f"then {resolved['transfer_volume']:g} uL carried down the series"
        )
    protocol.comment(
        f"Mix: {resolved['mix_cycles']} cycles x {resolved['mix_volume']:g} uL"
    )
    protocol.comment(
        f"Tip reuse: {CONFIG['tips'].get('pipette_tip_reuse', True)}"
    )
    protocol.comment("=== END PLAN ===")


def _run_dilution(protocol, labware, p20, resolved):
    dest_cfg = CONFIG["destination"]
    lh = CONFIG.get("liquid_handling", {})
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])
    return_tips = bool(CONFIG["tips"].get("return_tips", True))
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", True))
    air_gap = float(lh.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(lh.get("air_gap_height_mm", 5.0))

    tiprack = labware["tiprack_p20"]
    tip_names = list(tiprack.wells_by_name())
    next_tip = [tip_names.index(str(CONFIG["tips"].get("start_tip", "A1")).upper())]
    active_source = [None]

    def use_tip(source_key, reason):
        """One tip per distinct source when reusing; otherwise always fresh."""
        if tip_reuse and p20.has_tip and active_source[0] == source_key:
            return
        _release_tip(p20, return_tips)
        if next_tip[0] >= len(tip_names):
            raise RuntimeError("ran out of P20 tips")
        name = tip_names[next_tip[0]]
        next_tip[0] += 1
        p20.pick_up_tip(tiprack[name])
        active_source[0] = source_key
        protocol.comment(f"tip {name}: {reason}")

    dest_labware = labware["destination"]
    dispense_height = float(dest_cfg.get("dispense_height_mm", 2.0))
    dest_aspirate_height = float(dest_cfg.get("aspirate_height_mm", 1.0))

    def transfer(source_well, source_height, destination_well, volume, source_key, label):
        """Chunked aspirate/dispense; the air gap shrinks to whatever fits."""
        remaining = volume
        index = 0
        chunks = max(1, int(-(-volume // resolved["chunk"])))
        while remaining > 1e-9:
            index += 1
            this = min(resolved["chunk"], remaining)
            use_tip(source_key, f"{label} ({index}/{chunks})")
            gap = min(air_gap, max(0.0, p20_max - this))
            p20.aspirate(this, source_well.bottom(source_height))
            if gap > 0:
                p20.air_gap(gap, height=air_gap_height)
            p20.dispense(this + gap, destination_well.bottom(dispense_height))
            p20.blow_out(destination_well.bottom(dispense_height))
            remaining -= this
        protocol.comment(f"{label}: {volume:g} uL")

    def mix(well, name):
        if resolved["mix_cycles"] <= 0 or resolved["mix_volume"] <= 0:
            return
        use_tip(f"mix:{name}", f"mix {name}")
        p20.mix(
            resolved["mix_cycles"], resolved["mix_volume"],
            well.bottom(dispense_height),
        )
        protocol.comment(
            f"mixed {name}: {resolved['mix_cycles']} x {resolved['mix_volume']:g} uL"
        )

    stock_well = labware["stock"][resolved["stock_well"]]
    diluent_well = labware["diluent"][resolved["diluent_well"]]
    stock_height = float(CONFIG["stock"].get("aspirate_height_mm", 4.0))
    diluent_height = float(CONFIG["diluent"].get("aspirate_height_mm", 4.0))

    if resolved["mode"] == "single":
        for name in resolved["wells"]:
            well = dest_labware[name]
            protocol.comment(f"--- {name} ---")
            if resolved["diluent_volume"] > 0:
                transfer(diluent_well, diluent_height, well,
                         resolved["diluent_volume"], f"diluent:{resolved['diluent_well']}",
                         f"diluent -> {name}")
            transfer(stock_well, stock_height, well, resolved["stock_volume"],
                     f"stock:{resolved['stock_well']}", f"stock -> {name}")
            mix(well, name)
    else:
        # Every well gets diluent first.
        protocol.comment("--- diluent into every well ---")
        for name in resolved["wells"]:
            if resolved["diluent_volume"] > 0:
                transfer(diluent_well, diluent_height, dest_labware[name],
                         resolved["diluent_volume"], f"diluent:{resolved['diluent_well']}",
                         f"diluent -> {name}")
        # Stock into the first well, then carry the series down.
        first = resolved["wells"][0]
        protocol.comment(f"--- stock into {first} ---")
        transfer(stock_well, stock_height, dest_labware[first],
                 resolved["stock_volume"], f"stock:{resolved['stock_well']}",
                 f"stock -> {first}")
        mix(dest_labware[first], first)
        for previous, current in zip(resolved["wells"], resolved["wells"][1:]):
            protocol.comment(f"--- {previous} -> {current} ---")
            transfer(dest_labware[previous], dest_aspirate_height,
                     dest_labware[current], resolved["transfer_volume"],
                     f"series:{previous}", f"{previous} -> {current}")
            mix(dest_labware[current], current)

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Dilution complete: {len(resolved['wells'])} well(s), "
        f"{next_tip[0] - tip_names.index(str(CONFIG['tips'].get('start_tip', 'A1')).upper())}"
        " tip(s) used."
    )


def run(protocol: protocol_api.ProtocolContext):
    labware = _load_deck(protocol)
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "general_dilution")
    protocol.comment(f"=== General Dilution {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== General Dilution {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    _run_dilution(protocol, labware, p20, resolved)
    protocol.comment(f"=== General Dilution {label} Completed ===")
