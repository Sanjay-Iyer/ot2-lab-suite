"""Build and locally simulate the trusted standard printing executor.

The executor protocol is never rewritten per experiment. Building means copying
the trusted base file and replacing exactly one delimited block with an approved
:class:`ResolvedExperimentPlanV1`. The resulting artifact is hashed so what was
simulated is provably what would be uploaded.

Nothing in this module contacts a robot. Simulation is local only, and the
terminal state of the workflow is ``READY_FOR_EXECUTION``.
"""
from __future__ import annotations

import hashlib
import json
import os
import pprint
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from ..schemas.experiments import ResolvedExperimentPlanV1


BASE_PROTOCOL = (
    Path(REPO_ROOT) / "src" / "protocols" / "printing" / "01_printing_standard.py"
)
LABWARE_DIR = Path(REPO_ROOT) / "labware"
ARTIFACT_DIR = Path(REPO_ROOT) / ".test_tmp" / "standard-printing-artifacts"

START_SENTINEL = "# >>> PLAN START >>>"
END_SENTINEL = "# <<< PLAN END <<<"


class StandardProtocolBuildError(ValueError):
    """The trusted executor could not be specialized with this plan."""


@dataclass(frozen=True)
class BuiltStandardProtocol:
    """One exact, hashed protocol artifact and the plan it came from."""

    protocol_path: Path
    protocol_sha256: str
    base_protocol_sha256: str
    plan_id: str
    job_id: str
    experiment_id: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_protocol_source(plan: ResolvedExperimentPlanV1) -> str:
    """Return the trusted executor with only its plan block replaced."""
    base_text = BASE_PROTOCOL.read_text(encoding="utf-8")
    try:
        start = base_text.index(START_SENTINEL)
        end = base_text.index(END_SENTINEL)
    except ValueError as exc:
        raise StandardProtocolBuildError(
            "the trusted executor no longer contains its plan sentinels"
        ) from exc
    if end < start:
        raise StandardProtocolBuildError("executor plan sentinels are out of order")

    line_start = base_text.rfind("\n", 0, start) + 1
    line_end = base_text.index("\n", end)

    payload = plan.model_dump(mode="json")
    body = pprint.pformat(payload, indent=2, sort_dicts=False, width=96)
    block = (
        f"{START_SENTINEL} (auto-generated from an approved ResolvedExperimentPlanV1;\n"
        f"# edit the experiment configuration, never this block)\n"
        f"PLAN = {body}\n"
        f"{END_SENTINEL}"
    )
    provenance = (
        "# Immutable provenance for this build\n"
        f"# experiment_id     : {plan.experiment_id}\n"
        f"# job_sha256        : {plan.job_id}\n"
        f"# resolved_plan_sha256: {plan.plan_id}\n"
    )
    return provenance + base_text[:line_start] + block + base_text[line_end:]


def build_standard_protocol(
    plan: ResolvedExperimentPlanV1,
    *,
    output_dir: Path | str | None = None,
) -> BuiltStandardProtocol:
    """Write one hashed executor artifact carrying this resolved plan."""
    source = render_protocol_source(plan)
    data = source.encode("utf-8")
    digest = _sha256(data)
    directory = Path(output_dir) if output_dir else ARTIFACT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"01_printing_standard_{plan.experiment_id}_{digest[:12]}.py"
    path.write_bytes(data)
    return BuiltStandardProtocol(
        protocol_path=path,
        protocol_sha256=digest,
        base_protocol_sha256=_sha256(BASE_PROTOCOL.read_bytes()),
        plan_id=plan.plan_id,
        job_id=plan.job_id,
        experiment_id=plan.experiment_id,
    )


def simulate_standard_protocol(
    protocol_path: Path | str,
    *,
    expected_sha256: str | None = None,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Simulate one built artifact locally and return its run log.

    Returns ``(passed, run_log, text)``. The simulation exercises real motion
    planning, so an unreachable well or an impossible transfer fails here rather
    than on hardware.
    """
    path = Path(protocol_path)
    if not path.is_file():
        raise StandardProtocolBuildError(f"protocol artifact not found: {path}")
    data = path.read_bytes()
    if expected_sha256 is not None and _sha256(data) != expected_sha256:
        raise StandardProtocolBuildError(
            "protocol artifact changed after it was hashed; refusing to simulate"
        )

    import numpy as np

    np.trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    if not os.environ.get("OT_API_CONFIG_DIR"):
        simulator_config = Path(REPO_ROOT) / ".test_tmp" / "opentrons-simulator"
        simulator_config.mkdir(parents=True, exist_ok=True)
        os.environ["OT_API_CONFIG_DIR"] = str(simulator_config)

    from opentrons.simulate import simulate as opentrons_simulate

    with path.open("rb") as handle:
        run_log, _ = opentrons_simulate(
            handle, custom_labware_paths=[str(LABWARE_DIR)]
        )
    text = "\n".join(entry["payload"].get("text", "") for entry in run_log)
    return True, run_log, text


def write_plan_artifact(
    plan: ResolvedExperimentPlanV1, path: Path | str
) -> Path:
    """Persist a resolved plan as indented JSON with a stable canonical hash."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.model_dump(mode="json")
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return target
