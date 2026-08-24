"""Version 11 - clover paper printing (deterministic executor).

Runs exactly the CONFIG block below and nothing else. No agent, no LLM, no
runtime skill, no natural-language request reaches this file: an agent may edit
YAML, and only a resolved, validated configuration is written between the
sentinels.

    configs/templates/11_clover_print_template.yaml   <- copy and edit this
         |  src/printing/v11/clover_loader.py   (resolution + validation)
         v
    this file, with its CONFIG block replaced
         |  src/printing/v11/clover_builder.py  (build + local simulation)
         v
    src/protocols/generated/11_clover_print_latest.py  <- upload this

VALIDATED PAPER GEOMETRY. The paper surface sits at the paper well bottom and
paper.print_height_mm is the standoff above it. 0.5 mm is the height physically
confirmed to detach the droplet; larger values left drops on the tip. Do not
change it without revalidating on the instrument.

COORDINATE MODEL (unchanged from the physically validated four-clover protocol;
all distances in millimetres)::

    droplet absolute position
      = paper well centre (clover `reference`)          (from the loaded labware)
      + clover centre offset (x_offset_mm, y_offset_mm)
      + that droplet's own offset, rotated by rotation_deg

Droplet keys are always d1..d4, in this fixed order::

    d1 = (-half_width, +half_height)     d2 = (+half_width, +half_height)
    d3 = (-half_width, -half_height)     d4 = (+half_width, -half_height)

    d1 ....... d2          +y is toward paper row A
       .  C  .             +x is toward paper column 12
    d3 ....... d4

TERMINOLOGY. `half_width_mm` is the offset of a droplet FROM THE CENTRE, so the
centre-to-centre separation between two opposing droplets is TWICE that value:
half_width_mm 1.0 puts d1 and d2 2.0 mm apart. The resolved config carries both
forms (`half_width_mm` and `separation_x_mm`) so the plan can report the real
physical distance. The word "spacing" is deliberately never used as a key.

ROTATION is a plain 2-D rotation of those four offsets about the clover centre.
It does not invent a new corner model, and it preserves every pairwise distance.

TWO AIR VOLUMES -- they are not interchangeable
-----------------------------------------------
The tip points down, so whatever is aspirated FIRST sits furthest from the
orifice and leaves LAST.

  pre_air_chase_ul  aspirated BEFORE the liquid, in air above the source, so it
                    ends up on the piston side and chases the drop off the tip on
                    dispense. Laboratory-owned; 0.0 in the validated profile.
  air_gap_ul        aspirated AFTER the liquid via air_gap(), so it sits between
                    the liquid and the tip opening and leaves FIRST. Anti-drip in
                    transit only; it cannot help droplet release.

Piston displacement per drop = pre_air_chase_ul + droplet_volume_ul + air_gap_ul
and must fit the P20. Only droplet_volume_ul is liquid: air is never deposited
and never charged against the source.
"""
from __future__ import annotations

import math

from opentrons import protocol_api
from opentrons.types import Point


metadata = {
    "protocolName": "Version 11 - Clover Paper Print (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Config-driven four-droplet clover printing. Every droplet coordinate, "
        "volume and delay comes from a resolved Version 11 configuration; the "
        "geometry engine is the physically validated four-clover model."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False
DEFAULT_DO_PRINT = True

DROPLET_KEYS = ("d1", "d2", "d3", "d4")


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'schema': 'v11-clover-print',
  'protocol_label': '11_clover_print',
  'machine_profile': 'configs/machines/ot2_standard_printing_p20_v1.yaml',
  'deck': { 'source': { 'slot': 7,
                        'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 11,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': { 'name': 'p20_single_gen2',
               'mount': 'left',
               'max_volume_ul': 20.0,
               'min_volume_ul': 1.0},
  'source': { 'type': 'vial_rack',
              'wells': ['A1'],
              'well': 'A1',
              'material': 'print liquid',
              'aspirate_height_mm': 4.0,
              'park_height_mm': 5.0,
              'loaded_volume_ul': 5000.0,
              'minimum_remaining_ul': 100.0,
              'well_depth_mm': 55.0,
              'well_max_volume_ul': 20000.0},
  'paper': {'print_height_mm': 0.5, 'edge_margin_mm': 4.5},
  'printing': { 'droplet_volume_ul': 5.0,
                'pre_air_chase_ul': 0.0,
                'push_out_ul': 3.0,
                'blow_out': True},
  'pipetting': { 'aspirate_flow_rate_ul_s': 3.0,
                 'dispense_flow_rate_ul_s': 3.0,
                 'air_gap_ul': 1.5,
                 'air_gap_height_mm': 5.0,
                 'post_aspirate_delay_s': 0.0,
                 'post_dispense_delay_s': 0.0},
  'geometry_default': { 'half_width_mm': 1.0,
                        'half_height_mm': 1.0,
                        'separation_x_mm': 2.0,
                        'separation_y_mm': 2.0,
                        'x_offset_mm': 0.0,
                        'y_offset_mm': 0.0,
                        'rotation_deg': 0.0},
  'layers': 1,
  'clovers': [ { 'name': 'clover_01',
                 'reference': 'B3',
                 'source_well': 'A1',
                 'layers': 1,
                 'geometry': { 'half_width_mm': 1.0,
                               'half_height_mm': 1.0,
                               'separation_x_mm': 2.0,
                               'separation_y_mm': 2.0,
                               'x_offset_mm': 0.0,
                               'y_offset_mm': 0.0,
                               'rotation_deg': 0.0},
                 'droplet_offsets': { 'd1': [-1.0, 1.0],
                                      'd2': [1.0, 1.0],
                                      'd3': [-1.0, -1.0],
                                      'd4': [1.0, -1.0]}}],
  'timing': {'inter_drop_delay_s': 0.0, 'inter_layer_delay_s': 5.0, 'inter_clover_delay_s': 0.0},
  'tips': {'pipette_tip_reuse': True, 'return_tips': True, 'start_tip': 'A1'},
  'validation': {'droplet_radius_mm': 1.5, 'boundary_check': True},
  'totals': { 'clovers': 1,
              'droplets_per_clover': 4,
              'deposits': 4,
              'liquid_ul': 20.0,
              'per_source_well_ul': {'A1': 20.0},
              'tips_required': 1},
  'warnings': []}
# <<< CONFIG END <<<


# ── Small shared helpers ──────────────────────────────────────────────────────────

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


def _number(value, label):
    """Coerce to float or raise a message the pre-flight collector can report."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric, got {value!r}")
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return result


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ── Geometry engine (pure; no labware, no opentrons objects) ──────────────────────

def _geometry_offsets(geometry, label):
    """Resolve one geometry block into explicit {d1..d4: (x_mm, y_mm)} offsets.

    half_width_mm / half_height_mm are offsets FROM THE CENTRE, so opposing
    droplets end up twice that far apart. rotation_deg then rotates all four
    offsets about the centre; a rotation preserves every pairwise distance, so it
    changes the orientation of the clover and nothing else.
    """
    if not isinstance(geometry, dict):
        raise ValueError(f"{label} must be a mapping, got {type(geometry).__name__}")
    half_width = _number(geometry["half_width_mm"], f"{label}.half_width_mm")
    half_height = _number(geometry["half_height_mm"], f"{label}.half_height_mm")
    if half_width <= 0 or half_height <= 0:
        raise ValueError(
            f"{label}: half_width_mm and half_height_mm must be > 0, got "
            f"{half_width:g} and {half_height:g}"
        )
    # d1 top-left, d2 top-right, d3 bottom-left, d4 bottom-right.
    offsets = {
        "d1": (-half_width, half_height),
        "d2": (half_width, half_height),
        "d3": (-half_width, -half_height),
        "d4": (half_width, -half_height),
    }
    rotation = _number(geometry.get("rotation_deg", 0.0) or 0.0, f"{label}.rotation_deg")
    if rotation % 360.0 == 0.0:
        return offsets
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return {
        key: (x * cos_t - y * sin_t, x * sin_t + y * cos_t)
        for key, (x, y) in offsets.items()
    }


def _check_declared_offsets(offsets, clover, label):
    """Cross-check the loader's offsets against this engine's own arithmetic.

    The loader resolves the same geometry to run its pre-build paper-bounds
    check. If the two ever disagree the run stops here rather than printing
    somewhere nobody validated.
    """
    declared = clover.get("droplet_offsets")
    if not declared:
        return []
    errors = []
    for key in DROPLET_KEYS:
        if key not in declared:
            errors.append(f"{label}: config droplet_offsets is missing {key}")
            continue
        want = declared[key]
        got = offsets[key]
        if (
            abs(_number(want[0], f"{label}.droplet_offsets.{key}.x") - got[0]) > 1e-6
            or abs(_number(want[1], f"{label}.droplet_offsets.{key}.y") - got[1]) > 1e-6
        ):
            errors.append(
                f"{label}: config droplet_offsets {key} = "
                f"({float(want[0]):.4f}, {float(want[1]):.4f}) but this executor "
                f"resolves ({got[0]:.4f}, {got[1]:.4f}) from the geometry"
            )
    return errors


def _resolve_clovers(well_xy):
    """Resolve every clover into absolute droplet coordinates.

    `well_xy` maps a paper well name to its (x, y) centre in whatever frame the
    caller cares about: deck millimetres inside the protocol, paper-local
    millimetres in the loader. The arithmetic is identical either way because
    every step is a translation.
    """
    entries = CONFIG.get("clovers")
    if not isinstance(entries, list) or not entries:
        raise ValueError("clovers must be a nonempty list")
    default_layers = CONFIG.get("layers", 1)

    clovers = []
    errors = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"clovers[{index}] must be a mapping")
        name = str(entry.get("name") or f"clover_{index:02d}")
        reference = str(entry.get("reference", "")).upper()
        if not reference:
            raise ValueError(f"{name} is missing reference")
        geometry = entry.get("geometry") or CONFIG.get("geometry_default")
        offsets = _geometry_offsets(geometry, f"{name}.geometry")
        errors.extend(_check_declared_offsets(offsets, entry, name))

        layers = entry.get("layers", default_layers)
        if isinstance(layers, bool) or not isinstance(layers, int) or layers < 1:
            raise ValueError(f"{name}: layers must be an integer >= 1, got {layers!r}")

        x_offset = _number(geometry.get("x_offset_mm", 0.0), f"{name}.geometry.x_offset_mm")
        y_offset = _number(geometry.get("y_offset_mm", 0.0), f"{name}.geometry.y_offset_mm")
        well_x, well_y = well_xy(reference)
        centre_x = well_x + x_offset
        centre_y = well_y + y_offset

        droplets = {}
        for key in DROPLET_KEYS:
            dx, dy = offsets[key]
            droplets[key] = {
                "offset": (dx, dy),
                "absolute": (centre_x + dx, centre_y + dy),
            }
        xs = [droplets[key]["absolute"][0] for key in DROPLET_KEYS]
        ys = [droplets[key]["absolute"][1] for key in DROPLET_KEYS]
        clovers.append(
            {
                "name": name,
                "reference": reference,
                "source_well": str(
                    entry.get("source_well") or CONFIG["source"]["well"]
                ).upper(),
                "centre_offset": (x_offset, y_offset),
                "centre": (centre_x, centre_y),
                "geometry": geometry,
                "layers": layers,
                "droplets": droplets,
                "extents": {
                    "min_x": min(xs),
                    "max_x": max(xs),
                    "min_y": min(ys),
                    "max_y": max(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                },
            }
        )
    if errors:
        raise ValueError("; ".join(errors))
    return clovers


def _print_order(clovers):
    """Deterministic (clover, layer, droplet) execution list.

    One clover is finished before the next: for each clover, every layer, and
    within a layer d1..d4. Drying time between layers is timing.inter_layer_delay_s.
    """
    plan = []
    for clover in clovers:
        for layer in range(1, clover["layers"] + 1):
            for key in DROPLET_KEYS:
                plan.append((clover, layer, key))
    return plan


def _paper_bounds(well_xy, well_names):
    """Usable XY box for droplet centres, in the same frame as `well_xy`.

    The labware JSON has no explicit "printable area", so the box is derived from
    the well grid, which is the only geometry the OT-2 actually knows: the
    bounding box of every well centre on the paper labware, grown by
    paper.edge_margin_mm on all four sides. At the default 4.5 mm -- half of the
    9 mm well pitch -- that is exactly the union of the 96 nominal print cells and
    nothing beyond it.

    A droplet passes only if its whole expected footprint fits: the test is
    centre +/- validation.droplet_radius_mm, not the bare centre point.
    """
    margin = _number(CONFIG["paper"].get("edge_margin_mm", 4.5), "paper.edge_margin_mm")
    if margin < 0:
        raise ValueError("paper.edge_margin_mm must be >= 0")
    coordinates = [well_xy(name) for name in well_names]
    return {
        "margin_mm": margin,
        "min_x": min(x for x, _ in coordinates) - margin,
        "max_x": max(x for x, _ in coordinates) + margin,
        "min_y": min(y for _, y in coordinates) - margin,
        "max_y": max(y for _, y in coordinates) + margin,
    }


def _boundary_violations(clovers, bounds, radius):
    """Every droplet whose footprint leaves the usable box, as error strings."""
    violations = []
    for clover in clovers:
        for key in DROPLET_KEYS:
            x, y = clover["droplets"][key]["absolute"]
            if (
                x - radius < bounds["min_x"] - 1e-9
                or x + radius > bounds["max_x"] + 1e-9
                or y - radius < bounds["min_y"] - 1e-9
                or y + radius > bounds["max_y"] + 1e-9
            ):
                violations.append(
                    f"{clover['name']}.{key} at x {x:.2f} y {y:.2f} (footprint "
                    f"radius {radius:g} mm) falls outside the usable paper box "
                    f"x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
                    f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}]"
                )
    return violations


def _distance_report(clovers):
    """Minimum droplet separation within each clover and between clovers."""
    intra = []
    for clover in clovers:
        points = [clover["droplets"][key]["absolute"] for key in DROPLET_KEYS]
        worst = None
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                gap = _distance(points[i], points[j])
                if worst is None or gap < worst[0]:
                    worst = (gap, DROPLET_KEYS[i], DROPLET_KEYS[j])
        intra.append(
            {"clover": clover["name"], "min_distance": worst[0], "pair": (worst[1], worst[2])}
        )

    inter = []
    for i in range(len(clovers)):
        for j in range(i + 1, len(clovers)):
            worst = None
            for key_a in DROPLET_KEYS:
                for key_b in DROPLET_KEYS:
                    gap = _distance(
                        clovers[i]["droplets"][key_a]["absolute"],
                        clovers[j]["droplets"][key_b]["absolute"],
                    )
                    if worst is None or gap < worst[0]:
                        worst = (gap, key_a, key_b)
            inter.append(
                {
                    "clovers": (clovers[i]["name"], clovers[j]["name"]),
                    "min_distance": worst[0],
                    "pair": (worst[1], worst[2]),
                }
            )
    return intra, inter


def _piston_load():
    """Break one drop cycle into its piston components.

    `total` is everything the plunger displaces and is what must fit the P20.
    `liquid` is the only part that leaves the source, so it is the only part
    charged against source volume.
    """
    printing = CONFIG["printing"]
    pipetting = CONFIG["pipetting"]
    chase = float(printing.get("pre_air_chase_ul", 0.0) or 0.0)
    liquid = float(printing["droplet_volume_ul"])
    gap = float(pipetting.get("air_gap_ul", 0.0) or 0.0)
    return {
        "pre_air_chase": chase,
        "liquid": liquid,
        "air_gap": gap,
        "total": chase + liquid + gap,
    }


# ── Pre-flight ────────────────────────────────────────────────────────────────────

def _preflight(protocol, labware, p20):
    errors = []
    warnings = list(CONFIG.get("warnings") or [])
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    printing = CONFIG["printing"]
    pipetting = CONFIG["pipetting"]
    timing = CONFIG["timing"]
    validation = CONFIG.get("validation", {})
    pipette_cfg = CONFIG["pipette"]
    maximum = float(pipette_cfg["max_volume_ul"])

    # ── Deck ──────────────────────────────────────────────────────────────────────
    for role, spec in deck.items():
        slot = int(spec["slot"])
        if not 1 <= slot <= 11:
            errors.append(f"deck.{role} slot must be 1-11, got {slot}")
    if len({int(spec["slot"]) for spec in deck.values()}) != len(deck):
        errors.append("deck slots must be unique")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != pipette_cfg["name"]:
        errors.append(f"pipette must be {pipette_cfg['name']}, got {p20.name}")
    if pipette_cfg["mount"] not in ("left", "right"):
        errors.append("pipette.mount must be left or right")

    # ── Volumes ───────────────────────────────────────────────────────────────────
    load = _piston_load()
    volume = load["liquid"]
    if not 0 < volume <= maximum:
        errors.append(f"printing.droplet_volume_ul must be in (0, {maximum:g}]")
    if load["air_gap"] < 0 or load["pre_air_chase"] < 0:
        errors.append("air volumes must be >= 0")
    if load["total"] > maximum + 1e-9:
        errors.append(
            f"piston load {load['total']:g} uL exceeds the {maximum:g} uL pipette "
            f"capacity: pre_air_chase_ul {load['pre_air_chase']:g} + "
            f"droplet_volume_ul {load['liquid']:g} + air_gap_ul {load['air_gap']:g}"
        )
    push_out = float(printing.get("push_out_ul", 0.0) or 0.0)
    if not 0 <= push_out <= maximum:
        errors.append(f"printing.push_out_ul must be in [0, {maximum:g}]")
    for key in ("air_gap_height_mm", "post_aspirate_delay_s", "post_dispense_delay_s"):
        if float(pipetting.get(key, 0.0) or 0.0) < 0:
            errors.append(f"pipetting.{key} must be >= 0")
    for key in ("aspirate_flow_rate_ul_s", "dispense_flow_rate_ul_s"):
        if float(pipetting.get(key, 0.0) or 0.0) <= 0:
            errors.append(f"pipetting.{key} must be > 0")
    for key in ("inter_drop_delay_s", "inter_layer_delay_s", "inter_clover_delay_s"):
        if float(timing.get(key, 0.0) or 0.0) < 0:
            errors.append(f"timing.{key} must be >= 0")
    print_height = float(CONFIG["paper"]["print_height_mm"])
    if not 0 <= print_height < 10:
        errors.append("paper.print_height_mm must be >= 0 and < 10")

    # ── Geometry ──────────────────────────────────────────────────────────────────
    paper = labware["paper"]
    paper_names = paper.wells_by_name()

    def well_xy(name):
        well = paper_names[str(name).upper()]
        point = well.top().point
        return float(point.x), float(point.y)

    clovers = []
    plan = []
    bounds = None
    for entry in CONFIG.get("clovers") or []:
        reference = str(entry.get("reference", "")).upper()
        if reference not in paper_names:
            errors.append(
                f"{entry.get('name', reference)}: reference {reference} does not "
                f"exist on {deck['paper']['load_name']}"
            )
    try:
        if not errors:
            clovers = _resolve_clovers(well_xy)
            plan = _print_order(clovers)
            bounds = _paper_bounds(well_xy, list(paper_names))
    except (TypeError, ValueError, KeyError) as exc:
        errors.append(str(exc))

    if not clovers and not errors:
        errors.append("no clovers were resolved")

    names = [clover["name"] for clover in clovers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"clover names must be unique; repeated: {', '.join(duplicates)}")

    radius = float(validation.get("droplet_radius_mm", 0.0) or 0.0)
    if radius < 0:
        errors.append("validation.droplet_radius_mm must be >= 0")
        radius = 0.0
    if bounds is not None:
        # Always fatal, whatever validation.boundary_check says: a droplet off the
        # paper is a physical mess, not an experimental judgement call.
        errors.extend(_boundary_violations(clovers, bounds, radius))

    # ── Source liquid ─────────────────────────────────────────────────────────────
    deposits = sum(clover["layers"] for clover in clovers) * len(DROPLET_KEYS)
    declared_deposits = (CONFIG.get("totals") or {}).get("deposits")
    if declared_deposits is not None and clovers and int(declared_deposits) != deposits:
        errors.append(
            f"totals.deposits says {int(declared_deposits)} but the clovers resolve "
            f"to {deposits}"
        )

    source_labware = labware["source"]
    source_names = source_labware.wells_by_name()
    declared_wells = [str(w).upper() for w in src["wells"]]
    per_well = {}
    for clover in clovers:
        used = clover["layers"] * len(DROPLET_KEYS) * volume
        per_well[clover["source_well"]] = per_well.get(clover["source_well"], 0.0) + used
    aspirate_height = float(src["aspirate_height_mm"])
    loaded = float(src["loaded_volume_ul"])
    reserve = float(src.get("minimum_remaining_ul", 0.0) or 0.0)

    for well_name in declared_wells:
        if well_name not in source_names:
            errors.append(f"source well {well_name} does not exist on the source labware")
    for well_name in sorted(per_well):
        if well_name not in declared_wells:
            errors.append(
                f"source well {well_name} is used by a clover but is not declared in "
                "source.wells"
            )
    if not errors:
        for well_name, required in sorted(per_well.items()):
            source_well = source_names[well_name]
            if not 0 < aspirate_height < source_well.depth:
                errors.append(
                    f"source.aspirate_height_mm must be > 0 and < "
                    f"{source_well.depth:g} mm"
                )
            if not 0 < loaded <= source_well.max_volume:
                errors.append(
                    f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}]"
                )
            if loaded - required < reserve - 1e-9:
                errors.append(
                    f"source well {well_name} needs {required:g} uL plus a {reserve:g} "
                    f"uL reserve; loaded_volume_ul is {loaded:g}"
                )
            diameter = getattr(source_well, "diameter", None)
            if diameter:
                # Circular well: the remaining liquid column must still reach above
                # the aspiration point, which a raw uL budget can miss on a wide vial.
                cover = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
                if loaded - required <= cover:
                    errors.append(
                        f"source well {well_name} would retain {loaded - required:g} uL "
                        f"after printing, below the approximately {cover:g} uL needed "
                        f"to cover the {aspirate_height:g} mm aspiration height"
                    )

    # ── Tips ──────────────────────────────────────────────────────────────────────
    tips = CONFIG["tips"]
    tip_names = list(labware["tiprack_p20"].wells_by_name())
    start_tip = str(tips.get("start_tip", "A1")).upper()
    if start_tip not in tip_names:
        errors.append(f"tips.start_tip {start_tip} does not exist on the tiprack")
    else:
        distinct = []
        for clover in clovers:
            if clover["source_well"] not in distinct:
                distinct.append(clover["source_well"])
        needed = deposits if not tips.get("pipette_tip_reuse", True) else len(distinct)
        if tip_names.index(start_tip) + needed > len(tip_names):
            errors.append(
                f"this run needs {needed} tip(s) from {start_tip}, which runs past the "
                f"{len(tip_names)}-tip rack"
            )

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    for warning in warnings:
        protocol.comment(f"WARNING: {warning}")
    intra, inter = _distance_report(clovers) if clovers else ([], [])
    protocol.comment(f"Pre-flight validation passed ({len(warnings)} warning(s)).")
    return {
        "clovers": clovers,
        "plan": plan,
        "bounds": bounds,
        "intra": intra,
        "inter": inter,
        "deposits": deposits,
        "per_source_well": per_well,
        "load": load,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────────

def _report_plan(protocol, resolved):
    clovers = resolved["clovers"]
    bounds = resolved["bounds"]
    load = resolved["load"]
    src = CONFIG["source"]
    volume = load["liquid"]
    timing = CONFIG["timing"]

    protocol.comment("=== VERSION 11 CLOVER PRINT PLAN ===")
    protocol.comment(
        f"Label: {CONFIG.get('protocol_label')}; machine profile: "
        f"{CONFIG.get('machine_profile')}"
    )
    protocol.comment(
        f"Clovers: {len(clovers)}; droplets per clover: {len(DROPLET_KEYS)}; "
        f"total deposits: {resolved['deposits']}"
    )
    protocol.comment(
        f"Liquid: {resolved['deposits']} x {volume:g} uL = "
        f"{resolved['deposits'] * volume:g} uL of {src['material']} "
        "(liquid only; the air gap is never deposited)"
    )
    protocol.comment(
        f"Piston per drop: pre-air chase {load['pre_air_chase']:g} + liquid "
        f"{load['liquid']:g} + air gap {load['air_gap']:g} = {load['total']:g} uL "
        f"(capacity {CONFIG['pipette']['max_volume_ul']:g} uL)"
    )
    protocol.comment(
        f"Release: {CONFIG['paper']['print_height_mm']:g} mm above the paper, "
        f"push-out {CONFIG['printing'].get('push_out_ul', 0.0):g} uL, blow-out "
        f"{'on' if CONFIG['printing'].get('blow_out', True) else 'off'}"
    )
    for well, used in sorted(resolved["per_source_well"].items()):
        loaded = float(src["loaded_volume_ul"])
        protocol.comment(
            f"Source well {well}: loaded {loaded:g} uL, consumed {used:g} uL, "
            f"remaining {loaded - used:g} uL, reserve "
            f"{float(src.get('minimum_remaining_ul', 0.0) or 0.0):g} uL"
        )
    protocol.comment(
        f"Tips: {'one per source well, reused' if CONFIG['tips'].get('pipette_tip_reuse', True) else 'a fresh tip for every droplet'}"
        f"; start tip {CONFIG['tips'].get('start_tip')}; "
        f"{'returned to the rack' if CONFIG['tips'].get('return_tips', True) else 'dropped in the trash'}"
    )
    protocol.comment(
        f"Timing: {timing['inter_drop_delay_s']:g} s per drop, "
        f"{timing['inter_layer_delay_s']:g} s between layers, "
        f"{timing['inter_clover_delay_s']:g} s between clovers"
    )
    protocol.comment(
        f"Usable paper box (margin {bounds['margin_mm']:g} mm): "
        f"x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
        f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}] mm (deck coordinates); "
        f"droplet footprint radius "
        f"{float(CONFIG['validation'].get('droplet_radius_mm', 0.0) or 0.0):g} mm"
    )

    for clover in clovers:
        geometry = clover["geometry"]
        offset_x, offset_y = clover["centre_offset"]
        centre_x, centre_y = clover["centre"]
        extents = clover["extents"]
        protocol.comment(f"--- Clover: {clover['name']} ---")
        protocol.comment(
            f"    reference well = {clover['reference']}, centre offset "
            f"x {offset_x:+.2f} mm, y {offset_y:+.2f} mm; source well "
            f"{clover['source_well']}; layers = {clover['layers']}"
        )
        protocol.comment(
            f"    geometry: half_width {geometry['half_width_mm']:g} mm, half_height "
            f"{geometry['half_height_mm']:g} mm -> ACTUAL separation x "
            f"{float(geometry.get('separation_x_mm', geometry['half_width_mm'] * 2)):g} mm, y "
            f"{float(geometry.get('separation_y_mm', geometry['half_height_mm'] * 2)):g} mm; "
            f"rotation {float(geometry.get('rotation_deg', 0.0) or 0.0):g} deg"
        )
        protocol.comment(
            f"    resolved centre (deck): x {centre_x:.2f} mm, y {centre_y:.2f} mm"
        )
        for key in DROPLET_KEYS:
            dx, dy = clover["droplets"][key]["offset"]
            ax, ay = clover["droplets"][key]["absolute"]
            protocol.comment(
                f"    {key.upper()} = x {dx:+.2f} mm, y {dy:+.2f} mm "
                f"-> deck x {ax:.2f} mm, y {ay:.2f} mm"
            )
        protocol.comment(
            f"    extents: x [{extents['min_x']:.2f}, {extents['max_x']:.2f}], "
            f"y [{extents['min_y']:.2f}, {extents['max_y']:.2f}], "
            f"width {extents['width']:.2f} mm, height {extents['height']:.2f} mm"
        )

    if resolved["intra"]:
        tightest = min(resolved["intra"], key=lambda item: item["min_distance"])
        protocol.comment(
            f"Minimum intra-clover droplet distance: {tightest['min_distance']:.2f} mm "
            f"({tightest['clover']}, {tightest['pair'][0]} to {tightest['pair'][1]})"
        )
    if resolved["inter"]:
        tightest = min(resolved["inter"], key=lambda item: item["min_distance"])
        protocol.comment(
            f"Minimum inter-clover droplet distance: {tightest['min_distance']:.2f} mm "
            f"({tightest['clovers'][0]} to {tightest['clovers'][1]})"
        )
    else:
        protocol.comment("Minimum inter-clover droplet distance: n/a (single clover)")

    protocol.comment("Execution order (clover by clover, layer by layer, d1..d4):")
    for step, (clover, layer, key) in enumerate(resolved["plan"], start=1):
        ax, ay = clover["droplets"][key]["absolute"]
        protocol.comment(
            f"    {step:3d}. {clover['name']} layer {layer} {key.upper()} "
            f"-> x {ax:.2f} y {ay:.2f}"
        )
    protocol.comment("=== END VERSION 11 CLOVER PRINT PLAN ===")


# ── Motion ────────────────────────────────────────────────────────────────────────

def _set_flow_rates(p20):
    pipetting = CONFIG["pipetting"]
    p20.flow_rate.aspirate = float(pipetting["aspirate_flow_rate_ul_s"])
    p20.flow_rate.dispense = float(pipetting["dispense_flow_rate_ul_s"])


def _droplet_location(paper, clover, key, z):
    """Location for one droplet: the reference well bottom shifted in XY.

    `Location.move` applies a pure translation, so the reference well only ever
    supplies an origin -- the droplet is free to sit anywhere on the paper,
    including between wells or outside the reference well's own circle.
    """
    x, y = clover["droplets"][key]["offset"]
    centre_x, centre_y = clover["centre_offset"]
    well = paper[clover["reference"]]
    return well.bottom(z).move(Point(x=centre_x + x, y=centre_y + y, z=0))


def _print_clovers(protocol, labware, p20, resolved):
    src = CONFIG["source"]
    printing = CONFIG["printing"]
    pipetting = CONFIG["pipetting"]
    timing = CONFIG["timing"]
    tips = CONFIG["tips"]
    paper = labware["paper"]

    volume = float(printing["droplet_volume_ul"])
    chase = float(printing.get("pre_air_chase_ul", 0.0) or 0.0)
    push_out = float(printing.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(printing.get("blow_out", True))
    air_gap = float(pipetting.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pipetting.get("air_gap_height_mm", 5.0))
    post_aspirate_delay = float(pipetting.get("post_aspirate_delay_s", 0.0) or 0.0)
    post_dispense_delay = float(pipetting.get("post_dispense_delay_s", 0.0) or 0.0)
    aspirate_height = float(src["aspirate_height_mm"])
    park_height = float(src.get("park_height_mm", 5.0))
    z = float(CONFIG["paper"]["print_height_mm"])
    drop_delay = float(timing.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(timing.get("inter_layer_delay_s", 0.0) or 0.0)
    clover_delay = float(timing.get("inter_clover_delay_s", 0.0) or 0.0)

    tiprack = labware["tiprack_p20"]
    tip_names = list(tiprack.wells_by_name())
    start_index = tip_names.index(str(tips.get("start_tip", "A1")).upper())
    return_tips = bool(tips.get("return_tips", True))
    # tips.pipette_tip_reuse (default true): one tip per distinct source well,
    # held for the whole run. False: a fresh tip for every individual droplet.
    tip_reuse = bool(tips.get("pipette_tip_reuse", True))

    ordered_sources = []
    for clover in resolved["clovers"]:
        if clover["source_well"] not in ordered_sources:
            ordered_sources.append(clover["source_well"])
    tip_for_source = {
        well: tip_names[start_index + offset]
        for offset, well in enumerate(ordered_sources)
    }
    next_tip = [start_index if not tip_reuse else start_index + len(ordered_sources)]
    active_source = [None]

    def use_source(well_name):
        """Ensure the tip on the pipette is the one this droplet should use."""
        if not tip_reuse:
            _release_tip(p20, return_tips)
            if next_tip[0] >= len(tip_names):
                raise RuntimeError(
                    "ran out of P20 tips: pipette_tip_reuse is false, which needs "
                    "one tip per droplet"
                )
            chosen = tip_names[next_tip[0]]
            next_tip[0] += 1
            p20.pick_up_tip(tiprack[chosen])
            protocol.comment(f"P20 fresh tip {chosen} for source well {well_name}.")
            active_source[0] = None
            return labware["source"][well_name]
        if active_source[0] == well_name:
            return labware["source"][well_name]
        _release_tip(p20, return_tips)
        chosen = tip_for_source[well_name]
        p20.pick_up_tip(tiprack[chosen])
        protocol.comment(f"P20 tip {chosen} picked for source well {well_name}.")
        active_source[0] = well_name
        return labware["source"][well_name]

    # NOTE on blow_out in a loop. blow_out leaves the plunger unprepared, so the
    # next aspirate re-prepares it and can pull a small extra slug on every
    # iteration after the first. The usual fix, prepare_to_aspirate() in air, is
    # only available from API 2.16 and this family is pinned to 2.15, so the
    # behaviour here is deliberately identical to the validated four-clover
    # protocol already in physical use.

    park_well = labware["source"][resolved["clovers"][0]["source_well"]]
    previous = None
    for clover, layer, key in resolved["plan"]:
        if previous is not None:
            previous_clover, previous_layer = previous
            if clover["name"] != previous_clover["name"] and clover_delay > 0:
                p20.move_to(park_well.top(park_height))
                protocol.comment(
                    f"Resting {clover_delay:g} s before clover {clover['name']}; "
                    "tip parked over the source."
                )
                protocol.delay(seconds=clover_delay)
            elif (
                clover["name"] == previous_clover["name"]
                and layer != previous_layer
                and layer_delay > 0
            ):
                p20.move_to(park_well.top(park_height))
                protocol.comment(
                    f"Drying {layer_delay:g} s before {clover['name']} layer {layer}; "
                    "tip parked over the source."
                )
                protocol.delay(seconds=layer_delay)

        source_well = use_source(clover["source_well"])
        park_well = source_well
        destination = _droplet_location(paper, clover, key, z)

        # Pre-air chase FIRST, in air above the source: every aspirate draws in
        # through the tip opening, so this air is pushed up by the liquid that
        # follows and ends on the piston side of it, which is what makes it leave
        # BEHIND the liquid and chase the drop off the tip.
        if chase > 0:
            p20.move_to(source_well.top(park_height))
            p20.aspirate(chase)
        p20.aspirate(volume, source_well.bottom(aspirate_height))
        if post_aspirate_delay > 0:
            protocol.delay(seconds=post_aspirate_delay)
        # Trailing air gap LAST: air_gap() lifts clear of the well, so this air
        # sits between the liquid and the tip opening and leaves first.
        if air_gap > 0:
            p20.air_gap(air_gap, height=air_gap_height)

        piston = chase + volume + air_gap
        if push_out > 0:
            p20.dispense(piston, destination, push_out=push_out)
        else:
            p20.dispense(piston, destination)
        if blow_out:
            p20.blow_out(destination)
        protocol.comment(
            f"{clover['name']} layer {layer} {key.upper()} placed at "
            f"x {destination.point.x:.2f} y {destination.point.y:.2f}"
        )
        if post_dispense_delay > 0:
            protocol.delay(seconds=post_dispense_delay)
        if drop_delay > 0:
            protocol.delay(seconds=drop_delay)
        previous = (clover, layer)

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Print complete: {len(resolved['clovers'])} clover(s), "
        f"{resolved['deposits']} deposits, "
        f"{resolved['deposits'] * volume:g} uL total."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("source", "paper", "tiprack_p20")
    }
    pipette_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pipette_cfg["name"], pipette_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "11_clover_print")
    protocol.comment(f"=== Version 11 Clover Print {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Version 11 Clover Print {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_clovers(protocol, labware, p20, resolved)
    else:
        protocol.comment("do_print is false: plan reported, no printing performed.")
    protocol.comment(f"=== Version 11 Clover Print {label} Completed ===")
