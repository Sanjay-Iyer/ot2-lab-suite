"""Stage 8 trusted approval and local-simulation workflow tests."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import src.agents.printing_tools as printing_tools
from src.agents.printing_agent import STANDARD_EXPERIMENT_AGENT_TOOLS
from src.agents.printing_tools import StandardExperimentProposalV1
from src.agents.standard_experiment_workflow import (
    StandardExperimentLifecycle,
    StandardExperimentWorkflowTransitionError,
    approve_standard_experiment_workflow,
    present_standard_experiment_for_approval,
    reject_standard_experiment_workflow,
    simulate_approved_standard_experiment_workflow,
    StandardExperimentWorkflowStateV1,
)


REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "configs/templates/printing/01_printing_standard.template.yaml"


@pytest.fixture
def proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        printing_tools, "STANDARD_EXPERIMENT_PROPOSAL_DIR", tmp_path / "proposals"
    )
    monkeypatch.setenv("OT2_STANDARD_EXPERIMENT_APPROVAL_KEY", "22" * 32)
    monkeypatch.setenv("OT_API_CONFIG_DIR", str(tmp_path / "opentrons-config"))
    (tmp_path / "opentrons-config").mkdir()
    config = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    return present_standard_experiment_for_approval(
        "Prepare a neutral three-point ladder, print it, and add control replicates.",
        config,
        output_name="stage8_neutral_workflow",
    )


def test_proposal_stops_at_awaiting_approval_with_exact_artifacts(proposal):
    assert proposal.lifecycle_state == StandardExperimentLifecycle.AWAITING_APPROVAL
    assert proposal.approval is None
    assert proposal.simulation is None
    assert proposal.artifact.job_sha256 == proposal.validation.job_sha256
    assert proposal.validation.job_sha256 == proposal.resolution.job_sha256
    assert proposal.resolution.job_sha256 == proposal.preview.job_sha256
    assert Path(proposal.artifact.path).is_file()


def test_preapproval_review_exposes_scientific_and_physical_decisions(proposal):
    review = proposal.preview.review

    for required in (
        "LIQUIDS LOADED BY THE OPERATOR",
        "PREPARATION",
        "RESOLVED LIQUID OPERATIONS",
        "[transfer]",
        "[mix]",
        "SUBSTRATE LAYOUT",
        "PRINT SOURCES AND DESTINATIONS",
        "plate:A1 -> paper:A1",
        "PROCEDURE AS REQUESTED",
        "purpose=sample",
        "purpose=control",
        "targets",
        "rest after each pass",
        "TOTALS",
    ):
        assert required in review
    assert "paper:A1 condition=ladder_row purpose=sample series point 1/3" in review
    assert "paper:A2 condition=replicate_controls purpose=control replicate 1/3" in review
    assert "paper:B2 condition=replicate_controls purpose=control replicate 2/3" in review
    assert "paper:C2 condition=replicate_controls purpose=control replicate 3/3" in review
    assert "targets=['A2', 'B2', 'C2']" in review


def test_agent_cannot_mint_approval_or_bypass_workflow(proposal):
    tool_names = {item.name for item in STANDARD_EXPERIMENT_AGENT_TOOLS}
    assert "approve_standard_experiment_workflow" not in tool_names
    assert "seal_standard_experiment_approval" not in tool_names

    with pytest.raises(StandardExperimentWorkflowTransitionError, match="cannot simulate"):
        simulate_approved_standard_experiment_workflow(proposal)


def test_rejection_is_terminal_and_cannot_be_simulated(proposal):
    rejected = reject_standard_experiment_workflow(
        proposal, "Reject: the control layout must be revised."
    )

    assert rejected.lifecycle_state == StandardExperimentLifecycle.PLAN_REJECTED
    with pytest.raises(StandardExperimentWorkflowTransitionError, match="cannot simulate"):
        simulate_approved_standard_experiment_workflow(rejected)
    with pytest.raises(StandardExperimentWorkflowTransitionError, match="cannot approve"):
        approve_standard_experiment_workflow(rejected, "I approve this experiment.")


def test_approval_is_explicit_exact_job_only_and_local_simulation_ends_ready(proposal):
    with pytest.raises(ValueError, match="negation"):
        approve_standard_experiment_workflow(proposal, "Do not run; not approved")

    approved = approve_standard_experiment_workflow(
        proposal, "I approve this exact displayed experiment."
    )
    assert approved.lifecycle_state == StandardExperimentLifecycle.APPROVED
    assert approved.approval.job_sha256 == approved.artifact.job_sha256

    ready = simulate_approved_standard_experiment_workflow(approved)
    assert ready.lifecycle_state == StandardExperimentLifecycle.READY_FOR_EXECUTION
    assert ready.simulation.simulation == "PASS"
    assert ready.simulation.status == "READY_FOR_EXECUTION"
    assert ready.simulation.job_sha256 == ready.artifact.job_sha256
    assert "no live execution performed" in ready.history[-1]


def test_approval_seal_rejects_a_changed_config_snapshot(proposal):
    approved = approve_standard_experiment_workflow(
        proposal, "I approve this exact displayed experiment."
    )
    changed = deepcopy(approved.experiment_config.model_dump(mode="json"))
    changed["experiment"]["metadata"]["experiment_id"] = "changed_after_approval"
    tampered_state = approved.model_copy(
        update={"experiment_config": StandardExperimentProposalV1.model_validate(changed)}
    )

    with pytest.raises(ValueError, match="do not describe one exact job"):
        simulate_approved_standard_experiment_workflow(tampered_state)


def test_config_swap_before_approval_is_rejected_before_a_seal_is_minted(proposal):
    changed = deepcopy(proposal.experiment_config.model_dump(mode="json"))
    changed["experiment"]["metadata"]["experiment_id"] = "changed_before_approval"
    tampered_state = proposal.model_copy(
        update={"experiment_config": StandardExperimentProposalV1.model_validate(changed)}
    )

    with pytest.raises(ValueError, match="do not describe one exact job"):
        approve_standard_experiment_workflow(
            tampered_state, "I approve this exact displayed experiment."
        )


def test_deserialization_rejects_mismatched_displayed_evidence(proposal):
    payload = proposal.model_dump(mode="json")
    payload["preview"]["job_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="do not describe one exact job"):
        StandardExperimentWorkflowStateV1.model_validate(payload)


def test_durable_approval_record_verifies_in_a_fresh_process(proposal):
    approved = approve_standard_experiment_workflow(
        proposal, "I approve this exact displayed experiment."
    )
    code = (
        "import sys; "
        "from src.agents.printing_tools import "
        "StandardExperimentApprovalSealV1, _verify_standard_approval; "
        "a=StandardExperimentApprovalSealV1.model_validate_json(sys.argv[1]); "
        "_verify_standard_approval(sys.argv[2], a)"
    )
    environment = os.environ.copy()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            approved.approval.model_dump_json(),
            approved.artifact.job_sha256,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
