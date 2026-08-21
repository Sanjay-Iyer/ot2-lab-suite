"""Stage 2 tests: invalid experiments must be rejected before anything is built."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.printing.schemas.experiments import (
    PrintExperimentJobV1,
    ResolvedExperimentPlanV1,
)
from src.printing.standard.loader import (
    ExperimentJobLoadError,
    load_experiment_job_mapping,
    list_machine_profiles,
)
from src.printing.standard.resolver import (
    ExperimentResolutionError,
    resolve_experiment_job,
)


def _resolve(make_job, machine, experiment):
    return resolve_experiment_job(make_job(machine, experiment))


# --------------------------------------------------------------------------- #
# Structural schema validation
# --------------------------------------------------------------------------- #


def test_unknown_keys_are_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["drops_per_position"] = 3

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_non_positive_volumes_are_rejected(machine, single_source_experiment):
    for bad in (0.0, -5.0):
        experiment = deepcopy(single_source_experiment)
        experiment["procedure"][0]["volume_ul"] = bad
        with pytest.raises(ValidationError):
            PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_malformed_well_names_are_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["liquids"][0]["location"]["well"] = "Z9"

    with pytest.raises(ValidationError):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_fractional_or_negative_repeat_counts_are_rejected(
    machine, single_source_experiment
):
    for bad in (0, -1, 2.5):
        experiment = deepcopy(single_source_experiment)
        experiment["procedure"][0]["repeats"] = bad
        with pytest.raises(ValidationError):
            PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_negative_delays_are_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["delay_after_pass_s"] = -1.0

    with pytest.raises(ValidationError):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_duplicate_liquid_and_step_ids_are_rejected(machine, single_source_experiment):
    duplicated_liquid = deepcopy(single_source_experiment)
    duplicated_liquid["liquids"].append(deepcopy(duplicated_liquid["liquids"][0]))
    with pytest.raises(ValidationError, match="unique"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=duplicated_liquid)

    duplicated_step = deepcopy(single_source_experiment)
    duplicated_step["procedure"].append(deepcopy(duplicated_step["procedure"][0]))
    with pytest.raises(ValidationError, match="unique"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=duplicated_step)


def test_repeated_target_within_one_step_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["targets"] = ["A1", "A1"]

    with pytest.raises(ValidationError, match="repeat a target"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_a_source_cannot_be_its_own_diluent(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"].insert(
        0,
        {
            "id": "series",
            "type": "serial_dilution",
            "source_liquid_id": "marker_dye",
            "diluent_liquid_id": "marker_dye",
            "destination_labware": "plate",
            "destination_wells": ["A1", "B1"],
            "fold": 2,
            "target_usable_volume_ul": 30.0,
        },
    )
    with pytest.raises(ValidationError, match="source as its diluent"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


def test_machine_profile_requires_calibrated_heights(machine, single_source_experiment):
    broken = deepcopy(machine)
    for entry in broken["labware"]:
        if entry["role"] == "plate":
            entry.pop("aspirate_height_mm")

    with pytest.raises(ValidationError, match="aspirate and dispense heights"):
        PrintExperimentJobV1.from_content(
            machine=broken, experiment=single_source_experiment
        )


def test_machine_profile_rejects_duplicate_slots(machine, single_source_experiment):
    broken = deepcopy(machine)
    broken["labware"][1]["slot"] = broken["labware"][0]["slot"]

    with pytest.raises(ValidationError, match="deck slots must be unique"):
        PrintExperimentJobV1.from_content(
            machine=broken, experiment=single_source_experiment
        )


def test_job_id_must_match_its_content(machine, single_source_experiment):
    job = PrintExperimentJobV1.from_content(
        machine=machine, experiment=single_source_experiment
    )
    payload = job.model_dump(mode="json")
    payload["job_id"] = "f" * 64

    with pytest.raises(ValidationError, match="job_id does not match"):
        PrintExperimentJobV1.model_validate(payload)


# --------------------------------------------------------------------------- #
# Semantic validation performed by the resolver
# --------------------------------------------------------------------------- #


def test_missing_source_liquid_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["source"] = {
        "kind": "liquid",
        "liquid_id": "never_declared",
    }

    with pytest.raises(ExperimentResolutionError, match="neither declared nor produced"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_unknown_labware_role_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["substrate"] = "unknown_deck_role"

    with pytest.raises(ExperimentResolutionError, match="unknown labware role"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_well_outside_the_labware_definition_is_rejected(
    machine, single_source_experiment
):
    experiment = deepcopy(single_source_experiment)
    experiment["liquids"][0]["location"]["well"] = "H12"

    with pytest.raises(ExperimentResolutionError, match="does not exist in labware"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_printing_onto_a_non_substrate_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["substrate"] = "plate"

    with pytest.raises(ExperimentResolutionError, match="requires a substrate"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_droplet_beyond_the_pipette_range_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["volume_ul"] = 19.0  # 19 + 1.5 uL air gap > 20 uL

    with pytest.raises(ExperimentResolutionError, match="exceeding the"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_droplet_below_the_pipette_minimum_is_rejected(
    machine, single_source_experiment
):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"][0]["volume_ul"] = 0.5

    with pytest.raises(ExperimentResolutionError, match="below the"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_insufficient_prepared_volume_is_rejected(machine, single_source_experiment):
    """Printing more than a well holds must fail before the robot moves."""
    experiment = deepcopy(single_source_experiment)
    experiment["liquids"][0]["loaded_volume_ul"] = 20.0
    experiment["liquids"][0]["minimum_remaining_ul"] = 0.0

    with pytest.raises(ExperimentResolutionError, match="nominally dry"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_second_deposit_without_a_drying_delay_is_rejected(
    machine, single_source_experiment
):
    experiment = deepcopy(single_source_experiment)
    experiment["procedure"].append(
        {
            "id": "second_layer",
            "type": "print",
            "source": {"kind": "liquid", "liquid_id": "marker_dye"},
            "substrate": "paper",
            "targets": ["A1"],
            "volume_ul": 5.0,
        }
    )

    with pytest.raises(ExperimentResolutionError, match="no drying delay"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_drying_policy_can_be_switched_off_explicitly(
    machine, single_source_experiment
):
    experiment = deepcopy(single_source_experiment)
    experiment["policies"] = {"require_drying_delay_between_deposits": False}
    experiment["procedure"].append(
        {
            "id": "second_layer",
            "type": "print",
            "source": {"kind": "liquid", "liquid_id": "marker_dye"},
            "substrate": "paper",
            "targets": ["A1"],
            "volume_ul": 5.0,
        }
    )
    plan = _resolve(
        lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
        machine,
        experiment,
    )

    assert plan.totals.print_count == 3


def test_overwriting_an_occupied_well_is_rejected(machine, single_source_experiment):
    experiment = deepcopy(single_source_experiment)
    experiment["liquids"].append(
        {
            "liquid_id": "other_dye",
            "display_name": "Other dye",
            "location": {"labware": "vial_rack", "well": "A1"},
            "loaded_volume_ul": 4000.0,
            "minimum_remaining_ul": 2600.0,
        }
    )

    with pytest.raises(ExperimentResolutionError, match="already assigned"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_series_length_must_match_its_print_targets(machine):
    experiment = {
        "metadata": {"experiment_id": "mismatch", "title": "Mismatched series"},
        "liquids": [
            {
                "liquid_id": "src",
                "display_name": "Source",
                "location": {"labware": "vial_rack", "well": "A1"},
                "loaded_volume_ul": 4000.0,
                "minimum_remaining_ul": 2600.0,
            },
            {
                "liquid_id": "dil",
                "display_name": "Diluent",
                "location": {"labware": "vial_rack", "well": "A2"},
                "loaded_volume_ul": 4000.0,
                "minimum_remaining_ul": 2600.0,
            },
        ],
        "procedure": [
            {
                "id": "series",
                "type": "serial_dilution",
                "source_liquid_id": "src",
                "diluent_liquid_id": "dil",
                "destination_labware": "plate",
                "destination_wells": ["A1", "B1", "C1"],
                "fold": 2,
                "target_usable_volume_ul": 30.0,
            },
            {
                "id": "print_series",
                "type": "print",
                "source": {"kind": "series", "preparation_id": "series"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 5.0,
                "tip_policy": "per_target",
            },
        ],
    }

    with pytest.raises(ExperimentResolutionError, match="produces 3 liquids"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


def test_derived_liquid_name_collision_is_rejected(machine):
    experiment = {
        "metadata": {"experiment_id": "collision", "title": "Name collision"},
        "liquids": [
            {
                "liquid_id": "dye_stock",
                "display_name": "Dye stock",
                "location": {"labware": "vial_rack", "well": "A1"},
                "loaded_volume_ul": 4000.0,
                "minimum_remaining_ul": 2600.0,
            },
            {
                "liquid_id": "dil",
                "display_name": "Diluent",
                "location": {"labware": "vial_rack", "well": "A2"},
                "loaded_volume_ul": 4000.0,
                "minimum_remaining_ul": 2600.0,
            },
        ],
        "procedure": [
            {
                "id": "dye",
                "type": "serial_dilution",
                "source_liquid_id": "dye_stock",
                "diluent_liquid_id": "dil",
                "destination_labware": "plate",
                "destination_wells": ["A1", "B1"],
                "fold": 2,
                "target_usable_volume_ul": 30.0,
            }
        ],
    }

    with pytest.raises(ExperimentResolutionError, match="derived liquid name"):
        _resolve(lambda m, e: PrintExperimentJobV1.from_content(machine=m, experiment=e),
                 machine, experiment)


# --------------------------------------------------------------------------- #
# Loader behaviour
# --------------------------------------------------------------------------- #


def test_inline_machine_is_rejected_at_the_experiment_loader_boundary(machine):
    with pytest.raises(ExperimentJobLoadError, match="may not contain an inline"):
        load_experiment_job_mapping(
            {
                "schema_version": "print-experiment-job/v1",
                "machine_profile": "configs/machines/ot2_standard_printing_p20_v1.yaml",
                "machine": machine,
                "experiment": {
                    "metadata": {"experiment_id": "x", "title": "x"},
                    "liquids": [],
                    "procedure": [],
                },
            }
        )


def test_machine_profile_reference_cannot_escape_the_repository():
    with pytest.raises(ValueError, match="must resolve inside the repository"):
        load_experiment_job_mapping(
            {
                "schema_version": "print-experiment-job/v1",
                "machine_profile": "../../etc/passwd",
                "experiment": {
                    "metadata": {"experiment_id": "x", "title": "x"},
                    "liquids": [],
                    "procedure": [],
                },
            }
        )


def test_machine_profile_must_come_from_the_registered_directory():
    with pytest.raises(ExperimentJobLoadError, match="registered laboratory profile"):
        load_experiment_job_mapping(
            {
                "schema_version": "print-experiment-job/v1",
                "machine_profile": "configs/experiments/01_printing_standard.yaml",
                "experiment": {
                    "metadata": {"experiment_id": "x", "title": "x"},
                    "liquids": [],
                    "procedure": [],
                },
            }
        )


def test_machine_profile_is_required_for_experiment_yaml():
    with pytest.raises(ExperimentJobLoadError, match="must reference"):
        load_experiment_job_mapping(
            {
                "schema_version": "print-experiment-job/v1",
                "experiment": {
                    "metadata": {"experiment_id": "x", "title": "x"},
                    "liquids": [],
                    "procedure": [],
                },
            }
        )


def test_declared_job_id_must_match_the_content(machine, single_source_experiment):
    with pytest.raises(ExperimentJobLoadError, match="does not match"):
        load_experiment_job_mapping(
            {
                "schema_version": "print-experiment-job/v1",
                "job_id": "a" * 64,
                "machine_profile": "configs/machines/ot2_standard_printing_p20_v1.yaml",
                "experiment": single_source_experiment,
            }
        )


def test_registered_machine_profiles_are_discoverable():
    profiles = list_machine_profiles()

    assert "configs/machines/ot2_standard_printing_p20_v1.yaml" in profiles


def test_nonzero_pre_air_chase_is_rejected_until_executor_support_exists(
    machine, single_source_experiment
):
    machine["print_release"]["pre_air_chase_ul"] = 2.0

    with pytest.raises(ValidationError, match="pre_air_chase_ul is not supported"):
        PrintExperimentJobV1.from_content(
            machine=machine, experiment=single_source_experiment
        )


@pytest.mark.parametrize("unsafe_policy", [None, "per_step", "per_pass"])
def test_series_printing_requires_per_target_tips(
    machine, single_source_experiment, unsafe_policy
):
    experiment = deepcopy(single_source_experiment)
    experiment["liquids"].append(
        {
            "liquid_id": "buffer",
            "display_name": "Buffer",
            "location": {"labware": "vial_rack", "well": "A2"},
            "loaded_volume_ul": 4000.0,
            "minimum_remaining_ul": 2600.0,
        }
    )
    print_step = {
        "id": "series_row",
        "type": "print",
        "source": {"kind": "series", "preparation_id": "ladder"},
        "substrate": "paper",
        "targets": ["A1", "A2"],
        "volume_ul": 4.0,
    }
    if unsafe_policy is not None:
        print_step["tip_policy"] = unsafe_policy
    experiment["procedure"] = [
        {
            "id": "ladder",
            "type": "serial_dilution",
            "source_liquid_id": "marker_dye",
            "diluent_liquid_id": "buffer",
            "destination_labware": "plate",
            "destination_wells": ["A1", "A2"],
            "fold": 4,
            "target_usable_volume_ul": 24.0,
        },
        print_step,
    ]

    with pytest.raises(ValidationError, match="requires tip_policy='per_target'"):
        PrintExperimentJobV1.from_content(machine=machine, experiment=experiment)


# --------------------------------------------------------------------------- #
# Resolved plan integrity
# --------------------------------------------------------------------------- #


def test_resolved_plan_rejects_broken_sequence_numbering(
    machine, single_source_experiment, make_job
):
    plan = resolve_experiment_job(make_job(machine, single_source_experiment))
    payload = plan.model_dump(mode="json")
    payload["actions"][2]["sequence_index"] = 99

    with pytest.raises(ValidationError, match="numbered 1..n"):
        ResolvedExperimentPlanV1.model_validate(payload)


def test_resolved_plan_rejects_totals_that_do_not_describe_it(
    machine, single_source_experiment, make_job
):
    plan = resolve_experiment_job(make_job(machine, single_source_experiment))
    payload = plan.model_dump(mode="json")
    payload["totals"]["print_count"] += 1

    with pytest.raises(ValidationError, match="print_count"):
        ResolvedExperimentPlanV1.model_validate(payload)


def test_resolved_plan_id_must_match_its_content(
    machine, single_source_experiment, make_job
):
    plan = resolve_experiment_job(make_job(machine, single_source_experiment))
    payload = plan.model_dump(mode="json")
    payload["plan_id"] = "b" * 64

    with pytest.raises(ValidationError, match="plan_id does not match"):
        ResolvedExperimentPlanV1.model_validate(payload)
