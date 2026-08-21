"""CLI routing tests for documented local printing commands."""
from __future__ import annotations

import json

from src.printing.cli import main


def test_cli_lists_workflows(capsys):
    assert main(["list"]) == 0
    workflows = json.loads(capsys.readouterr().out)
    assert {item["family"] for item in workflows} == {"standard", "design"}


def test_cli_validates_default_standard_request(capsys):
    assert main([
        "validate",
        "--family", "standard",
        "--workflow", "complementary_bp_v10a",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_cli_previews_current_four_clover(capsys):
    assert main([
        "preview",
        "--family", "design",
        "--workflow", "four_clover_spacing",
        "--design", "four_clover",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["design_name"] == "four_clover"
    assert preview["clovers"]
