"""
tests/test_printing_demo_generated_protocol.py
================================================
Verifies that the generated robot protocol produced by
printing_demo_protocol.py contains no forbidden host-side imports
and includes all required robot-side patterns.
"""

import sys
import pytest
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONFIG_YAML = REPO_ROOT / "configs" / "workflows" / "defaults" / "printing_demo.yaml"

# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def generated_text() -> str:
    """
    Generate the robot protocol from the default printing_demo.yaml and
    return the generated file text.  Does NOT run opentrons simulation
    (no opentrons dependency required for this test).
    """
    import yaml
    from src.protocols.printing_demo_protocol import (
        _build_robot_script,
        _check_generated_script,
    )

    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    # Use Pydantic validation if available
    try:
        from src.protocols.printing_demo_protocol import PrintingDemoConfig, HAS_PYDANTIC
        if HAS_PYDANTIC:
            validated = PrintingDemoConfig(**config_data)
            config_dict = validated.model_dump()
        else:
            config_dict = config_data
    except Exception:
        config_dict = config_data

    script_text = _build_robot_script(config_dict)
    return script_text


# ── Forbidden import tests ─────────────────────────────────────────────

class TestForbiddenImports:
    """The generated robot protocol must contain none of these."""

    def test_no_import_yaml(self, generated_text: str) -> None:
        assert "import yaml" not in generated_text.lower(), (
            "Generated robot protocol contains 'import yaml'. "
            "YAML is forbidden on the OT-2 robot environment."
        )

    def test_no_from_yaml(self, generated_text: str) -> None:
        assert "from yaml" not in generated_text.lower(), (
            "Generated robot protocol contains 'from yaml'. "
            "YAML imports are forbidden on the OT-2 robot environment."
        )

    def test_no_pydantic(self, generated_text: str) -> None:
        assert "pydantic" not in generated_text.lower(), (
            "Generated robot protocol contains 'pydantic'. "
            "Pydantic is not available on the OT-2 robot."
        )

    def test_no_numpy(self, generated_text: str) -> None:
        assert "numpy" not in generated_text.lower(), (
            "Generated robot protocol contains 'numpy'. "
            "NumPy is not reliably available on the OT-2 robot."
        )

    def test_no_pandas(self, generated_text: str) -> None:
        assert "pandas" not in generated_text.lower(), (
            "Generated robot protocol contains 'pandas'. "
            "Pandas is not available on the OT-2 robot."
        )

    def test_no_pil(self, generated_text: str) -> None:
        lower = generated_text.lower()
        assert "from pil" not in lower and "import pil" not in lower, (
            "Generated robot protocol contains PIL/Pillow import. "
            "Pillow is not available on the OT-2 robot."
        )

    def test_no_argparse(self, generated_text: str) -> None:
        assert "argparse" not in generated_text.lower(), (
            "Generated robot protocol contains 'argparse'. "
            "CLI code must not be included in the robot protocol."
        )

    def test_no_yaml_safe_load(self, generated_text: str) -> None:
        assert "safe_load" not in generated_text, (
            "Generated robot protocol contains 'safe_load'. "
            "YAML loading must not appear in the robot protocol."
        )

    def test_no_src_core_imports(self, generated_text: str) -> None:
        assert "from src." not in generated_text, (
            "Generated robot protocol contains project-local 'from src.' import. "
            "Project-local imports are forbidden in the robot protocol."
        )

    def test_no_open_yaml_file(self, generated_text: str) -> None:
        assert "printing_demo.yaml" not in generated_text, (
            "Generated robot protocol references 'printing_demo.yaml'. "
            "The robot protocol must never open a YAML file."
        )


# ── Required pattern tests ────────────────────────────────────────────

class TestRequiredPatterns:
    """The generated robot protocol must contain all of these."""

    def test_has_config_dict(self, generated_text: str) -> None:
        assert "CONFIG =" in generated_text, (
            "Generated robot protocol does not contain 'CONFIG ='. "
            "The embedded CONFIG dict is required."
        )

    def test_has_run_function(self, generated_text: str) -> None:
        assert "def run(" in generated_text, (
            "Generated robot protocol does not define 'def run('. "
            "The OT-2 execution engine requires a run() entry point."
        )

    def test_has_is_simulating_check(self, generated_text: str) -> None:
        assert "protocol.is_simulating()" in generated_text, (
            "Generated robot protocol does not call 'protocol.is_simulating()'. "
            "Simulation guard is required for mock photo handling."
        )

    def test_has_opentrons_import(self, generated_text: str) -> None:
        assert "from opentrons import protocol_api" in generated_text, (
            "Generated robot protocol is missing 'from opentrons import protocol_api'."
        )

    def test_has_metadata(self, generated_text: str) -> None:
        assert "metadata =" in generated_text, (
            "Generated robot protocol is missing 'metadata =' dict."
        )

    def test_config_contains_demo_mode(self, generated_text: str) -> None:
        assert "demo_mode" in generated_text, (
            "Generated robot protocol CONFIG does not contain 'demo_mode' key."
        )

    def test_config_contains_printing(self, generated_text: str) -> None:
        assert "printing" in generated_text, (
            "Generated robot protocol CONFIG does not contain 'printing' key."
        )


# ── Safety check function test ────────────────────────────────────────

class TestSafetyCheckFunction:
    """Validate that _check_generated_script raises on bad input."""

    def test_check_raises_on_yaml_import(self) -> None:
        from src.protocols.printing_demo_protocol import _check_generated_script
        bad_script = "import yaml\nCONFIG = {}\ndef run(protocol):\n    protocol.is_simulating()\n"
        with pytest.raises(RuntimeError, match="import yaml"):
            _check_generated_script(bad_script, Path("fake.py"))

    def test_check_raises_on_missing_run(self) -> None:
        from src.protocols.printing_demo_protocol import _check_generated_script
        bad_script = "CONFIG = {}\nprotocol.is_simulating()\n"
        with pytest.raises(RuntimeError, match="def run\\("):
            _check_generated_script(bad_script, Path("fake.py"))

    def test_check_raises_on_missing_config(self) -> None:
        from src.protocols.printing_demo_protocol import _check_generated_script
        bad_script = "def run(protocol):\n    protocol.is_simulating()\n"
        with pytest.raises(RuntimeError, match="CONFIG ="):
            _check_generated_script(bad_script, Path("fake.py"))

    def test_check_passes_on_clean_script(self, generated_text: str) -> None:
        from src.protocols.printing_demo_protocol import _check_generated_script
        # Should not raise
        _check_generated_script(generated_text, Path("printing_demo_latest.py"))

    def test_check_case_insensitive_pil(self) -> None:
        from src.protocols.printing_demo_protocol import _check_generated_script
        # PIL in different case should still be caught
        bad_script = "from PIL import Image\nCONFIG = {}\ndef run(p):\n    p.is_simulating()\n"
        with pytest.raises(RuntimeError, match="pil"):
            _check_generated_script(bad_script, Path("fake.py"))

    def test_single_tip_reuse_logic_generated(self) -> None:
        """Verify generated code structure for reuse_single_tip_for_demo mode."""
        import yaml
        from src.protocols.printing_demo_protocol import _build_robot_script

        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        # Set single-tip-reuse configuration explicitly
        config_data["tip_strategy"] = {
            "mode": "reuse_single_tip_for_demo",
            "loaded_tip_count": 8,
            "starting_tip": "A1",
            "return_tip_at_end": True,
            "drop_tip_at_end": False,
            "reuse_for_water": True,
            "reuse_for_stock": True,
            "reuse_for_mixing": True,
            "reuse_for_printing": True,
            "allowed_modes": ["reuse_single_tip_for_demo"]
        }

        # Enable debug comments to check for verbose statements
        config_data["debug"]["enabled"] = True
        config_data["debug"]["verbose_protocol_comments"] = True

        script_text = _build_robot_script(config_data)

        # Assert tip strategy configuration is embedded in CONFIG
        assert "'mode': 'reuse_single_tip_for_demo'" in script_text
        assert "'starting_tip': 'A1'" in script_text

        # Helper functions should be present
        assert "def ensure_tip(" in script_text
        assert "def finish_tip(" in script_text

        # Verify end-of-run behavior
        assert "pipette.return_tip()" in script_text

        # Verify debug messages are printed
        assert "reusing same tip for water transfer" in script_text
        assert "reusing same tip for stock transfer" in script_text
        assert "reusing same tip for mixing" in script_text
        assert "reusing same tip for printing" in script_text

        # Double check no forbidden imports
        assert "import yaml" not in script_text.lower()
        assert "pydantic" not in script_text.lower()
