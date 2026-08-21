"""Exact build/simulation artifact provenance for printing requests."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from scripts.build_vial_dilution_print import build_source, simulate
from src.utils.paths import SIMULATION_RECORDS_PATH

from .compiler import apply_workflow_patch
from .canonical import canonical_json_bytes
from .config import REPO_ROOT, load_printing_config
from .schemas import PrintingFamily, ValidationReport
from .validation import validate_four_clover_config, validate_standard_config
from .workflows import ResolvedPrintingRequest, resolve_printing_request


ARTIFACT_DIR = REPO_ROOT / ".test_tmp" / "printing-artifacts"


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildArtifact(ArtifactModel):
    workflow_name: str
    family: PrintingFamily
    design_name: str | None = None
    base_config_reference: str
    request_payload: dict[str, Any]
    resolved_config_snapshot: str
    resolved_config_sha256: str
    protocol_path: str
    sha256: str
    protocol_dry_run: bool
    source_experiment_config_sha256: str | None = None
    source_job_sha256: str | None = None
    source_plan_sha256: str | None = None


class SimulationResult(ArtifactModel):
    status: str
    motion_path_exercised: bool
    artifact: BuildArtifact
    output_tail: str


class PreparedPrintingRequest(ArtifactModel):
    resolved: Any
    config: dict[str, Any]
    validation: ValidationReport

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def _repo_relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT.resolve()))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_printing_request(payload: dict[str, Any]) -> PreparedPrintingRequest:
    resolved = resolve_printing_request(payload)
    config_path = resolved.workflow.default_config
    if config_path is None:
        raise ValueError(f"workflow {resolved.workflow.name!r} has no registered config")
    base = load_printing_config(config_path)
    config = apply_workflow_patch(base, resolved.patch)
    if resolved.workflow.family == PrintingFamily.DESIGN:
        report = validate_four_clover_config(config, workflow_name=resolved.workflow.name)
    else:
        report = validate_standard_config(config, workflow_name=resolved.workflow.name)
    return PreparedPrintingRequest(resolved=resolved, config=config, validation=report)


def build_prepared_artifact(
    prepared: PreparedPrintingRequest,
    *,
    exercise_motion: bool,
    output_dir: Path | None = None,
    source_experiment_config_sha256: str | None = None,
    source_job_sha256: str | None = None,
    source_plan_sha256: str | None = None,
) -> BuildArtifact:
    """Build one exact local artifact; never writes a live/latest protocol alias."""
    if not prepared.validation.valid:
        raise ValueError("printing request failed deterministic validation")
    resolved: ResolvedPrintingRequest = prepared.resolved
    workflow = resolved.workflow
    config = dict(prepared.config)
    run_modes = dict(config.pop("run_modes", {}))
    version = int(config.pop("protocol_version"))
    if version != workflow.builder_version:
        raise ValueError(
            f"config protocol_version {version} does not match registry version "
            f"{workflow.builder_version}"
        )
    config["protocol_version"] = version
    run_modes.update(
        dry_run=not exercise_motion,
        do_dilution=False,
        do_print=True,
    )
    source = build_source(
        workflow.base_protocol.read_text(encoding="utf-8"),
        config,
        run_modes,
    )
    if any(
        value is not None
        for value in (
            source_experiment_config_sha256,
            source_job_sha256,
            source_plan_sha256,
        )
    ):
        provenance = (
            "# Stage 4 immutable provenance\n"
            f"# experiment_config_sha256: {source_experiment_config_sha256 or 'n/a'}\n"
            f"# print_job_sha256: {source_job_sha256 or 'n/a'}\n"
            f"# resolved_plan_sha256: {source_plan_sha256 or 'n/a'}\n"
        )
        source = provenance + source
    data = source.encode("utf-8")
    digest = _sha256_bytes(data)
    directory = (output_dir or ARTIFACT_DIR).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    mode = "motion_sim" if exercise_motion else "plan_only"
    path = directory / f"{workflow.generated_stem}_{mode}_{digest[:12]}.py"
    path.write_bytes(data)
    canonical_config = canonical_json_bytes(prepared.config)
    config_digest = _sha256_bytes(canonical_config)
    snapshot = directory / f"{workflow.generated_stem}_config_{config_digest[:12]}.json"
    snapshot.write_bytes(canonical_config)
    return BuildArtifact(
        workflow_name=workflow.name,
        family=workflow.family,
        design_name=workflow.design_name,
        base_config_reference=_repo_relative(workflow.default_config),
        request_payload=resolved.request.model_dump(mode="json"),
        resolved_config_snapshot=str(snapshot),
        resolved_config_sha256=config_digest,
        protocol_path=str(path),
        sha256=digest,
        protocol_dry_run=not exercise_motion,
        source_experiment_config_sha256=source_experiment_config_sha256,
        source_job_sha256=source_job_sha256,
        source_plan_sha256=source_plan_sha256,
    )


def _record_simulation(artifact: BuildArtifact, output: str) -> None:
    records: dict[str, Any] = {}
    if SIMULATION_RECORDS_PATH.is_file():
        try:
            records = json.loads(SIMULATION_RECORDS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = {}
    records[artifact.sha256] = {
        "path": artifact.protocol_path,
        "workflow_name": artifact.workflow_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "motion_path_exercised": True,
        "result": output[-4000:],
    }
    SIMULATION_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIMULATION_RECORDS_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def simulate_prepared_request(
    prepared: PreparedPrintingRequest,
    *,
    output_dir: Path | None = None,
    record: bool = True,
    source_experiment_config_sha256: str | None = None,
    source_job_sha256: str | None = None,
    source_plan_sha256: str | None = None,
) -> SimulationResult:
    artifact = build_prepared_artifact(
        prepared,
        exercise_motion=True,
        output_dir=output_dir,
        source_experiment_config_sha256=source_experiment_config_sha256,
        source_job_sha256=source_job_sha256,
        source_plan_sha256=source_plan_sha256,
    )
    return simulate_built_artifact(artifact, record=record)


def simulate_built_artifact(
    artifact: BuildArtifact,
    *,
    record: bool = True,
) -> SimulationResult:
    """Simulate the exact already-hashed protocol artifact, rejecting drift."""
    path = Path(artifact.protocol_path)
    if not path.is_file():
        raise FileNotFoundError(f"built protocol artifact is missing: {path}")
    actual_sha256 = _sha256_bytes(path.read_bytes())
    if actual_sha256 != artifact.sha256:
        raise ValueError(
            "built protocol artifact changed after hashing: "
            f"expected {artifact.sha256}, found {actual_sha256}"
        )
    passed, output = simulate(path)
    if not passed:
        return SimulationResult(
            status="FAIL",
            motion_path_exercised=True,
            artifact=artifact,
            output_tail=output[-4000:],
        )
    if record:
        _record_simulation(artifact, output)
    return SimulationResult(
        status="PASS",
        motion_path_exercised=True,
        artifact=artifact,
        output_tail=output[-4000:],
    )
