"""Deterministic Stage 2 compiler from scientific jobs to trusted plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, load_printing_config
from .plans import resolve_print_plan
from .schemas.jobs import FourCloverPatternV1, PrintJobV1, WellPatternV1
from .schemas.plans import ResolvedPrintPlanV1
from .references import (
    PrintingReferenceError,
    labware_definition_sha256,
    profile_for_pattern,
)
from .workflows import get_workflow
from src.labware.well_plate_96_templates import load_well_plate_96_template


class PrintJobCompilationError(ValueError):
    """Deterministic job-to-workflow adaptation failed."""


class PrintJobPhysicalValidationError(PrintJobCompilationError):
    """The interpreted job is structurally valid but physically invalid."""


def _resolve_labware_reference(job: PrintJobV1) -> dict[str, Any]:
    reference = job.substrate
    definition_path = REPO_ROOT / "labware" / f"{reference.load_name}.json"
    if not definition_path.is_file():
        raise PrintingReferenceError(
            f"unknown labware reference {reference.load_name!r}"
        )
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    parameters = definition.get("parameters", {})
    actual = {
        "load_name": parameters.get("loadName"),
        "namespace": definition.get("namespace"),
        "version": definition.get("version"),
        "definition_sha256": labware_definition_sha256(definition),
    }
    expected = {
        "load_name": reference.load_name,
        "namespace": reference.namespace,
        "version": reference.version,
        "definition_sha256": reference.definition_sha256,
    }
    if actual != expected:
        raise PrintingReferenceError(
            "labware reference does not match the registered definition: "
            f"expected {expected}, resolved {actual}"
        )

    template_path = Path(reference.template_id)
    if template_path.is_absolute() or ".." in template_path.parts:
        raise PrintingReferenceError(
            "labware template_id must be a registered relative template"
        )
    if len(template_path.parts) != 2 or template_path.parts[0] != "well_plate_96":
        raise PrintingReferenceError(
            "V1 supports well_plate_96/<template> labware template IDs"
        )
    template = load_well_plate_96_template(template_path.parts[1])
    if template.identity.load_name != reference.load_name:
        raise PrintingReferenceError(
            "labware template identity does not match reference load_name"
        )
    return definition


def _trusted_profile(job: PrintJobV1) -> tuple[str, dict[str, Any]]:
    profile = profile_for_pattern(job.pattern.type)
    workflow_name = profile.workflow_name
    workflow = get_workflow(workflow_name)
    assert workflow.default_config is not None
    config = load_printing_config(workflow.default_config)

    configured_material = str(config["source"]["material"])
    if job.deposition.material_id != configured_material:
        raise PrintingReferenceError(
            f"unknown material reference {job.deposition.material_id!r} for the "
            f"trusted {workflow_name!r} source profile; available: {configured_material!r}"
        )
    configured_substrate = config["deck"]["paper"]
    if (
        configured_substrate["load_name"] != job.substrate.load_name
        or configured_substrate.get("namespace") != job.substrate.namespace
        or int(configured_substrate.get("version", 1)) != job.substrate.version
    ):
        raise PrintingReferenceError(
            "job substrate is incompatible with the trusted workflow profile"
        )
    return workflow_name, config


def _standard_request(job: PrintJobV1, pattern: WellPatternV1) -> dict[str, Any]:
    return {
        "family": "standard",
        "workflow_name": profile_for_pattern("well_selection").workflow_name,
        "parameters": {
            "droplet_volume_ul": job.deposition.volume_ul,
            "replicate_columns": list(pattern.columns),
            "layers_by_row": dict(pattern.layers_by_row),
            "rest_minutes": job.ordering_intent.inter_layer_rest_minutes,
        },
    }


def _clover_request(
    job: PrintJobV1,
    pattern: FourCloverPatternV1,
    base_config: dict[str, Any],
) -> dict[str, Any]:
    """Emit only scientific deviations from the trusted v12 profile.

    The Stage 1 clover golden request used an empty patch. Omitting values that
    equal its trusted defaults preserves that request provenance and therefore
    the already-published plan hash without changing physical resolution.
    """
    parameters: dict[str, Any] = {}
    printing = base_config["printing"]
    destination = base_config["destination"]

    if job.deposition.volume_ul != float(printing["droplet_volume_ul"]):
        parameters["droplet_volume_ul"] = job.deposition.volume_ul
    if pattern.layers != int(printing["layers"]):
        parameters["layers"] = pattern.layers
    if job.ordering_intent.mode != base_config["order"]["mode"]:
        parameters["order_mode"] = job.ordering_intent.mode

    geometry = pattern.geometry.model_dump(exclude_none=True, exclude_unset=True)
    if geometry != destination["default_clover_geometry"]:
        parameters["default_geometry"] = geometry

    centers = [
        center.model_dump(exclude_none=True, exclude_unset=True)
        for center in pattern.centers
    ]
    if centers != destination["manual_clover_centers"]:
        parameters["manual_centers"] = centers

    return {
        "family": "design",
        "workflow_name": profile_for_pattern("four_clover").workflow_name,
        "design_name": "four_clover",
        "parameters": parameters,
    }


def _attach_job_provenance(
    plan: ResolvedPrintPlanV1,
    job: PrintJobV1,
    *,
    experiment_config_sha256: str | None = None,
    experiment_config_reference: str | None = None,
) -> ResolvedPrintPlanV1:
    content = plan.model_dump(mode="json", exclude={"plan_id"})
    content["provenance"]["source_job_sha256"] = job.job_id
    content["provenance"]["source_experiment_config_sha256"] = experiment_config_sha256
    content["provenance"]["source_experiment_config_reference"] = experiment_config_reference
    linked = ResolvedPrintPlanV1.from_content(**content)
    if linked.plan_id != plan.plan_id:
        raise RuntimeError("job provenance unexpectedly changed physical plan identity")
    return linked


def printing_request_from_job(job: PrintJobV1 | dict[str, Any]) -> dict[str, Any]:
    """Adapt a validated scientific job to an existing trusted workflow request."""
    validated = PrintJobV1.model_validate(
        job.model_dump(mode="json") if isinstance(job, PrintJobV1) else job
    )
    _resolve_labware_reference(validated)
    workflow_name, base_config = _trusted_profile(validated)

    if workflow_name == profile_for_pattern("well_selection").workflow_name:
        assert isinstance(validated.pattern, WellPatternV1)
        return _standard_request(validated, validated.pattern)
    else:
        assert isinstance(validated.pattern, FourCloverPatternV1)
        return _clover_request(validated, validated.pattern, base_config)


def compile_print_job(
    job: PrintJobV1 | dict[str, Any],
    *,
    experiment_config_sha256: str | None = None,
    experiment_config_reference: str | None = None,
) -> ResolvedPrintPlanV1:
    """Validate and compile one V1 scientific job without generating Python."""
    validated = PrintJobV1.model_validate(
        job.model_dump(mode="json") if isinstance(job, PrintJobV1) else job
    )
    request = printing_request_from_job(validated)

    try:
        plan = resolve_print_plan(request)
    except ValueError as exc:
        if "failed deterministic validation" in str(exc):
            raise PrintJobPhysicalValidationError(str(exc)) from exc
        raise PrintJobCompilationError(str(exc)) from exc
    return _attach_job_provenance(
        plan,
        validated,
        experiment_config_sha256=experiment_config_sha256,
        experiment_config_reference=experiment_config_reference,
    )
