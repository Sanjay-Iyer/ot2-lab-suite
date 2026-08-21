"""The two manual fallback printing workflows, exercised without the agent layer.

These tests guard the path an operator uses at the instrument laptop:

    configs/experiments/01_printing_standard.yaml
        -> src/protocols/printing/01_printing_standard.py
    configs/experiments/02_printing_four_clover.yaml
        -> src/protocols/printing/02_printing_four_clover.py

Nothing here imports an agent, a skill, or an approval workflow, and nothing here
contacts a robot. What is asserted is the science the operator actually cares
about: the printed layout, the dilution arithmetic, the delay placement, the
validated paper geometry, tip capacity, and the resolved clover coordinates.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.printing.standard.loader import load_experiment_job  # noqa: E402
from src.printing.standard.resolver import resolve_experiment_job  # noqa: E402


STANDARD_CONFIG = "configs/experiments/01_printing_standard.yaml"
STANDARD_EXECUTOR = REPO / "src" / "protocols" / "printing" / "01_printing_standard.py"
CLOVER_CONFIG = REPO / "configs" / "experiments" / "02_printing_four_clover.yaml"
CLOVER_EXECUTOR = REPO / "src" / "protocols" / "printing" / "02_printing_four_clover.py"
VALIDATED_CLOVER = REPO / "src" / "protocols" / "printing" / "12_four_clover_paper_print.py"
PAPER_LABWARE = REPO / "labware" / "paper_print_96_flat.json"
RUNNER = REPO / "scripts" / "run_printing_workflow.py"

CONFIG_END = "# <<< CONFIG END <<<"

# The physically confirmed printing geometry. Changing any of these three numbers
# is a laboratory decision that requires revalidating on the instrument.
PAPER_SURFACE_MM = 6.0
PRINT_STANDOFF_MM = 0.5
ABSOLUTE_DISPENSE_MM = 6.5


# ── fixtures ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def standard_plan():
    return resolve_experiment_job(load_experiment_job(STANDARD_CONFIG))


@pytest.fixture(scope="module")
def clover_engine():
    """Import the frozen clover executor as a plain module (no ProtocolContext)."""
    try:
        import numpy
    except ImportError:  # pragma: no cover - numpy ships with opentrons
        numpy = None
    if numpy is not None and not hasattr(numpy, "trapz") and hasattr(numpy, "trapezoid"):
        numpy.trapz = numpy.trapezoid
    spec = importlib.util.spec_from_file_location("clover_engine_test", CLOVER_EXECUTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def paper_wells() -> dict[str, tuple[float, float]]:
    definition = json.loads(PAPER_LABWARE.read_text(encoding="utf-8"))
    return {
        name: (float(well["x"]), float(well["y"]))
        for name, well in definition["wells"].items()
    }


@pytest.fixture(scope="module")
def clover_config() -> dict:
    loaded = yaml.safe_load(CLOVER_CONFIG.read_text(encoding="utf-8"))
    loaded.pop("run_modes", None)
    return loaded


# ── the fallback must not depend on the agent layer ───────────────────────────────

def test_manual_deliverables_exist() -> None:
    for path in (STANDARD_EXECUTOR, CLOVER_EXECUTOR, CLOVER_CONFIG, RUNNER):
        assert path.is_file(), f"missing manual-workflow file: {path}"
    assert (REPO / STANDARD_CONFIG).is_file()


@pytest.mark.parametrize(
    "path", [STANDARD_EXECUTOR, CLOVER_EXECUTOR, RUNNER], ids=lambda p: p.name
)
def test_manual_path_imports_no_agent_layer(path: Path) -> None:
    """A protocol or runner that reaches for the agent stack is not a fallback."""
    text = path.read_text(encoding="utf-8")
    for forbidden in (
        "src.agents",
        "from src import agents",
        "langchain",
        "anthropic",
        "openai",
        "google.generativeai",
    ):
        assert forbidden not in text, f"{path.name} references {forbidden}"


# ── validated paper geometry ──────────────────────────────────────────────────────

def test_paper_surface_height_is_pinned() -> None:
    definition = json.loads(PAPER_LABWARE.read_text(encoding="utf-8"))
    heights = {float(well["z"]) for well in definition["wells"].values()}
    assert heights == {PAPER_SURFACE_MM}


def test_standard_prints_at_the_validated_standoff(standard_plan) -> None:
    prints = [a for a in standard_plan.actions if a.action == "PRINT"]
    assert prints, "the standard plan prints nothing"
    assert {a.destination.reference for a in prints} == {"bottom"}
    assert {float(a.destination.z_mm) for a in prints} == {PRINT_STANDOFF_MM}


def test_clover_prints_at_the_validated_standoff(clover_config) -> None:
    assert clover_config["printing"]["dispense_height_mm"] == PRINT_STANDOFF_MM
    assert PAPER_SURFACE_MM + PRINT_STANDOFF_MM == ABSOLUTE_DISPENSE_MM


# ── workflow 1: the four-column SERS experiment ───────────────────────────────────

def _printed_by_well(plan) -> dict[str, list[tuple[str, float]]]:
    printed: dict[str, list[tuple[str, float]]] = {}
    for action in plan.actions:
        if action.action == "PRINT":
            printed.setdefault(action.destination.well, []).append(
                (action.liquid_id, float(action.volume_ul))
            )
    return printed


def test_standard_uses_only_the_four_intended_columns(standard_plan) -> None:
    printed = _printed_by_well(standard_plan)
    assert len(printed) == 32
    assert {int(well[1:]) for well in printed} == {1, 2, 3, 4}
    assert {well[0] for well in printed} == set("ABCDEFGH")


NANOPARTICLE_SERIES = [
    "np_stock", "np_1_2x", "np_1_4x", "np_1_8x",
    "np_1_16x", "np_1_32x", "np_1_64x", "np_1_128x",
]
CRYSTAL_VIOLET_SERIES = [
    "cv_stock", "cv_1_2x", "cv_1_4x", "cv_1_8x",
    "cv_1_16x", "cv_1_32x", "cv_1_64x", "cv_1_128x",
]


def test_column_1_is_one_nanoparticle_drop_then_stock_cv(standard_plan) -> None:
    printed = _printed_by_well(standard_plan)
    for row, liquid in zip("ABCDEFGH", NANOPARTICLE_SERIES):
        assert printed[f"{row}1"] == [(liquid, 5.0), ("cv_stock", 5.0)]


def test_column_2_is_three_nanoparticle_layers_then_stock_cv(standard_plan) -> None:
    printed = _printed_by_well(standard_plan)
    for row, liquid in zip("ABCDEFGH", NANOPARTICLE_SERIES):
        assert printed[f"{row}2"] == [
            (liquid, 5.0), (liquid, 5.0), (liquid, 5.0), ("cv_stock", 5.0)
        ]
        nanoparticle_ul = sum(
            volume for name, volume in printed[f"{row}2"] if name.startswith("np")
        )
        assert nanoparticle_ul == 15.0


def test_column_3_is_stock_cv_only(standard_plan) -> None:
    printed = _printed_by_well(standard_plan)
    for row in "ABCDEFGH":
        assert printed[f"{row}3"] == [("cv_stock", 5.0)]


def test_column_4_is_the_crystal_violet_series_without_nanoparticles(
    standard_plan,
) -> None:
    printed = _printed_by_well(standard_plan)
    for row, liquid in zip("ABCDEFGH", CRYSTAL_VIOLET_SERIES):
        assert printed[f"{row}4"] == [(liquid, 5.0)]
        assert not any(name.startswith("np") for name, _ in printed[f"{row}4"])


def test_drying_delays_land_between_the_intended_passes(standard_plan) -> None:
    """One rest before column 1's CV, and one after each column-2 layer."""
    actions = standard_plan.actions
    delays = [a for a in actions if a.action == "DELAY"]
    assert len(delays) == 4
    assert {float(a.duration_s) for a in delays} == {300.0}
    assert standard_plan.totals.configured_experimental_delay_s == 1200.0

    boundaries = []
    for index, action in enumerate(actions):
        if action.action != "DELAY":
            continue
        before = next(a for a in reversed(actions[:index]) if a.action == "PRINT")
        after = next(a for a in actions[index + 1:] if a.action == "PRINT")
        boundaries.append(
            (
                before.destination.well, before.liquid_id, before.drop_index,
                after.destination.well, after.liquid_id, after.drop_index,
            )
        )
    assert boundaries == [
        # column 1: last nanoparticle drop -> first stock CV drop
        ("H1", "np_1_128x", 1, "A1", "cv_stock", 1),
        # column 2: between each of the three nanoparticle layers ...
        ("H2", "np_1_128x", 1, "A2", "np_stock", 2),
        ("H2", "np_1_128x", 2, "A2", "np_stock", 3),
        # ... and before the stock CV that follows them
        ("H2", "np_1_128x", 3, "A2", "cv_stock", 1),
    ]


@pytest.mark.parametrize("preparation_id", ["np", "cv"])
def test_twofold_series_keeps_thirty_microlitres_usable(
    standard_plan, preparation_id: str
) -> None:
    """Eight twofold points, each retaining >= 30 uL once the next one is drawn."""
    step = next(
        s for s in standard_plan.preparation_math
        if s["preparation_id"] == preparation_id
    )
    assert step["fold"] == 2
    assert step["factors"] == [1, 2, 4, 8, 16, 32, 64, 128]
    assert len(step["products"]) == 8
    assert len(step["destination_wells"]) == 8
    for retained in step["retained_usable_ul"]:
        assert retained >= step["target_usable_ul"] - 1e-9

    # A twofold step means equal parts carried forward and diluent added, and
    # every well must balance: what arrives minus what leaves is what is kept.
    for index, retained in enumerate(step["retained_usable_ul"]):
        carried_in = step["outgoing_ul"][index - 1] if index else 0.0
        arriving = (step["stock_allocation_ul"] if index == 0 else carried_in)
        arriving += step["diluent_ul"][index]
        assert arriving == pytest.approx(step["pre_transfer_ul"][index])
        assert arriving - step["outgoing_ul"][index] == pytest.approx(retained)
        if index:
            assert step["diluent_ul"][index] == pytest.approx(carried_in)


def test_nanoparticle_stock_consumption_is_about_sixty_microlitres(
    standard_plan,
) -> None:
    stock = next(
        liquid for liquid in standard_plan.initial_liquids
        if liquid.liquid_id == "np_stock"
    )
    assert 55.0 <= stock.scientific_allocation_ul <= 65.0


def test_every_moved_volume_fits_the_p20(standard_plan) -> None:
    pipette = next(a for a in standard_plan.actions if a.action == "LOAD_PIPETTE")
    minimum, maximum = float(pipette.minimum_volume_ul), float(pipette.maximum_volume_ul)
    for action in standard_plan.actions:
        if action.action in {"TRANSFER", "MIX"}:
            assert minimum <= float(action.volume_ul) <= maximum, action.sequence_index
        elif action.action == "PRINT":
            assert float(action.piston_dispense_ul) <= maximum, action.sequence_index


def test_standard_fits_the_loaded_tip_rack(standard_plan) -> None:
    tiprack = next(
        a for a in standard_plan.actions
        if a.action == "LOAD_LABWARE" and a.role == "tiprack"
    )
    assert "96" in tiprack.load_name
    assert standard_plan.totals.tip_count <= 96


def test_standard_sources_stay_submerged(standard_plan) -> None:
    assert standard_plan.source_accessibility.status == "PASS"


# ── workflow 2: the four-clover pattern ───────────────────────────────────────────

def test_clover_engine_is_frozen_from_the_validated_implementation() -> None:
    """Everything below the CONFIG block must match v12 byte for byte.

    The clover geometry was validated on the instrument. This test is what makes
    "we did not redesign it" checkable rather than a claim in a docstring.
    """
    validated = VALIDATED_CLOVER.read_text(encoding="utf-8")
    frozen = CLOVER_EXECUTOR.read_text(encoding="utf-8")
    assert CONFIG_END in validated and CONFIG_END in frozen
    assert frozen[frozen.index(CONFIG_END):] == validated[validated.index(CONFIG_END):]


def test_clover_resolves_four_positions_per_pattern(
    clover_engine, clover_config, paper_wells
) -> None:
    clover_engine.CONFIG = clover_config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    assert clovers, "no clover centres resolved"
    for clover in clovers:
        assert set(clover["droplets"]) == set(clover_engine.DROPLET_KEYS)
        assert len(clover["droplets"]) == 4


def test_clover_droplets_sit_symmetrically_around_their_centre(
    clover_engine, clover_config, paper_wells
) -> None:
    clover_engine.CONFIG = clover_config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    for clover in clovers:
        centre_x, centre_y = clover["center"]
        offsets = {
            key: clover["droplets"][key]["offset"]
            for key in clover_engine.DROPLET_KEYS
        }
        half_width = abs(offsets["d1"][0])
        half_height = abs(offsets["d1"][1])
        assert offsets == {
            "d1": (-half_width, half_height),
            "d2": (half_width, half_height),
            "d3": (-half_width, -half_height),
            "d4": (half_width, -half_height),
        }
        for key, (dx, dy) in offsets.items():
            x, y = clover["droplets"][key]["absolute"]
            assert x == pytest.approx(centre_x + dx)
            assert y == pytest.approx(centre_y + dy)


def test_clover_coordinates_match_the_committed_baseline(
    clover_engine, clover_config, paper_wells
) -> None:
    """Frozen coordinates for the shipped spacing sweep, in paper-local mm."""
    clover_engine.CONFIG = clover_config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    resolved = {
        clover["name"]: {
            key: tuple(round(value, 3) for value in clover["droplets"][key]["absolute"])
            for key in clover_engine.DROPLET_KEYS
        }
        for clover in clovers
    }
    assert resolved == {
        "sep_2mm": {
            "d1": (22.38, 66.24), "d2": (24.38, 66.24),
            "d3": (22.38, 64.24), "d4": (24.38, 64.24),
        },
        "sep_3mm": {
            "d1": (48.88, 66.74), "d2": (51.88, 66.74),
            "d3": (48.88, 63.74), "d4": (51.88, 63.74),
        },
        "sep_4mm": {
            "d1": (75.38, 67.24), "d2": (79.38, 67.24),
            "d3": (75.38, 63.24), "d4": (79.38, 63.24),
        },
        "sep_5mm": {
            "d1": (101.88, 67.74), "d2": (106.88, 67.74),
            "d3": (101.88, 62.74), "d4": (106.88, 62.74),
        },
    }


def test_clover_droplets_stay_on_the_paper(
    clover_engine, clover_config, paper_wells
) -> None:
    clover_engine.CONFIG = clover_config
    well_xy = lambda name: paper_wells[str(name).upper()]  # noqa: E731
    clovers = clover_engine._resolve_clovers(well_xy)
    bounds = clover_engine._paper_bounds(well_xy, list(paper_wells))
    radius = float(clover_config["validation"]["droplet_radius_mm"])
    assert clover_engine._boundary_violations(clovers, bounds, radius) == []


def test_clover_piston_load_fits_the_p20(clover_engine, clover_config, paper_wells) -> None:
    clover_engine.CONFIG = clover_config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    printing = clover_config["printing"]
    maximum = float(clover_config["safety"]["p20_max_volume_ul"])
    assert clover_engine._capacity_errors(
        clovers,
        float(printing["droplet_volume_ul"]),
        float(printing.get("air_gap_ul", 0.0) or 0.0),
        maximum,
    ) == []
    load = clover_engine._piston_load(
        printing.get("pre_air_chase_ul", 0.0),
        printing["droplet_volume_ul"],
        printing.get("air_gap_ul", 0.0),
    )
    assert load["liquid"] == 5.0
    assert load["total"] <= maximum


def test_clover_uses_one_tip_and_has_enough_source(
    clover_engine, clover_config, paper_wells
) -> None:
    clover_engine.CONFIG = clover_config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    deposits = sum(c["layers"] for c in clovers) * len(clover_engine.DROPLET_KEYS)
    required = deposits * float(clover_config["printing"]["droplet_volume_ul"])
    source = clover_config["source"]
    reserve = float(source.get("minimum_remaining_ul", 0.0) or 0.0)
    assert float(source["loaded_volume_ul"]) >= required + reserve
    # One tip is picked up and held for the whole print, so a 96-tip rack is ample.
    assert str(clover_config["tips"]["p20"]["print_tip"]).upper() == "A1"


def test_clover_repeat_and_delay_come_from_the_config(
    clover_engine, clover_config, paper_wells
) -> None:
    """Layers and inter-layer rests are YAML fields, not Python branches."""
    config = json.loads(json.dumps(clover_config))
    config["printing"]["layers"] = 3
    config["printing"]["inter_layer_delay_s"] = 300.0
    clover_engine.CONFIG = config
    clovers = clover_engine._resolve_clovers(lambda name: paper_wells[str(name).upper()])
    assert {clover["layers"] for clover in clovers} == {3}
    _, plan = clover_engine._print_order(clovers)
    assert len(plan) == len(clovers) * 3 * 4
    # Order is deterministic: one clover finished before the next begins.
    assert [entry[0]["name"] for entry in plan[:12]] == [clovers[0]["name"]] * 12
