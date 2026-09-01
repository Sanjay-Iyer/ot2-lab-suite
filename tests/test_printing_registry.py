"""Workflow/design registry and production-adapter equivalence tests."""
from __future__ import annotations

import importlib.util
import json

import pytest

from scripts.build_vial_dilution_print import PROTOCOL_VERSIONS
from scripts import run_vial_print_robot as robot_runner
from src.printing.compiler import apply_workflow_patch
from src.printing.config import REPO_ROOT, load_printing_config
from src.printing.designs import get_design, list_designs
from src.printing.schemas import ComplementaryQuickPatch, FourCloverPatch, PrintingFamily
from src.printing.workflows import (
    builder_protocol_versions,
    embed_raw_versions,
    get_workflow,
    list_workflows,
    resolve_printing_request,
)


def test_builder_mapping_comes_from_registry_without_compatibility_drift():
    assert PROTOCOL_VERSIONS == builder_protocol_versions()
    assert set(PROTOCOL_VERSIONS) == {
        1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19
    }
    assert PROTOCOL_VERSIONS[18][1] == "four_clover_spacing_v13"
    assert PROTOCOL_VERSIONS[19][1] == "ai_agent_dilution_print_demo"
    assert robot_runner._PROTOCOL_BY_VERSION == {
        version: robot_runner.GENERATED_PROTOCOL_DIR / f"{stem}_latest.py"
        for version, (_, stem) in PROTOCOL_VERSIONS.items()
    }
    modern_versions = {spec.builder_version for spec in list_workflows()}
    assert modern_versions <= embed_raw_versions()
    assert modern_versions <= robot_runner.API_215_VERSIONS
    assert modern_versions <= robot_runner.IMAGELESS_VERSIONS
    assert modern_versions <= robot_runner.NO_MATRIX_VERSIONS


def test_runner_never_falls_back_to_v1_for_a_bad_supplied_config(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ValueError, match="cannot read printing config"):
        robot_runner._protocol_for_config(str(missing))

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("protocol_version: [", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read printing config"):
        robot_runner._protocol_for_config(str(malformed))

    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("protocol_version: 999\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown protocol_version"):
        robot_runner._protocol_for_config(str(unknown))


def test_legacy_entries_are_buildable_but_not_agent_discoverable():
    assert 1 in builder_protocol_versions()
    with pytest.raises(KeyError, match="hidden compatibility"):
        get_workflow("vial_dilution_print_v1")
    assert all(spec.builder_version >= 9 for spec in list_workflows())


def test_registry_advertises_only_existing_functioning_files():
    for spec in list_workflows():
        assert spec.base_protocol.is_file()
        assert spec.default_config is not None and spec.default_config.is_file()
        assert spec.patch_model is not None
        assert "NotImplementedError" not in spec.base_protocol.read_text(encoding="utf-8")


def test_initial_plus_extra_variants_use_the_matching_typed_patch():
    assert get_workflow("complementary_bp_quick_v10c").patch_model is ComplementaryQuickPatch
    assert get_workflow("complementary_dmmp_spot_v10bv2").patch_model is ComplementaryQuickPatch


def test_each_family_has_exactly_one_default():
    for family in PrintingFamily:
        defaults = [spec for spec in list_workflows(family=family) if spec.is_default]
        assert len(defaults) == 1


def test_family_mismatch_is_rejected_before_config_loading():
    with pytest.raises(ValueError, match="belongs to family"):
        resolve_printing_request(
            {"family": "standard", "workflow_name": "four_clover_spacing"}
        )


def test_four_clover_is_the_only_current_registered_design():
    assert [design.name for design in list_designs()] == ["four_clover"]
    assert get_design("four_clover").patch_model is FourCloverPatch
    with pytest.raises(KeyError, match="unknown printing design"):
        get_design("spiral")


def test_empty_typed_patch_preserves_registered_base_config():
    spec = get_workflow("four_clover_spacing")
    base = load_printing_config(spec.default_config)
    assert apply_workflow_patch(base, FourCloverPatch()) == base


def test_four_clover_adapter_matches_production_protocol_resolver_exactly():
    config = load_printing_config("configs/printing/four_clover_spacing_v13.yaml")
    adapter = get_design("four_clover").generate(config)

    protocol_path = REPO_ROOT / "src/protocols/printing/12_four_clover_paper_print.py"
    spec = importlib.util.spec_from_file_location("four_clover_equivalence", protocol_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG = config
    wells = json.loads(
        (REPO_ROOT / "labware/paper_print_96_flat.json").read_text(encoding="utf-8")
    )["wells"]

    def well_xy(name):
        well = wells[str(name).upper()]
        return float(well["x"]), float(well["y"])

    expected_clovers = module._resolve_clovers(well_xy)
    expected_mode, expected_plan = module._print_order(expected_clovers)
    assert adapter["clovers"] == expected_clovers
    assert adapter["order_mode"] == expected_mode
    assert adapter["plan"] == [
        {
            "clover": clover["name"],
            "layer": layer,
            "droplet": droplet,
            "absolute": list(clover["droplets"][droplet]["absolute"]),
        }
        for clover, layer, droplet in expected_plan
    ]
