"""Deterministic resolution of four-clover experiments into physical plans.

There is exactly ONE geometry implementation in this repository, and it is not
here. The pure resolver functions inside
``src/protocols/printing/02_printing_four_clover.py`` -- frozen byte-for-byte from
the physically validated v12 executor -- compute every droplet coordinate. This
module loads that engine and feeds it a configuration; it never reimplements the
arithmetic, so a preview can never disagree with what the robot will do.

Two entry points share one validation path:

    resolve_experiment_job(job)      a schema-validated, profile-referencing job
                                     (the AI-facing and template-driven route)
    resolve_manual_config(mapping)   a hand-written flat executor config
                                     (the manual fallback ground truth)

Both produce a :class:`ResolvedCloverPlanV1`, so the two routes can be compared
for physical equivalence.
"""
from __future__ import annotations

import importlib.util
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from .schemas import (
    DROPLET_KEYS,
    FourCloverExperimentJobV1,
    ResolvedCloverPlanV1,
)


EXECUTOR = (
    Path(REPO_ROOT) / "src" / "protocols" / "printing" / "02_printing_four_clover.py"
)
LABWARE_DIR = Path(REPO_ROOT) / "labware"


class CloverResolutionError(ValueError):
    """The experiment cannot be turned into a safe, physically valid plan."""


def _numpy_trapz_shim() -> None:
    """opentrons_shared_data still imports numpy.trapz, removed in numpy 2."""
    try:
        import numpy
    except ImportError:  # pragma: no cover - numpy ships with opentrons
        return
    if not hasattr(numpy, "trapz") and hasattr(numpy, "trapezoid"):
        numpy.trapz = numpy.trapezoid


@lru_cache(maxsize=1)
def geometry_engine():
    """Import the frozen clover executor as a plain module.

    Importing it costs one opentrons import and is cached. Nothing here creates a
    ProtocolContext, so no robot, simulator, or deck state is involved.
    """
    _numpy_trapz_shim()
    spec = importlib.util.spec_from_file_location("clover_geometry_engine", EXECUTOR)
    if spec is None or spec.loader is None:  # pragma: no cover - path is fixed
        raise CloverResolutionError(f"cannot import the clover executor: {EXECUTOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=8)
def _labware_definition(load_name: str) -> dict[str, Any]:
    path = LABWARE_DIR / f"{load_name}.json"
    if not path.is_file():
        raise CloverResolutionError(
            f"custom labware {load_name}.json not found in {LABWARE_DIR}; "
            "coordinates are read straight from the labware definition"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _well_centres(load_name: str) -> dict[str, tuple[float, float]]:
    definition = _labware_definition(load_name)
    return {
        name: (float(well["x"]), float(well["y"]))
        for name, well in definition["wells"].items()
    }


# ── job -> flat executor configuration ────────────────────────────────────────────

def build_executor_config(job: FourCloverExperimentJobV1) -> dict[str, Any]:
    """Merge a laboratory profile with a scientist's experiment.

    The result is exactly the mapping the deterministic executor consumes. The
    experiment contributes only scientific values; every hardware value comes from
    the profile, so an agent cannot reach a calibrated number by any path.
    """
    machine = job.machine
    experiment = job.experiment

    deck = {
        role: {
            key: value
            for key, value in slot.model_dump(mode="json").items()
            if value is not None
        }
        for role, slot in (
            ("source", machine.deck.source),
            ("paper", machine.deck.paper),
            ("tiprack_p20", machine.deck.tiprack_p20),
        )
    }

    clovers: list[dict[str, Any]] = []
    for placement in experiment.clovers:
        entry: dict[str, Any] = {
            "name": placement.name,
            "reference_well": placement.reference_well,
            "x_offset_mm": placement.x_offset_mm,
            "y_offset_mm": placement.y_offset_mm,
        }
        if placement.geometry is not None:
            entry["geometry"] = placement.geometry.model_dump(mode="json")
        if placement.layers is not None:
            entry["layers"] = placement.layers
        clovers.append(entry)

    return {
        "protocol_version": machine.protocol_version,
        "protocol_label": experiment.metadata.experiment_id[:24],
        "deck": deck,
        "pipette": machine.pipette.model_dump(mode="json"),
        "source": {
            "kind": machine.source_handling.kind,
            "well": experiment.source.well,
            "material": experiment.source.liquid_id,
            "loaded_volume_ul": experiment.source.loaded_volume_ul,
            "minimum_remaining_ul": experiment.source.minimum_remaining_ul,
            "aspirate_height_mm": machine.source_handling.aspirate_height_mm,
            "park_height_mm": machine.source_handling.park_height_mm,
        },
        "printing": {
            "droplet_volume_ul": experiment.printing.droplet_volume_ul,
            "pre_air_chase_ul": machine.print_release.pre_air_chase_ul,
            "dispense_height_mm": machine.print_release.dispense_height_mm,
            "air_gap_ul": machine.print_release.air_gap_ul,
            "air_gap_height_mm": machine.print_release.air_gap_height_mm,
            "push_out_ul": machine.print_release.push_out_ul,
            "blow_out": machine.print_release.blow_out,
            "inter_drop_delay_s": experiment.printing.inter_drop_delay_s,
            "inter_layer_delay_s": experiment.printing.inter_layer_delay_s,
            "inter_clover_delay_s": experiment.printing.inter_clover_delay_s,
            "layers": experiment.printing.layers,
        },
        "order": {"mode": experiment.printing.order},
        "destination": {
            "default_clover_geometry": experiment.default_geometry.model_dump(
                mode="json"
            ),
            "clover_grid": {"enabled": False},
            "manual_clover_centers": clovers,
            "paper_bounds": machine.paper_bounds.model_dump(mode="json"),
        },
        "validation": machine.validation.model_dump(mode="json"),
        "tips": {
            "return_tips": machine.tips.return_tips,
            "p20": {"print_tip": machine.tips.print_tip},
        },
        "flow_rates": {
            "p20": {
                "aspirate": machine.flow_rates.aspirate_ul_s,
                "dispense": machine.flow_rates.dispense_ul_s,
            }
        },
        "safety": machine.safety.model_dump(mode="json"),
    }


# ── the single validation and resolution path ─────────────────────────────────────

def resolve_executor_config(
    config: dict[str, Any],
    *,
    job_id: str,
    experiment_id: str,
) -> ResolvedCloverPlanV1:
    """Validate one flat executor configuration and resolve its coordinates.

    Fails closed. Every check the executor performs during its own pre-flight is
    performed here too, before anything is generated, so an operator or an agent
    sees the offending field at the terminal instead of inside a simulation
    traceback.
    """
    engine = geometry_engine()
    problems: list[str] = []
    warnings: list[str] = []

    deck = config.get("deck") or {}
    for role in ("source", "paper", "tiprack_p20"):
        if role not in deck:
            problems.append(f"deck.{role} is missing")
    slots: dict[int, str] = {}
    for role, spec in deck.items():
        slot = (spec or {}).get("slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or not 1 <= slot <= 11:
            problems.append(f"deck.{role}.slot must be an integer 1-11, got {slot!r}")
            continue
        if slot in slots:
            problems.append(
                f"deck slot {slot} is claimed by both {slots[slot]!r} and {role!r}"
            )
        slots[slot] = role
    if problems:
        raise CloverResolutionError(_render(problems))

    paper_load_name = deck["paper"].get("load_name")
    if not paper_load_name:
        raise CloverResolutionError(_render(["deck.paper.load_name is missing"]))
    centres = _well_centres(paper_load_name)
    paper_surface_mm = float(_labware_definition(paper_load_name)["wells"]["A1"]["z"])

    printing = config.get("printing") or {}
    safety = config.get("safety") or {}
    p20_max = float(safety.get("p20_max_volume_ul", 20.0))

    volume = printing.get("droplet_volume_ul")
    if (
        not isinstance(volume, (int, float))
        or isinstance(volume, bool)
        or not 0 < float(volume) <= p20_max
    ):
        problems.append(
            f"printing.droplet_volume_ul must be a number in (0, {p20_max:g}], "
            f"got {volume!r}"
        )
        volume = 0.0
    volume = float(volume)

    for key in (
        "dispense_height_mm", "air_gap_ul", "air_gap_height_mm", "push_out_ul",
        "pre_air_chase_ul", "inter_drop_delay_s", "inter_layer_delay_s",
        "inter_clover_delay_s",
    ):
        value = printing.get(key, 0.0) or 0.0
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"printing.{key} must be numeric, got {value!r}")
        elif float(value) < 0:
            problems.append(f"printing.{key} must be >= 0, got {value!r}")

    layers = printing.get("layers", 1)
    if isinstance(layers, bool) or not isinstance(layers, int) or layers < 1:
        problems.append(f"printing.layers must be an integer >= 1, got {layers!r}")

    source = config.get("source") or {}
    for name in ("well", "loaded_volume_ul", "aspirate_height_mm"):
        if name not in source:
            problems.append(f"source.{name} is missing")

    destination = config.get("destination") or {}
    for spec in destination.get("manual_clover_centers") or []:
        well = str((spec or {}).get("reference_well", "")).upper()
        if well not in centres:
            problems.append(
                f"clover {(spec or {}).get('name', '?')!r}: reference_well {well!r} "
                "does not exist on the paper labware"
            )

    tip = ((config.get("tips") or {}).get("p20") or {}).get("print_tip")
    if not isinstance(tip, str) or not tip.strip():
        problems.append("tips.p20.print_tip must name a tip well, e.g. A1")

    if problems:
        raise CloverResolutionError(_render(problems))

    engine.CONFIG = config
    well_xy = lambda name: centres[str(name).upper()]  # noqa: E731
    try:
        clovers = engine._resolve_clovers(well_xy)
        order_mode, execution = engine._print_order(clovers)
        bounds = engine._paper_bounds(well_xy, list(centres))
    except (TypeError, ValueError, KeyError) as exc:
        raise CloverResolutionError(f"coordinate resolution failed: {exc}") from exc

    if not clovers:
        raise CloverResolutionError(_render(["no clover centres were resolved"]))
    warnings.extend(bounds.get("notes") or [])

    air_gap = float(printing.get("air_gap_ul", 0.0) or 0.0)
    chase = float(printing.get("pre_air_chase_ul", 0.0) or 0.0)
    validation = config.get("validation") or {}
    radius = float(validation.get("droplet_radius_mm", 0.0) or 0.0)

    problems.extend(engine._capacity_errors(clovers, volume, air_gap, p20_max))
    # A droplet off the paper is a physical mess, not a judgement call, so a
    # boundary violation is fatal regardless of validation.mode.
    problems.extend(engine._boundary_violations(clovers, bounds, radius))

    intra, inter = engine._distance_report(clovers)
    mode = str(validation.get("mode", "warn")).lower()
    if mode not in {"warn", "error"}:
        problems.append(f"validation.mode must be warn or error, got {mode!r}")
        mode = "error"
    allow_duplicates = bool(validation.get("allow_duplicate_droplet_positions", False))
    min_intra = float(validation.get("min_intra_clover_distance_mm", 0.0) or 0.0)
    min_inter = float(validation.get("min_inter_clover_distance_mm", 0.0) or 0.0)
    report = problems.append if mode == "error" else warnings.append
    for entry in intra:
        if entry["min_distance"] <= 1e-6 and not allow_duplicates:
            problems.append(
                f"{entry['clover']}: {entry['pair'][0]} and {entry['pair'][1]} share "
                "the same coordinate"
            )
        elif entry["min_distance"] < min_intra:
            report(
                f"{entry['clover']}: droplets {entry['pair'][0]}/{entry['pair'][1]} "
                f"are {entry['min_distance']:.2f} mm apart, below "
                f"min_intra_clover_distance_mm {min_intra:g}"
            )
    for entry in inter:
        if entry["min_distance"] < min_inter:
            report(
                f"{entry['clovers'][0]} and {entry['clovers'][1]} approach to "
                f"{entry['min_distance']:.2f} mm, below "
                f"min_inter_clover_distance_mm {min_inter:g}"
            )

    layer_total = sum(int(clover["layers"]) for clover in clovers)
    deposits = layer_total * len(DROPLET_KEYS)
    required = deposits * volume
    loaded = float(source["loaded_volume_ul"])
    reserve = float(source.get("minimum_remaining_ul", 0.0) or 0.0)
    aspirate_height = float(source["aspirate_height_mm"])

    vial_definition = _labware_definition(deck["source"]["load_name"])
    source_well = str(source["well"]).upper()
    if source_well not in vial_definition["wells"]:
        problems.append(
            f"source.well {source_well!r} does not exist on "
            f"{deck['source']['load_name']}"
        )
        submersion = 0.0
    else:
        well = vial_definition["wells"][source_well]
        depth = float(well["depth"])
        if not 0 < aspirate_height < depth:
            problems.append(
                f"source.aspirate_height_mm must be > 0 and < {depth:g} mm"
            )
        if loaded > float(well["totalLiquidVolume"]):
            problems.append(
                f"source.loaded_volume_ul {loaded:g} exceeds the vial capacity "
                f"{float(well['totalLiquidVolume']):g}"
            )
        submersion = math.pi * (float(well["diameter"]) / 2.0) ** 2 * aspirate_height

    if loaded < required + reserve:
        problems.append(
            f"insufficient source: the run needs {required:g} uL plus a {reserve:g} uL "
            f"reserve but source.loaded_volume_ul is {loaded:g}"
        )
    remaining = loaded - required
    if submersion and remaining <= submersion:
        problems.append(
            f"source would retain {remaining:g} uL, below the approximately "
            f"{submersion:g} uL needed to keep the {aspirate_height:g} mm aspiration "
            "height submerged"
        )

    tip_name = str(tip).upper()
    tiprack_load_name = deck["tiprack_p20"]["load_name"]
    if "96" in tiprack_load_name and tip_name not in centres:
        problems.append(f"tips.p20.print_tip {tip_name!r} is not a valid rack position")

    if problems:
        raise CloverResolutionError(_render(problems))

    dispense_standoff = float(printing["dispense_height_mm"])
    absolute_dispense = paper_surface_mm + dispense_standoff
    piston = engine._piston_load(chase, volume, air_gap)

    resolved_clovers = [
        {
            "name": clover["name"],
            "reference_well": clover["reference_well"],
            "center_offset_x_mm": clover["center_offset"][0],
            "center_offset_y_mm": clover["center_offset"][1],
            "center_x_mm": clover["center"][0],
            "center_y_mm": clover["center"][1],
            "geometry_source": clover["geometry_source"],
            "layers": int(clover["layers"]),
            "droplets": [
                {
                    "key": key,
                    "offset_x_mm": clover["droplets"][key]["offset"][0],
                    "offset_y_mm": clover["droplets"][key]["offset"][1],
                    "x_mm": clover["droplets"][key]["absolute"][0],
                    "y_mm": clover["droplets"][key]["absolute"][1],
                    "z_mm": absolute_dispense,
                }
                for key in DROPLET_KEYS
            ],
        }
        for clover in clovers
    ]

    drop_delay = float(printing.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(printing.get("inter_layer_delay_s", 0.0) or 0.0)
    clover_delay = float(printing.get("inter_clover_delay_s", 0.0) or 0.0)
    layer_boundaries = sum(max(int(c["layers"]) - 1, 0) for c in clovers)
    configured_delay = (
        drop_delay * len(execution)
        + layer_delay * layer_boundaries
        + clover_delay * max(len(clovers) - 1, 0)
    )

    return ResolvedCloverPlanV1.from_content(
        job_id=job_id,
        experiment_id=experiment_id,
        order=order_mode,
        droplet_volume_ul=volume,
        paper_surface_mm=paper_surface_mm,
        dispense_standoff_mm=dispense_standoff,
        absolute_dispense_mm=absolute_dispense,
        piston_load_ul=piston["total"],
        usable_box={
            "min_x": bounds["min_x"],
            "max_x": bounds["max_x"],
            "min_y": bounds["min_y"],
            "max_y": bounds["max_y"],
        },
        clovers=resolved_clovers,
        execution_order=[
            {"clover": clover["name"], "layer": layer, "droplet": key}
            for clover, layer, key in execution
        ],
        totals={
            "clover_count": len(clovers),
            "layer_total": layer_total,
            "deposit_count": deposits,
            "printed_liquid_ul": deposits * volume,
            "execution_steps": len(execution),
            "configured_delay_s": configured_delay,
        },
        source={
            "liquid_id": str(source.get("material", "source")),
            "well": source_well,
            "loaded_volume_ul": loaded,
            "required_volume_ul": required,
            "remaining_volume_ul": remaining,
            "minimum_remaining_ul": reserve,
            "submersion_volume_ul": submersion,
            "submerged_margin_ul": remaining - submersion,
        },
        minimum_intra_clover_distance_mm=(
            min(entry["min_distance"] for entry in intra) if intra else None
        ),
        minimum_inter_clover_distance_mm=(
            min(entry["min_distance"] for entry in inter) if inter else None
        ),
        warnings=warnings,
        executor_config=config,
    )


def resolve_experiment_job(job: FourCloverExperimentJobV1) -> ResolvedCloverPlanV1:
    """Resolve a schema-validated, profile-referencing four-clover job."""
    return resolve_executor_config(
        build_executor_config(job),
        job_id=job.job_id,
        experiment_id=job.experiment.metadata.experiment_id,
    )


def resolve_manual_config(
    config: dict[str, Any], *, experiment_id: str = "manual_clover_config"
) -> ResolvedCloverPlanV1:
    """Resolve a hand-written flat executor config through the same checks.

    The manual fallback ground truth is intentionally not schema-bound, so its
    ``job_id`` is the content hash of the mapping itself rather than of a
    validated job model.
    """
    from ..canonical import canonical_sha256

    return resolve_executor_config(
        config,
        job_id=canonical_sha256(config),
        experiment_id=experiment_id,
    )


def _render(problems: list[str]) -> str:
    return "FOUR-CLOVER VALIDATION FAILED:\n- " + "\n- ".join(problems)
