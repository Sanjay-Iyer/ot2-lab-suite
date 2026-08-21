"""Deterministic resolver from experimental intent to canonical physical actions.

This module is the boundary the paper architecture depends on. Above it, a
language model may propose scientific intent; below it, nothing is inferred.
Given the same :class:`PrintExperimentJobV1` it always produces byte-identical
canonical output, and it refuses to emit a plan it cannot prove is physically
executable.

It contains no experiment-specific knowledge: no fixed dilution factors, no
column semantics, no fixed row counts, no default liquids. Every scientific value
is read from the validated job.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from ..schemas.experiments import (
    DelayStepV1,
    DirectDilutionStepV1,
    LabwareProfileV1,
    MixSpecV1,
    MixStepV1,
    PrintExperimentJobV1,
    PrintStepV1,
    ResolvedExperimentPlanV1,
    SerialDilutionStepV1,
    TransferStepV1,
)
from .capabilities import (
    CapabilityError,
    as_fraction,
    direct_dilution_volumes,
    serial_cascade,
    split_transfer,
)


class ExperimentResolutionError(ValueError):
    """The requested experiment cannot be resolved into safe physical actions."""


_WELL_CACHE: dict[tuple[str, str, int], frozenset[str]] = {}


def _labware_wells(profile: LabwareProfileV1) -> frozenset[str]:
    """Resolve the addressable wells of one labware definition."""
    key = (profile.namespace, profile.load_name, profile.version)
    cached = _WELL_CACHE.get(key)
    if cached is not None:
        return cached

    path = Path(REPO_ROOT) / "labware" / f"{profile.load_name}.json"
    if path.is_file():
        definition = json.loads(path.read_text(encoding="utf-8"))
        wells = frozenset(definition["wells"])
    else:
        try:
            from opentrons_shared_data.labware import load_definition
        except Exception as exc:  # pragma: no cover - environment dependent
            raise ExperimentResolutionError(
                f"no definition available for labware {profile.load_name!r}"
            ) from exc
        try:
            definition = load_definition(profile.load_name, profile.version)
        except Exception as exc:
            raise ExperimentResolutionError(
                f"no definition available for labware {profile.load_name!r}"
            ) from exc
        wells = frozenset(definition["wells"])
    _WELL_CACHE[key] = wells
    return wells


def _f(value: Fraction | float | int) -> float:
    return float(value)


class _Resolver:
    """One resolution pass; instances are single use and never shared."""

    def __init__(self, job: PrintExperimentJobV1) -> None:
        self.job = job
        self.machine = job.machine
        self.experiment = job.experiment
        self.pipette = job.machine.pipette
        self.actions: list[dict[str, Any]] = []
        # liquid_id -> (labware_role, well)
        self.liquid_locations: dict[str, tuple[str, str]] = {}
        # preparation step id -> ordered product liquid ids
        self.series_products: dict[str, tuple[str, ...]] = {}
        # preparation step id -> ordered product wells, so printing a series never
        # depends on a liquid id being globally unique
        self.series_locations: dict[str, tuple[tuple[str, str], ...]] = {}
        self.preparation_math: list[dict[str, Any]] = []
        self.occupied_wells: dict[tuple[str, str], str] = {}

    # -- helpers ---------------------------------------------------------- #

    def _fail(self, message: str) -> None:
        raise ExperimentResolutionError(message)

    def _labware(self, role: str, *, context: str) -> LabwareProfileV1:
        try:
            return self.machine.role(role)
        except KeyError:
            self._fail(f"{context} references unknown labware role {role!r}")
        raise AssertionError  # pragma: no cover - _fail always raises

    def _check_well(self, profile: LabwareProfileV1, well: str, *, context: str) -> None:
        wells = _labware_wells(profile)
        if well not in wells:
            self._fail(
                f"{context}: well {well!r} does not exist in labware "
                f"{profile.load_name!r} (role {profile.role!r})"
            )

    def _aspirate_location(self, role: str, well: str, *, context: str) -> dict[str, Any]:
        profile = self._labware(role, context=context)
        self._check_well(profile, well, context=context)
        if profile.kind not in {"source", "preparation"}:
            self._fail(
                f"{context}: cannot aspirate from {profile.kind} role {role!r}"
            )
        return {
            "labware": role,
            "well": well,
            "reference": profile.aspirate_reference,
            "z_mm": float(profile.aspirate_height_mm),
        }

    def _dispense_location(self, role: str, well: str, *, context: str) -> dict[str, Any]:
        profile = self._labware(role, context=context)
        self._check_well(profile, well, context=context)
        if profile.kind not in {"source", "preparation"}:
            self._fail(
                f"{context}: liquid transfers cannot target {profile.kind} role "
                f"{role!r}; use a print step for a substrate"
            )
        return {
            "labware": role,
            "well": well,
            "reference": profile.dispense_reference,
            "z_mm": float(profile.dispense_height_mm),
        }

    def _print_location(self, role: str, well: str, *, context: str) -> dict[str, Any]:
        profile = self._labware(role, context=context)
        if profile.kind != "substrate":
            self._fail(f"{context}: printing requires a substrate labware, not {role!r}")
        self._check_well(profile, well, context=context)
        release = self.machine.print_release
        return {
            "labware": role,
            "well": well,
            "reference": release.dispense_reference,
            "z_mm": float(release.dispense_height_mm),
        }

    def _add(self, action: str, **fields: Any) -> None:
        self.actions.append(
            {"sequence_index": len(self.actions) + 1, "action": action, **fields}
        )

    def _liquid_location(self, liquid_id: str, *, context: str) -> tuple[str, str]:
        location = self.liquid_locations.get(liquid_id)
        if location is None:
            self._fail(
                f"{context} references liquid {liquid_id!r}, which is neither declared "
                "nor produced by an earlier preparation step"
            )
        return location  # type: ignore[return-value]

    def _register_products(
        self,
        step_id: str,
        products: tuple[str, ...],
        wells: tuple[str, ...],
        labware: str,
        *,
        explicit: tuple[bool, ...],
        permitted_reuse_id: tuple[str | None, ...],
        context: str,
    ) -> None:
        """Bind preparation products to their wells, failing on accidental reuse.

        A derived product name that collides with an existing liquid is a mistake:
        the same identifier would then mean two different physical locations. An
        explicitly written ``product_liquid_ids`` entry may reuse a declared id --
        that is the scientist stating that a plate aliquot is still the same
        liquid -- and the earlier location stays authoritative for lookups.
        """
        for product, well, is_explicit, permitted in zip(
            products, wells, explicit, permitted_reuse_id
        ):
            if product in self.liquid_locations:
                if not is_explicit:
                    self._fail(
                        f"{context}: derived liquid name {product!r} already refers to "
                        f"{self.liquid_locations[product][0]}:{self.liquid_locations[product][1]}; "
                        "rename the preparation or declare the product id explicitly"
                    )
                if permitted != product:
                    self._fail(
                        f"{context}: explicit product id {product!r} already refers to "
                        "another liquid or location; only the factor-1 product may "
                        "explicitly reuse the source liquid id"
                    )
            self._claim_well(labware, well, product, context=context)
            if product not in self.liquid_locations:
                self.liquid_locations[product] = (labware, well)
        self.series_products[step_id] = products
        self.series_locations[step_id] = tuple((labware, well) for well in wells)

    def _claim_well(self, role: str, well: str, liquid_id: str, *, context: str) -> None:
        key = (role, well)
        existing = self.occupied_wells.get(key)
        if existing is not None and existing != liquid_id:
            self._fail(
                f"{context}: {role}:{well} is already assigned to liquid {existing!r}; "
                f"refusing to overwrite it with {liquid_id!r}"
            )
        self.occupied_wells[key] = liquid_id

    def _mix_within_range(self, mix: MixSpecV1, *, context: str) -> None:
        if not (
            self.pipette.minimum_volume_ul
            <= mix.volume_ul
            <= self.pipette.maximum_volume_ul
        ):
            self._fail(
                f"{context}: mix volume {mix.volume_ul:g} uL is outside the "
                f"{self.pipette.name} range "
                f"[{self.pipette.minimum_volume_ul:g}, {self.pipette.maximum_volume_ul:g}] uL"
            )

    def _split(self, total: Fraction, *, context: str) -> tuple[Fraction, ...]:
        try:
            return split_transfer(
                total,
                maximum_ul=self.pipette.maximum_volume_ul,
                minimum_ul=self.pipette.minimum_volume_ul,
            )
        except CapabilityError as exc:
            self._fail(f"{context}: {exc}")
        raise AssertionError  # pragma: no cover - _fail always raises

    # -- emitters --------------------------------------------------------- #

    def _emit_transfer(
        self,
        *,
        operation_id: str,
        liquid_id: str,
        result_liquid_id: str,
        source: dict[str, Any],
        destination: dict[str, Any],
        total: Fraction,
        tip_group: str,
        context: str,
    ) -> None:
        chunks = self._split(total, context=context)
        for index, chunk in enumerate(chunks, start=1):
            self._add(
                "TRANSFER",
                operation_id=operation_id,
                liquid_id=liquid_id,
                result_liquid_id=result_liquid_id,
                source=source,
                destination=destination,
                volume_ul=_f(chunk),
                chunk_index=index,
                chunk_count=len(chunks),
                pipette=self.pipette.name,
                tip_group=tip_group,
            )

    def _emit_mix(
        self,
        *,
        operation_id: str,
        liquid_id: str,
        role: str,
        well: str,
        mix: MixSpecV1,
        tip_group: str,
        context: str,
    ) -> None:
        self._mix_within_range(mix, context=context)
        self._add(
            "MIX",
            operation_id=operation_id,
            liquid_id=liquid_id,
            location=self._aspirate_location(role, well, context=context),
            cycles=mix.cycles,
            volume_ul=float(mix.volume_ul),
            pipette=self.pipette.name,
            tip_group=tip_group,
        )

    # -- procedure steps -------------------------------------------------- #

    def _serial_dilution(self, step: SerialDilutionStepV1) -> None:
        context = f"serial_dilution step {step.id!r}"
        wells = step.destination_wells
        products = step.products()
        source_role, source_well = self._liquid_location(step.source_liquid_id, context=context)
        diluent_role, diluent_well = self._liquid_location(
            step.diluent_liquid_id, context=context
        )
        try:
            cascade = serial_cascade(
                target_usable_ul=step.target_usable_volume_ul,
                count=len(wells),
                fold=step.fold,
            )
        except CapabilityError as exc:
            self._fail(f"{context}: {exc}")

        self._register_products(
            step.id,
            products,
            tuple(wells),
            step.destination_labware,
            explicit=tuple(step.product_liquid_ids is not None for _ in products),
            permitted_reuse_id=tuple(
                step.source_liquid_id if factor == 1 else None
                for factor in step.factors
            ),
            context=context,
        )

        stock_group = f"{step.id}_stock_setup"
        self._emit_transfer(
            operation_id=f"prepare_{step.id}_stock",
            liquid_id=step.source_liquid_id,
            result_liquid_id=products[0],
            source=self._aspirate_location(source_role, source_well, context=context),
            destination=self._dispense_location(
                step.destination_labware, wells[0], context=context
            ),
            total=cascade["stock_allocation"],
            tip_group=stock_group,
            context=context,
        )
        if step.mix is not None:
            self._emit_mix(
                operation_id=f"mix_{step.id}_stock",
                liquid_id=products[0],
                role=step.destination_labware,
                well=wells[0],
                mix=step.mix,
                tip_group=stock_group,
                context=context,
            )

        diluent_group = f"{step.id}_diluent_setup"
        for index in range(1, len(wells)):
            volume = cascade["diluent"][index]
            if volume == 0:
                continue
            self._emit_transfer(
                operation_id=f"prepare_{step.id}_diluent_{index + 1}",
                liquid_id=step.diluent_liquid_id,
                result_liquid_id=step.diluent_liquid_id,
                source=self._aspirate_location(
                    diluent_role, diluent_well, context=context
                ),
                destination=self._dispense_location(
                    step.destination_labware, wells[index], context=context
                ),
                total=volume,
                tip_group=diluent_group,
                context=context,
            )

        for index in range(len(wells) - 1):
            volume = cascade["outgoing"][index]
            if volume == 0:
                continue
            serial_group = f"{step.id}_serial_{index + 1}_to_{index + 2}"
            self._emit_transfer(
                operation_id=f"serial_{step.id}_{index + 1}_to_{index + 2}",
                liquid_id=products[index],
                result_liquid_id=products[index + 1],
                source=self._aspirate_location(
                    step.destination_labware, wells[index], context=context
                ),
                destination=self._dispense_location(
                    step.destination_labware, wells[index + 1], context=context
                ),
                total=volume,
                tip_group=serial_group,
                context=context,
            )
            if step.mix is not None:
                self._emit_mix(
                    operation_id=f"mix_{step.id}_{index + 2}",
                    liquid_id=products[index + 1],
                    role=step.destination_labware,
                    well=wells[index + 1],
                    mix=step.mix,
                    tip_group=serial_group,
                    context=context,
                )

        self.preparation_math.append(
            {
                "preparation_id": step.id,
                "method": "back_calculated_serial_cascade",
                "fold": step.fold,
                "factors": list(cascade["factors"]),
                "target_usable_ul": float(step.target_usable_volume_ul),
                "stock_allocation_ul": _f(cascade["stock_allocation"]),
                "total_diluent_ul": _f(cascade["total_diluent"]),
                "pre_transfer_ul": [_f(value) for value in cascade["pre_transfer"]],
                "outgoing_ul": [_f(value) for value in cascade["outgoing"]],
                "diluent_ul": [_f(value) for value in cascade["diluent"]],
                "retained_usable_ul": [_f(value) for value in cascade["retained"]],
                "products": list(products),
                "destination_wells": list(wells),
            }
        )

    def _direct_dilution(self, step: DirectDilutionStepV1) -> None:
        context = f"direct_dilution step {step.id!r}"
        products = step.products()
        source_role, source_well = self._liquid_location(
            step.source_liquid_id, context=context
        )
        diluent_role, diluent_well = self._liquid_location(
            step.diluent_liquid_id, context=context
        )
        try:
            volumes = direct_dilution_volumes(
                (point.factor, point.total_volume_ul) for point in step.points
            )
        except CapabilityError as exc:
            self._fail(f"{context}: {exc}")

        self._register_products(
            step.id,
            products,
            tuple(point.well for point in step.points),
            step.destination_labware,
            explicit=tuple(
                point.product_liquid_id is not None for point in step.points
            ),
            permitted_reuse_id=tuple(
                step.source_liquid_id if point.factor == 1 else None
                for point in step.points
            ),
            context=context,
        )

        for index, (point, product) in enumerate(zip(step.points, products), start=1):
            stock, diluent = volumes[index - 1]
            group = f"{step.id}_point_{index}"
            if diluent > 0:
                self._emit_transfer(
                    operation_id=f"{step.id}_diluent_{index}",
                    liquid_id=step.diluent_liquid_id,
                    result_liquid_id=step.diluent_liquid_id,
                    source=self._aspirate_location(
                        diluent_role, diluent_well, context=context
                    ),
                    destination=self._dispense_location(
                        step.destination_labware, point.well, context=context
                    ),
                    total=diluent,
                    tip_group=f"{step.id}_diluent_setup",
                    context=context,
                )
            self._emit_transfer(
                operation_id=f"{step.id}_stock_{index}",
                liquid_id=step.source_liquid_id,
                result_liquid_id=product,
                source=self._aspirate_location(source_role, source_well, context=context),
                destination=self._dispense_location(
                    step.destination_labware, point.well, context=context
                ),
                total=stock,
                tip_group=group,
                context=context,
            )
            if step.mix is not None:
                self._emit_mix(
                    operation_id=f"mix_{step.id}_{index}",
                    liquid_id=product,
                    role=step.destination_labware,
                    well=point.well,
                    mix=step.mix,
                    tip_group=group,
                    context=context,
                )

        self.preparation_math.append(
            {
                "preparation_id": step.id,
                "method": "independent_direct_dilution",
                "factors": [float(point.factor) for point in step.points],
                "total_volume_ul": [float(point.total_volume_ul) for point in step.points],
                "stock_ul": [_f(stock) for stock, _ in volumes],
                "diluent_ul": [_f(diluent) for _, diluent in volumes],
                "products": list(products),
                "destination_wells": [point.well for point in step.points],
            }
        )

    def _transfer(self, step: TransferStepV1) -> None:
        context = f"transfer step {step.id!r}"
        registered_source = self._liquid_location(step.liquid_id, context=context)
        if step.source is not None:
            source_role, source_well = step.source.labware, step.source.well
            if (source_role, source_well) != registered_source:
                self._fail(
                    f"{context}: explicit source {source_role}:{source_well} does not "
                    f"match liquid {step.liquid_id!r} at "
                    f"{registered_source[0]}:{registered_source[1]}"
                )
        else:
            source_role, source_well = registered_source
        result = step.result_liquid_id or step.liquid_id
        destination = (step.destination.labware, step.destination.well)
        if destination == registered_source:
            self._fail(f"{context}: source and destination are the same well")
        if result in self.liquid_locations:
            self._fail(
                f"{context}: result liquid id {result!r} already refers to "
                f"{self.liquid_locations[result][0]}:{self.liquid_locations[result][1]}; "
                "declare a distinct result_liquid_id for the destination aliquot"
            )
        self._claim_well(
            step.destination.labware, step.destination.well, result, context=context
        )
        self.liquid_locations[result] = destination
        group = f"{step.id}_transfer"
        self._emit_transfer(
            operation_id=step.id,
            liquid_id=step.liquid_id,
            result_liquid_id=result,
            source=self._aspirate_location(source_role, source_well, context=context),
            destination=self._dispense_location(
                step.destination.labware, step.destination.well, context=context
            ),
            total=as_fraction(step.volume_ul),
            tip_group=group,
            context=context,
        )
        if step.mix_after is not None:
            self._emit_mix(
                operation_id=f"mix_{step.id}",
                liquid_id=result,
                role=step.destination.labware,
                well=step.destination.well,
                mix=step.mix_after,
                tip_group=group,
                context=context,
            )

    def _mix(self, step: MixStepV1) -> None:
        context = f"mix step {step.id!r}"
        registered = self._liquid_location(step.liquid_id, context=context)
        requested = (step.location.labware, step.location.well)
        if requested != registered:
            self._fail(
                f"{context}: requested location {requested[0]}:{requested[1]} does not "
                f"match liquid {step.liquid_id!r} at {registered[0]}:{registered[1]}"
            )
        self._emit_mix(
            operation_id=step.id,
            liquid_id=step.liquid_id,
            role=step.location.labware,
            well=step.location.well,
            mix=MixSpecV1(cycles=step.cycles, volume_ul=step.volume_ul),
            tip_group=f"{step.id}_mix",
            context=context,
        )

    def _print(self, step: PrintStepV1) -> None:
        context = f"print step {step.id!r}"
        release = self.machine.print_release
        piston = (
            release.pre_air_chase_ul + step.volume_ul + release.trailing_air_gap_ul
        )
        if step.volume_ul < self.pipette.minimum_volume_ul:
            self._fail(
                f"{context}: droplet {step.volume_ul:g} uL is below the "
                f"{self.pipette.name} minimum {self.pipette.minimum_volume_ul:g} uL"
            )
        if piston > self.pipette.maximum_volume_ul + 1e-9:
            self._fail(
                f"{context}: droplet plus air handling needs {piston:g} uL, exceeding the "
                f"{self.pipette.name} maximum {self.pipette.maximum_volume_ul:g} uL"
            )

        if step.source.kind == "series":
            products = self.series_products.get(step.source.preparation_id)
            if products is None:
                self._fail(
                    f"{context} references preparation {step.source.preparation_id!r}, "
                    "which no earlier step produced"
                )
            if len(products) != len(step.targets):
                self._fail(
                    f"{context}: preparation {step.source.preparation_id!r} produces "
                    f"{len(products)} liquids but the step lists {len(step.targets)} targets"
                )
            liquids = list(products)
            origins = list(self.series_locations[step.source.preparation_id])
        else:
            liquids = [step.source.liquid_id] * len(step.targets)
            origins = [
                self._liquid_location(step.source.liquid_id, context=context)
            ] * len(step.targets)

        if step.mix_before_aspirate is not None:
            self._mix_within_range(step.mix_before_aspirate, context=context)

        for drop_index in range(1, step.repeats + 1):
            suffix = f"_drop_{drop_index}" if step.repeats > 1 else ""
            for target, liquid_id, (role, well) in zip(step.targets, liquids, origins):
                if step.tip_policy == "per_target":
                    group = f"{step.id}{suffix}_{target}"
                elif step.tip_policy == "per_pass":
                    group = f"{step.id}{suffix}"
                else:
                    group = step.id
                if step.mix_before_aspirate is not None:
                    self._emit_mix(
                        operation_id=f"mix_before_{step.id}{suffix}_{target}",
                        liquid_id=liquid_id,
                        role=role,
                        well=well,
                        mix=step.mix_before_aspirate,
                        tip_group=group,
                        context=context,
                    )
                self._add(
                    "PRINT",
                    operation_id=f"print_{step.id}{suffix}_{target}",
                    condition_id=f"{step.substrate}_{target}",
                    liquid_id=liquid_id,
                    source=self._aspirate_location(role, well, context=context),
                    destination=self._print_location(
                        step.substrate, target, context=context
                    ),
                    volume_ul=float(step.volume_ul),
                    air_gap_ul=float(release.trailing_air_gap_ul),
                    air_gap_height_mm=float(release.air_gap_height_mm),
                    piston_dispense_ul=float(piston),
                    push_out_ul=float(release.push_out_ul),
                    blow_out=bool(release.blow_out),
                    post_dispense_delay_s=float(release.post_dispense_delay_s),
                    pipette=self.pipette.name,
                    drop_index=drop_index,
                    repeat_count=step.repeats,
                    tip_group=group,
                )
            if step.delay_after_pass_s > 0:
                self._add(
                    "DELAY",
                    operation_id=f"{step.id}{suffix}_rest",
                    duration_s=float(step.delay_after_pass_s),
                    reason=step.description
                    or f"configured rest after {step.id}{suffix}",
                )

    def _delay(self, step: DelayStepV1) -> None:
        self._add(
            "DELAY",
            operation_id=step.id,
            duration_s=float(step.duration_s),
            reason=step.reason,
        )

    # -- validation ------------------------------------------------------- #

    def _validate_deposit_spacing(self) -> None:
        if not self.experiment.policies.require_drying_delay_between_deposits:
            return
        last_deposit: dict[tuple[str, str], int] = {}
        last_delay_index = -1
        for action in self.actions:
            if action["action"] == "DELAY" and action["duration_s"] > 0:
                last_delay_index = action["sequence_index"]
                continue
            if action["action"] != "PRINT":
                continue
            destination = action["destination"]
            key = (destination["labware"], destination["well"])
            previous = last_deposit.get(key)
            if previous is not None and last_delay_index < previous:
                self._fail(
                    f"{key[0]}:{key[1]} receives a second deposit at action "
                    f"{action['sequence_index']} with no drying delay after action "
                    f"{previous}; add a delay or disable "
                    "experiment.policies.require_drying_delay_between_deposits"
                )
            last_deposit[key] = action["sequence_index"]

    def _source_accessibility(self) -> tuple[dict[str, Any], dict[tuple[str, str], float]]:
        volumes: dict[tuple[str, str], float] = {}
        minimum_remaining: dict[tuple[str, str], float] = {}
        for liquid in self.experiment.liquids:
            key = (liquid.location.labware, liquid.location.well)
            volumes[key] = float(liquid.loaded_volume_ul)
            minimum_remaining[key] = float(liquid.minimum_remaining_ul)

        areas: dict[str, float] = {}
        for profile in self.machine.labware:
            if profile.well_diameter_mm is not None:
                areas[profile.role] = math.pi * (profile.well_diameter_mm / 2.0) ** 2

        def cover(location: dict[str, Any]) -> float:
            role = location["labware"]
            if location["reference"] != "bottom":
                return 0.0
            area = areas.get(role)
            if area is None:
                self._fail(
                    f"labware role {role!r} aspirates from its bottom but declares no "
                    "well_diameter_mm, so submersion cannot be verified"
                )
            return float(area) * float(location["z_mm"])

        minimum_margin = math.inf
        checked = 0
        for action in self.actions:
            kind = action["action"]
            if kind == "TRANSFER":
                source = action["source"]
                key = (source["labware"], source["well"])
                after = volumes.get(key, 0.0) - float(action["volume_ul"])
                margin = after - cover(source)
                if margin < -1e-9:
                    self._fail(
                        f"action {action['sequence_index']} aspirates from a nominally dry "
                        f"{key[0]}:{key[1]}: {after:g} uL remains but {cover(source):g} uL "
                        "is needed to cover the configured aspiration height"
                    )
                volumes[key] = after
                destination = action["destination"]
                dest_key = (destination["labware"], destination["well"])
                volumes[dest_key] = volumes.get(dest_key, 0.0) + float(action["volume_ul"])
                minimum_margin = min(minimum_margin, margin)
                checked += 1
            elif kind == "MIX":
                location = action["location"]
                key = (location["labware"], location["well"])
                trough = volumes.get(key, 0.0) - float(action["volume_ul"])
                margin = trough - cover(location)
                if margin < -1e-9:
                    self._fail(
                        f"action {action['sequence_index']} mixes a nominally dry "
                        f"{key[0]}:{key[1]}: trough {trough:g} uL, cover {cover(location):g} uL"
                    )
                minimum_margin = min(minimum_margin, margin)
                checked += 1
            elif kind == "PRINT":
                source = action["source"]
                key = (source["labware"], source["well"])
                after = volumes.get(key, 0.0) - float(action["volume_ul"])
                margin = after - cover(source)
                if margin < -1e-9:
                    self._fail(
                        f"action {action['sequence_index']} prints from a nominally dry "
                        f"{key[0]}:{key[1]}: {after:g} uL remains, cover {cover(source):g} uL"
                    )
                volumes[key] = after
                minimum_margin = min(minimum_margin, margin)
                checked += 1

        for key, required in minimum_remaining.items():
            if volumes[key] < required - 1e-9:
                self._fail(
                    f"declared source {key[0]}:{key[1]} finishes at {volumes[key]:g} uL, "
                    f"below its minimum remaining volume {required:g} uL"
                )

        geometry: dict[str, float] = {}
        for profile in self.machine.labware:
            if profile.well_diameter_mm is not None:
                geometry[f"{profile.role}_well_diameter_mm"] = float(
                    profile.well_diameter_mm
                )
            if profile.aspirate_height_mm is not None:
                geometry[f"{profile.role}_aspirate_height_mm"] = float(
                    profile.aspirate_height_mm
                )
        geometry["substrate_dispense_height_mm"] = float(
            self.machine.print_release.dispense_height_mm
        )

        report = {
            "status": "PASS",
            "checked_aspirate_or_mix_actions": checked,
            "minimum_nominal_submerged_margin_ul": (
                minimum_margin if checked else 0.0
            ),
            "ending_volumes_ul": {
                f"{labware}:{well}": volume
                for (labware, well), volume in sorted(volumes.items())
            },
            "geometry": geometry,
        }
        return report, volumes

    def _tip_count(self) -> int:
        active: str | None = None
        count = 0
        for action in self.actions:
            kind = action["action"]
            if kind in {"LOAD_LABWARE", "LOAD_PIPETTE"}:
                continue
            if kind == "DELAY":
                active = None
                continue
            group = action["tip_group"]
            if group != active:
                count += 1
                active = group
        return count

    # -- entry point ------------------------------------------------------ #

    def resolve(self) -> ResolvedExperimentPlanV1:
        for profile in self.machine.labware:
            self._add(
                "LOAD_LABWARE",
                role=profile.role,
                slot=profile.slot,
                load_name=profile.load_name,
                namespace=profile.namespace,
                version=profile.version,
            )
        tiprack = self.machine.tiprack
        self._add(
            "LOAD_PIPETTE",
            pipette=self.pipette.name,
            mount=self.pipette.mount,
            tiprack_role=tiprack.role,
            minimum_volume_ul=float(self.pipette.minimum_volume_ul),
            maximum_volume_ul=float(self.pipette.maximum_volume_ul),
            flow_rates={
                "aspirate_ul_s": float(self.pipette.flow_rates.aspirate_ul_s),
                "dispense_ul_s": float(self.pipette.flow_rates.dispense_ul_s),
            },
        )

        for liquid in self.experiment.liquids:
            context = f"declared liquid {liquid.liquid_id!r}"
            profile = self._labware(liquid.location.labware, context=context)
            if profile.kind not in {"source", "preparation"}:
                self._fail(
                    f"{context} sits on {profile.role!r}, which is a {profile.kind} labware"
                )
            self._check_well(profile, liquid.location.well, context=context)
            self._claim_well(
                liquid.location.labware,
                liquid.location.well,
                liquid.liquid_id,
                context=context,
            )
            self.liquid_locations[liquid.liquid_id] = (
                liquid.location.labware,
                liquid.location.well,
            )

        handlers = {
            SerialDilutionStepV1: self._serial_dilution,
            DirectDilutionStepV1: self._direct_dilution,
            TransferStepV1: self._transfer,
            MixStepV1: self._mix,
            PrintStepV1: self._print,
            DelayStepV1: self._delay,
        }
        for step in self.experiment.procedure:
            handler = handlers.get(type(step))
            if handler is None:  # pragma: no cover - discriminated union is exhaustive
                self._fail(f"unsupported procedure step type {type(step).__name__}")
            handler(step)

        self._validate_deposit_spacing()
        tip_count = self._tip_count()
        tip_capacity = len(_labware_wells(tiprack))
        if tip_count > tip_capacity:
            self._fail(
                f"resolved experiment requires {tip_count} tips but registered tip rack "
                f"{tiprack.role!r} provides only {tip_capacity}"
            )
        accessibility, ending = self._source_accessibility()

        initial_liquids = []
        for liquid in self.experiment.liquids:
            key = (liquid.location.labware, liquid.location.well)
            profile = self.machine.role(liquid.location.labware)
            initial_liquids.append(
                {
                    "liquid_id": liquid.liquid_id,
                    "display_name": liquid.display_name,
                    "location": {
                        "labware": liquid.location.labware,
                        "well": liquid.location.well,
                        "reference": profile.aspirate_reference,
                        "z_mm": float(profile.aspirate_height_mm),
                    },
                    "loaded_volume_ul": float(liquid.loaded_volume_ul),
                    "minimum_remaining_ul": float(liquid.minimum_remaining_ul),
                    "scientific_allocation_ul": float(liquid.loaded_volume_ul)
                    - ending[key],
                }
            )

        prints = [a for a in self.actions if a["action"] == "PRINT"]
        delays = [a for a in self.actions if a["action"] == "DELAY"]
        totals = {
            "action_count": len(self.actions),
            "transfer_count": sum(1 for a in self.actions if a["action"] == "TRANSFER"),
            "mix_count": sum(1 for a in self.actions if a["action"] == "MIX"),
            "print_count": len(prints),
            "delay_count": len(delays),
            "configured_experimental_delay_s": float(
                sum(a["duration_s"] for a in delays)
            ),
            "printed_liquid_ul": float(sum(a["volume_ul"] for a in prints)),
            "tip_count": tip_count,
        }

        return ResolvedExperimentPlanV1.from_content(
            job_id=self.job.job_id,
            experiment_id=self.experiment.metadata.experiment_id,
            initial_liquids=initial_liquids,
            preparation_math=self.preparation_math,
            actions=self.actions,
            totals=totals,
            source_accessibility=accessibility,
        )


def resolve_experiment_job(job: PrintExperimentJobV1) -> ResolvedExperimentPlanV1:
    """Resolve validated experimental intent into canonical physical actions."""
    return _Resolver(job).resolve()
