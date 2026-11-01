#!/usr/bin/env python3
"""Launch the existing terminal agent or NiceGUI using a small YAML profile."""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def launch_arguments(profile: dict, *, terminal: bool = False) -> tuple[str, list[str]]:
    """Translate configuration into the existing entry points' public arguments."""
    interface = "terminal" if terminal else profile.get("interface", "gui")
    if interface not in {"gui", "terminal"}:
        raise ValueError("interface must be gui or terminal")
    for key in ("simulate", "offline"):
        if not isinstance(profile.get(key), bool):
            raise ValueError(f"{key} must be a YAML boolean")
    workflow = profile.get("workflow_config")
    if not isinstance(workflow, str) or not workflow:
        raise ValueError("workflow_config must name an experiment YAML")
    temperature = profile.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
        raise ValueError("temperature must be a finite number")
    args = ["--config", workflow, "--temperature", str(temperature)]
    for key in ("simulate", "offline"):
        if profile[key]:
            args.append("--" + key)
    for key in ("operator", "session_label"):
        if profile.get(key):
            args += ["--" + key.replace("_", "-"), str(profile[key])]
    if interface == "gui":
        args += ["--host", str(profile.get("host", "127.0.0.1")), "--port", str(profile.get("port", 8080))]
        if profile.get("no_browser", False):
            args.append("--no-browser")
    return interface, args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/home/agent.yaml", help="Launcher YAML, not experiment YAML")
    parser.add_argument("--terminal", action="store_true", help="Use the terminal interface")
    parser.add_argument("--preview", action="store_true", help="Show launch arguments without starting a session")
    args = parser.parse_args(argv)
    try:
        path = Path(args.config)
        path = path if path.is_absolute() else REPO / path
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("launcher YAML must be a mapping")
        interface, forwarded = launch_arguments(profile, terminal=args.terminal)
        if args.preview:
            print(f"{interface}: {forwarded}")
            return 0
        # Model override is process-local. Credentials/provider remain in .env/ADC.
        model = profile.get("model")
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise ValueError("model must be null (existing .env) or a model name")
            os.environ["GEMINI_MODEL"] = model.strip()
        if interface == "gui":
            from scripts.ai_dye_demo_gui import main as launch
        else:
            from scripts.ai_dye_demo import main as launch
        return launch(forwarded)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Launcher error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
