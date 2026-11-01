"""Representative NanoDrop plans for golden motion traces. Each starts from the shipped demo config.

Numbers here select plans to record; they are not recommendations and carry no physical validation. SOFTWARE_ONLY plans
exist to cover code branches and should not be read as methods anyone has used at the bench.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from src.agents.dye_demo.compiler import compile_plan
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config


def _three(config: dict[str, Any]) -> None:
    config["dilution"].update(factors=[2, 5, 10], total_volume_ul=100.0)


def default_eight_dilutions(config):
    pass


def dilution_only(config):
    config["print"]["enabled"] = False


def print_only_prepared(config):
    _three(config)
    config["dilution"].update(enabled=False, prepared_volume_ul=100.0)


def print_only_vials_off_deck(config):
    _three(config)
    config["dilution"].update(enabled=False, prepared_volume_ul=100.0)
    config["deck"]["tuberack"]["slot"] = "OFF_DECK"
    config["print"].update(droplets_per_spot=3, paper_start_column=2)
    config["tips"]["start_tip"] = "D1"


def changed_factors_and_volume(config):
    config["dilution"].update(factors=[1.5, 3, 7, 20], total_volume_ul=200.0, plate_column="9", start_row="C")


def small_remainder_rebalanced(config):
    """95.51 uL of sample at 19 uL per transfer leaves 0.51 uL, under the P20 minimum, so the last two transfers share."""
    config["dilution"].update(factors=[1.047], total_volume_ul=100.0)


def replicates(config):
    _three(config)
    config["print"]["replicates"] = 3


def stacked_drops(config):
    _three(config)
    config["print"]["droplets_per_spot"] = 4


def two_drop_volumes(config):
    _three(config)
    config["print"].update(droplet_volume_ul=[5.0, 10.0], replicates=2)


def explicit_source_map(config):
    _three(config)
    config["print"]["source_map"] = [{"source": "A11", "positions": ["A1", "B2", "H12"]},
                                     {"source": "C11", "positions": ["D5"], "volume_ul": 2.5}]


def drops_per_position(config):
    _three(config)
    config["print"]["drops_at"] = {"A1": 3, "C1": 2}


def selected_dilutions_on_named_columns(config):
    _three(config)
    config["print"].update(source=["dilution:1", "dilution:3"], columns=[4, 7])


def per_source_counts_and_rows(config):
    _three(config)
    config["print"]["source"] = [{"source": "dilution:1", "count": 3}, {"source": "dilution:2", "rows": ["C", "E"]},
                                 {"source": "dilution:3", "positions": ["H12"], "drops": 2}]


def sparse_rows_independent_paper_rows(config):
    _three(config)
    config["dilution"].update(rows=["B", "D", "H"], plate_column="10")
    config["print"]["paper_rows"] = ["A", "B", "C"]


def tips_per_liquid(config):
    _three(config)
    config["tips"].update(policy="per_liquid", return_tips=False)


def tips_new_every_transfer(config):
    _three(config)
    config["tips"].update(policy="new_tip_every_transfer", return_tips=False, start_tip="B3")


def tips_single_dropped(config):
    _three(config)
    config["tips"].update(policy="single_tip", return_tips=False)


def no_air_gap_blow_out_or_shake(config):
    _three(config)
    config["liquid_handling"].update(air_gap_ul=0.0, blow_out=False)
    config["liquid_handling"]["well_plate_shake"]["enabled"] = False


def no_mixing(config):
    _three(config)
    config["mixing"]["enabled"] = False


def print_release_variations(config):
    _three(config)
    config["print"].update(air_gap_ul=0.0, push_out_ul=0.0, blow_out=False, post_dispense_delay_s=0.0)


def paper_release_at_0_5_mm(config):
    _three(config)
    config["print"]["z_mm"] = 0.5


def mixing_and_shake_variations(config):
    _three(config)
    config["mixing"].update(reps=3, volume_ul=10.0)
    config["liquid_handling"]["well_plate_shake"].update(cycles=2, radius=0.8, v_offset_mm=-2.0, speed_mm_s=40.0)


def flow_rates_changed(config):
    _three(config)
    config["flow_rates"].update(aspirate=5.0, dispense=7.0)


def many_replicates_overflow(config):
    _three(config)
    config["print"]["replicates"] = 14


def second_run_avoids_printed_positions(config):
    _three(config)
    config["print"]["replicates"] = 2


def per_liquid_multi_position_sources(config):
    _three(config)
    config["tips"].update(policy="per_liquid", return_tips=False)
    config["print"]["source_map"] = [{"source": "A11", "positions": ["A1", "A2", "A3"]},
                                     {"source": "B11", "positions": ["B1", "B2"]}]


def several_tips_returned(config):
    _three(config)
    config["tips"].update(policy="per_liquid", return_tips=True)


def dry_run_moves_nothing(config):
    config["run_modes"]["dry_run"] = True


def moved_deck_and_vials(config):
    _three(config)
    config["deck"]["plate"]["slot"] = 1
    config["deck"]["paper"]["slot"] = 2
    config["deck"]["tuberack"]["slot"] = 8
    config["deck"]["tiprack"]["slot"] = 11
    config["materials"]["water"]["vial"] = "B1"
    config["materials"]["dye"]["vial"] = "B2"


CASES: dict[str, Callable[[dict[str, Any]], None]] = {function.__name__: function for function in (
    default_eight_dilutions, dilution_only, print_only_prepared, print_only_vials_off_deck, changed_factors_and_volume,
    small_remainder_rebalanced, replicates, stacked_drops, two_drop_volumes, explicit_source_map, drops_per_position,
    selected_dilutions_on_named_columns, per_source_counts_and_rows, sparse_rows_independent_paper_rows,
    tips_per_liquid, tips_new_every_transfer, tips_single_dropped, no_air_gap_blow_out_or_shake, no_mixing,
    moved_deck_and_vials, print_release_variations, paper_release_at_0_5_mm, mixing_and_shake_variations,
    flow_rates_changed, many_replicates_overflow, second_run_avoids_printed_positions,
    per_liquid_multi_position_sources, several_tips_returned, dry_run_moves_nothing,
)}

# Paper positions an earlier live run already printed, as a second run sees them.
OCCUPIED = {"second_run_avoids_printed_positions": {"A1", "B1", "C1"}}

SOFTWARE_ONLY = {"no_air_gap_blow_out_or_shake", "print_release_variations", "small_remainder_rebalanced",
                 "mixing_and_shake_variations", "flow_rates_changed", "many_replicates_overflow"}


def compiled_config(name: str) -> dict[str, Any]:
    """The exact config a run of this case hands the protocol: the PlanSpec after compile_plan, as session._run does."""
    spec = deepcopy(load_config(DEFAULT_CONFIG))
    CASES[name](spec)
    compiled = compile_plan(spec, occupied=OCCUPIED.get(name, ()))
    if not compiled.ok:
        raise ValueError(f"golden case {name} does not compile: {compiled.errors}")
    return deepcopy(compiled.config)
