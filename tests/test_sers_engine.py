"""Focused integration checks for the modular SERS demo engine."""

from __future__ import annotations

from pathlib import Path

from src.sers_engine.agent_tools import execute_sers_workflow
from src.sers_engine.dilution import plan_transfer_chunks
from src.sers_engine.schema import (
    config_as_dict,
    load_experiment_config,
    validate_experiment_config,
)


REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "configs" / "sers_cv_titration_demo.yaml"


def test_p20_chunks_leave_no_subminimum_remainder() -> None:
    assert plan_transfer_chunks(20) == [18.0, 2.0]
    assert plan_transfer_chunks(80) == [18.0, 18.0, 18.0, 18.0, 8.0]
    assert plan_transfer_chunks(18.5) == [17.5, 1.0]


def test_demo_schema_preserves_physical_order_and_limits() -> None:
    config = load_experiment_config(DEMO)
    assert [(step.kind, step.ref) for step in config.ordered_workflow()] == [
        ("print_layer", "nanoparticle_print"),
        ("dilution", "cv_5x_in_b11"),
        ("print_layer", "cv_overprint"),
    ]
    assert config.deck_layout.labware["working_plate"].safe_max_volume_ul == 250
    assert config.tips_required == 5


def test_demo_runs_end_to_end_on_virtual_hardware() -> None:
    config = load_experiment_config(DEMO)
    result = execute_sers_workflow(config_as_dict(config))
    assert result["status"] == "completed", result
    assert result["mode"] == "simulate"
    assert result["tips_used"] == 5
    assert result["deposits"] == 24
    assert result["printed_volume_ul"] == 120
    assert result["drying_time_s"] == 1800
    dilution = result["executed_steps"][1]
    assert dilution["stock_chunks_ul"] == [18.0, 2.0]
    assert dilution["diluent_chunks_ul"] == [18.0, 18.0, 18.0, 18.0, 8.0]


def test_agent_boundary_rejects_live_motion() -> None:
    config = load_experiment_config(DEMO)
    result = execute_sers_workflow(config_as_dict(config), live=True)
    assert result["status"] == "rejected"
    assert result["mode"] == "live"


def _simulate(config):
    """Run one validated config on virtual hardware and return the command log."""
    from src.sers_engine.agent_tools import _numpy_compatibility_shim
    from src.sers_engine.orchestrator import run_unified_protocol

    _numpy_compatibility_shim()
    from opentrons.simulate import get_protocol_api

    protocol = get_protocol_api(
        config.api_level, robot_type=config.robot_type, use_virtual_hardware=True
    )
    summary = run_unified_protocol(protocol, config)
    return protocol.commands(), summary


def test_each_transfer_purges_once_so_the_dilution_ratio_holds() -> None:
    # blow_out leaves the plunger unprepared and API 2.15 cannot re-arm it in
    # air, so a purge between chunks would add an extra slug to every later
    # aspiration.  24 printed deposits + one purge per dilution transfer.
    commands, _ = _simulate(load_experiment_config(DEMO))
    assert sum(1 for line in commands if line.startswith("Blowing out")) == 24 + 2


def test_per_target_strategy_takes_a_fresh_tip_for_every_spot() -> None:
    payload = config_as_dict(load_experiment_config(DEMO))
    for layer in payload["print_layers"]:
        if layer["layer_name"] == "cv_overprint":
            layer["tip_strategy"] = "per_target"
    config = validate_experiment_config(payload)
    assert config.tips_required == 1 + 3 + 12
    result = execute_sers_workflow(payload)
    assert result["status"] == "completed", result
    assert result["tips_used"] == config.tips_required


def test_aspirating_above_the_liquid_surface_is_refused() -> None:
    # 500 uL is a 0.8 mm puddle in the 28 mm vial, so the laboratory's usual
    # 4.0 mm vial height would draw air rather than Crystal Violet.
    payload = config_as_dict(load_experiment_config(DEMO))
    payload["dilutions"][0]["source"]["bottom_offset_mm"] = 4.0
    result = execute_sers_workflow(payload)
    assert result["status"] == "error"
    assert any("would draw air" in message for message in result["errors"]), result


def test_thin_submersion_margins_are_reported() -> None:
    _, summary = _simulate(load_experiment_config(DEMO))
    flagged = {note.split(":")[0] for note in summary["depth_warnings"]}
    assert flagged == {"dilution cv_5x_in_b11 stock", "print layer cv_overprint source"}
