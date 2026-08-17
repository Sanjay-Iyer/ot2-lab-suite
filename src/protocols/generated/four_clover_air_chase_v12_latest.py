"""Four-clover paper print, v12 (OT-2 API 2.15).

Prints groups of four droplets ("clovers") clustered around a configurable center
point so their dried coffee rings can overlap toward the middle. Nothing about the
geometry is hard-coded: every droplet offset, every clover center, the grid pitch,
the volumes, the delays and the layer counts come from YAML.

Coordinate model (all distances in millimetres)::

    droplet absolute position
      = paper well `reference_well` center      (from the loaded labware)
      + clover center offset (x_offset_mm, y_offset_mm)
      + individual droplet offset (x_mm, y_mm)

Droplet keys are always d1..d4. With the symmetric `half_width_mm` /
`half_height_mm` form they expand to the four corners in this fixed order::

    d1 = (-half_width, +half_height)      d2 = (+half_width, +half_height)
    d3 = (-half_width, -half_height)      d4 = (+half_width, -half_height)

    d1 ....... d2          +y is toward plate row A
       .  C  .             +x is toward plate column 12
    d3 ....... d4

TERMINOLOGY. `half_width_mm` is the offset of a droplet FROM THE CENTER, so the
center-to-center separation between two opposing droplets is TWICE that value.
half_width_mm: 2.0 puts d1/d3 4.0 mm away from d2/d4. The word "spacing" is
deliberately not used as a config key because it is ambiguous. Explicit per-droplet
offsets remain available and always win over the symmetric form.

The geometry resolvers below take a `well_xy` callable rather than reaching into
labware, so scripts/plot_four_clover_layout.py and the unit tests can drive exactly
the same code with no robot and no opentrons hardware stack.

TWO DIFFERENT AIR VOLUMES -- they are not interchangeable
---------------------------------------------------------
The tip points down and every aspirate draws in through the orifice at the bottom,
so whatever is aspirated FIRST is pushed FURTHEST from the orifice, and whatever is
aspirated LAST sits nearest the orifice and comes out first.

  pre_air_chase_ul  aspirated BEFORE the liquid, in air above the source. The liquid
                    then enters underneath it and pushes it up, so it ends up on the
                    PISTON side of the liquid. On dispense it follows the liquid out
                    and chases the last of it off the tip. This is the droplet-release
                    mechanism.

  air_gap_ul        aspirated AFTER the liquid via air_gap(), which raises the tip out
                    of the well first. It therefore sits BETWEEN the liquid and the
                    tip opening, and on dispense it comes out BEFORE the liquid. Its
                    only job is stopping the drop dribbling out during travel. It
                    cannot help with detachment.

Tip contents, orifice at the bottom, with both enabled::

      piston side
        [ pre_air_chase ]   <- aspirated 1st, expelled LAST  (chases the liquid)
        [ liquid        ]   <- aspirated 2nd, expelled 2nd
        [ air_gap       ]   <- aspirated 3rd, expelled FIRST (anti-drip in transit)
      tip opening

Piston displacement per drop = pre_air_chase_ul + droplet_volume_ul + air_gap_ul,
and must fit the P20. Only droplet_volume_ul is liquid: air never counts toward
source consumption.
"""
from __future__ import annotations

import math

from opentrons import protocol_api
from opentrons.types import Point


metadata = {
    "protocolName": "Four Clover Paper Print v12 (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Config-driven four-droplet clover printing for coffee-ring overlap "
        "experiments. Every droplet coordinate comes from YAML. No dilution."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = False
DEFAULT_DO_DILUTION = False
DEFAULT_DO_PRINT    = True

DROPLET_KEYS = ("d1", "d2", "d3", "d4")


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'v12-airchase',
  'deck': { 'source': { 'slot': 7,
                        'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'kind': '20 mL vial',
              'well': 'A2',
              'material': 'BP',
              'loaded_volume_ul': 5000.0,
              'minimum_remaining_ul': 100.0,
              'aspirate_height_mm': 4.0,
              'park_height_mm': 5.0},
  'printing': { 'droplet_volume_ul': 5.0,
                'pre_air_chase_ul': 5.0,
                'dispense_height_mm': 4.0,
                'air_gap_ul': 1.5,
                'air_gap_height_mm': 5.0,
                'push_out_ul': 3.0,
                'blow_out': False,
                'inter_drop_delay_s': 2.0,
                'inter_layer_delay_s': 0.0,
                'inter_clover_delay_s': 0.0,
                'layers': 1},
  'order': {'mode': 'clover_by_clover'},
  'validation': { 'mode': 'warn',
                  'min_intra_clover_distance_mm': 1.0,
                  'min_inter_clover_distance_mm': 8.0,
                  'droplet_radius_mm': 1.5,
                  'allow_duplicate_droplet_positions': False},
  'tips': {'return_tips': True, 'p20': {'print_tip': 'A1'}},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0, 'expected_source_slot': 7},
  'destination': { 'default_clover_geometry': {'half_width_mm': 2.0, 'half_height_mm': 2.0},
                   'clover_grid': {'enabled': False},
                   'manual_clover_centers': [ { 'name': 'air_chase_5ul',
                                                'reference_well': 'E6',
                                                'x_offset_mm': 4.5,
                                                'y_offset_mm': 4.5}],
                   'paper_bounds': { 'x_dimension_mm': 127.76,
                                     'y_dimension_mm': 85.48,
                                     'grid_inset_x_mm': 14.38,
                                     'grid_inset_y_mm': 11.24,
                                     'boundary_mode': 'grid',
                                     'edge_margin_mm': 4.5}},
  'protocol_version': 16}
# <<< CONFIG END <<<


# ── Small shared helpers (same contract as the v10/v11 printing protocols) ────────

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


# ── Geometry engine (pure; no labware, no opentrons objects) ──────────────────────

def _geometry_from_spec(spec, label):
    """Resolve one geometry spec into explicit {d1..d4: (x_mm, y_mm)} offsets.

    Accepted forms, most flexible first:

    1. Explicit    -- {"d1": {"x_mm": .., "y_mm": ..}, ... all four keys}
    2. Symmetric   -- {"half_width_mm": W, "half_height_mm": H}
    3. Symmetric with per-droplet overrides -- form 2 plus
       {"droplet_overrides": {"d3": {"x_mm": ..}}}, which shifts individual
       droplets without giving up the compact form. Missing x_mm/y_mm inside an
       override keeps the symmetric value for that axis.

    Form 1 is the escape hatch for fully asymmetric layouts (diamond, cross, one
    droplet nudged, unequal X and Y). Forms 2/3 always resolve down to form 1, so
    only one representation ever reaches the motion code.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"{label} must be a mapping, got {type(spec).__name__}")

    explicit_keys = [key for key in DROPLET_KEYS if key in spec]
    if explicit_keys:
        missing = [key for key in DROPLET_KEYS if key not in spec]
        if missing:
            raise ValueError(
                f"{label} lists {', '.join(explicit_keys)} but is missing "
                f"{', '.join(missing)}; an explicit geometry needs all four droplets"
            )
        offsets = {}
        for key in DROPLET_KEYS:
            entry = spec[key]
            if not isinstance(entry, dict):
                raise ValueError(f"{label}.{key} must be a mapping with x_mm and y_mm")
            unknown = set(entry) - {"x_mm", "y_mm"}
            if unknown:
                raise ValueError(
                    f"{label}.{key} has unknown field(s): {', '.join(sorted(unknown))}"
                )
            for axis in ("x_mm", "y_mm"):
                if axis not in entry:
                    raise ValueError(f"{label}.{key} is missing {axis}")
            offsets[key] = (
                _number(entry["x_mm"], f"{label}.{key}.x_mm"),
                _number(entry["y_mm"], f"{label}.{key}.y_mm"),
            )
        return offsets

    if "half_width_mm" not in spec or "half_height_mm" not in spec:
        raise ValueError(
            f"{label} needs either all four explicit droplets (d1..d4) or both "
            "half_width_mm and half_height_mm"
        )
    unknown = set(spec) - {"half_width_mm", "half_height_mm", "droplet_overrides"}
    if unknown:
        raise ValueError(
            f"{label} has unknown field(s): {', '.join(sorted(unknown))}"
        )
    half_width = _number(spec["half_width_mm"], f"{label}.half_width_mm")
    half_height = _number(spec["half_height_mm"], f"{label}.half_height_mm")
    # d1 top-left, d2 top-right, d3 bottom-left, d4 bottom-right.
    offsets = {
        "d1": (-half_width, half_height),
        "d2": (half_width, half_height),
        "d3": (-half_width, -half_height),
        "d4": (half_width, -half_height),
    }

    overrides = spec.get("droplet_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"{label}.droplet_overrides must be a mapping")
    for key, entry in overrides.items():
        name = str(key).lower()
        if name not in DROPLET_KEYS:
            raise ValueError(
                f"{label}.droplet_overrides has unknown droplet {key!r} "
                f"(expected one of {', '.join(DROPLET_KEYS)})"
            )
        if not isinstance(entry, dict):
            raise ValueError(f"{label}.droplet_overrides.{name} must be a mapping")
        unknown = set(entry) - {"x_mm", "y_mm"}
        if unknown:
            raise ValueError(
                f"{label}.droplet_overrides.{name} has unknown field(s): "
                f"{', '.join(sorted(unknown))}"
            )
        x, y = offsets[name]
        if "x_mm" in entry:
            x = _number(entry["x_mm"], f"{label}.droplet_overrides.{name}.x_mm")
        if "y_mm" in entry:
            y = _number(entry["y_mm"], f"{label}.droplet_overrides.{name}.y_mm")
        offsets[name] = (x, y)
    return offsets


def _center_specs():
    """Return the ordered list of raw clover-center specs from the config.

    Grid mode and manual mode are mutually exclusive: `clover_grid.enabled: true`
    generates centers from an anchor well plus a pitch; otherwise the explicit
    `manual_clover_centers` list is used verbatim.
    """
    destination = CONFIG["destination"]
    grid = destination.get("clover_grid") or {}
    manual = destination.get("manual_clover_centers") or []

    if not bool(grid.get("enabled", False)):
        if not isinstance(manual, list) or not manual:
            raise ValueError(
                "destination.manual_clover_centers must be a nonempty list when "
                "destination.clover_grid.enabled is false"
            )
        specs = []
        for index, entry in enumerate(manual):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"destination.manual_clover_centers[{index}] must be a mapping"
                )
            if "reference_well" not in entry:
                raise ValueError(
                    f"destination.manual_clover_centers[{index}] is missing "
                    "reference_well"
                )
            specs.append(
                {
                    "name": str(entry.get("name") or f"clover_{index + 1:02d}"),
                    "reference_well": str(entry["reference_well"]).upper(),
                    "x_offset_mm": _number(
                        entry.get("x_offset_mm", 0.0),
                        f"manual_clover_centers[{index}].x_offset_mm",
                    ),
                    "y_offset_mm": _number(
                        entry.get("y_offset_mm", 0.0),
                        f"manual_clover_centers[{index}].y_offset_mm",
                    ),
                    "geometry_spec": entry.get("geometry")
                    or entry.get("geometry_override"),
                    "layers": entry.get("layers"),
                    # Optional per-clover liquid-handling override. Only the air
                    # chase is overridable today; the same slot is where any future
                    # per-clover volume/height override would hang.
                    "pre_air_chase_ul": entry.get("pre_air_chase_ul"),
                    "source": "manual",
                }
            )
        return specs

    # Grid mode. Centers march out from `anchor_well` by whole millimetres, never
    # by well indices, so a pitch need not be a multiple of the 9 mm well pitch.
    for key in ("anchor_well", "rows", "columns", "x_pitch_mm", "y_pitch_mm"):
        if key not in grid:
            raise ValueError(f"destination.clover_grid is missing {key}")
    rows = grid["rows"]
    columns = grid["columns"]
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError(f"destination.clover_grid.rows must be an integer >= 1, got {rows!r}")
    if isinstance(columns, bool) or not isinstance(columns, int) or columns < 1:
        raise ValueError(
            f"destination.clover_grid.columns must be an integer >= 1, got {columns!r}"
        )
    x_pitch = _number(grid["x_pitch_mm"], "destination.clover_grid.x_pitch_mm")
    y_pitch = _number(grid["y_pitch_mm"], "destination.clover_grid.y_pitch_mm")
    if columns > 1 and x_pitch <= 0:
        raise ValueError("destination.clover_grid.x_pitch_mm must be > 0 for 2+ columns")
    if rows > 1 and y_pitch <= 0:
        raise ValueError("destination.clover_grid.y_pitch_mm must be > 0 for 2+ rows")

    anchor = str(grid["anchor_well"]).upper()
    base_x = _number(grid.get("x_offset_mm", 0.0), "destination.clover_grid.x_offset_mm")
    base_y = _number(grid.get("y_offset_mm", 0.0), "destination.clover_grid.y_offset_mm")
    prefix = str(grid.get("name_prefix", "clover"))
    # Plate row A sits at HIGH y and row H at LOW y, so successive grid rows step
    # toward -y by default: an anchor in a top row grows downward, as drawn.
    row_direction = str(grid.get("row_direction", "-y")).lower()
    column_direction = str(grid.get("column_direction", "+x")).lower()
    if row_direction not in ("-y", "+y"):
        raise ValueError("destination.clover_grid.row_direction must be '-y' or '+y'")
    if column_direction not in ("+x", "-x"):
        raise ValueError("destination.clover_grid.column_direction must be '+x' or '-x'")
    row_sign = -1.0 if row_direction == "-y" else 1.0
    column_sign = 1.0 if column_direction == "+x" else -1.0

    overrides = grid.get("geometry_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("destination.clover_grid.geometry_overrides must be a mapping")
    layer_overrides = grid.get("layer_overrides") or {}
    if not isinstance(layer_overrides, dict):
        raise ValueError("destination.clover_grid.layer_overrides must be a mapping")
    chase_overrides = grid.get("pre_air_chase_overrides") or {}
    if not isinstance(chase_overrides, dict):
        raise ValueError(
            "destination.clover_grid.pre_air_chase_overrides must be a mapping"
        )

    specs = []
    for row in range(rows):
        for column in range(columns):
            name = f"{prefix}_r{row + 1}c{column + 1}"
            specs.append(
                {
                    "name": name,
                    "reference_well": anchor,
                    "x_offset_mm": base_x + column_sign * column * x_pitch,
                    "y_offset_mm": base_y + row_sign * row * y_pitch,
                    "geometry_spec": overrides.get(name),
                    "layers": layer_overrides.get(name),
                    "pre_air_chase_ul": chase_overrides.get(name),
                    "source": "grid",
                }
            )
    return specs


def _resolve_clovers(well_xy):
    """Resolve every clover into absolute droplet coordinates.

    `well_xy` maps a paper well name to its (x, y) center in whatever frame the
    caller cares about: absolute deck millimetres inside the protocol, paper-local
    millimetres in the plotting script. The arithmetic is identical either way
    because only translations are involved.
    """
    destination = CONFIG["destination"]
    default_spec = destination.get("default_clover_geometry")
    if default_spec is None:
        raise ValueError("destination.default_clover_geometry is required")
    default_offsets = _geometry_from_spec(default_spec, "default_clover_geometry")
    printing = CONFIG.get("printing", {})
    default_layers = printing.get("layers", 1)
    default_chase = _number(
        printing.get("pre_air_chase_ul", 0.0) or 0.0, "printing.pre_air_chase_ul"
    )

    clovers = []
    for spec in _center_specs():
        name = spec["name"]
        if spec["geometry_spec"] is None:
            offsets = dict(default_offsets)
            geometry_source = "default"
        else:
            offsets = _geometry_from_spec(spec["geometry_spec"], f"{name}.geometry")
            geometry_source = "override"

        layers = spec["layers"] if spec["layers"] is not None else default_layers
        if isinstance(layers, bool) or not isinstance(layers, int) or layers < 1:
            raise ValueError(f"{name}: layers must be an integer >= 1, got {layers!r}")

        if spec.get("pre_air_chase_ul") is None:
            chase = default_chase
            chase_source = "global"
        else:
            chase = _number(
                spec["pre_air_chase_ul"], f"{name}.pre_air_chase_ul"
            )
            chase_source = "override"
        if chase < 0:
            raise ValueError(
                f"{name}: pre_air_chase_ul must be >= 0, got {chase:g}"
            )

        well_x, well_y = well_xy(spec["reference_well"])
        center_x = well_x + spec["x_offset_mm"]
        center_y = well_y + spec["y_offset_mm"]

        droplets = {}
        for key in DROPLET_KEYS:
            dx, dy = offsets[key]
            droplets[key] = {
                "offset": (dx, dy),
                "absolute": (center_x + dx, center_y + dy),
            }
        xs = [droplets[key]["absolute"][0] for key in DROPLET_KEYS]
        ys = [droplets[key]["absolute"][1] for key in DROPLET_KEYS]
        clovers.append(
            {
                "name": name,
                "reference_well": spec["reference_well"],
                "center_offset": (spec["x_offset_mm"], spec["y_offset_mm"]),
                "center": (center_x, center_y),
                "geometry_source": geometry_source,
                "layers": layers,
                "pre_air_chase_ul": chase,
                "pre_air_chase_source": chase_source,
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
    return clovers


def _print_order(clovers):
    """Deterministic (clover, layer, droplet) execution list.

    clover_by_clover  -- finish one physical experiment before moving on: for each
                         clover, every layer, and within a layer d1..d4.
    position_by_position -- for each layer, all d1 positions across every clover,
                         then all d2, and so on. Useful when the drying time
                         between droplets of one clover must be maximised.
    """
    mode = str(CONFIG.get("order", {}).get("mode", "clover_by_clover")).lower()
    plan = []
    if mode == "clover_by_clover":
        for clover in clovers:
            for layer in range(1, clover["layers"] + 1):
                for key in DROPLET_KEYS:
                    plan.append((clover, layer, key))
    elif mode == "position_by_position":
        maximum = max((clover["layers"] for clover in clovers), default=0)
        for layer in range(1, maximum + 1):
            for key in DROPLET_KEYS:
                for clover in clovers:
                    if clover["layers"] >= layer:
                        plan.append((clover, layer, key))
    else:
        raise ValueError(
            "order.mode must be clover_by_clover or position_by_position, "
            f"got {mode!r}"
        )
    return mode, plan


def _paper_bounds(well_xy, well_names):
    """Usable XY box for droplet centers, in the same frame as `well_xy`.

    The labware JSON gives no explicit "printable area", so the box is derived from
    the well grid, which is the only geometry the OT-2 actually knows:

    1. Take the bounding box of every well center on the paper labware
       (A1..H12). For paper_print_96_flat that is 99.00 x 63.00 mm.
    2. `boundary_mode: grid` (default, conservative) expands that box by
       `edge_margin_mm` on all four sides. At the default 4.5 mm -- half of the
       9 mm well pitch -- the usable box is exactly the union of the 96 nominal
       print cells and nothing beyond it.
    3. `boundary_mode: labware` instead reconstructs the physical labware
       footprint: the outer edges sit `grid_inset_x_mm` / `grid_inset_y_mm` beyond
       the outermost well centers, then `edge_margin_mm` is subtracted as a safety
       inset. The declared insets are cross-checked against
       x_dimension_mm / y_dimension_mm, so a labware swap that breaks the
       "grid centered in footprint" relationship is caught rather than assumed.

    A droplet passes only if its whole expected footprint fits: the check is
    center +/- validation.droplet_radius_mm, not the bare center point.
    """
    bounds_cfg = CONFIG["destination"].get("paper_bounds") or {}
    mode = str(bounds_cfg.get("boundary_mode", "grid")).lower()
    margin = _number(
        bounds_cfg.get("edge_margin_mm", 4.5), "paper_bounds.edge_margin_mm"
    )
    if margin < 0:
        raise ValueError("paper_bounds.edge_margin_mm must be >= 0")

    coordinates = [well_xy(name) for name in well_names]
    grid_min_x = min(x for x, _ in coordinates)
    grid_max_x = max(x for x, _ in coordinates)
    grid_min_y = min(y for _, y in coordinates)
    grid_max_y = max(y for _, y in coordinates)

    notes = []
    if mode == "grid":
        box = (
            grid_min_x - margin,
            grid_max_x + margin,
            grid_min_y - margin,
            grid_max_y + margin,
        )
    elif mode == "labware":
        inset_x = _number(
            bounds_cfg.get("grid_inset_x_mm", 0.0), "paper_bounds.grid_inset_x_mm"
        )
        inset_y = _number(
            bounds_cfg.get("grid_inset_y_mm", 0.0), "paper_bounds.grid_inset_y_mm"
        )
        x_dimension = _number(
            bounds_cfg.get("x_dimension_mm", 0.0), "paper_bounds.x_dimension_mm"
        )
        y_dimension = _number(
            bounds_cfg.get("y_dimension_mm", 0.0), "paper_bounds.y_dimension_mm"
        )
        implied_x = (grid_max_x - grid_min_x) + 2 * inset_x
        implied_y = (grid_max_y - grid_min_y) + 2 * inset_y
        if abs(implied_x - x_dimension) > 0.5:
            notes.append(
                f"paper_bounds: grid span + 2 x inset_x = {implied_x:.2f} mm does not "
                f"match x_dimension_mm {x_dimension:.2f} mm"
            )
        if abs(implied_y - y_dimension) > 0.5:
            notes.append(
                f"paper_bounds: grid span + 2 x inset_y = {implied_y:.2f} mm does not "
                f"match y_dimension_mm {y_dimension:.2f} mm"
            )
        box = (
            grid_min_x - inset_x + margin,
            grid_max_x + inset_x - margin,
            grid_min_y - inset_y + margin,
            grid_max_y + inset_y - margin,
        )
    else:
        raise ValueError(
            f"paper_bounds.boundary_mode must be grid or labware, got {mode!r}"
        )

    min_x, max_x, min_y, max_y = box
    if min_x >= max_x or min_y >= max_y:
        raise ValueError(
            f"paper_bounds leaves no usable area in {mode} mode with "
            f"edge_margin_mm {margin:g}"
        )
    return {
        "mode": mode,
        "margin_mm": margin,
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "grid": {
            "min_x": grid_min_x,
            "max_x": grid_max_x,
            "min_y": grid_min_y,
            "max_y": grid_max_y,
        },
        "notes": notes,
    }


def _piston_load(pre_air_chase_ul, droplet_volume_ul, air_gap_ul):
    """Break one drop cycle into its piston components.

    `total` is everything the plunger displaces on the dispense stroke and is what
    must fit the P20. `liquid` is the only part that leaves the source, so it is
    the only part that may be charged against source volume. Pure arithmetic, kept
    separate so both the validator and the tests can call it.
    """
    chase = float(pre_air_chase_ul or 0.0)
    liquid = float(droplet_volume_ul)
    gap = float(air_gap_ul or 0.0)
    return {
        "pre_air_chase": chase,
        "liquid": liquid,
        "air_gap": gap,
        "air_total": chase + gap,
        "total": chase + liquid + gap,
    }


def _capacity_errors(clovers, volume, air_gap, p20_max):
    """Reject any clover whose piston load will not fit the P20.

    Reported per distinct chase value with the arithmetic spelled out, so a config
    like 15 uL chase + 10 uL liquid names the offending volumes instead of failing
    somewhere inside the run.
    """
    errors = []
    seen = {}
    for clover in clovers:
        seen.setdefault(clover["pre_air_chase_ul"], []).append(clover["name"])
    for chase, names in sorted(seen.items()):
        load = _piston_load(chase, volume, air_gap)
        if load["total"] > p20_max + 1e-9:
            errors.append(
                f"piston load {load['total']:g} uL exceeds the P20 capacity "
                f"{p20_max:g} uL for {', '.join(sorted(names))}: "
                f"pre_air_chase_ul {load['pre_air_chase']:g} + "
                f"droplet_volume_ul {load['liquid']:g} + "
                f"air_gap_ul {load['air_gap']:g}. Reduce pre_air_chase_ul by at "
                f"least {load['total'] - p20_max:g} uL, or lower the droplet volume."
            )
    return errors


def _boundary_violations(clovers, bounds, radius):
    """Every droplet whose footprint leaves the usable box, as error strings.

    The test is on the footprint, not the center: a droplet passes only when
    center +/- `radius` fits on all four sides. Kept separate from _preflight so
    it can be exercised without a ProtocolContext.
    """
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


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


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
        intra.append({"clover": clover["name"], "min_distance": worst[0],
                      "pair": (worst[1], worst[2])})

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


# ── Pre-flight ────────────────────────────────────────────────────────────────────

def _preflight(protocol, labware, p20):
    errors = []
    warnings = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    safety = CONFIG["safety"]
    validation = CONFIG.get("validation", {})
    p20_max = float(safety["p20_max_volume_ul"])

    validation_mode = str(validation.get("mode", "warn")).lower()
    if validation_mode not in ("warn", "error"):
        errors.append(
            f"validation.mode must be warn or error, got {validation_mode!r}"
        )
        validation_mode = "error"

    def report(message):
        """Route a spacing finding to errors or warnings per validation.mode."""
        if validation_mode == "error":
            errors.append(message)
        else:
            warnings.append(message)

    # ── Deck ──────────────────────────────────────────────────────────────────────
    expected_source_slot = int(safety["expected_source_slot"])
    for role, expected in (
        ("source", expected_source_slot), ("paper", 5), ("tiprack_p20", 9)
    ):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")
    if len({int(spec["slot"]) for spec in deck.values()}) != len(deck):
        errors.append("deck slots must be unique")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")
    if CONFIG["pipette"]["mount"] not in ("left", "right"):
        errors.append("pipette.mount must be left or right")

    # ── Pipette volumes ───────────────────────────────────────────────────────────
    volume = float(pr["droplet_volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    global_chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    if not (0 < volume <= p20_max):
        errors.append(f"printing.droplet_volume_ul must be in (0, {p20_max:g}]")
    if air_gap < 0:
        errors.append("printing.air_gap_ul must be >= 0")
    if global_chase < 0:
        errors.append("printing.pre_air_chase_ul must be >= 0")
    if not (0 <= push_out <= p20_max):
        errors.append(f"printing.push_out_ul must be in [0, {p20_max:g}]")
    for key in (
        "dispense_height_mm", "air_gap_height_mm", "inter_drop_delay_s",
        "inter_layer_delay_s", "inter_clover_delay_s",
    ):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"printing.{key} must be >= 0")
    layers = pr.get("layers", 1)
    if isinstance(layers, bool) or not isinstance(layers, int) or layers < 1:
        errors.append(f"printing.layers must be an integer >= 1, got {layers!r}")

    # ── Geometry ──────────────────────────────────────────────────────────────────
    paper = labware["paper"]
    paper_names = paper.wells_by_name()

    def well_xy(name):
        well = paper_names[str(name).upper()]
        point = well.top().point
        return float(point.x), float(point.y)

    clovers = []
    plan = []
    order_mode = str(CONFIG.get("order", {}).get("mode", "clover_by_clover"))
    bounds = None
    try:
        for spec in _center_specs():
            if spec["reference_well"] not in paper_names:
                errors.append(
                    f"{spec['name']}: reference_well {spec['reference_well']} does "
                    "not exist on the paper labware"
                )
        if not errors:
            clovers = _resolve_clovers(well_xy)
            order_mode, plan = _print_order(clovers)
            bounds = _paper_bounds(well_xy, list(paper_names))
    except (TypeError, ValueError, KeyError) as exc:
        errors.append(str(exc))

    if not clovers and not errors:
        errors.append("no clover centers were resolved")

    names = [clover["name"] for clover in clovers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"clover names must be unique; repeated: {', '.join(duplicates)}")

    # Capacity: pre-air chase + liquid + trailing air gap all ride in the tip at
    # once, so their sum is what has to fit the P20.
    errors.extend(_capacity_errors(clovers, volume, air_gap, p20_max))

    radius = float(validation.get("droplet_radius_mm", 0.0) or 0.0)
    if radius < 0:
        errors.append("validation.droplet_radius_mm must be >= 0")
        radius = 0.0

    if bounds is not None:
        warnings.extend(bounds["notes"])
        # Boundary is always fatal regardless of validation.mode: a droplet off
        # the paper is a physical mess, not an experimental judgement call.
        errors.extend(_boundary_violations(clovers, bounds, radius))

    # ── Spacing / collision ───────────────────────────────────────────────────────
    intra, inter = ([], [])
    if clovers:
        intra, inter = _distance_report(clovers)
        allow_duplicates = bool(
            validation.get("allow_duplicate_droplet_positions", False)
        )
        min_intra = float(validation.get("min_intra_clover_distance_mm", 0.0) or 0.0)
        min_inter = float(validation.get("min_inter_clover_distance_mm", 0.0) or 0.0)
        for entry in intra:
            if entry["min_distance"] <= 1e-6 and not allow_duplicates:
                errors.append(
                    f"{entry['clover']}: {entry['pair'][0]} and {entry['pair'][1]} "
                    "share the same coordinate; set "
                    "validation.allow_duplicate_droplet_positions to allow stacking"
                )
            elif entry["min_distance"] < min_intra:
                report(
                    f"{entry['clover']}: droplets {entry['pair'][0]}/"
                    f"{entry['pair'][1]} are {entry['min_distance']:.2f} mm apart, "
                    f"below min_intra_clover_distance_mm {min_intra:g}"
                )
        for entry in inter:
            if entry["min_distance"] < min_inter:
                report(
                    f"{entry['clovers'][0]} and {entry['clovers'][1]} approach to "
                    f"{entry['min_distance']:.2f} mm ({entry['pair'][0]} to "
                    f"{entry['pair'][1]}), below min_inter_clover_distance_mm "
                    f"{min_inter:g}"
                )

    # ── Source liquid ─────────────────────────────────────────────────────────────
    deposits = sum(clover["layers"] for clover in clovers) * len(DROPLET_KEYS)
    source_name = str(src["well"]).upper()
    source_names = labware["source"].wells_by_name()
    if source_name not in source_names:
        errors.append(f"source well {source_name} does not exist")
    else:
        source_well = source_names[source_name]
        aspirate_height = float(src["aspirate_height_mm"])
        if not (0 < aspirate_height < source_well.depth):
            errors.append(
                f"source.aspirate_height_mm must be > 0 and < {source_well.depth:g} mm"
            )
        loaded = float(src["loaded_volume_ul"])
        reserve = float(src.get("minimum_remaining_ul", 0.0) or 0.0)
        required = deposits * volume
        if not (0 < loaded <= source_well.max_volume):
            errors.append(
                f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}]"
            )
        if reserve < 0 or loaded < required + max(reserve, 0.0):
            errors.append(
                f"source needs {required:g} uL print volume plus {reserve:g} uL "
                f"reserve; loaded_volume_ul is {loaded:g}"
            )
        diameter = getattr(source_well, "diameter", None)
        if diameter:
            # Circular flat-bottom well: the remaining liquid column must still
            # reach above the aspiration point, which a raw uL budget can miss on a
            # wide 20 mL vial.
            cover_volume = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
            remaining = loaded - required
            if remaining <= cover_volume:
                errors.append(
                    f"source would retain {remaining:g} uL after printing, below the "
                    f"approximately {cover_volume:g} uL needed to cover the "
                    f"{aspirate_height:g} mm aspiration height"
                )

    tip_name = str(CONFIG["tips"]["p20"]["print_tip"]).upper()
    if tip_name not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"P20 tip {tip_name} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    for warning in warnings:
        protocol.comment(f"WARNING: {warning}")
    protocol.comment(
        f"Pre-flight validation passed ({len(warnings)} warning(s); "
        f"validation.mode={validation_mode})."
    )
    return {
        "clovers": clovers,
        "plan": plan,
        "order_mode": order_mode,
        "bounds": bounds,
        "intra": intra,
        "inter": inter,
        "deposits": deposits,
        "source_name": source_name,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────────

def _report_drop_sequence(protocol, resolved):
    """Spell out the resolved per-drop liquid-handling sequence before any motion."""
    pr = CONFIG["printing"]
    src = CONFIG["source"]
    volume = float(pr["droplet_volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    z = float(pr["dispense_height_mm"])
    aspirate_height = float(src["aspirate_height_mm"])
    park_height = float(src.get("park_height_mm", 5.0))

    groups = {}
    for clover in resolved["clovers"]:
        groups.setdefault(clover["pre_air_chase_ul"], []).append(clover["name"])

    protocol.comment("=== FOUR CLOVER DROP SEQUENCE ===")
    for chase in sorted(groups):
        load = _piston_load(chase, volume, air_gap)
        names = ", ".join(sorted(groups[chase]))
        protocol.comment(f"--- clovers: {names} ---")
        protocol.comment(f"    Pre-air chase:      {load['pre_air_chase']:.1f} uL")
        protocol.comment(f"    Liquid aspiration:  {load['liquid']:.1f} uL")
        protocol.comment(f"    Trailing air gap:   {load['air_gap']:.1f} uL")
        protocol.comment(f"    Total piston load:  {load['total']:.1f} uL")
        protocol.comment("    Sequence:")
        step = 1
        if chase > 0:
            protocol.comment(
                f"      {step}. Move above source, aspirate {chase:.1f} uL AIR "
                f"(pre-air chase, {park_height:g} mm over the source top)"
            )
            step += 1
        protocol.comment(
            f"      {step}. Aspirate {volume:.1f} uL LIQUID at "
            f"{aspirate_height:g} mm above the source bottom"
        )
        step += 1
        if air_gap > 0:
            protocol.comment(
                f"      {step}. Aspirate {air_gap:.1f} uL air gap at "
                f"{air_gap_height:g} mm over the source top (anti-drip in transit)"
            )
            step += 1
        protocol.comment(f"      {step}. Move to the destination droplet coordinate")
        step += 1
        protocol.comment(
            f"      {step}. Dispense {load['total']:.1f} uL piston volume at "
            f"{z:g} mm above the paper"
        )
        step += 1
        if push_out > 0:
            protocol.comment(
                f"      {step}. push_out {push_out:g} uL (extra plunger travel, "
                "part of the same dispense command)"
            )
            step += 1
        if blow_out:
            protocol.comment(
                f"      {step}. blow_out at the droplet -- SEPARATE forceful air "
                "pulse, additional to the pre-air chase"
            )
            step += 1
        if chase > 0:
            protocol.comment(
                "    Tip order (piston side -> tip opening): "
                f"[{chase:.1f} uL chase air][{volume:.1f} uL liquid]"
                + (f"[{air_gap:.1f} uL gap air]" if air_gap > 0 else "")
                + " so liquid exits first and the chase air follows it out."
            )
        else:
            protocol.comment(
                "    No pre-air chase: normal liquid aspiration path."
            )
    protocol.comment(
        f"    Liquid deposited per spot: {volume:g} uL "
        "(air volumes are NOT deposited and do not count against the source)"
    )
    if blow_out:
        protocol.comment(
            "    NOTE: blow_out is enabled. It is independent of the pre-air chase "
            "and adds a forceful pulse that can disturb the deposited droplet. Set "
            "printing.blow_out to false to test the chase on its own."
        )
    protocol.comment("=== END FOUR CLOVER DROP SEQUENCE ===")


def _report_plan(protocol, resolved):
    clovers = resolved["clovers"]
    bounds = resolved["bounds"]
    pr = CONFIG["printing"]
    volume = float(pr["droplet_volume_ul"])
    src = CONFIG["source"]

    _report_drop_sequence(protocol, resolved)
    protocol.comment("=== FOUR CLOVER PLAN ===")
    protocol.comment(
        f"Clovers: {len(clovers)}; droplets per clover: {len(DROPLET_KEYS)}; "
        f"total deposits: {resolved['deposits']}"
    )
    protocol.comment(
        f"Liquid: {resolved['deposits']} x {volume:g} uL = "
        f"{resolved['deposits'] * volume:g} uL of {src['material']} "
        "(liquid only; air chase and air gap are excluded)"
    )
    loaded = float(src["loaded_volume_ul"])
    reserve = float(src.get("minimum_remaining_ul", 0.0) or 0.0)
    protocol.comment(
        f"Source {src['kind']} well {resolved['source_name']}: loaded {loaded:g} uL, "
        f"consumed {resolved['deposits'] * volume:g} uL, remaining "
        f"{loaded - resolved['deposits'] * volume:g} uL, reserve {reserve:g} uL"
    )
    protocol.comment(
        f"Usable paper box ({bounds['mode']} mode, margin {bounds['margin_mm']:g} mm): "
        f"x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
        f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}] mm (deck coordinates)"
    )

    for clover in clovers:
        offset_x, offset_y = clover["center_offset"]
        center_x, center_y = clover["center"]
        extents = clover["extents"]
        protocol.comment(f"--- Clover: {clover['name']} ---")
        protocol.comment(
            f"    center: reference well = {clover['reference_well']}, "
            f"center x offset = {offset_x:+.2f} mm, "
            f"center y offset = {offset_y:+.2f} mm"
        )
        protocol.comment(
            f"    resolved center (deck): x {center_x:.2f} mm, y {center_y:.2f} mm; "
            f"geometry = {clover['geometry_source']}; layers = {clover['layers']}; "
            f"pre-air chase = {clover['pre_air_chase_ul']:g} uL "
            f"({clover['pre_air_chase_source']})"
        )
        for key in DROPLET_KEYS:
            dx, dy = clover["droplets"][key]["offset"]
            ax, ay = clover["droplets"][key]["absolute"]
            protocol.comment(
                f"    {key.upper()} = x {dx:+.2f} mm, y {dy:+.2f} mm "
                f"-> deck x {ax:.2f} mm, y {ay:.2f} mm"
            )
        protocol.comment(
            f"    extents: min x {extents['min_x']:.2f}, max x {extents['max_x']:.2f}, "
            f"min y {extents['min_y']:.2f}, max y {extents['max_y']:.2f}, "
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

    all_x = [value for clover in clovers
             for value in (clover["extents"]["min_x"], clover["extents"]["max_x"])]
    all_y = [value for clover in clovers
             for value in (clover["extents"]["min_y"], clover["extents"]["max_y"])]
    protocol.comment(
        f"Overall droplet extents: x [{min(all_x):.2f}, {max(all_x):.2f}] "
        f"y [{min(all_y):.2f}, {max(all_y):.2f}] mm"
    )

    protocol.comment(f"Execution order ({resolved['order_mode']}):")
    for step, (clover, layer, key) in enumerate(resolved["plan"], start=1):
        ax, ay = clover["droplets"][key]["absolute"]
        protocol.comment(
            f"    {step:3d}. {clover['name']} layer {layer} {key.upper()} "
            f"-> x {ax:.2f} y {ay:.2f}"
        )
    protocol.comment("=== END FOUR CLOVER PLAN ===")


# ── Motion ────────────────────────────────────────────────────────────────────────

def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _droplet_location(paper, clover, key, z):
    """Location for one droplet: the reference well bottom shifted in XY.

    `Location.move` applies a pure translation, so the reference well only ever
    supplies an origin -- the droplet is free to sit anywhere on the paper,
    including between wells or outside the reference well's own circle.
    """
    x, y = clover["droplets"][key]["offset"]
    center_x, center_y = clover["center_offset"]
    well = paper[clover["reference_well"]]
    return well.bottom(z).move(Point(x=center_x + x, y=center_y + y, z=0))


def _print_clovers(protocol, labware, p20, resolved):
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    paper = labware["paper"]
    source_well = labware["source"][resolved["source_name"]]

    volume = float(pr["droplet_volume_ul"])
    aspirate_height = float(src["aspirate_height_mm"])
    park_height = float(src.get("park_height_mm", 5.0))
    z = float(pr["dispense_height_mm"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    drop_delay = float(pr.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(pr.get("inter_layer_delay_s", 0.0) or 0.0)
    clover_delay = float(pr.get("inter_clover_delay_s", 0.0) or 0.0)
    tip_name = str(CONFIG["tips"]["p20"]["print_tip"]).upper()

    # NOTE on blow_out in a loop. blow_out leaves the plunger unprepared, so the
    # next aspirate re-prepares it and can pull a small extra slug on every
    # iteration after the first. The usual fix, prepare_to_aspirate() in air, is
    # only available from API 2.16 and this family is pinned to 2.15, so the
    # behaviour here is deliberately identical to the v10/v11 protocols already
    # in physical use. If the first prints show growing droplet volume down the
    # run, set printing.blow_out to false and rely on push_out_ul alone.
    p20.pick_up_tip(labware["tiprack_p20"][tip_name])
    protocol.comment(f"P20 tip {tip_name} picked and held for the complete run.")

    previous = None
    for clover, layer, key in resolved["plan"]:
        if previous is not None:
            previous_clover, previous_layer = previous
            if clover["name"] != previous_clover["name"] and clover_delay > 0:
                p20.move_to(source_well.top(park_height))
                protocol.comment(
                    f"Resting {clover_delay:g} s before clover {clover['name']}; "
                    "tip parked over the source."
                )
                protocol.delay(seconds=clover_delay)
            elif layer != previous_layer and layer_delay > 0:
                p20.move_to(source_well.top(park_height))
                protocol.comment(
                    f"Resting {layer_delay:g} s before layer {layer}; "
                    "tip parked over the source."
                )
                protocol.delay(seconds=layer_delay)

        destination = _droplet_location(paper, clover, key, z)
        chase = float(clover["pre_air_chase_ul"])

        # Pre-air chase FIRST, in air above the source. Every aspirate draws in
        # through the tip opening, so this air is pushed up by the liquid that
        # follows and ends up on the piston side of it -- which is what makes it
        # come out BEHIND the liquid and chase the drop off the tip.
        if chase > 0:
            p20.move_to(source_well.top(park_height))
            p20.aspirate(chase)
        p20.aspirate(volume, source_well.bottom(aspirate_height))
        # Trailing air gap LAST: air_gap() lifts clear of the well, so this air sits
        # between the liquid and the tip opening and leaves first. Anti-drip only.
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
        if drop_delay > 0:
            protocol.delay(seconds=drop_delay)
        previous = (clover, layer)

    _release_tip(p20, bool(CONFIG["tips"].get("return_tips", True)))
    protocol.comment(
        f"Print complete: {len(resolved['clovers'])} clovers, "
        f"{resolved['deposits']} deposits, {resolved['deposits'] * volume:g} uL total."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("source", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "v12")
    protocol.comment(f"=== Four Clover Paper Print {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Four Clover Paper Print {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_clovers(protocol, labware, p20, resolved)
    protocol.comment(f"=== Four Clover Paper Print {label} Completed ===")
