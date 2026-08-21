"""Stage 4 tests: the generalized template teaches the language, not the answers.

The template must be able to express a standard printing experiment without
pre-solving any particular one. These tests check both halves of that: it works,
and it does not leak Experiment 01.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.printing.standard import builder
from src.printing.standard.loader import load_experiment_job, load_experiment_job_mapping
from src.printing.standard.resolver import resolve_experiment_job
from src.printing.standard.review import render_plan_review


REPO = Path(__file__).resolve().parents[1]
TEMPLATE = "configs/templates/printing/01_printing_standard.template.yaml"
TEMPLATE_PATH = REPO / TEMPLATE

BLIND_CONTEXT_PATHS = (
    "configs/templates/printing/01_printing_standard.template.yaml",
    "configs/machines/ot2_standard_printing_p20_v1.yaml",
    "src/printing/schemas/experiments.py",
    "src/printing/standard/capabilities.py",
    "src/printing/standard/loader.py",
    "src/printing/standard/resolver.py",
    "src/printing/standard/review.py",
    "src/protocols/printing/01_printing_standard.py",
    "tests/test_standard_printing_capabilities.py",
    "tests/test_standard_printing_schemas.py",
    "tests/test_standard_printing_resolver.py",
)


@pytest.fixture(scope="module")
def template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_mapping() -> dict:
    return yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The template must not pre-solve Experiment 01
# --------------------------------------------------------------------------- #


#: Answers a blind agent must derive from the scientist's request, not read here.
EXPERIMENT_01_ANSWERS = (
    "nanoparticle",
    "crystal",
    "violet",
    "sers",
    "twofold",
    "1/128",
    "128",
    "np_",
    "cv_",
    "300",
    "five minute",
    "five-minute",
    "column 1",
    "column 2",
    "column 3",
    "column 4",
    "column1",
    "column2",
    "column3",
    "column4",
)


def test_template_does_not_leak_experiment_01_answers(template_text):
    lowered = template_text.lower()
    leaked = [token for token in EXPERIMENT_01_ANSWERS if token in lowered]

    assert leaked == [], f"template leaks Experiment 01 answers: {leaked}"


def test_blind_generalized_context_does_not_leak_experiment_01_answers():
    forbidden = EXPERIMENT_01_ANSWERS + (
        "3ce809a8133a95207da62fce7bea44977cf4b134490478559c903b6b77e77313",
        "eight twofold",
        "2-fold",
        "three np drops",
        "a1, b1, c1, d1, e1, f1, g1, h1",
        "a4, b4, c4, d4, e4, f4, g4, h4",
    )
    leaks = {}
    for reference in BLIND_CONTEXT_PATHS:
        lowered = (REPO / reference).read_text(encoding="utf-8").lower()
        present = [token for token in forbidden if token in lowered]
        if present:
            leaks[reference] = present

    assert leaks == {}, f"blind generalized context leaks Experiment 01: {leaks}"


def test_template_does_not_encode_experiment_01_numbers(template_mapping):
    procedure = template_mapping["experiment"]["procedure"]
    dilutions = [step for step in procedure if step["type"] == "serial_dilution"]
    prints = [step for step in procedure if step["type"] == "print"]

    for step in dilutions:
        assert step["fold"] != 2, "a twofold ladder is Experiment 01's answer"
        assert len(step["destination_wells"]) != 8, "eight points is Experiment 01"
        assert step["target_usable_volume_ul"] != 30.0
    for step in prints:
        assert step["volume_ul"] != 5.0, "5 uL droplets are Experiment 01's answer"
        assert step.get("delay_after_pass_s", 0.0) != 300.0
        assert step.get("repeats", 1) != 3
        assert len(step["targets"]) != 8


def test_template_carries_no_machine_owned_values(template_mapping, template_text):
    """Deck slots, heights, and air handling belong to the machine profile."""
    assert "machine" not in template_mapping
    assert template_mapping["machine_profile"].startswith("configs/machines/")

    experiment_text = template_text.split("experiment:", 1)[1]
    forbidden = (
        "slot:",
        "aspirate_height_mm",
        "dispense_height_mm",
        "air_gap",
        "push_out",
        "blow_out",
        "flow_rates",
        "load_name",
        "namespace",
        "p20_single_gen2",
        "tip_rack",
    )
    present = [token for token in forbidden if token in experiment_text]
    assert present == [], f"machine-owned values leaked into the template: {present}"


def test_template_contains_no_executable_instructions(template_text):
    """A configuration may not smuggle in robot commands or Python."""
    forbidden = (
        r"\baspirate\(",
        r"\bdispense\(",
        r"\bpick_up_tip\b",
        r"\bdrop_tip\b",
        r"\bprotocol\.",
        r"\bimport\b",
        r"\bdef\b",
        r"\blambda\b",
    )
    for pattern in forbidden:
        assert not re.search(pattern, template_text), f"template contains {pattern}"


# --------------------------------------------------------------------------- #
# The template must actually teach the whole language
# --------------------------------------------------------------------------- #


def test_template_documents_every_step_type(template_text):
    for step_type in (
        "serial_dilution",
        "direct_dilution",
        "transfer",
        "mix",
        "print",
        "delay",
    ):
        assert step_type in template_text, f"template never mentions {step_type}"


def test_template_documents_every_configurable_behaviour(template_text):
    for concept in (
        "repeats",
        "delay_after_pass_s",
        "mix_before_aspirate",
        "tip_policy",
        "per_target",
        "per_step",
        "purpose",
        "control",
        "product_liquid_ids",
        "minimum_remaining_ul",
        "require_drying_delay_between_deposits",
    ):
        assert concept in template_text, f"template never explains {concept}"


def test_template_explains_replicates_and_controls(template_mapping):
    procedure = template_mapping["experiment"]["procedure"]
    controls = [
        step
        for step in procedure
        if step["type"] == "print" and step.get("purpose") == "control"
    ]

    assert controls, "the template must show how a control is expressed"
    assert len(controls[0]["targets"]) > 1, "controls must demonstrate replicates"
    assert controls[0]["source"]["kind"] == "liquid"


# --------------------------------------------------------------------------- #
# The template is itself a valid, runnable experiment
# --------------------------------------------------------------------------- #


def test_template_validates_resolves_and_simulates(tmp_path, monkeypatch):
    monkeypatch.setenv("OT_API_CONFIG_DIR", str(tmp_path / "opentrons-config"))
    (tmp_path / "opentrons-config").mkdir()

    job = load_experiment_job(TEMPLATE)
    plan = resolve_experiment_job(job)
    artifact = builder.build_standard_protocol(plan, output_dir=tmp_path)
    passed, run_log, text = builder.simulate_standard_protocol(
        artifact.protocol_path, expected_sha256=artifact.protocol_sha256
    )

    assert passed is True
    assert plan.totals.action_count == 25
    assert plan.totals.transfer_count == 7
    assert plan.totals.mix_count == 6
    assert plan.totals.print_count == 6
    assert plan.totals.delay_count == 1
    assert plan.totals.tip_count == 8
    assert "standard printing complete: 6 droplets" in text
    assert "EXPERIMENT:" in render_plan_review(plan, job)


# --------------------------------------------------------------------------- #
# Alternative experiments built from the template's vocabulary
# --------------------------------------------------------------------------- #


def _variant(template_mapping, mutate):
    payload = deepcopy(template_mapping)
    mutate(payload["experiment"])
    return resolve_experiment_job(load_experiment_job_mapping(payload))


def test_variant_printing_only_needs_no_preparation(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_printing_only"
        experiment["procedure"] = [
            {
                "id": "just_print",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "solution_a"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 3.0,
            }
        ]

    plan = _variant(template_mapping, mutate)

    assert plan.totals.transfer_count == 0
    assert plan.totals.mix_count == 0
    assert plan.totals.delay_count == 0
    assert plan.totals.print_count == 2


def test_variant_prints_exactly_one_droplet(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_one_droplet"
        experiment["procedure"] = [
            {
                "id": "one_drop",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "solution_a"},
                "substrate": "paper",
                "targets": ["H12"],
                "volume_ul": 3.0,
            }
        ]

    plan = _variant(template_mapping, mutate)

    assert plan.totals.print_count == 1
    assert plan.totals.tip_count == 1


def test_variant_transfer_then_prints_the_new_aliquot(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_transfer_print"
        experiment["procedure"] = [
            {
                "id": "make_aliquot",
                "type": "transfer",
                "liquid_id": "solution_a",
                "destination": {"labware": "plate", "well": "D1"},
                "volume_ul": 12.0,
                "result_liquid_id": "working_solution",
            },
            {
                "id": "print_aliquot",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "working_solution"},
                "substrate": "paper",
                "targets": ["D1"],
                "volume_ul": 3.0,
            },
        ]

    plan = _variant(template_mapping, mutate)
    transfers = [a for a in plan.action_dicts() if a["action"] == "TRANSFER"]
    prints = [a for a in plan.action_dicts() if a["action"] == "PRINT"]

    assert len(transfers) == len(prints) == 1
    assert prints[0]["liquid_id"] == "working_solution"
    assert prints[0]["source"]["labware"] == "plate"


def test_variant_mix_then_prints_from_one_vial_source(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_mix_print"
        experiment["procedure"] = [
            {
                "id": "mix_source",
                "type": "mix",
                "liquid_id": "solution_a",
                "location": {"labware": "vial_rack", "well": "A1"},
                "cycles": 2,
                "volume_ul": 4.0,
            },
            {
                "id": "print_source",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "solution_a"},
                "substrate": "paper",
                "targets": ["E1"],
                "volume_ul": 3.0,
            },
        ]

    plan = _variant(template_mapping, mutate)

    assert plan.totals.mix_count == 1
    assert plan.totals.print_count == 1


def test_variant_dilute_and_print_without_mixing(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_no_mixing"
        experiment["procedure"] = [
            {
                "id": "ladder",
                "type": "serial_dilution",
                "source_liquid_id": "solution_a",
                "diluent_liquid_id": "solvent",
                "destination_labware": "plate",
                "destination_wells": ["A1", "B1"],
                "fold": 4,
                "target_usable_volume_ul": 20.0,
            },
            {
                "id": "row",
                "type": "print",
                "source": {"kind": "series", "preparation_id": "ladder"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 3.0,
                "tip_policy": "per_target",
            },
        ]

    plan = _variant(template_mapping, mutate)

    assert plan.totals.mix_count == 0
    assert plan.totals.transfer_count > 0
    assert plan.totals.print_count == 2
    assert plan.totals.tip_count >= 2
    assert plan.preparation_math[0]["factors"] == [1, 4]


def test_variant_direct_dilution_from_a_plate_source(template_mapping):
    def mutate(experiment):
        experiment["metadata"]["experiment_id"] = "variant_plate_source"
        experiment["liquids"] = [
            {
                "liquid_id": "plate_stock",
                "display_name": "Plate stock",
                "location": {"labware": "plate", "well": "H12"},
                "loaded_volume_ul": 300.0,
                "minimum_remaining_ul": 0.0,
            },
            {
                "liquid_id": "plate_solvent",
                "display_name": "Plate solvent",
                "location": {"labware": "plate", "well": "G12"},
                "loaded_volume_ul": 300.0,
                "minimum_remaining_ul": 0.0,
            },
        ]
        experiment["procedure"] = [
            {
                "id": "points",
                "type": "direct_dilution",
                "source_liquid_id": "plate_stock",
                "diluent_liquid_id": "plate_solvent",
                "destination_labware": "plate",
                "points": [
                    {"well": "A1", "factor": 1, "total_volume_ul": 20.0},
                    {"well": "B1", "factor": 6, "total_volume_ul": 20.0},
                ],
                "mix": {"cycles": 2, "volume_ul": 4.0},
            },
            {
                "id": "row",
                "type": "print",
                "source": {"kind": "series", "preparation_id": "points"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 3.0,
                "tip_policy": "per_target",
            },
        ]

    plan = _variant(template_mapping, mutate)
    sources = {
        action["source"]["labware"]
        for action in plan.action_dicts()
        if action["action"] in {"TRANSFER", "PRINT"}
    }

    assert sources == {"plate"}
    assert plan.preparation_math[0]["method"] == "independent_direct_dilution"
    assert plan.totals.print_count == 2
    assert plan.totals.mix_count == 2


def test_variant_many_drops_with_and_without_rests(template_mapping):
    def with_rest(experiment):
        experiment["metadata"]["experiment_id"] = "variant_seven_drops_rested"
        experiment["procedure"] = [
            {
                "id": "layers",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "solution_a"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 2.0,
                "repeats": 7,
                "delay_after_pass_s": 30.0,
            }
        ]

    def without_rest(experiment):
        experiment["metadata"]["experiment_id"] = "variant_seven_drops_wet"
        experiment["policies"] = {"require_drying_delay_between_deposits": False}
        experiment["procedure"] = [
            {
                "id": "layers",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "solution_a"},
                "substrate": "paper",
                "targets": ["A1"],
                "volume_ul": 2.0,
                "repeats": 7,
            }
        ]

    rested = _variant(template_mapping, with_rest)
    wet = _variant(template_mapping, without_rest)

    assert rested.totals.print_count == wet.totals.print_count == 7
    assert rested.totals.delay_count == 7
    assert wet.totals.delay_count == 0
