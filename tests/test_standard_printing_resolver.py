"""Stage 2 tests: the generic resolver does exactly what configuration says.

Every experiment in this module is deliberately unrelated to Experiment 01. The
point is to show that behaviour is driven by configuration, not by a hidden
experiment baked into the resolver or the executor.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.printing.standard import equivalence
from src.printing.standard.resolver import resolve_experiment_job


def _liquid(liquid_id, well, *, labware="vial_rack", volume=4000.0, reserve=2600.0):
    return {
        "liquid_id": liquid_id,
        "display_name": liquid_id.replace("_", " ").title(),
        "location": {"labware": labware, "well": well},
        "loaded_volume_ul": volume,
        "minimum_remaining_ul": reserve,
    }


def _experiment(experiment_id, liquids, procedure, **extra):
    return {
        "metadata": {"experiment_id": experiment_id, "title": experiment_id},
        "liquids": liquids,
        "procedure": procedure,
        **extra,
    }


def _actions(plan, kind):
    return [action for action in plan.action_dicts() if action["action"] == kind]


# --------------------------------------------------------------------------- #
# Configuration drives behaviour
# --------------------------------------------------------------------------- #


def test_printing_only_emits_no_transfers_mixes_or_delays(
    machine, single_source_experiment, make_job
):
    plan = resolve_experiment_job(make_job(machine, single_source_experiment))

    assert plan.totals.print_count == 2
    assert plan.totals.transfer_count == 0
    assert plan.totals.mix_count == 0
    assert plan.totals.delay_count == 0
    assert plan.preparation_math == []


def test_no_mix_configuration_emits_no_mix_actions(machine, make_job):
    experiment = _experiment(
        "no_mix",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "destination_wells": ["A1", "B1"],
                "fold": 2,
                "target_usable_volume_ul": 30.0,
            }
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))

    assert plan.totals.mix_count == 0
    assert plan.totals.transfer_count > 0


def test_mix_configuration_emits_exactly_the_requested_cycles(machine, make_job):
    experiment = _experiment(
        "with_mix",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "destination_wells": ["A1", "B1"],
                "fold": 2,
                "target_usable_volume_ul": 30.0,
                "mix": {"cycles": 4, "volume_ul": 6.0},
            }
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    mixes = _actions(plan, "MIX")

    assert len(mixes) == 2  # one after the stock aliquot, one after the cascade step
    assert all(mix["cycles"] == 4 and mix["volume_ul"] == 6.0 for mix in mixes)


def test_one_drop_and_multiple_drops_differ_only_by_configuration(machine, make_job):
    def build(repeats, delay):
        return _experiment(
            f"drops_{repeats}",
            [_liquid("dye", "A1")],
            [
                {
                    "id": "row",
                    "type": "print",
                    "source": {"kind": "liquid", "liquid_id": "dye"},
                    "substrate": "paper",
                    "targets": ["A1", "B1"],
                    "volume_ul": 5.0,
                    "repeats": repeats,
                    "delay_after_pass_s": delay,
                }
            ],
        )

    single = resolve_experiment_job(make_job(machine, build(1, 0.0)))
    triple = resolve_experiment_job(make_job(machine, build(3, 90.0)))

    assert single.totals.print_count == 2
    assert single.totals.delay_count == 0
    assert triple.totals.print_count == 6
    assert triple.totals.delay_count == 3
    assert [action["duration_s"] for action in _actions(triple, "DELAY")] == [90.0] * 3
    assert [action["drop_index"] for action in _actions(triple, "PRINT")] == [
        1, 1, 2, 2, 3, 3
    ]
    assert all(action["repeat_count"] == 3 for action in _actions(triple, "PRINT"))


def test_repeated_prints_interleave_passes_and_rests_in_order(machine, make_job):
    experiment = _experiment(
        "layers",
        [_liquid("dye", "A1")],
        [
            {
                "id": "row",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 4.0,
                "repeats": 4,
                "delay_after_pass_s": 45.0,
            }
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    sequence = [
        action["action"]
        for action in plan.action_dicts()
        if action["action"] in {"PRINT", "DELAY"}
    ]

    assert sequence == ["PRINT", "DELAY"] * 4


def test_delay_step_is_placed_exactly_where_configured(machine, make_job):
    experiment = _experiment(
        "explicit_delay",
        [_liquid("dye", "A1")],
        [
            {
                "id": "first",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 5.0,
            },
            {
                "id": "settle",
                "type": "delay",
                "duration_s": 45.0,
                "reason": "let the first deposit dry",
            },
            {
                "id": "second",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 5.0,
            },
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    kinds = [
        action["action"]
        for action in plan.action_dicts()
        if action["action"] in {"PRINT", "DELAY"}
    ]

    assert kinds == ["PRINT", "DELAY", "PRINT"]
    assert plan.totals.configured_experimental_delay_s == 45.0


def test_a_vial_source_and_a_plate_source_use_their_own_calibrated_heights(
    machine, make_job
):
    experiment = _experiment(
        "two_sources",
        [_liquid("vial_dye", "A1"), _liquid("plate_dye", "H12", labware="plate",
                                            volume=200.0, reserve=0.0)],
        [
            {
                "id": "from_vial",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "vial_dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 5.0,
            },
            {
                "id": "from_plate",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "plate_dye"},
                "substrate": "paper",
                "targets": ["B1"],
                "volume_ul": 5.0,
            },
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    prints = _actions(plan, "PRINT")

    assert prints[0]["source"] == {
        "labware": "vial_rack", "well": "A1", "reference": "bottom", "z_mm": 4.0
    }
    assert prints[1]["source"] == {
        "labware": "plate", "well": "H12", "reference": "bottom", "z_mm": 0.2
    }
    assert all(
        action["destination"]["labware"] == "paper"
        and action["destination"]["reference"] == "bottom"
        and action["destination"]["z_mm"] == 0.5
        for action in prints
    )


def test_transfers_are_split_to_the_configured_pipette_maximum(machine, make_job):
    experiment = _experiment(
        "chunking",
        [_liquid("dye", "A1")],
        [
            {
                "id": "bulk",
                "type": "transfer",
                "liquid_id": "dye",
                "destination": {"labware": "plate", "well": "A1"},
                "volume_ul": 55.0,
                "result_liquid_id": "working_dye",
            },
            {
                "id": "row",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "working_dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 5.0,
            },
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    transfers = _actions(plan, "TRANSFER")

    assert [action["volume_ul"] for action in transfers] == [20.0, 20.0, 15.0]
    assert [action["chunk_index"] for action in transfers] == [1, 2, 3]
    assert all(action["chunk_count"] == 3 for action in transfers)
    assert all(action["volume_ul"] <= 20.0 for action in transfers)


def test_transfer_source_must_match_the_declared_liquid(machine, make_job):
    experiment = _experiment(
        "mismatched_transfer_source",
        [_liquid("dye", "A1"), _liquid("other", "A2")],
        [
            {
                "id": "wrong_source",
                "type": "transfer",
                "liquid_id": "dye",
                "source": {"labware": "vial_rack", "well": "A2"},
                "destination": {"labware": "plate", "well": "A1"},
                "volume_ul": 12.0,
                "result_liquid_id": "working_dye",
            }
        ],
    )

    with pytest.raises(ValueError, match="does not match liquid"):
        resolve_experiment_job(make_job(machine, experiment))


def test_mix_location_must_match_the_declared_liquid(machine, make_job):
    experiment = _experiment(
        "mismatched_mix_location",
        [_liquid("dye", "A1"), _liquid("other", "A2")],
        [
            {
                "id": "wrong_mix",
                "type": "mix",
                "liquid_id": "dye",
                "location": {"labware": "vial_rack", "well": "A2"},
                "cycles": 2,
                "volume_ul": 4.0,
            }
        ],
    )

    with pytest.raises(ValueError, match="does not match liquid"):
        resolve_experiment_job(make_job(machine, experiment))


@pytest.mark.parametrize("destination_role", ["paper", "tiprack"])
def test_transfer_cannot_bypass_printing_or_target_a_tiprack(
    machine, make_job, destination_role
):
    experiment = _experiment(
        f"bad_destination_{destination_role}",
        [_liquid("dye", "A1")],
        [
            {
                "id": "bad_destination",
                "type": "transfer",
                "liquid_id": "dye",
                "destination": {"labware": destination_role, "well": "A1"},
                "volume_ul": 12.0,
                "result_liquid_id": "working_dye",
            }
        ],
    )

    with pytest.raises(ValueError, match="liquid transfers cannot target"):
        resolve_experiment_job(make_job(machine, experiment))


def test_transfer_result_id_must_not_alias_an_existing_liquid(machine, make_job):
    experiment = _experiment(
        "result_alias",
        [_liquid("dye", "A1"), _liquid("existing", "A2")],
        [
            {
                "id": "alias",
                "type": "transfer",
                "liquid_id": "dye",
                "destination": {"labware": "plate", "well": "A1"},
                "volume_ul": 12.0,
                "result_liquid_id": "existing",
            }
        ],
    )

    with pytest.raises(ValueError, match="already refers to"):
        resolve_experiment_job(make_job(machine, experiment))


def test_transfer_to_a_new_location_requires_a_distinct_result_id(machine, make_job):
    experiment = _experiment(
        "missing_result_alias",
        [_liquid("dye", "A1")],
        [
            {
                "id": "aliquot",
                "type": "transfer",
                "liquid_id": "dye",
                "destination": {"labware": "plate", "well": "A1"},
                "volume_ul": 12.0,
            }
        ],
    )

    with pytest.raises(ValueError, match="distinct result_liquid_id"):
        resolve_experiment_job(make_job(machine, experiment))


def test_direct_dilution_prepares_each_point_from_undiluted_stock(machine, make_job):
    experiment = _experiment(
        "direct",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "points",
                "type": "direct_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "points": [
                    {"well": "A1", "factor": 2, "total_volume_ul": 30.0},
                    {"well": "B1", "factor": 3, "total_volume_ul": 30.0},
                ],
                "mix": {"cycles": 2, "volume_ul": 5.0},
            },
            {
                "id": "row",
                "type": "print",
                "source": {"kind": "series", "preparation_id": "points"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 5.0,
                "tip_policy": "per_target",
            },
        ],
    )
    plan = resolve_experiment_job(make_job(machine, experiment))
    stock_transfers = [
        action
        for action in _actions(plan, "TRANSFER")
        if action["source"]["labware"] == "vial_rack"
        and action["source"]["well"] == "A1"
    ]

    assert [action["volume_ul"] for action in stock_transfers] == [15.0, 10.0]
    math = plan.preparation_math[0]
    assert math["method"] == "independent_direct_dilution"
    assert math["diluent_ul"] == [15.0, 20.0]


def test_serial_dilution_produces_the_configured_number_of_points(machine, make_job):
    for count, fold in ((2, 2), (4, 3), (6, 10)):
        wells = [f"{row}1" for row in "ABCDEFGH"][:count]
        experiment = _experiment(
            f"series_{count}_{fold}",
            [_liquid("dye", "A1"), _liquid("buffer", "A2")],
            [
                {
                    "id": "ladder",
                    "type": "serial_dilution",
                    "source_liquid_id": "dye",
                    "diluent_liquid_id": "buffer",
                    "destination_labware": "plate",
                    "destination_wells": wells,
                    "fold": fold,
                    "target_usable_volume_ul": 25.0,
                }
            ],
        )
        plan = resolve_experiment_job(make_job(machine, experiment))
        math = plan.preparation_math[0]

        assert math["factors"] == [fold**index for index in range(count)]
        assert math["retained_usable_ul"] == [25.0] * count
        assert len(math["products"]) == count


def test_direct_dilution_collision_permission_is_per_product(machine, make_job):
    experiment = _experiment(
        "partial_explicit_collision",
        [
            _liquid("dye", "A1"),
            _liquid("buffer", "A2"),
            _liquid("points_1_2x", "A3"),
        ],
        [
            {
                "id": "points",
                "type": "direct_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "points": [
                    {"well": "A1", "factor": 2, "total_volume_ul": 24.0},
                    {
                        "well": "A2",
                        "factor": 5,
                        "total_volume_ul": 24.0,
                        "product_liquid_id": "custom_fifth",
                    },
                ],
            }
        ],
    )

    with pytest.raises(ValueError, match="derived liquid name"):
        resolve_experiment_job(make_job(machine, experiment))


def test_direct_factor_one_product_may_reuse_its_source_id(machine, make_job):
    experiment = _experiment(
        "valid_direct_source_reuse",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "points",
                "type": "direct_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "points": [
                    {
                        "well": "A1",
                        "factor": 1,
                        "total_volume_ul": 24.0,
                        "product_liquid_id": "dye",
                    },
                    {"well": "A2", "factor": 5, "total_volume_ul": 24.0},
                ],
            }
        ],
    )

    plan = resolve_experiment_job(make_job(machine, experiment))

    assert plan.preparation_math[0]["products"][0] == "dye"


def test_direct_factor_one_cannot_reuse_an_unrelated_liquid_id(machine, make_job):
    experiment = _experiment(
        "invalid_direct_unrelated_reuse",
        [_liquid("dye", "A1"), _liquid("buffer", "A2"), _liquid("other", "A3")],
        [
            {
                "id": "points",
                "type": "direct_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "points": [
                    {
                        "well": "A1",
                        "factor": 1,
                        "total_volume_ul": 24.0,
                        "product_liquid_id": "other",
                    }
                ],
            }
        ],
    )

    with pytest.raises(ValueError, match="only the factor-1 product"):
        resolve_experiment_job(make_job(machine, experiment))


def test_explicit_series_reuse_is_limited_to_factor_one_source(machine, make_job):
    experiment = _experiment(
        "invalid_explicit_reuse",
        [_liquid("dye", "A1"), _liquid("buffer", "A2"), _liquid("other", "A3")],
        [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "destination_wells": ["A1", "A2"],
                "fold": 4,
                "target_usable_volume_ul": 24.0,
                "product_liquid_ids": ["dye", "other"],
            }
        ],
    )

    with pytest.raises(ValueError, match="only the factor-1 product"):
        resolve_experiment_job(make_job(machine, experiment))


def test_factor_one_product_may_explicitly_reuse_its_source_id(machine, make_job):
    experiment = _experiment(
        "valid_explicit_reuse",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "destination_wells": ["A1", "A2"],
                "fold": 4,
                "target_usable_volume_ul": 24.0,
                "product_liquid_ids": ["dye", "dye_quarter"],
            }
        ],
    )

    plan = resolve_experiment_job(make_job(machine, experiment))

    assert plan.preparation_math[0]["products"] == ["dye", "dye_quarter"]


def test_source_accessibility_reports_every_checked_action(
    machine, single_source_experiment, make_job
):
    plan = resolve_experiment_job(make_job(machine, single_source_experiment))
    accessibility = plan.source_accessibility

    assert accessibility.status == "PASS"
    assert accessibility.checked_aspirate_or_mix_actions == 2
    assert accessibility.ending_volumes_ul["vial_rack:A1"] == pytest.approx(3990.0)


# --------------------------------------------------------------------------- #
# Determinism and canonical identity
# --------------------------------------------------------------------------- #


def test_resolution_is_deterministic(machine, single_source_experiment, make_job):
    job = make_job(machine, single_source_experiment)
    first = resolve_experiment_job(job)
    second = resolve_experiment_job(job)

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.plan_id == second.plan_id


def test_canonical_identity_ignores_formatting_but_not_physics(
    machine, single_source_experiment, make_job
):
    job = make_job(machine, single_source_experiment)
    plan = resolve_experiment_job(job)

    relabelled = deepcopy(single_source_experiment)
    relabelled["metadata"]["title"] = "A completely different title"
    relabelled_plan = resolve_experiment_job(make_job(machine, relabelled))

    changed = deepcopy(single_source_experiment)
    changed["procedure"][0]["volume_ul"] = 6.0
    changed_plan = resolve_experiment_job(make_job(machine, changed))

    assert equivalence.physical_sha256(relabelled_plan) == equivalence.physical_sha256(plan)
    assert equivalence.physical_sha256(changed_plan) != equivalence.physical_sha256(plan)


def test_structural_trace_is_independent_of_every_identifier(machine, make_job):
    base = _experiment(
        "named_one",
        [_liquid("alpha", "A1")],
        [
            {
                "id": "step_one",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "alpha"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 5.0,
            }
        ],
    )
    renamed = _experiment(
        "named_two",
        [_liquid("omega", "A1")],
        [
            {
                "id": "different_step_name",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "omega"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 5.0,
            }
        ],
    )
    left = resolve_experiment_job(make_job(machine, base))
    right = resolve_experiment_job(make_job(machine, renamed))

    assert equivalence.physical_sha256(left) != equivalence.physical_sha256(right)
    assert equivalence.structural_sha256(left) == equivalence.structural_sha256(right)

    report = equivalence.compare_plans(left, right)
    assert report["structural_match"] is True
    assert report["physical_match"] is False
    assert any("liquid_id" in line for line in report["physical_differences"])


def test_tip_index_tracks_real_tip_changes(machine, make_job):
    experiment = _experiment(
        "tips",
        [_liquid("dye", "A1")],
        [
            {
                "id": "shared_tip",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "dye"},
                "substrate": "paper",
                "targets": ["A1", "B1", "C1"],
                "volume_ul": 5.0,
                "tip_policy": "per_step",
            }
        ],
    )
    shared = resolve_experiment_job(make_job(machine, experiment))

    per_target = deepcopy(experiment)
    per_target["metadata"]["experiment_id"] = "tips_per_target"
    per_target["procedure"][0]["tip_policy"] = "per_target"
    separate = resolve_experiment_job(make_job(machine, per_target))

    assert shared.totals.tip_count == 1
    assert separate.totals.tip_count == 3
    assert {
        record["tip_index"] for record in equivalence.physical_trace(separate)
        if "tip_index" in record
    } == {1, 2, 3}


def test_per_target_tips_distinguish_targets_in_the_same_row(machine, make_job):
    experiment = _experiment(
        "same_row_series",
        [_liquid("dye", "A1"), _liquid("buffer", "A2")],
        [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "dye",
                "diluent_liquid_id": "buffer",
                "destination_labware": "plate",
                "destination_wells": ["A1", "A2"],
                "fold": 4,
                "target_usable_volume_ul": 24.0,
            },
            {
                "id": "same_row",
                "type": "print",
                "source": {"kind": "series", "preparation_id": "ladder"},
                "substrate": "paper",
                "targets": ["A1", "A2"],
                "volume_ul": 4.0,
                "tip_policy": "per_target",
            },
        ],
    )

    plan = resolve_experiment_job(make_job(machine, experiment))
    prints = _actions(plan, "PRINT")

    assert [action["tip_group"] for action in prints] == [
        "same_row_A1",
        "same_row_A2",
    ]
    print_tip_indices = [
        record["tip_index"]
        for record in equivalence.physical_trace(plan)
        if record["action"] == "PRINT"
    ]
    assert len(set(print_tip_indices)) == 2


def test_tip_demand_must_fit_the_registered_rack(machine, make_job):
    experiment = _experiment(
        "too_many_tips",
        [_liquid("dye", "A1")],
        [
            {
                "id": "many_passes",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "dye"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 1.0,
                "repeats": 97,
                "delay_after_pass_s": 1.0,
                "tip_policy": "per_pass",
            }
        ],
    )

    with pytest.raises(ValueError, match="requires 97 tips"):
        resolve_experiment_job(make_job(machine, experiment))


def test_semantic_diff_names_the_field_that_differs(machine, make_job):
    base = deepcopy(
        _experiment(
            "diff_base",
            [_liquid("dye", "A1")],
            [
                {
                    "id": "row",
                    "type": "print",
                    "source": {"kind": "liquid", "liquid_id": "dye"},
                    "substrate": "paper",
                    "targets": ["A1"],
                    "volume_ul": 5.0,
                }
            ],
        )
    )
    changed = deepcopy(base)
    changed["metadata"]["experiment_id"] = "diff_changed"
    changed["procedure"][0]["volume_ul"] = 7.0

    left = resolve_experiment_job(make_job(machine, base))
    right = resolve_experiment_job(make_job(machine, changed))
    differences = equivalence.semantic_diff(
        equivalence.physical_trace(left), equivalence.physical_trace(right)
    )

    assert any("volume_ul" in line for line in differences)
