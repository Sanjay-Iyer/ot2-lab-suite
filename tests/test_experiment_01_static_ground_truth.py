"""Stage 1 tests for the independent static Experiment 01 reference."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
PROTOCOL = REPO / "src" / "protocols" / "printing" / "01_printing_standard_ground_truth.py"
PAPER = REPO / "labware" / "paper_print_96_flat.json"
TRACE = REPO / "experiment_01" / "ground_truth" / "static_canonical_trace.json"
TRACE_HASH = REPO / "experiment_01" / "ground_truth" / "static_canonical_sha256.txt"


def _module():
    spec = importlib.util.spec_from_file_location("experiment_01_static_ground_truth", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_back_calculated_cascade_is_exact_and_p20_compatible():
    module = _module()
    cascade = module.serial_cascade()

    assert cascade["factors"] == [1, 2, 4, 8, 16, 32, 64, 128]
    assert cascade["stock_allocation_ul"] == pytest.approx(59.765625)
    assert cascade["retained_usable_ul"] == pytest.approx([30.0] * 8)
    assert cascade["outgoing_ul"] == pytest.approx(
        [29.765625, 29.53125, 29.0625, 28.125, 26.25, 22.5, 15.0, 0.0]
    )
    for volume in [cascade["stock_allocation_ul"], *cascade["outgoing_ul"][:-1], *cascade["diluent_ul"][1:]]:
        chunks = module.split_p20_volume(volume)
        assert sum(chunks) == pytest.approx(volume)
        assert all(0 < chunk <= 20.0 for chunk in chunks)


def test_static_trace_encodes_the_four_column_scientific_layout():
    plan = _module().build_ground_truth_plan()
    prints = [action for action in plan["actions"] if action["action"] == "PRINT"]
    delays = [action for action in plan["actions"] if action["action"] == "DELAY"]

    assert len(prints) == 64
    assert [action["duration_s"] for action in delays] == [300.0] * 4
    assert plan["totals"]["printed_liquid_ul"] == pytest.approx(320.0)
    assert plan["totals"]["configured_experimental_delay_s"] == pytest.approx(1200.0)

    for row_index, row in enumerate("ABCDEFGH"):
        at_col1 = [a for a in prints if a["destination"]["well"] == f"{row}1"]
        at_col2 = [a for a in prints if a["destination"]["well"] == f"{row}2"]
        at_col3 = [a for a in prints if a["destination"]["well"] == f"{row}3"]
        at_col4 = [a for a in prints if a["destination"]["well"] == f"{row}4"]
        factor = 2**row_index
        np_id = f"np_{'stock' if factor == 1 else f'1_{factor}x'}"
        cv_id = f"cv_{'stock' if factor == 1 else f'1_{factor}x'}"

        assert [a["liquid_id"] for a in at_col1] == [np_id, "cv_stock"]
        assert [a["liquid_id"] for a in at_col2] == [np_id, np_id, np_id, "cv_stock"]
        assert [a["drop_index"] for a in at_col2[:3]] == [1, 2, 3]
        assert [a["liquid_id"] for a in at_col3] == ["cv_stock"]
        assert [a["liquid_id"] for a in at_col4] == [cv_id]


def test_static_trace_uses_latest_release_geometry_and_no_oversized_action():
    plan = _module().build_ground_truth_plan()
    pipette = next(
        action for action in plan["actions"] if action["action"] == "LOAD_PIPETTE"
    )
    assert pipette["flow_rates"] == {
        "aspirate_ul_s": 3.0,
        "dispense_ul_s": 3.0,
    }
    for action in plan["actions"]:
        if action["action"] == "TRANSFER":
            assert 0 < action["volume_ul"] <= 20.0
        if action["action"] == "MIX":
            assert action["volume_ul"] == pytest.approx(3.0)
            assert action["location"]["z_mm"] == pytest.approx(0.2)
        if action["action"] == "PRINT":
            assert action["destination"]["z_mm"] == pytest.approx(0.5)
            assert action["volume_ul"] == pytest.approx(5.0)
            assert action["piston_dispense_ul"] == pytest.approx(6.5)
            assert action["push_out_ul"] == pytest.approx(3.0)
            assert action["blow_out"] is True


def test_static_trace_declares_and_validates_source_accessibility():
    plan = _module().build_ground_truth_plan()

    assert len(plan["initial_liquids"]) == 4
    assert all(
        source["loaded_volume_ul"] == pytest.approx(5000.0)
        and source["minimum_remaining_ul"] == pytest.approx(2600.0)
        for source in plan["initial_liquids"]
    )
    accessibility = plan["source_accessibility"]
    assert accessibility["status"] == "PASS"
    assert accessibility["checked_aspirate_or_mix_actions"] == 178
    assert accessibility["minimum_nominal_submerged_margin_ul"] > 2.6
    assert [
        accessibility["ending_volumes_ul"][f"plate:{row}1"]
        for row in "ABCDEFGH"
    ] == pytest.approx([10.0] * 8)
    assert [
        accessibility["ending_volumes_ul"][f"plate:{row}2"]
        for row in "ABCDEFGH"
    ] == pytest.approx([25.0] * 8)
    assert min(
        accessibility["ending_volumes_ul"][f"vial_rack:A{column}"]
        for column in range(1, 5)
    ) > 4800.0
    assert plan["totals"]["tip_count"] == 61


def test_static_canonical_identity_is_stable():
    module = _module()
    first = module.canonical_ground_truth_bytes()
    second = module.canonical_ground_truth_bytes()

    assert first == second
    assert module.ground_truth_sha256() == hashlib.sha256(first).hexdigest()


def test_exported_static_artifacts_match_the_protocol_exactly():
    module = _module()
    exported = json.loads(TRACE.read_text(encoding="utf-8"))
    expected_hash = TRACE_HASH.read_text(encoding="utf-8").strip()

    assert exported["canonical_sha256"] == expected_hash
    exported.pop("canonical_sha256")
    assert exported == module.build_ground_truth_plan()
    assert expected_hash == module.ground_truth_sha256()


def test_static_ground_truth_forced_motion_simulates(tmp_path, monkeypatch):
    import numpy as np

    np.trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    simulator_config = tmp_path / "opentrons-config"
    simulator_config.mkdir()
    monkeypatch.setenv("OT_API_CONFIG_DIR", str(simulator_config))
    from opentrons.simulate import simulate

    with PROTOCOL.open("rb") as protocol_file:
        run_log, _ = simulate(
            protocol_file,
            custom_labware_paths=[str(REPO / "labware")],
        )

    text = "\n".join(entry["payload"].get("text", "") for entry in run_log)
    paper_dispenses = [
        entry["payload"]["location"].point.z
        for entry in run_log
        if entry["payload"].get("text", "").startswith("Dispensing 6.5 uL into")
        and "Paper Print Surface 96" in entry["payload"]["text"]
    ]
    paper_bottom = json.loads(PAPER.read_text(encoding="utf-8"))["wells"]["A1"]["z"]

    assert "Experiment 01 static ground truth complete" in text
    assert "at 3.0 uL/sec" in text
    assert len(paper_dispenses) == 64
    assert all(z == pytest.approx(paper_bottom + 0.5) for z in paper_dispenses)
