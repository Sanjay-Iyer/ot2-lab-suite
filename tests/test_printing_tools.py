"""High-level printing tool and exact-artifact safety tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.agents.printing_tools import (
    PRINTING_TOOLS,
    describe_printing_workflow,
    list_printing_designs,
    list_printing_workflows,
    validate_printing_request,
)
from src.agents.robot_http_tools import run_vial_print_robot_http
from src.agents.vial_print_tools import load_vial_print_defaults, update_vial_print_params
from src.printing.artifacts import (
    build_prepared_artifact,
    prepare_printing_request,
    simulate_prepared_request,
)


STANDARD_REQUEST = {
    "family": "standard",
    "workflow_name": "complementary_bp_v10a",
    "parameters": {},
}


def test_printing_agent_surface_contains_only_high_level_nonlive_capabilities():
    names = {tool.name for tool in PRINTING_TOOLS}
    assert names == {
        "list_printing_workflows",
        "describe_printing_workflow",
        "list_printing_designs",
        "validate_printing_request",
        "preview_design_coordinates",
        "build_printing_protocol",
        "simulate_printing_protocol",
    }
    assert not any(token in name for name in names for token in ("aspirate", "dispense", "live", "execute"))


def test_discovery_tools_are_deterministic_and_exclude_legacy_stubs():
    workflows = json.loads(list_printing_workflows.invoke({}))
    assert workflows
    assert all(item["family"] in {"standard", "design"} for item in workflows)
    assert not {"austar", "cleanup"} & {item["workflow_name"] for item in workflows}
    designs = json.loads(list_printing_designs.invoke({}))
    assert [item["design_name"] for item in designs] == ["four_clover"]


def test_workflow_description_exposes_units_and_forbids_extra_fields():
    described = json.loads(
        describe_printing_workflow.invoke({"workflow_name": "four_clover_spacing"})
    )
    schema = described["parameter_schema"]
    assert schema["additionalProperties"] is False
    assert "droplet_volume_ul" in schema["properties"]
    assert schema["properties"]["droplet_volume_ul"]["unit"] == "uL"


def test_validation_tool_runs_registry_bound_nested_schema_and_physics():
    report = json.loads(validate_printing_request.invoke(STANDARD_REQUEST))
    assert report["valid"] is True
    with pytest.raises(ValueError, match="invalid parameters"):
        validate_printing_request.invoke(
            {
                **STANDARD_REQUEST,
                "parameters": {"droplet_volume_ul": -1},
            }
        )


def test_plan_only_build_has_exact_path_hash_and_no_live_state(tmp_path):
    prepared = prepare_printing_request(STANDARD_REQUEST)
    artifact = build_prepared_artifact(
        prepared,
        exercise_motion=False,
        output_dir=tmp_path,
    )
    path = Path(artifact.protocol_path)
    assert path.parent == tmp_path.resolve()
    assert artifact.protocol_dry_run is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    snapshot = Path(artifact.resolved_config_snapshot)
    assert snapshot.is_file()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == artifact.resolved_config_sha256
    assert artifact.request_payload == STANDARD_REQUEST
    assert artifact.base_config_reference.endswith("configs\\printing\\complementary_bp_print_v10a.yaml") or artifact.base_config_reference.endswith("configs/printing/complementary_bp_print_v10a.yaml")
    source = path.read_text(encoding="utf-8")
    assert "DEFAULT_DRY_RUN     = True" in source


def test_simulation_builds_and_runs_the_exact_motion_artifact(tmp_path):
    prepared = prepare_printing_request(STANDARD_REQUEST)
    result = simulate_prepared_request(prepared, output_dir=tmp_path, record=False)
    path = Path(result.artifact.protocol_path)
    assert result.status == "PASS", result.output_tail
    assert result.motion_path_exercised is True
    assert result.artifact.protocol_dry_run is False
    assert "DEFAULT_DRY_RUN     = False" in path.read_text(encoding="utf-8")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result.artifact.sha256


def test_legacy_agent_http_tool_cannot_authorize_live_motion():
    response = run_vial_print_robot_http.invoke(
        {"live": True}
    )
    assert response.startswith("REFUSED: AI tools cannot authorize live OT-2 motion")


def test_legacy_vial_advanced_updates_cannot_switch_protocol_or_run_mode():
    assert "loaded" in load_vial_print_defaults.invoke({}).lower()
    response = update_vial_print_params.invoke(
        {"advanced_updates": {"protocol_version": 18, "run_modes": {"dry_run": False}}}
    )
    assert response.startswith("REFUSED:")
    assert "protocol version" in response


def test_general_agent_prompt_and_surface_end_at_manual_handoff():
    source = (Path(__file__).resolve().parents[1] / "src/agents/main.py").read_text(encoding="utf-8")
    tool_list = source.split("tools = [", 1)[1].split("]", 1)[0]
    assert "deploy_protocol_to_robot" not in tool_list
    assert "execute_protocol_on_robot" not in tool_list
    assert "Agent tools cannot deploy or execute protocols" in source
    assert "call 'deploy_protocol_to_robot'" not in source


def test_tracked_legacy_workflow_defaults_and_latest_artifacts_are_plan_only():
    root = Path(__file__).resolve().parents[1]
    config = (root / "configs/workflows/defaults/vial_dilution_print.yaml").read_text(encoding="utf-8")
    assert "dry_run: true" in config
    for name in ("vial_dilution_print_latest.py", "vial_dilution_print_v2_latest.py"):
        source = (root / "src/protocols/generated" / name).read_text(encoding="utf-8")
        assert "DEFAULT_DRY_RUN     = True" in source
    for name in (
        "01_vial_dilution_paper_print.py",
        "02_vial_dilution_paper_print_p20_dilution.py",
    ):
        source = (root / "src/protocols/printing" / name).read_text(encoding="utf-8")
        assert "DEFAULT_DRY_RUN     = True" in source
