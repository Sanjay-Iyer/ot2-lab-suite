"""Build the frozen Version 11 general-dilution executor.

Mirrors the Version 1 builders exactly: the executor protocol is never rewritten
per experiment. Building means copying the frozen file and replacing exactly one
delimited block with the resolved configuration, so the code that reaches the
robot is byte-identical to the code that was physically validated.

Nothing in this module contacts a robot. Simulation is local only.
"""
from __future__ import annotations

import hashlib
import os
import pprint
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT


BASE_PROTOCOL = (
    Path(REPO_ROOT) / "src" / "protocols" / "printing" / "11_general_dilution.py"
)
LABWARE_DIR = Path(REPO_ROOT) / "labware"
ARTIFACT_DIR = Path(REPO_ROOT) / ".test_tmp" / "v11-dilution-artifacts"
GENERATED_PATH = (
    Path(REPO_ROOT) / "src" / "protocols" / "generated" / "11_general_dilution_latest.py"
)

START_SENTINEL = "# >>> CONFIG START >>>"
END_SENTINEL = "# <<< CONFIG END <<<"

_FLAG_SUBS = {
    "dry_run": (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"), "DEFAULT_DRY_RUN = {}"),
}

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


class V11DilutionBuildError(ValueError):
    """The frozen executor could not be specialized with this configuration."""


#: The Version 1 name, kept so callers can catch either.
DilutionBuildError = V11DilutionBuildError


@dataclass(frozen=True)
class BuiltDilutionProtocol:
    protocol_path: Path
    protocol_sha256: str
    base_protocol_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_protocol_source(
    config: dict[str, Any], *, run_modes: dict[str, Any] | None = None
) -> str:
    """Return the frozen executor with only its config block (and flags) replaced."""
    text = BASE_PROTOCOL.read_text(encoding="utf-8")
    for key, (pattern, template) in (_FLAG_SUBS.items() if run_modes else ()):
        if key in run_modes:
            text = pattern.sub(template.format(bool(run_modes[key])), text)

    try:
        start = text.index(START_SENTINEL)
        end = text.index(END_SENTINEL)
    except ValueError as exc:
        raise V11DilutionBuildError(
            "the frozen V11 dilution executor no longer contains its config sentinels"
        ) from exc
    if end < start:
        raise V11DilutionBuildError("executor config sentinels are out of order")

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.index("\n", end)
    body = pprint.pformat(config, indent=2, sort_dicts=False, width=100)
    block = (
        f"{START_SENTINEL} (auto-generated from YAML; edit the YAML, not this file)\n"
        f"CONFIG = {body}\n"
        f"{END_SENTINEL}"
    )
    return text[:line_start] + block + text[line_end:]


def build_dilution_protocol(
    config: dict[str, Any],
    *,
    run_modes: dict[str, Any] | None = None,
    output_dir: Path | str | None = None,
) -> BuiltDilutionProtocol:
    """Write one hashed executor artifact carrying this configuration."""
    source = render_protocol_source(config, run_modes=run_modes)
    data = source.encode("utf-8")
    digest = _sha256(data)
    directory = Path(output_dir) if output_dir else ARTIFACT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    label = str(config.get("protocol_label", "v11_dilution"))
    path = directory / f"11_general_dilution_{label}_{digest[:12]}.py"
    path.write_bytes(data)
    return BuiltDilutionProtocol(
        protocol_path=path,
        protocol_sha256=digest,
        base_protocol_sha256=_sha256(BASE_PROTOCOL.read_bytes()),
    )


def simulate_dilution_protocol(
    protocol_path: Path | str,
    *,
    expected_sha256: str | None = None,
    python_executable: str | None = None,
) -> tuple[bool, str]:
    """Simulate one built artifact locally and return ``(passed, output)``."""
    path = Path(protocol_path)
    if not path.is_file():
        raise V11DilutionBuildError(f"protocol artifact not found: {path}")
    data = path.read_bytes()
    if expected_sha256 is not None and _sha256(data) != expected_sha256:
        raise V11DilutionBuildError(
            "protocol artifact changed after it was hashed; refusing to simulate"
        )

    simulator_config = Path(REPO_ROOT) / ".test_tmp" / "opentrons-simulator"
    simulator_config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OT_API_CONFIG_DIR"] = str(simulator_config)
    process = subprocess.run(
        [python_executable or sys.executable, "-c", _SHIM, "-L", str(LABWARE_DIR), path.name],
        cwd=str(path.parent),
        capture_output=True,
        text=True,
        env=env,
    )
    output = f"{process.stdout}\n{process.stderr}"
    errors = [line for line in output.splitlines() if _ERROR_RE.search(line)]
    return (process.returncode == 0 and not errors), output


def write_generated_protocol(
    config: dict[str, Any], *, run_modes: dict[str, Any] | None = None
) -> Path:
    """Refresh the uploadable copy at GENERATED_PATH and return its path."""
    source = render_protocol_source(config, run_modes=run_modes)
    GENERATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_PATH.write_text(source, encoding="utf-8")
    return GENERATED_PATH
