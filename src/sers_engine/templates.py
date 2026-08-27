"""Canonical starting patterns for SERS experiments.

A template is not a second config architecture.  Each one is an ordinary
:class:`SERSExperimentV1` document that happens to live in a known place and
carry a name.  Loading one produces experiment state the agent then patches like
any other; from that moment on nothing in the system knows it came from a
template.

The registry is closed on purpose.  The agent picks a template by name from a
fixed set; it never supplies a filesystem path, so there is no route from the
conversation to reading arbitrary YAML off this machine.

Descriptions live here rather than inside the YAML because
:class:`SERSExperimentV1` forbids unknown fields, and polluting the experiment
schema with authoring metadata would be the wrong trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .intent import SERSExperimentV1, intent_as_dict, validate_intent
from .schema import REPO_ROOT, SERSConfigError

TEMPLATE_DIR = REPO_ROOT / "configs" / "templates" / "sers"


@dataclass(frozen=True)
class TemplateSpec:
    """One approved starting pattern."""

    name: str
    filename: str
    summary: str
    when_to_use: str
    shape: str

    @property
    def path(self) -> Path:
        return TEMPLATE_DIR / self.filename


SERS_TEMPLATES: dict[str, TemplateSpec] = {
    "dilution": TemplateSpec(
        name="dilution",
        filename="dilution.template.yaml",
        summary="Prepare one or more dilutions. No printing.",
        when_to_use=(
            "The user only wants conditions prepared in the working plate - "
            "a dilution series, a single dilution, a concentration ladder - "
            "and has not asked for anything to be printed onto paper."
        ),
        shape="dilution [, dilution, ...]",
    ),
    "printing": TemplateSpec(
        name="printing",
        filename="printing.template.yaml",
        summary="Print a stock or prepared liquid onto A1:H12 paper. No dilution.",
        when_to_use=(
            "The user wants an existing liquid deposited onto paper as-is, with "
            "no dilution step - printing a stock, or reprinting something already "
            "prepared by hand."
        ),
        shape="print [, print, ...]",
    ),
    "workflow": TemplateSpec(
        name="workflow",
        filename="workflow.template.yaml",
        summary=(
            "Arbitrary ordered combination of dilution, print and wait, "
            "including drying between layers and overprinting."
        ),
        when_to_use=(
            "The user's experiment mixes preparation and printing, or needs a "
            "wait between layers. This is the right default whenever more than "
            "one kind of step is involved."
        ),
        shape="dilution -> print -> dilution -> print -> wait -> print",
    ),
}


def template_names() -> list[str]:
    """Every approved template name."""
    return list(SERS_TEMPLATES)


def _spec(name: str) -> TemplateSpec:
    key = str(name).strip().lower()
    spec = SERS_TEMPLATES.get(key)
    if spec is None:
        raise SERSConfigError(
            f"unknown template {name!r}; approved templates are "
            f"{sorted(SERS_TEMPLATES)}"
        )
    return spec


def load_sers_template(name: str) -> SERSExperimentV1:
    """Load one approved template and validate it as a real experiment.

    Raises :class:`SERSConfigError` if the template is missing, unreadable, or no
    longer valid against the current schema - a template that has drifted out of
    date must fail loudly rather than half-load.
    """
    spec = _spec(name)
    path = spec.path
    if not path.is_file():
        raise SERSConfigError(f"template {spec.name!r} is missing from disk: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SERSConfigError(f"cannot read template {spec.name!r} ({path}): {exc}") from exc
    if not isinstance(payload, dict):
        raise SERSConfigError(f"template {spec.name!r} must contain a YAML mapping: {path}")
    try:
        return validate_intent(payload)
    except SERSConfigError as exc:
        raise SERSConfigError(
            f"template {spec.name!r} is no longer a valid SERSExperimentV1 ({path}): {exc}"
        ) from exc


def template_payload(
    name: str,
    experiment_name: str | None = None,
    experiment_id: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Return one template as a fresh, JSON-safe experiment mapping.

    The identity fields are overridable so a new experiment does not inherit the
    template's own name and id.
    """
    payload = intent_as_dict(load_sers_template(name))
    if experiment_name:
        payload["experiment_name"] = experiment_name
        payload.setdefault("experiment_id", experiment_name)
    if experiment_id:
        payload["experiment_id"] = experiment_id
    elif experiment_name:
        payload["experiment_id"] = experiment_name
    if description is not None:
        payload["description"] = description
    return payload


def describe_templates() -> list[dict[str, Any]]:
    """Structured catalogue for the agent and for humans."""
    catalogue: list[dict[str, Any]] = []
    for spec in SERS_TEMPLATES.values():
        entry: dict[str, Any] = {
            "name": spec.name,
            "summary": spec.summary,
            "when_to_use": spec.when_to_use,
            "shape": spec.shape,
            "path": str(spec.path.relative_to(REPO_ROOT)).replace("\\", "/"),
        }
        try:
            experiment = load_sers_template(spec.name)
        except SERSConfigError as exc:
            entry["available"] = False
            entry["error"] = str(exc)
        else:
            entry["available"] = True
            entry["deck"] = [
                f"{item.role} ({item.kind}, slot {item.slot})" for item in experiment.deck
            ]
            entry["liquids"] = [item.name for item in experiment.liquids]
            entry["steps"] = [
                f"{item.step_type}:{item.step_id}" for item in experiment.steps
            ]
        catalogue.append(entry)
    return catalogue
