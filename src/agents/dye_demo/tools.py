"""Reusable NanoDrop workflow tools; shared with the manual Python entry points.

Conversation/session code owns interpretation and edits. These functions reuse
the existing compiler and protocol builder; they do not contact an instrument.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

from src.agents.dye_demo.compiler import CompiledPlan, compile_plan
from src.agents.dye_demo.model import load_config


def prepare_workflow(config: str | Path) -> CompiledPlan:
    """Load the same YAML and compile the same plan as the agent and GUI."""
    spec = load_config(Path(config))
    if spec.get("protocol_version") != 19:
        raise ValueError("NanoDrop entry points use protocol_version: 19; use the existing builders for other families")
    return compile_plan(spec)


def build_protocol(compiled: CompiledPlan, directory: Path) -> Path:
    """Write a standalone v19 protocol and its resolved YAML to a new directory.

    No shared *_latest.py is overwritten. Existing validation errors are reported
    by the compiler rather than embedding an unfinished plan.
    """
    if not compiled.ok or compiled.plan is None:
        raise ValueError("Cannot build this plan: " + "; ".join(compiled.errors))
    if compiled.config.get("protocol_version") != 19:
        raise ValueError("This builder uses NanoDrop protocol version 19")
    from scripts.manual.build_vial_dilution_print import PROTOCOL_VERSIONS, build_source

    template, _ = PROTOCOL_VERSIONS[19]
    source = build_source(template.read_text(encoding="utf-8"), compiled.config,
                          compiled.config.get("run_modes", {}))
    ast.parse(source)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "config.yaml").write_text(yaml.safe_dump(compiled.config, sort_keys=False), encoding="utf-8")
    protocol = directory / "protocol.py"
    protocol.write_text(source, encoding="utf-8")
    return protocol
