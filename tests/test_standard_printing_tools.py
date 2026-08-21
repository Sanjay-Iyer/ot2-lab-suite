"""Stage 5 high-level tool boundary for standard printing experiments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import src.agents.printing_tools as printing_tools
from src.agents.printing_tools import (
    STANDARD_PRINT_EXPERIMENT_TOOLS,
    StandardExperimentConfigArtifactV1,
    StandardExperimentPreviewResultV1,
    StandardExperimentResolutionResultV1,
    StandardExperimentSimulationResultV1,
    StandardExperimentValidationResultV1,
    inspect_standard_printing_layout,
    create_standard_printing_experiment_config,
    resolve_standard_printing_experiment,
    seal_standard_experiment_approval,
    simulate_approved_standard_printing_experiment,
    validate_standard_printing_experiment,
)


REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "configs/templates/printing/01_printing_standard.template.yaml"


@pytest.fixture(scope="module")
def generalized_config():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_proposal_directory(monkeypatch, tmp_path):
    proposal_dir = tmp_path / "proposals"
    monkeypatch.setattr(
        printing_tools, "STANDARD_EXPERIMENT_PROPOSAL_DIR", proposal_dir
    )
    monkeypatch.setenv("OT2_STANDARD_EXPERIMENT_APPROVAL_KEY", "11" * 32)


def test_standard_experiment_tool_surface_is_high_level_and_nonlive():
    names = {item.name for item in STANDARD_PRINT_EXPERIMENT_TOOLS}

    assert {
        "validate_standard_printing_experiment",
        "create_standard_printing_experiment_config",
        "resolve_standard_printing_experiment",
        "inspect_standard_printing_layout",
        "simulate_approved_standard_printing_experiment",
    } <= names
    assert not any(
        token in name
        for name in names
        for token in ("aspirate", "dispense", "move", "live", "execute")
    )


def test_tool_input_schema_forbids_unknown_top_level_arguments():
    schema = validate_standard_printing_experiment.args_schema.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"experiment_config"}


def test_validation_tool_returns_a_typed_canonical_config(generalized_config):
    payload = json.loads(
        validate_standard_printing_experiment.invoke(
            {"experiment_config": generalized_config}
        )
    )
    result = StandardExperimentValidationResultV1.model_validate(payload)

    assert result.status == "PASS"
    assert result.schema_version == "print-experiment-job/v1"
    assert result.machine_profile.startswith("configs/machines/")
    assert "machine:" not in result.canonical_config_yaml


def test_create_tool_persists_an_immutable_validated_proposal(generalized_config):
    raw = create_standard_printing_experiment_config.invoke(
        {
            "experiment_config": generalized_config,
            "output_name": "neutral_tool_test",
        }
    )
    artifact = StandardExperimentConfigArtifactV1.model_validate_json(raw)
    path = REPO / artifact.path

    assert path.is_file()
    assert path.read_text(encoding="utf-8") == artifact.canonical_config_yaml
    assert raw == create_standard_printing_experiment_config.invoke(
        {
            "experiment_config": generalized_config,
            "output_name": "neutral_tool_test",
        }
    )


def test_proposal_yaml_and_artifact_identity_ignore_mapping_key_order(
    generalized_config,
):
    reversed_config = dict(reversed(list(generalized_config.items())))
    first = StandardExperimentConfigArtifactV1.model_validate_json(
        create_standard_printing_experiment_config.invoke(
            {
                "experiment_config": generalized_config,
                "output_name": "neutral_order_test",
            }
        )
    )
    second = StandardExperimentConfigArtifactV1.model_validate_json(
        create_standard_printing_experiment_config.invoke(
            {
                "experiment_config": reversed_config,
                "output_name": "neutral_order_test",
            }
        )
    )

    assert first.job_sha256 == second.job_sha256
    assert first.canonical_config_yaml == second.canonical_config_yaml
    assert first.file_sha256 == second.file_sha256
    assert first.path == second.path


def test_approved_simulation_requires_an_unforgeable_exact_job_seal(
    generalized_config,
):
    with pytest.raises(ValueError, match="negation"):
        seal_standard_experiment_approval(generalized_config, "Do not run; not approved")

    approval = seal_standard_experiment_approval(
        generalized_config, "I approve this exact proposed experiment."
    )
    raw = simulate_approved_standard_printing_experiment.invoke(
        {"experiment_config": generalized_config, "approval": approval.model_dump()}
    )
    result = StandardExperimentSimulationResultV1.model_validate_json(raw)

    assert result.status == "READY_FOR_EXECUTION"
    assert result.simulation == "PASS"
    assert result.print_count == 6

    tampered = approval.model_copy(update={"seal": "0" * 64})
    with pytest.raises(ValueError, match="does not authorize"):
        simulate_approved_standard_printing_experiment.invoke(
            {
                "experiment_config": generalized_config,
                "approval": tampered.model_dump(),
            }
        )


def test_approval_minting_requires_a_protected_persistent_key(
    generalized_config, monkeypatch
):
    monkeypatch.delenv("OT2_STANDARD_EXPERIMENT_APPROVAL_KEY")

    with pytest.raises(RuntimeError, match="trusted workflow must configure"):
        seal_standard_experiment_approval(
            generalized_config, "I approve this exact proposed experiment."
        )


def test_validation_tool_cannot_accept_agent_invented_machine_geometry(
    generalized_config,
):
    payload = dict(generalized_config)
    payload.pop("machine_profile")
    payload["machine"] = {"robot_type": "OT-2"}

    with pytest.raises(ValueError, match="machine_profile|Extra inputs"):
        validate_standard_printing_experiment.invoke({"experiment_config": payload})


def test_resolution_tool_returns_deterministic_summary_not_robot_actions(
    generalized_config,
):
    raw = resolve_standard_printing_experiment.invoke(
        {"experiment_config": generalized_config}
    )
    result = StandardExperimentResolutionResultV1.model_validate_json(raw)

    assert result.status == "PASS"
    assert result.totals["action_count"] == 25
    assert result.totals["print_count"] == 6
    assert "actions" not in json.loads(raw)
    assert "aspirate" not in raw.lower()
    assert raw == resolve_standard_printing_experiment.invoke(
        {"experiment_config": generalized_config}
    )


def test_layout_inspection_returns_scientist_review_without_low_level_commands(
    generalized_config,
):
    raw = inspect_standard_printing_layout.invoke(
        {"experiment_config": generalized_config}
    )
    result = StandardExperimentPreviewResultV1.model_validate_json(raw)

    assert result.totals["print_count"] == 6
    assert "EXPERIMENT:" in result.review
    assert "SUBSTRATE LAYOUT" in result.review
    assert "pick_up_tip" not in result.review
    assert "pipette.aspirate" not in result.review
