"""Stage 3 tests: the human-written YAML reproduces the frozen static reference.

This is the central claim of the paper architecture at this stage:

    01_printing_standard_ground_truth.py      (independently written Python)
                    ==
    configs/experiments/01_printing_standard.yaml
        -> PrintExperimentJobV1 -> ResolvedExperimentPlanV1
        -> src/protocols/printing/01_printing_standard.py

Equivalence is asserted on canonical resolved physical actions, never on file
names, YAML text, or visual similarity.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.printing.standard import builder, equivalence
from src.printing.standard.loader import load_experiment_job
from src.printing.standard.resolver import resolve_experiment_job
from src.printing.standard.review import render_plan_review, render_substrate_map


REPO = Path(__file__).resolve().parents[1]
CONFIG = "configs/experiments/01_printing_standard.yaml"
STATIC_TRACE = REPO / "experiment_01" / "ground_truth" / "static_canonical_trace.json"
PAPER = REPO / "labware" / "paper_print_96_flat.json"
ROWS = "ABCDEFGH"


@pytest.fixture(scope="module")
def job():
    return load_experiment_job(CONFIG)


@pytest.fixture(scope="module")
def plan(job):
    return resolve_experiment_job(job)


@pytest.fixture(scope="module")
def static_plan():
    return json.loads(STATIC_TRACE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Equivalence with the static ground truth
# --------------------------------------------------------------------------- #


def test_config_ground_truth_matches_the_static_physical_trace(plan, static_plan):
    report = equivalence.compare_plans(
        static_plan, plan, left_label="static", right_label="config"
    )

    assert report["physical_differences"] == []
    assert report["setup_differences"] == []
    assert report["physical_match"] is True
    assert report["setup_match"] is True
    assert report["execution_match"] is True
    assert report["structural_match"] is True
    assert report["left_action_count"] == report["right_action_count"] == 187
    assert report["left_physical_sha256"] == report["right_physical_sha256"]
    assert report["left_setup_sha256"] == report["right_setup_sha256"]


def test_initial_liquid_setup_is_required_for_execution_equivalence(static_plan):
    changed = deepcopy(static_plan)
    changed["initial_liquids"][0]["location"]["well"] = "H12"
    changed["initial_liquids"][0]["loaded_volume_ul"] = 999999.0

    report = equivalence.compare_plans(static_plan, changed)

    assert report["physical_match"] is True
    assert report["setup_match"] is False
    assert report["execution_match"] is False
    assert report["setup_differences"]


def test_numeric_normalization_keeps_equality_and_hashing_consistent(static_plan):
    changed = deepcopy(static_plan)
    print_action = next(
        action for action in changed["actions"] if action["action"] == "PRINT"
    )
    print_action["volume_ul"] = int(print_action["volume_ul"])

    report = equivalence.compare_plans(static_plan, changed)

    assert report["physical_match"] is True
    assert report["left_physical_sha256"] == report["right_physical_sha256"]


def test_execution_comparison_rejects_bare_action_lists(static_plan):
    with pytest.raises(ValueError, match="requires a complete plan"):
        equivalence.compare_plans(static_plan["actions"], static_plan["actions"])


def test_setup_declaration_order_is_not_physical(static_plan):
    reordered = deepcopy(static_plan)
    reordered["initial_liquids"] = list(reversed(reordered["initial_liquids"]))

    report = equivalence.compare_plans(static_plan, reordered)

    assert report["setup_match"] is True
    assert report["execution_match"] is True
    assert report["left_setup_sha256"] == report["right_setup_sha256"]


def test_config_ground_truth_matches_the_static_totals(plan):
    static = json.loads(STATIC_TRACE.read_text(encoding="utf-8"))

    assert plan.totals.model_dump(mode="json") == static["totals"]


def test_config_ground_truth_reproduces_the_static_dilution_arithmetic(plan):
    static = json.loads(STATIC_TRACE.read_text(encoding="utf-8"))["dilution_math"]
    for entry in plan.preparation_math:
        assert entry["method"] == static["method"]
        assert entry["factors"] == static["factors"]
        assert entry["stock_allocation_ul"] == pytest.approx(
            static["stock_allocation_ul_per_series"]
        )
        assert entry["outgoing_ul"] == pytest.approx(static["outgoing_ul"])
        assert entry["diluent_ul"] == pytest.approx(static["diluent_ul"])
        assert entry["retained_usable_ul"] == pytest.approx(static["retained_usable_ul"])


def test_config_ground_truth_reproduces_the_static_source_accounting(plan):
    static = json.loads(STATIC_TRACE.read_text(encoding="utf-8"))
    resolved = {
        liquid.liquid_id: liquid.scientific_allocation_ul
        for liquid in plan.initial_liquids
    }
    expected = {
        source["liquid_id"]: source["scientific_allocation_ul"]
        for source in static["initial_liquids"]
    }

    assert resolved == pytest.approx(expected)
    assert plan.source_accessibility.status == "PASS"
    assert (
        plan.source_accessibility.checked_aspirate_or_mix_actions
        == static["source_accessibility"]["checked_aspirate_or_mix_actions"]
    )
    assert plan.source_accessibility.minimum_nominal_submerged_margin_ul > 2.6


# --------------------------------------------------------------------------- #
# The scientific layout the configuration asked for
# --------------------------------------------------------------------------- #


def test_the_four_printed_columns_carry_the_requested_conditions(plan):
    prints = [action for action in plan.action_dicts() if action["action"] == "PRINT"]

    for index, row in enumerate(ROWS):
        factor = 2**index
        np_id = "np_stock" if factor == 1 else f"np_1_{factor}x"
        cv_id = "cv_stock" if factor == 1 else f"cv_1_{factor}x"

        column1 = [p for p in prints if p["destination"]["well"] == f"{row}1"]
        column2 = [p for p in prints if p["destination"]["well"] == f"{row}2"]
        column3 = [p for p in prints if p["destination"]["well"] == f"{row}3"]
        column4 = [p for p in prints if p["destination"]["well"] == f"{row}4"]

        assert [p["liquid_id"] for p in column1] == [np_id, "cv_stock"]
        assert [p["liquid_id"] for p in column2] == [np_id] * 3 + ["cv_stock"]
        assert [p["liquid_id"] for p in column3] == ["cv_stock"]
        assert [p["liquid_id"] for p in column4] == [cv_id]
        assert all(p["volume_ul"] == 5.0 for p in column1 + column2 + column3 + column4)

    assert len(prints) == 64


def test_every_repeated_nanoparticle_layer_is_separated_by_five_minutes(plan):
    delays = [action for action in plan.action_dicts() if action["action"] == "DELAY"]

    assert [action["duration_s"] for action in delays] == [300.0] * 4
    assert plan.totals.configured_experimental_delay_s == 1200.0


def test_the_controls_are_nanoparticle_free(job, plan):
    controls = [
        step for step in job.experiment.procedure
        if step.type == "print" and step.purpose == "control"
    ]
    control_targets = {target for step in controls for target in step.targets}
    prints = [action for action in plan.action_dicts() if action["action"] == "PRINT"]

    assert control_targets == {f"{row}{column}" for row in ROWS for column in (3, 4)}
    for action in prints:
        if action["destination"]["well"] in control_targets:
            assert action["liquid_id"].startswith("cv_")


def test_the_validated_paper_geometry_is_used(plan):
    prints = [action for action in plan.action_dicts() if action["action"] == "PRINT"]
    loads = {
        action["role"]: action
        for action in plan.action_dicts()
        if action["action"] == "LOAD_LABWARE"
    }

    assert loads["paper"]["load_name"] == "paper_print_96_flat"
    assert loads["paper"]["namespace"] == "custom_beta"
    assert loads["paper"]["slot"] == 5
    assert all(
        action["destination"]["reference"] == "bottom"
        and action["destination"]["z_mm"] == 0.5
        for action in prints
    )


# --------------------------------------------------------------------------- #
# Review, build, and local simulation
# --------------------------------------------------------------------------- #


def test_the_review_describes_every_position_without_yaml(plan, job):
    review = render_plan_review(plan, job)
    substrate = render_substrate_map(plan)

    assert "A1: np_stock 1 x 5 uL; then [after 300 s rest] cv_stock 1 x 5 uL" in substrate
    assert (
        "A2: np_stock 3 x 5 uL at 300 s spacing; then [after 300 s rest] "
        "cv_stock 1 x 5 uL" in substrate
    )
    assert "A3: cv_stock 1 x 5 uL" in substrate
    assert "H4: cv_1_128x 1 x 5 uL" in substrate
    assert "Row A" in substrate and "Row H" in substrate
    assert "EXPERIMENT: Standard SERS paper printing" in review
    assert "p20_single_gen2 on left mount" in review
    assert "printed liquid            : 320 uL" in review


def test_the_built_executor_carries_the_plan_and_stays_trusted(plan, tmp_path):
    artifact = builder.build_standard_protocol(plan, output_dir=tmp_path)
    source = artifact.protocol_path.read_text(encoding="utf-8")
    base = builder.BASE_PROTOCOL.read_text(encoding="utf-8")

    assert artifact.plan_id == plan.plan_id
    assert f"resolved_plan_sha256: {plan.plan_id}" in source
    # only the delimited plan block and the provenance header differ from the base
    base_body = base.split(builder.START_SENTINEL)[0]
    built_body = source.split(builder.START_SENTINEL)[0]
    assert built_body.endswith(base_body)
    assert base.split(builder.END_SENTINEL)[1] == source.split(builder.END_SENTINEL)[1]


def test_config_ground_truth_simulates_with_forced_motion(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("OT_API_CONFIG_DIR", str(tmp_path / "opentrons-config"))
    (tmp_path / "opentrons-config").mkdir()

    artifact = builder.build_standard_protocol(plan, output_dir=tmp_path)
    passed, run_log, text = builder.simulate_standard_protocol(
        artifact.protocol_path, expected_sha256=artifact.protocol_sha256
    )

    paper_bottom = json.loads(PAPER.read_text(encoding="utf-8"))["wells"]["A1"]["z"]
    dispenses = [
        entry["payload"]["location"].point.z
        for entry in run_log
        if entry["payload"].get("text", "").startswith("Dispensing 6.5 uL into")
        and "Paper Print Surface" in entry["payload"]["text"]
    ]

    assert passed is True
    assert len(dispenses) == 64
    assert all(z == pytest.approx(paper_bottom + 0.5) for z in dispenses)
    assert "at 3.0 uL/sec" in text
    assert "standard printing complete: 64 droplets, 320.0 uL printed." in text


def test_a_tampered_artifact_is_refused_before_simulation(plan, tmp_path):
    artifact = builder.build_standard_protocol(plan, output_dir=tmp_path)
    artifact.protocol_path.write_text(
        artifact.protocol_path.read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )

    with pytest.raises(builder.StandardProtocolBuildError, match="changed after"):
        builder.simulate_standard_protocol(
            artifact.protocol_path, expected_sha256=artifact.protocol_sha256
        )
