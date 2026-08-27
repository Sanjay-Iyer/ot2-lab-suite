"""Tests for the canonical SERS template layer.

The point of these is to prove that templates are starting patterns and nothing
more: they parse as ordinary SERSExperimentV1 documents, they expand and reorder
freely once loaded, and they inherit every safety behaviour of a hand-written
experiment.
"""

from __future__ import annotations

import json

import pytest

from src.sers_engine.agent_tools import (
    list_sers_templates,
    start_experiment_from_template,
    update_experiment,
)
from src.sers_engine.intent import SERSExperimentV1
from src.sers_engine.schema import SERSConfigError
from src.sers_engine.state import REGISTRY, ExperimentSession, ExperimentStatus
from src.sers_engine.templates import (
    SERS_TEMPLATES,
    describe_templates,
    load_sers_template,
    template_payload,
)
from src.sers_engine.validator import validate_experiment
from src.sers_engine.agent.graph import SERSExperimentAgent
from tests.fake_llm import ScriptedChatModel


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def session_from(template: str) -> ExperimentSession:
    session = ExperimentSession.create(template_payload(template, experiment_name="t"))
    REGISTRY.add(session)
    session.resolve_and_validate()
    return session


def steps_of(session: ExperimentSession) -> list[str]:
    return [step.step_type for step in session.experiment.steps]


# ---------------------------------------------------------------------------
# Every template is a real experiment
# ---------------------------------------------------------------------------


def test_every_template_is_a_valid_sers_experiment_v1():
    for name in SERS_TEMPLATES:
        experiment = load_sers_template(name)
        assert isinstance(experiment, SERSExperimentV1)
        report, plan = validate_experiment(experiment)
        assert report.ok, (name, report.errors)
        assert plan is not None


def test_the_registry_is_closed_to_arbitrary_paths():
    for attempt in ["../../../etc/passwd", "configs/machines/ot2_sers_p20_v1.yaml", "nope"]:
        with pytest.raises(SERSConfigError, match="unknown template"):
            load_sers_template(attempt)


def test_catalogue_describes_each_template_for_the_agent():
    catalogue = {entry["name"]: entry for entry in describe_templates()}
    assert set(catalogue) == {"dilution", "printing", "workflow"}
    for entry in catalogue.values():
        assert entry["available"] is True
        assert entry["summary"] and entry["when_to_use"] and entry["shape"]
    assert catalogue["dilution"]["steps"] == ["dilution:dilution_1"]
    assert catalogue["printing"]["steps"] == ["print:print_1"]
    assert [item.split(":")[0] for item in catalogue["workflow"]["steps"]] == [
        "dilution", "print", "dilution", "print", "wait", "print"
    ]


# ---------------------------------------------------------------------------
# Test A - dilution template
# ---------------------------------------------------------------------------


def test_a_dilution_template_loads_expands_and_rescales():
    session = session_from("dilution")
    assert steps_of(session) == ["dilution"]
    assert session.resolved.totals.print_count == 0
    # 10x of 100 uL is 10 uL of stock plus 90 uL of diluent.
    first = session.resolved.steps[0]
    assert (first.stock_volume_ul, first.diluent_volume_ul) == (10.0, 90.0)

    session.apply_patch({
        "update_steps": [{"step_id": "dilution_1", "dilution_factor": 30}],
        "add_steps": [{
            "step_type": "dilution", "step_id": "dilution_2", "source": "nanoparticles",
            "diluent": "water", "destination": "working_plate:A2",
            "dilution_factor": 50, "final_volume_ul": 100,
        }],
    })
    report = session.resolve_and_validate()

    assert steps_of(session) == ["dilution", "dilution"]
    first, second = session.resolved.steps
    # 50x of 100 uL lands exactly on 2.0 + 98.0 uL.
    assert (second.stock_volume_ul, second.diluent_volume_ul) == (2.0, 98.0)
    assert second.dilution_factor_achieved == 50
    # 30x of 100 uL needs 3.33 uL, which a P20 cannot command at 0.1 uL
    # resolution. The resolver rounds to 3.3 uL and SAYS SO rather than
    # silently delivering a ratio the user did not ask for.
    assert (first.stock_volume_ul, first.diluent_volume_ul) == (3.3, 96.7)
    assert first.dilution_factor_achieved == pytest.approx(30.303, abs=0.001)
    assert any("30x requested" in warning for warning in report.warnings)


def test_a_dilution_template_expands_to_a_full_series():
    session = session_from("dilution")
    session.apply_patch({"add_steps": [
        {
            "step_type": "dilution", "step_id": f"dilution_{index}",
            "source": "nanoparticles", "diluent": "water",
            "destination": f"working_plate:A{index}",
            "dilution_factor": factor, "final_volume_ul": 100,
        }
        for index, factor in enumerate([10, 20, 30, 40, 50, 75, 100], start=2)
    ]})
    report = session.resolve_and_validate()
    assert report.ok, report.errors
    assert len(session.experiment.steps) == 8
    assert session.resolved.totals.dilution_count == 8


# ---------------------------------------------------------------------------
# Test B - printing template
# ---------------------------------------------------------------------------


def test_b_printing_template_loads_and_retargets():
    session = session_from("printing")
    assert steps_of(session) == ["print"]
    assert session.resolved.totals.dilution_count == 0
    assert session.resolved.steps[0].total_deposits == 8  # column 1

    session.apply_patch({"update_steps": [
        {"step_id": "print_1", "targets": ["columns 1 and 2"], "drops_per_target": 3}
    ]})
    session.resolve_and_validate()

    step = session.resolved.steps[0]
    assert len(step.targets) == 16
    assert step.drops_per_target == 3
    assert step.total_deposits == 48


# ---------------------------------------------------------------------------
# Test C - workflow template
# ---------------------------------------------------------------------------


def test_c_workflow_template_is_interleaved_not_grouped():
    session = session_from("workflow")
    # The shipped order proves dilutions are not forced to come first.
    assert steps_of(session) == ["dilution", "print", "dilution", "print", "wait", "print"]
    assert session.resolved.totals.wait_count == 1
    assert session.resolved.totals.hold_time_s == 1800


def test_c_workflow_template_regroups_to_dilutions_first():
    session = session_from("workflow")
    session.apply_patch({"reorder_steps": [
        "dilution_1", "dilution_2", "print_1", "print_2", "dry_1", "print_3"
    ]})
    report = session.resolve_and_validate()
    assert report.ok, report.errors
    assert steps_of(session) == ["dilution", "dilution", "print", "print", "wait", "print"]


# ---------------------------------------------------------------------------
# Test D - arbitrary reordering and expansion
# ---------------------------------------------------------------------------


def test_d_workflow_template_becomes_an_arbitrary_ordering():
    session = session_from("workflow")
    session.apply_patch({"remove_steps": ["dry_1", "print_3"]})
    report = session.resolve_and_validate()
    assert report.ok, report.errors
    assert steps_of(session) == ["dilution", "print", "dilution", "print"]


def test_d_a_dilution_can_be_inserted_after_a_print():
    session = session_from("workflow")
    session.apply_patch({"add_steps": [{
        "step_type": "dilution", "step_id": "dilution_3", "source": "nanoparticles",
        "diluent": "water", "destination": "working_plate:A3",
        "dilution_factor": 100, "final_volume_ul": 150,
        "insert_after": "print_1",
    }]})
    report = session.resolve_and_validate()
    assert report.ok, report.errors
    assert [step.step_id for step in session.experiment.steps] == [
        "dilution_1", "print_1", "dilution_3", "dilution_2", "print_2", "dry_1", "print_3"
    ]


def test_d_a_long_mixed_workflow_resolves():
    session = session_from("workflow")
    additions = []
    for index in range(3, 9):
        additions.append({
            "step_type": "dilution", "step_id": f"dilution_{index}",
            "source": "nanoparticles", "diluent": "water",
            "destination": f"working_plate:A{index}",
            "dilution_factor": index * 10, "final_volume_ul": 150,
        })
        additions.append({
            "step_type": "print", "step_id": f"print_x{index}",
            "source": f"dilution_{index}", "targets": [f"A{index}:C{index}"],
            "drop_volume_ul": 5, "drops_per_target": 1,
        })
        if index % 3 == 0:
            additions.append({
                "step_type": "wait", "step_id": f"wait_{index}", "duration_s": 300,
            })
    session.apply_patch({"add_steps": additions})
    report = session.resolve_and_validate()
    assert report.ok, report.errors
    assert session.resolved.totals.dilution_count == 8
    assert session.resolved.totals.print_count == 9
    assert session.resolved.totals.wait_count == 3


# ---------------------------------------------------------------------------
# Test E - agent template choice
# ---------------------------------------------------------------------------


def _tool_result(agent: SERSExperimentAgent, name: str) -> dict:
    for message in reversed(agent.tool_transcript()):
        if message["name"] == name:
            return json.loads(message["content"])
    raise AssertionError(f"no tool result for {name}")


@pytest.mark.parametrize(
    "template,expected_shape",
    [
        ("printing", ["print"]),
        ("dilution", ["dilution"]),
        ("workflow", ["dilution", "print", "dilution", "print", "wait", "print"]),
    ],
)
def test_e_agent_can_start_from_any_template(template, expected_shape):
    # Deliberately not asserting on model wording: the contract under test is
    # that the tool produces a usable, validated starting experiment.
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [{"name": "list_sers_templates", "args": {}}],
            [{"name": "start_experiment_from_template",
              "args": {"template": template, "experiment_name": f"{template}_run"}}],
            "Here is the proposed workflow.",
        ]),
        thread_id=f"tmpl-{template}",
        allow_robot_tools=False,
    )
    agent.send("set up an experiment")

    catalogue = _tool_result(agent, "list_sers_templates")
    assert {entry["name"] for entry in catalogue["templates"]} == {
        "dilution", "printing", "workflow"
    }
    started = _tool_result(agent, "start_experiment_from_template")
    assert started["ok"] is True
    assert started["started_from_template"] == template
    assert steps_of(REGISTRY.get()) == expected_shape
    assert REGISTRY.get().status is ExperimentStatus.VALIDATED


def test_e_unknown_template_is_refused_with_the_approved_list():
    result = start_experiment_from_template.invoke(
        {"template": "clover", "experiment_name": "x"}
    )
    assert result["ok"] is False
    assert result["approved_templates"] == ["dilution", "printing", "workflow"]
    assert REGISTRY.list_ids() == []


# ---------------------------------------------------------------------------
# Test F - template-derived experiments patch like any other
# ---------------------------------------------------------------------------


def test_f_template_experiments_use_the_normal_patch_system():
    start_experiment_from_template.invoke(
        {"template": "workflow", "experiment_name": "np_cv"}
    )
    result = update_experiment.invoke({"update_steps": [
        {"step_id": "dilution_1", "dilution_factor": 40},
    ]})
    assert result["ok"] is True
    assert result["changes"] == ["step dilution_1: dilution_factor=40"]

    session = REGISTRY.get()
    # Everything unrelated survives, exactly as for a hand-built experiment.
    assert [step.step_id for step in session.experiment.steps] == [
        "dilution_1", "print_1", "dilution_2", "print_2", "dry_1", "print_3"
    ]
    prints = [step for step in session.experiment.steps if step.step_type == "print"]
    assert [step.drops_per_target for step in prints] == [1, 3, 1]
    assert session.revision == 1


# ---------------------------------------------------------------------------
# Test G - templates inherit every safety behaviour
# ---------------------------------------------------------------------------


def test_g_editing_a_simulated_template_experiment_invalidates_everything():
    session = session_from("workflow")
    session.approve_plan()
    assert session.simulate().passed
    session.approve_live_execution()
    simulated = session.simulated_hash
    assert session.status is ExperimentStatus.APPROVED_FOR_LIVE

    session.apply_patch({"update_steps": [{"step_id": "print_2", "drops_per_target": 5}]})

    assert session.status is ExperimentStatus.DRAFT
    assert session.simulation is None
    assert session.simulated_hash is None
    assert session.plan_approved is False
    assert session.live_execution_approved is False
    session.resolve_and_validate()
    assert session.resolved.resolved_hash != simulated
    assert session.hash_is_current() is False


def test_g_templates_do_not_bypass_the_machine_profile():
    session = session_from("workflow")
    config = session.resolved.as_experiment_config()
    assert config.pipette.mount == "left"
    assert config.dilutions[0].source.bottom_offset_mm == 4.0
    assert config.dilutions[0].destination.dispense_reference == "top"


def test_g_templates_do_not_bypass_physical_validation():
    session = session_from("dilution")
    # Drop the vial below what a 4.0 mm aspirate height can reach.
    session.apply_patch({"set_liquids": [{
        "name": "nanoparticles", "labware": "vial_rack", "well": "A1",
        "loaded_volume_ul": 500, "minimum_remaining_volume_ul": 0,
    }]})
    report = session.resolve_and_validate()
    assert not report.ok
    assert any("would draw air" in error for error in report.errors)
