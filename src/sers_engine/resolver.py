"""SERSExperimentV1 -> ResolvedWorkflowV1.

Every calculation the robot depends on happens here, in ordinary deterministic
Python: dilution arithmetic, P20 chunking, target expansion, liquid accounting,
and the calibrated geometry pulled from the laboratory machine profile.

The language model chooses what the experiment *is*.  This module decides what
the robot actually *does*, and the two are deliberately kept apart.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .dilution import plan_transfer_chunks
from .intent import (
    DilutionStep,
    LiquidDeclaration,
    PrintStep,
    SERSExperimentV1,
    WaitStep,
    intent_as_dict,
    parse_location,
    validate_intent,
)
from .machine import MachineProfile, ProfileLabware, load_machine_profile
from .recorder import record_operations
from .schema import ExperimentConfig, SERSConfigError, validate_experiment_config
from .targets import TargetSpecError, resolve_targets

RESOLVER_VERSION = "sers-resolved-workflow/v1"

# Volumes are commanded to 0.1 uL; finer than that is below what a P20 can
# meaningfully deliver, and rounding here keeps the plan reproducible.
VOLUME_RESOLUTION_UL = 0.1
# A dilution whose achievable ratio drifts further than this from the request is
# reported rather than silently accepted.
FACTOR_TOLERANCE = 0.01

# Rough motion model for the duration estimate, in seconds.
_TIP_PICKUP_S = 5.0
_TIP_DROP_S = 4.0
_MOVE_S = 3.5
_MIX_OVERHEAD_S = 1.0


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _round(volume: float) -> float:
    return round(round(volume / VOLUME_RESOLUTION_UL) * VOLUME_RESOLUTION_UL, 6)


class ResolvedDilution(_Out):
    kind: Literal["dilution"] = "dilution"
    step_id: str
    label: str
    source_liquid: str
    source_location: str
    diluent_liquid: str
    diluent_location: str
    destination: str
    dilution_factor_requested: float | None = None
    dilution_factor_achieved: float
    final_volume_ul: float
    stock_volume_ul: float
    diluent_volume_ul: float
    stock_chunks_ul: list[float]
    diluent_chunks_ul: list[float]
    mix_cycles: int
    mix_volume_ul: float
    tips: int
    estimated_duration_s: float


class ResolvedPrint(_Out):
    kind: Literal["print"] = "print"
    step_id: str
    label: str
    source_liquid: str
    source_location: str
    paper: str
    paper_slot: int
    targets: list[str]
    drop_volume_ul: float
    drops_per_target: int
    total_deposits: int
    printed_volume_ul: float
    dispense_height_mm: float
    tip_strategy: str
    tips: int
    estimated_duration_s: float


class ResolvedWait(_Out):
    kind: Literal["wait"] = "wait"
    step_id: str
    label: str
    duration_s: float
    reason: str | None = None
    estimated_duration_s: float


ResolvedStep = Union[ResolvedDilution, ResolvedPrint, ResolvedWait]


class LiquidRequirement(_Out):
    liquid: str
    location: str
    loaded_volume_ul: float
    consumed_ul: float
    remaining_ul: float
    reserve_ul: float


class ResolvedTotals(_Out):
    dilution_count: int
    print_count: int
    wait_count: int
    deposits: int
    printed_volume_ul: float
    tips_required: int
    hold_time_s: float
    estimated_duration_s: float
    liquid_requirements: list[LiquidRequirement]
    final_well_volumes: dict[str, float]


class ResolvedWorkflowV1(_Out):
    """Every physical operation, in exact order, with nothing left to interpret."""

    resolver_version: Literal[RESOLVER_VERSION] = RESOLVER_VERSION
    experiment_id: str
    experiment_name: str
    description: str | None = None
    machine_profile_id: str
    machine_profile_path: str | None = None
    deck: dict[str, str]
    steps: list[ResolvedStep]
    totals: ResolvedTotals
    execution_config: dict[str, Any]
    operations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    config_hash: str
    resolved_hash: str

    def as_experiment_config(self) -> ExperimentConfig:
        """The proven, fully validated execution contract."""
        return validate_experiment_config(self.execution_config)


def _hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class _Resolution:
    """Working state for one resolve pass."""

    def __init__(self, experiment: SERSExperimentV1, profile: MachineProfile) -> None:
        self.experiment = experiment
        self.profile = profile
        self.warnings: list[str] = []
        # (role, well) -> running volume, plus what the operator must load.
        self.volumes: dict[tuple[str, str], float] = {}
        self.consumed: dict[tuple[str, str], float] = {}
        self.loaded: dict[tuple[str, str], LiquidDeclaration] = {}
        self.produced: dict[str, tuple[str, str, str]] = {}  # step_id -> role, well, label
        for liquid in experiment.liquids:
            key = (liquid.labware, liquid.well)
            self.loaded[key] = liquid
            self.volumes[key] = liquid.loaded_volume_ul

    # ---- geometry ---------------------------------------------------------
    def labware_of(self, role: str) -> ProfileLabware:
        kind = self.experiment.deck_by_role[role].kind
        return self.profile.labware_for(kind)

    def aspirate_height(self, role: str) -> float:
        spec = self.labware_of(role)
        if spec.aspirate_reference != "bottom" or spec.aspirate_height_mm is None:
            raise SERSConfigError(
                f"machine profile has no bottom-referenced aspirate height for "
                f"{spec.load_name!r}; this needs human hardware confirmation"
            )
        return float(spec.aspirate_height_mm)

    # ---- liquid references ------------------------------------------------
    def locate(self, reference: str, label: str) -> tuple[str, str, str]:
        """Resolve a source reference to (role, well, human label)."""
        liquid = self.experiment.liquid_by_name.get(reference)
        if liquid is not None:
            return liquid.labware, liquid.well, liquid.name
        if reference in self.produced:
            return self.produced[reference]
        try:
            role, well = parse_location(reference)
        except ValueError as exc:
            raise SERSConfigError(f"{label}: {exc}") from exc
        return role, well, reference

    def source_block(self, role: str, well: str, human: str) -> dict[str, Any]:
        """Build a LiquidSource mapping with profile-owned aspiration geometry."""
        block: dict[str, Any] = {
            "labware": role,
            "well": well,
            "bottom_offset_mm": self.aspirate_height(role),
            "material": human,
        }
        declaration = self.loaded.get((role, well))
        if declaration is not None:
            block["loaded_volume_ul"] = declaration.loaded_volume_ul
            block["minimum_remaining_volume_ul"] = declaration.minimum_remaining_volume_ul
        return block

    def draw(self, role: str, well: str, amount: float) -> None:
        key = (role, well)
        self.volumes[key] = self.volumes.get(key, 0.0) - amount
        self.consumed[key] = self.consumed.get(key, 0.0) + amount


def _resolve_dilution_volumes(
    step: DilutionStep, profile: MachineProfile, warnings: list[str]
) -> tuple[float, float, float, float | None]:
    """Return (stock_ul, diluent_ul, final_ul, requested_factor).

    C1V1 = C2V2 for a factor D and final volume V is simply stock = V / D and
    diluent = V - stock.  All of it happens here so the language model never
    performs the arithmetic that decides a concentration.
    """
    minimum = profile.machine.pipette.minimum_volume_ul

    if step.specified_by_target:
        factor = float(step.dilution_factor)
        final = float(step.final_volume_ul)
        stock = _round(final / factor)
        diluent = _round(final - stock)
        if stock < minimum:
            needed = minimum * factor
            raise SERSConfigError(
                f"dilution {step.step_id}: a {factor:g}x dilution of {final:g} uL needs "
                f"{final / factor:.2f} uL of stock, below the P20 minimum of {minimum:g} uL. "
                f"Raise the final volume to at least {needed:.0f} uL, or prepare an "
                "intermediate dilution and dilute from that."
            )
        achieved = (stock + diluent) / stock if stock else float("inf")
        if abs(achieved - factor) / factor > FACTOR_TOLERANCE:
            warnings.append(
                f"dilution {step.step_id}: {factor:g}x requested, but P20 volume "
                f"resolution gives {achieved:.2f}x "
                f"({stock:g} uL stock + {diluent:g} uL diluent)"
            )
        return stock, diluent, _round(stock + diluent), factor

    stock = _round(float(step.stock_volume_ul))
    diluent = _round(float(step.diluent_volume_ul))
    if stock < minimum:
        raise SERSConfigError(
            f"dilution {step.step_id}: {stock:g} uL of stock is below the P20 "
            f"minimum of {minimum:g} uL"
        )
    if 0 < diluent < minimum:
        raise SERSConfigError(
            f"dilution {step.step_id}: {diluent:g} uL of diluent is below the P20 "
            f"minimum of {minimum:g} uL; use 0 for an undiluted condition"
        )
    return stock, diluent, _round(stock + diluent), None


def _transfer_seconds(volume: float, chunks: list[float], flow: float) -> float:
    """Aspirate + dispense time for one chunked transfer, plus travel."""
    return sum((chunk / flow) * 2 + _MOVE_S for chunk in chunks) if chunks else 0.0


def resolve_experiment(
    experiment: SERSExperimentV1 | dict[str, Any],
    profile: MachineProfile | None = None,
) -> ResolvedWorkflowV1:
    """Turn scientific intent into an exact, executable workflow."""
    intent = experiment if isinstance(experiment, SERSExperimentV1) else validate_intent(experiment)
    profile = profile or load_machine_profile(intent.machine_profile)
    state = _Resolution(intent, profile)
    pipette = profile.machine.pipette
    release = profile.machine.print_release

    # ---- deck -------------------------------------------------------------
    deck_labware: dict[str, Any] = {}
    tip_racks: dict[str, Any] = {}
    deck_summary: dict[str, str] = {}
    for assignment in intent.deck:
        approved = profile.labware_for(assignment.kind)
        spec: dict[str, Any] = {
            "slot": assignment.slot,
            "kind": assignment.kind,
            "load_name": approved.load_name,
            "namespace": approved.namespace,
            "version": approved.version,
            "definition_path": approved.definition_path,
        }
        if assignment.kind == "plate" and approved.safe_max_volume_ul is not None:
            spec["safe_max_volume_ul"] = approved.safe_max_volume_ul
        if assignment.kind == "tiprack":
            tip_racks[assignment.role] = spec
        else:
            deck_labware[assignment.role] = spec
        deck_summary[f"slot {assignment.slot}"] = f"{assignment.role} ({approved.load_name})"

    dilutions: list[dict[str, Any]] = []
    print_layers: list[dict[str, Any]] = []
    waits: list[dict[str, Any]] = []
    workflow: list[dict[str, str]] = []
    resolved_steps: list[ResolvedStep] = []

    park_role = next(
        (item.role for item in intent.deck if item.kind == "plate"),
        next((item.role for item in intent.deck if item.kind == "vial_rack"), None),
    )

    # ---- steps ------------------------------------------------------------
    for step in intent.steps:
        if isinstance(step, DilutionStep):
            resolved_steps.append(
                _resolve_one_dilution(step, state, profile, dilutions, workflow)
            )
        elif isinstance(step, PrintStep):
            resolved_steps.append(
                _resolve_one_print(step, state, profile, print_layers, workflow)
            )
        elif isinstance(step, WaitStep):
            hold: dict[str, Any] = {
                "wait_id": step.step_id,
                "duration_s": step.duration_s,
                "reason": step.reason,
            }
            if park_role is not None:
                hold["park_labware"] = park_role
                hold["park_well"] = "A1"
                hold["park_height_mm"] = 50.0
            waits.append(hold)
            workflow.append({"kind": "wait", "ref": step.step_id})
            resolved_steps.append(
                ResolvedWait(
                    step_id=step.step_id,
                    label=step.label or (step.reason or "hold"),
                    duration_s=step.duration_s,
                    reason=step.reason,
                    estimated_duration_s=step.duration_s + _TIP_DROP_S,
                )
            )

    # ---- the proven execution contract ------------------------------------
    execution_payload: dict[str, Any] = {
        "experiment_name": intent.experiment_name,
        "description": intent.description,
        "deck_layout": {"labware": deck_labware, "tip_racks": tip_racks},
        "pipette": {
            "name": pipette.name,
            "mount": pipette.mount,
            "tip_rack_roles": list(tip_racks),
            "minimum_volume_ul": pipette.minimum_volume_ul,
            "maximum_volume_ul": pipette.maximum_volume_ul,
            "max_transfer_volume_ul": pipette.max_transfer_volume_ul,
            "transfer_air_gap_ul": release.trailing_air_gap_ul,
            "air_gap_height_mm": release.air_gap_height_mm,
            "aspirate_flow_rate_ul_s": pipette.flow_rates.aspirate_ul_s,
            "dispense_flow_rate_ul_s": pipette.flow_rates.dispense_ul_s,
        },
        "tips": {"start_tip": intent.tips.start_tip, "return_tips": intent.tips.return_tips},
        "dilutions": dilutions,
        "print_layers": print_layers,
        "waits": waits,
        "workflow": workflow,
        "safety": {
            "live_execution_allowed": False,
            "hardware_profile_verified": False,
            "required_laptop_role": "real-robot",
        },
    }
    config = validate_experiment_config(execution_payload)
    normalized = config.model_dump(mode="json")
    # The flat operation list is produced by the same engine that simulation and
    # the robot run, so the uploaded protocol never re-derives a volume.
    operations = record_operations(config)

    # ---- totals -----------------------------------------------------------
    requirements = [
        LiquidRequirement(
            liquid=declaration.name,
            location=f"{role}:{well}",
            loaded_volume_ul=declaration.loaded_volume_ul,
            consumed_ul=round(state.consumed.get((role, well), 0.0), 3),
            remaining_ul=round(state.volumes.get((role, well), 0.0), 3),
            reserve_ul=declaration.minimum_remaining_volume_ul,
        )
        for (role, well), declaration in state.loaded.items()
    ]
    hold_time = sum(item.duration_s for item in resolved_steps if isinstance(item, ResolvedWait))
    deposits = sum(item.total_deposits for item in resolved_steps if isinstance(item, ResolvedPrint))
    printed = sum(item.printed_volume_ul for item in resolved_steps if isinstance(item, ResolvedPrint))
    totals = ResolvedTotals(
        dilution_count=len(dilutions),
        print_count=len(print_layers),
        wait_count=len(waits),
        deposits=deposits,
        printed_volume_ul=round(printed, 3),
        tips_required=config.tips_required,
        hold_time_s=hold_time,
        estimated_duration_s=round(
            sum(item.estimated_duration_s for item in resolved_steps), 1
        ),
        liquid_requirements=sorted(requirements, key=lambda item: item.location),
        final_well_volumes={
            f"{role}:{well}": round(volume, 3)
            for (role, well), volume in sorted(state.volumes.items())
        },
    )

    config_hash = _hash(intent_as_dict(intent))
    resolved_hash = _hash(
        {
            "config": normalized,
            "steps": [item.model_dump(mode="json") for item in resolved_steps],
            "operations": operations,
            "resolver": RESOLVER_VERSION,
            "profile": profile.profile_id,
        }
    )

    return ResolvedWorkflowV1(
        experiment_id=intent.experiment_id,
        experiment_name=intent.experiment_name,
        description=intent.description,
        machine_profile_id=profile.profile_id,
        machine_profile_path=profile.source_path,
        deck=dict(sorted(deck_summary.items(), key=lambda kv: int(kv[0].split()[1]))),
        steps=resolved_steps,
        totals=totals,
        execution_config=normalized,
        operations=operations,
        warnings=state.warnings,
        config_hash=config_hash,
        resolved_hash=resolved_hash,
    )


def _resolve_one_dilution(
    step: DilutionStep,
    state: _Resolution,
    profile: MachineProfile,
    dilutions: list[dict[str, Any]],
    workflow: list[dict[str, str]],
) -> ResolvedDilution:
    pipette = profile.machine.pipette
    stock_ul, diluent_ul, final_ul, requested = _resolve_dilution_volumes(
        step, profile, state.warnings
    )

    source_role, source_well, source_label = state.locate(
        step.source, f"dilution {step.step_id} source"
    )
    diluent_role, diluent_well, diluent_label = state.locate(
        step.diluent, f"dilution {step.step_id} diluent"
    )
    dest_role, dest_well = parse_location(step.destination)

    destination_spec = state.labware_of(dest_role)
    if destination_spec.dispense_reference is None or destination_spec.dispense_height_mm is None:
        raise SERSConfigError(
            f"machine profile has no dispense geometry for {destination_spec.load_name!r}; "
            "this needs human hardware confirmation"
        )

    dilutions.append(
        {
            "operation_id": step.step_id,
            "source": state.source_block(source_role, source_well, source_label),
            "diluent": state.source_block(diluent_role, diluent_well, diluent_label),
            "destination": {
                "labware": dest_role,
                "well": dest_well,
                "dispense_reference": destination_spec.dispense_reference,
                "dispense_offset_mm": destination_spec.dispense_height_mm,
                "mix_bottom_offset_mm": profile.machine.mixing.plate_mix_bottom_offset_mm,
                "initial_volume_ul": 0.0,
            },
            "stock_volume_ul": stock_ul,
            "diluent_volume_ul": diluent_ul,
            "pre_mix_cycles": step.pre_mix_cycles,
            "pre_mix_volume_ul": step.pre_mix_volume_ul,
            "mix_cycles": step.mix_cycles,
            "mix_volume_ul": step.mix_volume_ul,
            "blow_out": True,
        }
    )
    workflow.append({"kind": "dilution", "ref": step.step_id})

    state.draw(source_role, source_well, stock_ul)
    if diluent_ul:
        state.draw(diluent_role, diluent_well, diluent_ul)
    state.volumes[(dest_role, dest_well)] = (
        state.volumes.get((dest_role, dest_well), 0.0) + stock_ul + diluent_ul
    )

    achieved = (stock_ul + diluent_ul) / stock_ul if stock_ul else float("inf")
    label = step.label or (
        f"{source_label} {achieved:g}x" if diluent_ul else f"{source_label} neat"
    )
    state.produced[step.step_id] = (dest_role, dest_well, label)

    stock_chunks = plan_transfer_chunks(
        stock_ul, pipette.max_transfer_volume_ul, pipette.minimum_volume_ul
    )
    diluent_chunks = (
        plan_transfer_chunks(
            diluent_ul, pipette.max_transfer_volume_ul, pipette.minimum_volume_ul
        )
        if diluent_ul
        else []
    )
    flow = pipette.flow_rates.aspirate_ul_s
    tips = 1 + int(bool(diluent_ul)) + int(step.mix_cycles > 0)
    duration = (
        tips * (_TIP_PICKUP_S + _TIP_DROP_S)
        + _transfer_seconds(stock_ul, stock_chunks, flow)
        + _transfer_seconds(diluent_ul, diluent_chunks, flow)
        + step.mix_cycles * ((step.mix_volume_ul / flow) * 2 + _MIX_OVERHEAD_S)
        + step.pre_mix_cycles * ((step.pre_mix_volume_ul / flow) * 2 + _MIX_OVERHEAD_S)
    )

    return ResolvedDilution(
        step_id=step.step_id,
        label=label,
        source_liquid=source_label,
        source_location=f"{source_role}:{source_well}",
        diluent_liquid=diluent_label,
        diluent_location=f"{diluent_role}:{diluent_well}",
        destination=f"{dest_role}:{dest_well}",
        dilution_factor_requested=requested,
        dilution_factor_achieved=round(achieved, 4),
        final_volume_ul=final_ul,
        stock_volume_ul=stock_ul,
        diluent_volume_ul=diluent_ul,
        stock_chunks_ul=stock_chunks,
        diluent_chunks_ul=diluent_chunks,
        mix_cycles=step.mix_cycles,
        mix_volume_ul=step.mix_volume_ul,
        tips=tips,
        estimated_duration_s=round(duration, 1),
    )


def _resolve_one_print(
    step: PrintStep,
    state: _Resolution,
    profile: MachineProfile,
    print_layers: list[dict[str, Any]],
    workflow: list[dict[str, str]],
) -> ResolvedPrint:
    release = profile.machine.print_release
    pipette = profile.machine.pipette

    source_role, source_well, source_label = state.locate(
        step.source, f"print {step.step_id} source"
    )
    paper_role = step.paper or state.experiment.roles_of_kind("paper")[0]
    try:
        targets = resolve_targets(step.targets)
    except TargetSpecError as exc:
        raise SERSConfigError(f"print {step.step_id}: {exc}") from exc

    height, clamp_warning = profile.clamp_paper_height(step.dispense_height_mm)
    if clamp_warning:
        state.warnings.append(f"print {step.step_id}: {clamp_warning}")

    delay = (
        release.post_dispense_delay_s
        if step.post_dispense_delay_s is None
        else step.post_dispense_delay_s
    )

    print_layers.append(
        {
            "layer_name": step.step_id,
            "source_location": state.source_block(source_role, source_well, source_label),
            "drop_volume_ul": step.drop_volume_ul,
            "drops_per_target": step.drops_per_target,
            "paper_targets": [{"labware": paper_role, "wells": targets}],
            "dispense_height_mm": height,
            "air_gap_ul": release.trailing_air_gap_ul,
            "air_gap_height_mm": release.air_gap_height_mm,
            "push_out_ul": release.push_out_ul,
            "blow_out": release.blow_out,
            "touch_tip": False,
            "tip_strategy": step.tip_strategy,
            "post_dispense_delay_s": delay,
            "drying_time_s": 0.0,
            "park_height_mm": 50.0,
        }
    )
    workflow.append({"kind": "print_layer", "ref": step.step_id})

    deposits = len(targets) * step.drops_per_target
    volume = deposits * step.drop_volume_ul
    state.draw(source_role, source_well, volume)

    tips = {
        "per_layer": 1,
        "per_paper": 1,
        "per_target": len(targets),
    }[step.tip_strategy]
    flow = pipette.flow_rates.aspirate_ul_s
    duration = tips * (_TIP_PICKUP_S + _TIP_DROP_S) + deposits * (
        (step.drop_volume_ul / flow) * 2 + _MOVE_S * 2 + delay
    )

    return ResolvedPrint(
        step_id=step.step_id,
        label=step.label or f"print {source_label}",
        source_liquid=source_label,
        source_location=f"{source_role}:{source_well}",
        paper=paper_role,
        paper_slot=state.experiment.deck_by_role[paper_role].slot,
        targets=targets,
        drop_volume_ul=step.drop_volume_ul,
        drops_per_target=step.drops_per_target,
        total_deposits=deposits,
        printed_volume_ul=round(volume, 3),
        dispense_height_mm=height,
        tip_strategy=step.tip_strategy,
        tips=tips,
        estimated_duration_s=round(duration, 1),
    )
