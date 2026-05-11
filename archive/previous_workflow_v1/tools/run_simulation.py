#!/usr/bin/env python3
"""
Simulation Runner for the Opentrons Dilution Workflow
=====================================================

Loads a YAML config, sets ``OT_CONFIG_PATH`` for the protocol, runs the
protocol through ``opentrons.simulate``, and writes a structured log +
raw transcript.

Usage
-----
    python tools/run_simulation.py configs/example_experiment.yaml
    python tools/run_simulation.py configs/smoketest_quick.yaml \\
        --protocol protocols/smoketest_protocol.py
    python tools/run_simulation.py --dry-validate configs/my_config.yaml

Exit codes
----------
    0  Success
    1  Config invalid (YAML parse error or validation failure)
    2  Simulator failed (protocol raised an exception)
    3  I/O failure (can't read config or write output files)
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import pathlib
import sys
import textwrap
from typing import Any, Dict, List

import yaml

# =====================================================================
#  Constants
# =====================================================================

DEFAULT_PROTOCOL = "protocols/dilution_protocol.py"
EXIT_SUCCESS = 0
EXIT_CONFIG_INVALID = 1
EXIT_SIMULATOR_FAILED = 2
EXIT_IO_FAILURE = 3

logger = logging.getLogger("run_simulation")


# =====================================================================
#  Config loading (lightweight — protocol does its own full validation)
# =====================================================================

def load_config(config_path: pathlib.Path) -> Dict[str, Any]:
    """
    Reads a YAML configuration file and converts it into a Python dictionary.
    
    Args:
        config_path: The absolute or relative path to the .yaml file.
        
    Returns:
        A dictionary containing the experiment parameters.
        
    Raises:
        FileNotFoundError: If the config path doesn't exist.
        ValueError: If the YAML file is empty or not a mapping.
    """
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    if not isinstance(cfg, dict):
        raise ValueError(
            f"Config root must be a YAML mapping, got {type(cfg).__name__}."
        )
    return cfg


def basic_validate(cfg: Dict[str, Any]) -> List[str]:
    """Run lightweight structural checks.

    The protocol itself runs full semantic validation (labware wells,
    volumes, etc.) at runtime.  This catches obvious problems early so
    the user gets fast feedback with ``--dry-validate``.
    """
    errors: List[str] = []

    if "experiment" not in cfg:
        errors.append("Missing top-level key: 'experiment'.")
    else:
        if "project" not in cfg["experiment"]:
            errors.append("Missing 'experiment.project'.")
    if "labware" not in cfg:
        errors.append("Missing top-level key: 'labware'.")
    if "pipette" not in cfg:
        errors.append("Missing top-level key: 'pipette'.")
    if "nanoparticle_stock" not in cfg:
        errors.append("Missing top-level key: 'nanoparticle_stock'.")
    if "requested_dilutions" not in cfg:
        errors.append("Missing top-level key: 'requested_dilutions'.")

    # Check that labware entries have load_name
    for slot, info in cfg.get("labware", {}).items():
        if not isinstance(info, dict) or "load_name" not in info:
            errors.append(
                f"labware.{slot}: must be a mapping with 'load_name'."
            )

    # Check pipette has required fields
    pip = cfg.get("pipette", {})
    for field in ("model", "mount", "tip_rack_slot"):
        if field not in pip:
            errors.append(f"pipette.{field}: missing required field.")

    return errors


# =====================================================================
#  Simulation
# =====================================================================

def run_simulation(
    protocol_path: pathlib.Path,
    config_path: pathlib.Path,
) -> tuple:
    """
    Core execution logic for simulating a protocol locally.
    
    This function bridges the gap between your local YAML configuration and 
    the robot's JSON requirements. It:
    1. Loads the YAML.
    2. Generates 'protocols/config.json'.
    3. Triggers 'opentrons.simulate'.
    
    Args:
        protocol_path: Path to the .py protocol file.
        config_path: Path to the source .yaml configuration.
        
    Returns:
        A tuple of (runlog, transcript) representing the simulated actions.
    """
    import json
    
    # Import here so --dry-validate doesn't pay the opentrons import cost
    from opentrons.simulate import simulate, format_runlog

    # 1. Load YAML and convert to JSON
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    
    # Save to the standard location for the robot-ready workflow
    json_output_path = pathlib.Path("protocols/config.json")
    with open(json_output_path, 'w', encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    # 2. Run simulation
    try:
        # Set environment so the protocol can find the JSON config
        os.environ["OT_CONFIG_PATH"] = str(json_output_path.resolve())

        with open(protocol_path, "r", encoding="utf-8") as fh:
            runlog, _bundle = simulate(fh, str(protocol_path))

        transcript = format_runlog(runlog)
        return runlog, transcript
    except Exception:
        raise


# =====================================================================
#  Log writing
# =====================================================================

def write_structured_log(
    runlog: list,
    log_path: pathlib.Path,
) -> None:
    """Write a structured log from the simulator's runlog."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w", encoding="utf-8") as fh:
        for entry in runlog:
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            level = "INFO"

            # entry is a dict with 'payload' containing 'text'
            payload = entry.get("payload", {})
            text = payload.get("text", "")

            if not text:
                continue

            fh.write(f"{ts} {level:<5} {text}\n")


def write_transcript(transcript: str, transcript_path: pathlib.Path) -> None:
    """Write the raw simulator transcript to disk."""
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with open(transcript_path, "w", encoding="utf-8") as fh:
        fh.write(transcript)


def resolve_output_paths(cfg: Dict[str, Any]) -> tuple:
    """Extract output paths from config, with sensible fallbacks.
    Appends a HHMM timestamp to ensure unique filenames per run.
    """
    outputs = cfg.get("outputs", {})
    log_file = outputs.get("log_file")
    transcript_file = outputs.get("transcript")

    now = datetime.datetime.now().strftime("%H%M")

    # Fallback: derive from experiment name + date + project
    if not log_file or not transcript_file:
        exp = cfg.get("experiment", {})
        name = exp.get("name", "run")
        project = exp.get("project", "default")
        date = exp.get("date", datetime.date.today().isoformat())
        date_compact = date.replace("-", "")
        stem = f"{name}_{date}"

        if not log_file:
            log_file = f"logs/{date_compact}/{project}/{stem}.log"
        if not transcript_file:
            transcript_file = f"outputs/{date_compact}/{project}/{stem}_transcript.txt"

    # Append timestamp to the filename (before the extension)
    lp = pathlib.Path(log_file)
    tp = pathlib.Path(transcript_file)

    log_file = lp.with_name(f"{lp.stem}_{now}{lp.suffix}")
    transcript_file = tp.with_name(f"{tp.stem}_{now}{tp.suffix}")

    return log_file, transcript_file


# =====================================================================
#  CLI
# =====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run an Opentrons protocol through the simulator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Exit codes:
              0  Success
              1  Config invalid
              2  Simulator failed
              3  I/O failure

            Examples:
              python tools/run_simulation.py configs/example_experiment.yaml
              python tools/run_simulation.py --dry-validate configs/my_config.yaml
              python tools/run_simulation.py configs/smoketest_quick.yaml \\
                  --protocol protocols/smoketest_protocol.py
        """),
    )

    p.add_argument(
        "config",
        type=str,
        help="Path to the YAML config file.",
    )
    p.add_argument(
        "--protocol",
        type=str,
        default=DEFAULT_PROTOCOL,
        help=f"Path to the protocol .py file (default: {DEFAULT_PROTOCOL}).",
    )
    p.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python log level (default: INFO).",
    )
    p.add_argument(
        "--no-transcript",
        action="store_true",
        help="Skip writing the raw transcript file.",
    )
    p.add_argument(
        "--dry-validate",
        action="store_true",
        help="Validate the config and exit without running the simulator.",
    )

    return p


# =====================================================================
#  Main
# =====================================================================

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # ── Set up logging ───────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    config_path = pathlib.Path(args.config)
    protocol_path = pathlib.Path(args.protocol)

    # ── Load config ──────────────────────────────────────────────────
    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        logger.error("Config load failed: %s", exc)
        return EXIT_CONFIG_INVALID
    except OSError as exc:
        logger.error("I/O error reading config: %s", exc)
        return EXIT_IO_FAILURE

    logger.info("Config loaded: %s", config_path)

    # ── Basic validation ─────────────────────────────────────────────
    errors = basic_validate(cfg)
    if errors:
        logger.error("Config validation failed:")
        for e in errors:
            logger.error("  • %s", e)
        return EXIT_CONFIG_INVALID

    logger.info("Basic config validation passed.")

    # ── Dry-validate mode — stop here ────────────────────────────────
    if args.dry_validate:
        logger.info("Dry validation complete.  No simulation run.")
        return EXIT_SUCCESS

    # ── Check protocol file exists ───────────────────────────────────
    if not protocol_path.is_file():
        logger.error("Protocol file not found: %s", protocol_path)
        return EXIT_SIMULATOR_FAILED

    # ── Resolve output paths ─────────────────────────────────────────
    try:
        log_path, transcript_path = resolve_output_paths(cfg)
    except Exception as exc:
        logger.error("Could not resolve output paths: %s", exc)
        return EXIT_IO_FAILURE

    # ── Run simulation ───────────────────────────────────────────────
    logger.info("Running simulation: %s with %s", protocol_path, config_path)
    try:
        runlog, transcript = run_simulation(protocol_path, config_path)
    except Exception as exc:
        logger.error("Simulation failed: %s", exc, exc_info=True)
        return EXIT_SIMULATOR_FAILED

    logger.info("Simulation complete.  %d runlog entries.", len(runlog))

    # ── Write outputs ────────────────────────────────────────────────
    try:
        write_structured_log(runlog, log_path)
        logger.info("Structured log written: %s", log_path)

        if not args.no_transcript:
            write_transcript(transcript, transcript_path)
            logger.info("Transcript written: %s", transcript_path)
        else:
            logger.info("Transcript skipped (--no-transcript).")

    except OSError as exc:
        logger.error("I/O error writing outputs: %s", exc)
        return EXIT_IO_FAILURE

    # ── Print summary ────────────────────────────────────────────────
    # Extract tagged comments from runlog for a quick summary
    tags_seen: Dict[str, int] = {}
    for entry in runlog:
        text = entry.get("payload", {}).get("text", "")
        if text.startswith("["):
            bracket_end = text.find("]")
            if bracket_end > 0:
                tag = text[1:bracket_end]
                tags_seen[tag] = tags_seen.get(tag, 0) + 1

    if tags_seen:
        tag_summary = ", ".join(f"{t}:{n}" for t, n in tags_seen.items())
        logger.info("Tags: %s", tag_summary)

    logger.info("Done.  Log: %s  |  Transcript: %s", log_path, transcript_path)
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
