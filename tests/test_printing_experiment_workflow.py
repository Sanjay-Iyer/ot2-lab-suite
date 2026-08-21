"""Stage 4 persistent experiment YAML and approval-workflow tests."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.printing.artifacts import SimulationResult
from src.printing.experiment_configs import (
    ExperimentConfigPhysicalValidationError,
    PrintingExperimentConfigV1,
    PrintingExperimentDraftV1,
    create_printing_experiment_config,
    describe_experiment_config,
    load_experiment_template,
    load_printing_experiment_config,
)
from src.printing.experiment_workflow import (
    ExperimentLifecycle,
    WorkflowTransitionError,
    advance_approved_experiment_to_ready,
    approve_experiment_workflow,
    build_approved_experiment,
    draft_experiment_workflow,
    reject_experiment_workflow,
    resolve_approved_experiment,
    revise_experiment_workflow,
    simulate_approved_experiment,
    transition_workflow,
)
from src.printing.schemas import parse_resolved_print_plan_json
from src.agents.printing_agent import plan_printing_intent
from src.agents.printing_tools import draft_printing_experiment


REFERENCE_CONFIG = Path("configs/experiments/nanoparticle_drop_series_triplicate_v1.yaml")
STANDARD_TEMPLATE = Path("configs/templates/printing/standard_paper_printing.yaml")


def _layout(columns=(1, 2, 3)) -> dict:
    return {
        "kind": "well_conditions",
        "conditions": [
            {
                "name": f"{drops}_drop" if drops == 1 else f"{drops}_drops",
                "drops_per_position": drops,
                "wells": [f"{row}{column}" for column in columns],
            }
            for row, drops in zip("ABC", (1, 2, 3))
        ],
    }


def _draft(name="Nanoparticle droplet-number series", columns=(1, 2, 3)) -> dict:
    return {
        "name": name,
        "description": "1, 2, and 3 drops in triplicate.",
        "material_display_name": "nanoparticle_A",
        "layout": _layout(columns),
    }


def _awaiting(tmp_path: Path):
    return draft_experiment_workflow(
        "I want to print this nanoparticle with 1, 2, and 3 drops in triplicate.",
        _draft(),
        template_id="standard_paper_printing/v1",
        output_name="nanoparticle_drop_series_triplicate",
        output_dir=tmp_path / "configs",
    )


def test_templates_load_and_are_separate_from_machine_owned_capability_configs():
    standard = load_experiment_template("standard_paper_printing/v1")
    clover = load_experiment_template("four_clover_printing/v1")
    assert standard.constraints.allowed_layout == "well_conditions"
    assert standard.capability_config_reference == "configs/printing/plate_well_direct_print_v9.yaml"
    assert clover.constraints.allowed_layout == "four_clover"
    assert clover.capability_config_reference == "configs/printing/four_clover_air_chase_v12.yaml"
    serialized = str(standard.model_dump())
    for machine_field in ("pipette", "mount", "robot_ip", "tiprack", "source_well"):
        assert machine_field not in serialized


def test_reference_yaml_is_human_readable_strict_and_canonically_hashed():
    artifact = load_printing_experiment_config(REFERENCE_CONFIG)
    text = REFERENCE_CONFIG.read_text(encoding="utf-8")
    assert "drops_per_position: 3" in text
    assert "paper_print_96_flat" in text
    assert "x_mm" not in text and "deck" not in text and "pipette" not in text
    assert artifact.config_sha256 == artifact.config.config_sha256()
    assert artifact.summary.total_deposition_events == 18
    assert artifact.summary.total_liquid_ul == 90.0
    assert artifact.summary.condition_to_wells["1_drop"] == ["A1", "A2", "A3"]
    assert artifact.job.pattern.layers_by_row == {"A": 1, "B": 2, "C": 3}
    assert artifact.job.materials[0].display_name == "nanoparticle_A"


def test_generation_creates_new_version_without_overwriting_template(tmp_path):
    before = STANDARD_TEMPLATE.read_bytes()
    first = create_printing_experiment_config(
        _draft(),
        template_id="standard_paper_printing/v1",
        output_name="drop_series",
        output_dir=tmp_path,
    )
    second = create_printing_experiment_config(
        _draft(),
        template_id="standard_paper_printing/v1",
        output_name="drop_series",
        output_dir=tmp_path,
    )
    assert Path(first.path).name == "drop_series_v1.yaml"
    assert Path(second.path).name == "drop_series_v2.yaml"
    assert first.config.experiment.version == 1
    assert second.config.experiment.version == 2
    assert STANDARD_TEMPLATE.read_bytes() == before


def test_reference_request_stops_at_awaiting_approval_without_plan_build_or_simulation(tmp_path):
    state = _awaiting(tmp_path)
    assert state.lifecycle_state == ExperimentLifecycle.AWAITING_APPROVAL
    assert [transition.to_state for transition in state.transitions] == [
        ExperimentLifecycle.CONFIG_DRAFTED,
        ExperimentLifecycle.CONFIG_VALIDATED,
        ExperimentLifecycle.PLAN_PRESENTED,
        ExperimentLifecycle.AWAITING_APPROVAL,
    ]
    assert state.resolved_plan_sha256 is None
    assert state.build_artifact is None
    assert state.simulation_result is None
    assert state.config_summary.total_deposition_events == 18


def test_agent_routes_reference_language_to_deterministic_yaml_tool(monkeypatch, tmp_path):
    import src.printing.experiment_configs as configs

    monkeypatch.setattr(configs, "EXPERIMENT_DIR", tmp_path / "configs")
    request = "Print this nanoparticle with 1, 2, and 3 drops in triplicate."
    route = plan_printing_intent(request)
    assert route.tool_name == "draft_printing_experiment"
    assert route.skill_names == ["standard-paper-printing"]
    result = json.loads(
        draft_printing_experiment.invoke(
            {
                "request_text": request,
                "draft": _draft(),
                "template_id": "standard_paper_printing/v1",
                "output_name": "agent_reference_case",
            }
        )
    )
    assert result["lifecycle_state"] == "AWAITING_APPROVAL"
    assert result["config_summary"]["total_deposition_events"] == 18


def test_repeated_drops_compile_as_repeated_events_not_larger_dispenses(tmp_path):
    state = approve_experiment_workflow(_awaiting(tmp_path), statement="Approve exact YAML")
    state = resolve_approved_experiment(state, output_dir=tmp_path / "artifacts")
    plan = parse_resolved_print_plan_json(Path(state.resolved_plan_path).read_bytes())
    assert len([item for item in plan.deposits if item.destination.well == "A1"]) == 1
    assert len([item for item in plan.deposits if item.destination.well == "B1"]) == 2
    assert len([item for item in plan.deposits if item.destination.well == "C1"]) == 3
    assert {item.deposition.liquid_volume_ul for item in plan.deposits} == {5.0}


def test_user_change_creates_child_version_new_hash_and_returns_to_approval(tmp_path):
    original = _awaiting(tmp_path)
    original_bytes = Path(original.config_path).read_bytes()
    revised = revise_experiment_workflow(
        original,
        {"layout": _layout((4, 5, 6))},
        output_dir=tmp_path / "configs",
    )
    assert revised.lifecycle_state == ExperimentLifecycle.AWAITING_APPROVAL
    assert revised.config_version == 2
    assert revised.parent_config_sha256 == original.config_sha256
    assert revised.config_sha256 != original.config_sha256
    assert Path(original.config_path).read_bytes() == original_bytes
    assert revised.config_summary.condition_to_wells == {
        "1_drop": ["A4", "A5", "A6"],
        "2_drops": ["B4", "B5", "B6"],
        "3_drops": ["C4", "C5", "C6"],
    }
    assert revised.approved_config_sha256 is None


def test_modifying_an_approved_config_invalidates_old_approval(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved v1")
    revised = revise_experiment_workflow(
        approved,
        {"layout": _layout((4, 5, 6))},
        output_dir=tmp_path / "configs",
    )
    assert revised.lifecycle_state == ExperimentLifecycle.AWAITING_APPROVAL
    assert revised.approval is None
    assert revised.approved_config_sha256 is None
    with pytest.raises(WorkflowTransitionError, match="only an APPROVED"):
        resolve_approved_experiment(revised)


def test_explicit_approval_seals_exact_config_sha(tmp_path):
    awaiting = _awaiting(tmp_path)
    approved = approve_experiment_workflow(awaiting, statement="Yes, run that exact plan.")
    assert approved.lifecycle_state == ExperimentLifecycle.APPROVED
    assert approved.approval.decision == "APPROVE"
    assert approved.approved_config_sha256 == awaiting.config_sha256
    assert approved.resolved_plan_sha256 is None


def test_config_content_change_after_approval_is_rejected(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    path = Path(approved.config_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: 1, 2, and 3 drops in triplicate.",
            "description: Changed after approval.",
        ),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowTransitionError, match="changed after workflow validation"):
        resolve_approved_experiment(approved)


def test_rejection_is_terminal_and_creates_no_execution_artifacts(tmp_path):
    rejected = reject_experiment_workflow(_awaiting(tmp_path), statement="No, don't run that.")
    assert rejected.lifecycle_state == ExperimentLifecycle.PLAN_REJECTED
    assert rejected.approval.decision == "REJECT"
    assert rejected.build_artifact is None
    with pytest.raises(WorkflowTransitionError):
        resolve_approved_experiment(rejected)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value["layout"]["conditions"][0].update(drops_per_position=0), "greater than or equal to 1"),
        (lambda value: value["layout"]["conditions"][0].update(wells=["A20"]), "valid paper positions"),
        (lambda value: value["layout"]["conditions"][1].update(wells=["A1", "A2", "A3"]), "share paper positions|distinct paper rows"),
        (lambda value: value["layout"]["conditions"][2].update(wells=["C1", "C2"]), "same replicate columns"),
    ],
)
def test_invalid_scientific_configs_fail_strict_schema(mutator, message):
    payload = _draft()
    mutator(payload)
    with pytest.raises(ValidationError, match=message):
        PrintingExperimentDraftV1.model_validate(payload)


def test_impossible_droplet_volume_is_a_physical_validation_error(tmp_path):
    payload = _draft()
    payload["droplet_volume_ul"] = 5000.0
    with pytest.raises(ExperimentConfigPhysicalValidationError):
        create_printing_experiment_config(
            payload,
            template_id="standard_paper_printing/v1",
            output_name="impossible",
            output_dir=tmp_path,
        )


def test_workflow_records_physical_config_failure_as_structured_state(tmp_path):
    payload = _draft()
    payload["droplet_volume_ul"] = 5000.0
    invalid = draft_experiment_workflow(
        "Print 5000 uL per drop.",
        payload,
        template_id="standard_paper_printing/v1",
        output_name="invalid_volume",
        output_dir=tmp_path,
    )
    assert invalid.lifecycle_state == ExperimentLifecycle.CONFIG_INVALID
    assert invalid.error_stage == "physical_plan_validation"
    assert invalid.config_path is None


def test_invalid_state_transitions_are_rejected_with_allowed_states(tmp_path):
    awaiting = _awaiting(tmp_path)
    with pytest.raises(WorkflowTransitionError, match="AWAITING_APPROVAL -> READY_FOR_EXECUTION"):
        transition_workflow(
            awaiting,
            ExperimentLifecycle.READY_FOR_EXECUTION,
            reason="attempted bypass",
        )
    with pytest.raises(WorkflowTransitionError, match="only a RESOLVED"):
        build_approved_experiment(awaiting)


def test_config_job_plan_provenance_chain_preserves_physical_plan_identity(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    resolved = resolve_approved_experiment(approved, output_dir=tmp_path / "artifacts")
    plan = parse_resolved_print_plan_json(Path(resolved.resolved_plan_path).read_bytes())
    assert plan.provenance.source_experiment_config_sha256 == resolved.config_sha256
    assert plan.provenance.source_job_sha256 == resolved.print_job_sha256
    assert plan.plan_id == plan.plan_sha256() == resolved.resolved_plan_sha256
    payload = plan.model_dump(mode="json", exclude={"plan_id"})
    payload["provenance"]["source_experiment_config_sha256"] = "f" * 64
    relinked = type(plan).from_content(**payload)
    assert relinked.plan_id == plan.plan_id


def test_four_clover_yaml_path_uses_same_approval_gate(tmp_path):
    state = draft_experiment_workflow(
        "Print one four-clover with 5 uL BP droplets.",
        {
            "name": "Four-clover config case",
            "layout": {
                "kind": "four_clover",
                "geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
                "centers": [
                    {
                        "name": "air_chase_5ul",
                        "reference_well": "E6",
                        "x_offset_mm": 4.5,
                        "y_offset_mm": 4.5,
                    }
                ],
                "layers": 1,
            },
        },
        template_id="four_clover_printing/v1",
        output_name="clover_case",
        output_dir=tmp_path / "configs",
    )
    assert state.lifecycle_state == ExperimentLifecycle.AWAITING_APPROVAL
    approved = approve_experiment_workflow(state, statement="Approved")
    resolved = resolve_approved_experiment(approved, output_dir=tmp_path / "artifacts")
    plan = parse_resolved_print_plan_json(Path(resolved.resolved_plan_path).read_bytes())
    assert plan.totals.clover_count == 1
    assert plan.totals.deposit_count == 4


def test_build_reuses_existing_protocol_and_embeds_full_provenance(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    resolved = resolve_approved_experiment(approved, output_dir=tmp_path / "artifacts")
    built = build_approved_experiment(resolved, output_dir=tmp_path / "artifacts")
    source = Path(built.build_artifact.protocol_path).read_text(encoding="utf-8")
    assert built.lifecycle_state == ExperimentLifecycle.PROTOCOL_BUILT
    assert built.build_artifact.source_experiment_config_sha256 == built.config_sha256
    assert built.build_artifact.source_job_sha256 == built.print_job_sha256
    assert built.build_artifact.source_plan_sha256 == built.resolved_plan_sha256
    assert f"# experiment_config_sha256: {built.config_sha256}" in source
    assert "=== Direct Plate-Well -> Paper Print V9" in source


def test_protocol_file_change_after_build_is_rejected_before_simulation(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    resolved = resolve_approved_experiment(approved, output_dir=tmp_path / "artifacts")
    built = build_approved_experiment(resolved, output_dir=tmp_path / "artifacts")
    path = Path(built.build_artifact.protocol_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after hashing"):
        simulate_approved_experiment(built, record=False)


def test_simulation_failure_never_reaches_ready(monkeypatch, tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    resolved = resolve_approved_experiment(approved, output_dir=tmp_path / "artifacts")
    built = build_approved_experiment(resolved, output_dir=tmp_path / "artifacts")

    def fail(artifact, *, record=True):
        return SimulationResult(
            status="FAIL",
            motion_path_exercised=True,
            artifact=artifact,
            output_tail="synthetic simulator failure",
        )

    monkeypatch.setattr("src.printing.experiment_workflow.simulate_built_artifact", fail)
    failed = simulate_approved_experiment(built, record=False)
    assert failed.lifecycle_state == ExperimentLifecycle.SIMULATION_FAILED
    assert "synthetic simulator failure" in failed.errors[0]


def test_exact_approved_standard_config_simulates_to_ready_for_execution(tmp_path):
    approved = approve_experiment_workflow(_awaiting(tmp_path), statement="Approved")
    ready = advance_approved_experiment_to_ready(
        approved,
        output_dir=tmp_path / "artifacts",
        record_simulation=False,
    )
    assert ready.lifecycle_state == ExperimentLifecycle.READY_FOR_EXECUTION
    assert ready.simulation_result.status == "PASS"
    assert ready.simulation_result.artifact.sha256 == ready.build_artifact.sha256
    assert "18 deposits, 90 uL total" in ready.simulation_result.output_tail


def test_config_inspection_reports_canonical_identity_and_scientific_summary():
    summary = describe_experiment_config(REFERENCE_CONFIG)
    assert summary.config_version == 1
    assert summary.config_sha256 == "532ea5e724a3e832dd4911e2b1d8e37ab60fc6046a210e391f10bb2425b8d069"
    assert "Material: nanoparticle_A" in summary.as_text(path=str(REFERENCE_CONFIG))
