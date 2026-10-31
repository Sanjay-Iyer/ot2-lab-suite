"""Focused offline checks for the YAML/manual organization; no SDK or model calls."""
from copy import deepcopy
from pathlib import Path
import os
import subprocess
import sys

import pytest
import yaml

from scripts.launch_agent import launch_arguments
from scripts.manual.test_single_dilution_c12 import _check_settings, _config
from src.agents.dye_demo import experiment_memory, memory
from src.agents.dye_demo.model import DEFAULT_CONFIG, REPO, load_config
from src.agents.dye_demo.redteam.fake_opentrons import FakeProtocol, load_protocol_module
from src.agents.dye_demo.tools import build_protocol, prepare_workflow


@pytest.mark.parametrize("name,expected", [("dilution", {"transfer", "mix"}), ("paper_printing", {"print"})])
def test_yaml_builds_the_same_protocol_operations_offline(name, expected, tmp_path):
    reference = f"configs/experiments/{name}/example.yaml"
    compiled = prepare_workflow(reference)
    assert compiled.ok, compiled.errors
    assert {op.kind for op in compiled.plan.operations} == expected
    protocol = build_protocol(compiled, tmp_path / "artifact")
    module = load_protocol_module(protocol)
    context = FakeProtocol()
    module.run(context)
    assert any(row[0] == "aspirate" for row in context.log)
    paper_dispenses = [row for row in context.log if row[0] == "dispense" and "paper_print" in str(row)]
    assert bool(paper_dispenses) == (name == "paper_printing")
    assert len([row for row in context.log if row[0] == "pick_up_tip"]) == len(compiled.plan.tips)
    assert load_config(protocol.parent / "config.yaml") == compiled.config


def test_paths_are_repository_relative_and_base_is_not_mutated(tmp_path, monkeypatch):
    before = DEFAULT_CONFIG.read_bytes()
    monkeypatch.chdir(tmp_path)
    config = load_config(Path("configs/experiments/dilution/example.yaml"))
    assert config["dilution"]["factors"] == [2, 5, 10]
    assert DEFAULT_CONFIG.read_bytes() == before


def test_shared_config_has_one_level_only(tmp_path):
    nested = tmp_path / "nested.yaml"
    nested.write_text("base_config: another.yaml\n")
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(yaml.safe_dump({"base_config": str(nested)}))
    with pytest.raises(ValueError, match="without another base_config"):
        load_config(experiment)


def test_builder_does_not_overwrite_an_existing_directory(tmp_path):
    compiled = prepare_workflow("configs/experiments/dilution/example.yaml")
    with pytest.raises(FileExistsError):
        build_protocol(compiled, tmp_path)


def test_memory_compatibility_preserves_identity_and_files(tmp_path):
    assert experiment_memory.save_run is memory.save_run
    config = load_config(DEFAULT_CONFIG)
    entry = memory.save_run(operator="Offline Tester", config=config, mode="simulation", directory=tmp_path)
    assert experiment_memory.load_run("Offline Tester", entry, directory=tmp_path) == config
    assert memory.load_index("Offline Tester", tmp_path)[0]["run"] == 1


def test_launcher_profiles_keep_existing_modes_and_workflow_paths():
    for name in ("home", "work"):
        profile = yaml.safe_load((REPO / f"configs/{name}/agent.yaml").read_text())
        interface, args = launch_arguments(profile)
        assert interface == "gui"
        assert ("--simulate" in args) == (name == "home")
        assert ("--offline" in args) == (name == "home")
        assert args[1] == "configs/workflows/defaults/ai_agent_dilution_print_demo.yaml"
    invalid = deepcopy(profile)
    invalid["simulate"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        launch_arguments(invalid)


def test_one_well_check_uses_yaml_values_and_preserves_bounds():
    config = _config()
    assert _check_settings(config)[:2] == (50, 50)
    config.update(destination_well="D12", final_volume_ul=120, dilution_factor=4)
    assert _check_settings(config)[:2] == (30, 90)
    config["p20_max_ul"] = 300
    with pytest.raises(ValueError, match="P20 bounds"):
        _check_settings(config)


@pytest.mark.parametrize("script", ["run_dilution.py", "run_paper_printing.py"])
def test_manual_build_imports_in_a_fresh_process(script, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", str(REPO / "scripts" / script), "--build", "--output", str(tmp_path / "artifact")],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "artifact/protocol.py").is_file()


@pytest.mark.parametrize("simulate", [True, False])
def test_session_dispatch_uses_existing_relocated_scripts(simulate):
    from src.agents.dye_demo.session import SubprocessExecutor
    command = SubprocessExecutor().command(DEFAULT_CONFIG, simulate)
    assert (REPO / command[1]).is_file()
    assert command[1].startswith("scripts/manual/")
    assert ("--live" in command) == (not simulate)
