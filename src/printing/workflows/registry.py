"""One registry for build compatibility and agent-visible printing capabilities."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import REPO_ROOT
from ..schemas import (
    CombinedOverlayPatch,
    ComplementaryColumnPatch,
    ComplementaryQuickPatch,
    ComplementaryRowPatch,
    DesignPrintingRequest,
    FourCloverPatch,
    PrintingFamily,
    PrintingRequest,
    StandardPrintingRequest,
    StandardWellGridPatch,
    parse_printing_request,
)


class Lifecycle(str, Enum):
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class PrintingWorkflowSpec:
    name: str
    builder_version: int
    base_protocol: Path
    generated_stem: str
    default_config: Path | None = None
    family: PrintingFamily | None = None
    design_name: str | None = None
    patch_model: type[BaseModel] | None = None
    lifecycle: Lifecycle = Lifecycle.DEPRECATED
    is_default: bool = False
    discoverable: bool = False
    description: str = ""


@dataclass(frozen=True)
class ResolvedPrintingRequest:
    request: PrintingRequest
    workflow: PrintingWorkflowSpec
    patch: BaseModel


_PRINT = REPO_ROOT / "src" / "protocols" / "printing"
_CONFIG = REPO_ROOT / "configs" / "printing"
_WORKFLOW_DEFAULTS = REPO_ROOT / "configs" / "workflows" / "defaults"


def _legacy(
    name: str,
    version: int,
    protocol: str,
    stem: str,
    config: str | None = None,
) -> PrintingWorkflowSpec:
    return PrintingWorkflowSpec(
        name=name,
        builder_version=version,
        base_protocol=_PRINT / protocol,
        generated_stem=stem,
        default_config=(_WORKFLOW_DEFAULTS / config) if config else None,
        lifecycle=Lifecycle.DEPRECATED,
        discoverable=False,
        description="Legacy compatibility entry; buildable by historical version id only.",
    )


_SPECS = (
    _legacy("vial_dilution_print_v1", 1, "01_vial_dilution_paper_print.py", "vial_dilution_print", "vial_dilution_print.yaml"),
    _legacy("vial_dilution_print_v2", 2, "02_vial_dilution_paper_print_p20_dilution.py", "vial_dilution_print_v2"),
    _legacy("vial_dilution_print_v3", 3, "03_vial_dilution_paper_print_v3.py", "vial_dilution_print_v3"),
    _legacy("vial_dilution_print_v4", 4, "04_vial_dilution_paper_print_v4_quicktest.py", "vial_dilution_print_v4"),
    _legacy("vial_dilution_print_v6", 6, "06_vial_dilution_paper_print_v6_p20only.py", "vial_dilution_print_v6"),
    _legacy("vial_dilution_print_v7", 7, "07_paper_print_v7_print_only.py", "vial_dilution_print_v7"),
    _legacy("vial_dilution_print_v8", 8, "08_vial_direct_paper_print_v8.py", "vial_dilution_print_v8"),
    PrintingWorkflowSpec(
        "plate_well_direct_v9", 9, _PRINT / "09_plate_well_direct_paper_print_v9.py",
        "plate_well_direct_print_v9", _CONFIG / "plate_well_direct_print_v9.yaml",
        PrintingFamily.STANDARD, patch_model=StandardWellGridPatch,
        lifecycle=Lifecycle.SUPPORTED, discoverable=True,
        description="Print exact paper wells from one configured plate source.",
    ),
    PrintingWorkflowSpec(
        "complementary_bp_v10a", 10, _PRINT / "10_complementary_direct_paper_print.py",
        "complementary_bp_print_v10a", _CONFIG / "complementary_bp_print_v10a.yaml",
        PrintingFamily.STANDARD, patch_model=ComplementaryColumnPatch,
        lifecycle=Lifecycle.SUPPORTED, is_default=True, discoverable=True,
        description="Print configured BP source on the shared exact-well grid by column layers.",
    ),
    PrintingWorkflowSpec(
        "complementary_dmmp_v10b", 11, _PRINT / "10_complementary_direct_paper_print.py",
        "complementary_dmmp_print_v10b", _CONFIG / "complementary_dmmp_print_v10b.yaml",
        PrintingFamily.STANDARD, patch_model=ComplementaryRowPatch,
        lifecycle=Lifecycle.SUPPORTED, discoverable=True,
        description="Print configured DMMP plate source on the shared exact-well grid by row layers.",
    ),
    PrintingWorkflowSpec(
        "combined_bp_dmmp_v11", 12, _PRINT / "11_combined_bp_dmmp_paper_print.py",
        "combined_bp_dmmp_print_v11", _CONFIG / "combined_bp_dmmp_print_v11.yaml",
        PrintingFamily.STANDARD, patch_model=CombinedOverlayPatch,
        lifecycle=Lifecycle.SUPPORTED, discoverable=True,
        description="Print the configured two-source BP/DMMP overlay sequence.",
    ),
    PrintingWorkflowSpec(
        "complementary_bp_quick_v10c", 13, _PRINT / "10_complementary_direct_paper_print.py",
        "complementary_bp_quick_print_v10c", _CONFIG / "complementary_bp_quick_print_v10c.yaml",
        PrintingFamily.STANDARD, patch_model=ComplementaryQuickPatch,
        lifecycle=Lifecycle.EXPERIMENTAL, discoverable=True,
        description="Initial-plus-extra exact-well BP quick-print experiment.",
    ),
    PrintingWorkflowSpec(
        "complementary_dmmp_spot_v10bv2", 14, _PRINT / "10_complementary_direct_paper_print.py",
        "complementary_dmmp_spot_test_v10bv2", _CONFIG / "complementary_dmmp_spot_test_v10bv2.yaml",
        PrintingFamily.STANDARD, patch_model=ComplementaryQuickPatch,
        lifecycle=Lifecycle.EXPERIMENTAL, discoverable=True,
        description="Configured DMMP exact-well spot-test variant.",
    ),
    PrintingWorkflowSpec(
        "four_clover_manual", 15, _PRINT / "12_four_clover_paper_print.py",
        "four_clover_print_v12", _CONFIG / "four_clover_v12.yaml",
        PrintingFamily.DESIGN, "four_clover", FourCloverPatch,
        Lifecycle.SUPPORTED, False, True,
        "Manual-center four-clover spacing sweep.",
    ),
    PrintingWorkflowSpec(
        "four_clover_air_chase", 16, _PRINT / "12_four_clover_paper_print.py",
        "four_clover_air_chase_v12", _CONFIG / "four_clover_air_chase_v12.yaml",
        PrintingFamily.DESIGN, "four_clover", FourCloverPatch,
        Lifecycle.EXPERIMENTAL, False, True,
        "Single-clover pre-air-chase experiment; committed config is plan-only.",
    ),
    PrintingWorkflowSpec(
        "four_clover_grid", 17, _PRINT / "12_four_clover_paper_print.py",
        "four_clover_grid_v12", _CONFIG / "four_clover_grid_v12.yaml",
        PrintingFamily.DESIGN, "four_clover", FourCloverPatch,
        Lifecycle.SUPPORTED, False, True,
        "Generated grid of continuous-coordinate four-clover designs.",
    ),
    PrintingWorkflowSpec(
        "four_clover_spacing", 18, _PRINT / "12_four_clover_paper_print.py",
        "four_clover_spacing_v13", _CONFIG / "four_clover_spacing_v13.yaml",
        PrintingFamily.DESIGN, "four_clover", FourCloverPatch,
        Lifecycle.SUPPORTED, True, True,
        "Current four-clover spacing sweep at the configured paper location.",
    ),
    PrintingWorkflowSpec(
        "ai_agent_dilution_print_demo", 19,
        _PRINT / "13_ai_agent_dilution_print_demo.py",
        "ai_agent_dilution_print_demo",
        _WORKFLOW_DEFAULTS / "ai_agent_dilution_print_demo.yaml",
        lifecycle=Lifecycle.SUPPORTED, discoverable=False,
        description=(
            "Conversational P20 demo: dilution series in one plate column, then "
            "printed onto paper. Self-validating; driven by scripts/ai_dye_demo.py."
        ),
    ),
)

_BY_NAME = {spec.name: spec for spec in _SPECS}
_BY_VERSION = {spec.builder_version: spec for spec in _SPECS}
if len(_BY_NAME) != len(_SPECS) or len(_BY_VERSION) != len(_SPECS):
    raise RuntimeError("printing workflow names and builder versions must be unique")


def get_workflow(name: str, *, include_hidden: bool = False) -> PrintingWorkflowSpec:
    try:
        spec = _BY_NAME[name]
    except KeyError as exc:
        known = ", ".join(sorted(spec.name for spec in _SPECS if spec.discoverable))
        raise KeyError(f"unknown printing workflow {name!r}; available: {known}") from exc
    if not include_hidden and not spec.discoverable:
        raise KeyError(f"printing workflow {name!r} is a hidden compatibility entry")
    return spec


def list_workflows(
    *,
    family: PrintingFamily | str | None = None,
    design_name: str | None = None,
) -> tuple[PrintingWorkflowSpec, ...]:
    selected_family = PrintingFamily(family) if family is not None else None
    return tuple(
        spec
        for spec in _SPECS
        if spec.discoverable
        and (selected_family is None or spec.family == selected_family)
        and (design_name is None or spec.design_name == design_name)
    )


def builder_protocol_versions() -> dict[int, tuple[Path, str]]:
    """Complete historical mapping consumed by the existing builder."""
    return {
        version: (spec.base_protocol, spec.generated_stem)
        for version, spec in _BY_VERSION.items()
    }


# Versions outside the modern family registry whose protocol validates its own
# YAML: the builder embeds the mapping as CONFIG and the protocol's pre-flight is
# the authority on its shape. v19 is the conversational demo.
_SELF_VALIDATING = {4, 6, 7, 8, 19}


def embed_raw_versions() -> set[int]:
    """Versions whose protocol consumes the resolved YAML mapping directly."""
    modern = {spec.builder_version for spec in _SPECS if spec.family is not None}
    return modern | _SELF_VALIDATING


def api_215_versions() -> set[int]:
    """Versions whose runtime flags must be embedded for the OT-2 API 2.15 path."""
    modern = {spec.builder_version for spec in _SPECS if spec.family is not None}
    return modern | _SELF_VALIDATING | {3}


def imageless_versions() -> set[int]:
    """Versions that do not use the historical before/after camera workflow."""
    modern = {spec.builder_version for spec in _SPECS if spec.family is not None}
    return modern | _SELF_VALIDATING


def no_matrix_versions() -> set[int]:
    """Versions validated by their own simulation rather than the legacy v1 matrix."""
    return imageless_versions()


def resolve_printing_request(payload: dict[str, Any]) -> ResolvedPrintingRequest:
    """Validate the envelope and its nested parameters against registry ownership."""
    request = parse_printing_request(payload)
    spec = get_workflow(request.workflow_name)
    if request.family != spec.family:
        raise ValueError(
            f"workflow {spec.name!r} belongs to family {spec.family.value!r}, "
            f"not {request.family.value!r}"
        )
    if isinstance(request, DesignPrintingRequest):
        if request.design_name != spec.design_name:
            known = sorted({item.design_name for item in list_workflows(family=PrintingFamily.DESIGN) if item.design_name})
            raise ValueError(
                f"unknown design {request.design_name!r} for workflow {spec.name!r}; "
                f"registered designs: {', '.join(known)}"
            )
    elif not isinstance(request, StandardPrintingRequest):
        raise TypeError(f"unsupported request type: {type(request).__name__}")
    if spec.patch_model is None:
        raise ValueError(f"workflow {spec.name!r} has no AI-selectable parameter schema")
    try:
        patch = spec.patch_model.model_validate(request.parameters)
    except ValidationError as exc:
        raise ValueError(f"invalid parameters for workflow {spec.name!r}: {exc}") from exc
    return ResolvedPrintingRequest(request=request, workflow=spec, patch=patch)
