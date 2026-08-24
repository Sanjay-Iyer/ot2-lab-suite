"""Build and locally simulate the Version 11 clover print executor.

The executor protocol is never rewritten per experiment. Building means copying
the frozen file and replacing exactly one delimited block with the resolved
configuration, so the geometry code that reaches the robot is byte-identical to
the code that was reviewed and simulated.

Nothing in this module contacts a robot. Simulation is local only, through
``opentrons.simulate`` in a subprocess, which runs the executor's own pre-flight
and real motion planning: an unreachable position or an impossible transfer
fails here rather than on hardware.
"""
from __future__ import annotations

import hashlib
import json
import os
import pprint
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clover_loader import CloverConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]

BASE_PROTOCOL = REPO_ROOT / "src" / "protocols" / "printing" / "11_clover_print.py"
GENERATED_PATH = (
    REPO_ROOT / "src" / "protocols" / "generated" / "11_clover_print_latest.py"
)
LABWARE_DIR = REPO_ROOT / "labware"
ARTIFACT_DIR = REPO_ROOT / ".test_tmp" / "v11-clover-artifacts"

START_SENTINEL = "# >>> CONFIG START >>>"
END_SENTINEL = "# <<< CONFIG END <<<"

_FLAG_SUBS = {
    "dry_run": (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"), "DEFAULT_DRY_RUN = {}"),
    "do_print": (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"), "DEFAULT_DO_PRINT = {}"),
}

# numpy 2 removed np.trapz, which older opentrons still calls; the shim keeps the
# simulator importable without touching the installed package.
_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)
_ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|RuntimeError|LabwareNotFoundError|"
    r"ProtocolCommandFailedError|InvalidProtocolData|FileNotFoundError|"
    r"KeyError|AttributeError",
    re.IGNORECASE,
)


class CloverProtocolBuildError(CloverConfigError):
    """The executor could not be specialized with this configuration."""


@dataclass(frozen=True)
class BuiltCloverProtocol:
    """One exact, hashed protocol artifact and the config it came from."""

    protocol_path: Path
    latest_path: Path | None
    protocol_sha256: str
    base_protocol_sha256: str
    config_sha256: str
    label: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_sha256(config: dict[str, Any]) -> str:
    """Stable content hash of a resolved configuration."""
    return _sha256(json.dumps(config, sort_keys=True, default=str).encode("utf-8"))


def render_protocol_source(
    config: dict[str, Any], *, run_modes: dict[str, Any] | None = None
) -> str:
    """Return the executor with only its CONFIG block (and flags) replaced."""
    if not isinstance(config, dict):
        raise CloverProtocolBuildError("config must be a mapping")
    if not BASE_PROTOCOL.is_file():
        raise CloverProtocolBuildError(f"executor not found: {BASE_PROTOCOL}")
    text = BASE_PROTOCOL.read_text(encoding="utf-8")

    if run_modes:
        for key, (pattern, template) in _FLAG_SUBS.items():
            if key in run_modes:
                text = pattern.sub(template.format(bool(run_modes[key])), text)

    try:
        start = text.index(START_SENTINEL)
        end = text.index(END_SENTINEL)
    except ValueError as exc:
        raise CloverProtocolBuildError(
            "the clover executor no longer contains its CONFIG sentinels"
        ) from exc
    if end < start:
        raise CloverProtocolBuildError("executor CONFIG sentinels are out of order")

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.index("\n", end)
    body = pprint.pformat(config, indent=2, sort_dicts=False, width=100)
    block = (
        f"{START_SENTINEL} (auto-generated from YAML; edit the YAML, not this file)\n"
        f"CONFIG = {body}\n"
        f"{END_SENTINEL}"
    )
    return text[:line_start] + block + text[line_end:]


def build_clover_protocol(
    config: dict[str, Any],
    *,
    run_modes: dict[str, Any] | None = None,
    output_dir: Path | str | None = None,
    label: str | None = None,
    write_latest: bool = False,
) -> BuiltCloverProtocol:
    """Write one hashed executor artifact carrying this resolved configuration."""
    modes = dict(run_modes or {})
    modes.setdefault("dry_run", False)
    modes.setdefault("do_print", True)
    source = render_protocol_source(config, run_modes=modes)

    digest_of_config = config_sha256(config)
    name = str(label or config.get("protocol_label") or "11_clover_print")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    provenance = (
        "# Immutable provenance for this build\n"
        f"# workflow            : v11 clover print\n"
        f"# protocol_label      : {name}\n"
        f"# machine_profile     : {config.get('machine_profile')}\n"
        f"# resolved_config_sha : {digest_of_config}\n"
        f"# run_modes           : {modes}\n"
    )
    data = (provenance + source).encode("utf-8")
    digest = _sha256(data)

    directory = Path(output_dir) if output_dir else ARTIFACT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"11_clover_print_{safe}_{digest[:12]}.py"
    path.write_bytes(data)

    latest = None
    if write_latest:
        GENERATED_PATH.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_PATH.write_bytes(data)
        latest = GENERATED_PATH

    return BuiltCloverProtocol(
        protocol_path=path,
        latest_path=latest,
        protocol_sha256=digest,
        base_protocol_sha256=_sha256(BASE_PROTOCOL.read_bytes()),
        config_sha256=digest_of_config,
        label=name,
    )


def simulate_clover_protocol(
    protocol_path: Path | str,
    *,
    expected_sha256: str | None = None,
    python_executable: str | None = None,
) -> tuple[bool, str]:
    """Simulate one built artifact locally and return ``(passed, output)``."""
    path = Path(protocol_path)
    if not path.is_file():
        raise CloverProtocolBuildError(f"protocol artifact not found: {path}")
    data = path.read_bytes()
    if expected_sha256 is not None and _sha256(data) != expected_sha256:
        raise CloverProtocolBuildError(
            "protocol artifact changed after it was hashed; refusing to simulate"
        )

    simulator_config = REPO_ROOT / ".test_tmp" / "opentrons-simulator"
    simulator_config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OT_API_CONFIG_DIR"] = str(simulator_config)
    process = subprocess.run(
        [
            python_executable or sys.executable,
            "-c",
            _SHIM,
            "-L",
            str(LABWARE_DIR),
            path.name,
        ],
        cwd=str(path.parent),
        capture_output=True,
        text=True,
        env=env,
    )
    output = f"{process.stdout}\n{process.stderr}"
    errors = [line for line in output.splitlines() if _ERROR_RE.search(line)]
    return (process.returncode == 0 and not errors), output


def build_and_simulate(
    config: dict[str, Any],
    *,
    run_modes: dict[str, Any] | None = None,
    output_dir: Path | str | None = None,
    label: str | None = None,
    write_latest: bool = False,
    python_executable: str | None = None,
) -> tuple[BuiltCloverProtocol, bool, str]:
    """Build one artifact and immediately simulate it. The normal entry point."""
    built = build_clover_protocol(
        config,
        run_modes=run_modes,
        output_dir=output_dir,
        label=label,
        write_latest=write_latest,
    )
    passed, output = simulate_clover_protocol(
        built.protocol_path,
        expected_sha256=built.protocol_sha256,
        python_executable=python_executable,
    )
    return built, passed, output
