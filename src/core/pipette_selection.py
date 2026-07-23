"""
src/core/pipette_selection.py
=============================
ONE centralized service that decides which pipette handles a given droplet
volume. This replaces the scattered, hardcoded ``p300_multi_gen2`` assumptions in
the printing protocols and supersedes the legacy per-volume selector formerly at
``src/printing/tools/config.py`` (``select_pipette``) — which was never wired into
the live protocols and is now archived under ``archive/legacy_src_printing/``.

Design goals (from the config-driven-pipette refactor):
  * Respect an explicitly configured pipette; validate its volume range.
  * Support ``auto`` selection: prefer the smallest-capacity mounted pipette that
    can accurately reach the volume (so the P20 wins for small volumes, the P300
    for larger ones).
  * Never assume a pipette is mounted — read the mounted set from config and fail
    clearly when nothing mounted can perform the requested transfer.
  * Give actionable errors (which pipette, which range, what is mounted).

Canonical volume ranges come from ``configs/constraints/pipette_constraints.yaml``
(loaded on demand); a built-in table mirrors them so the service is usable even
without the repo's config tree (keeps unit tests dependency-light).

Channel count is derived from the pipette *name* ("multi" -> 8, else 1) because
the constraints file intentionally does not carry a channel field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "PipetteSelectionError",
    "PipetteSpec",
    "MountedPipette",
    "PipetteChoice",
    "load_pipette_specs",
    "parse_mounted_pipettes",
    "select_pipette",
    "assert_layout_supported",
]


class PipetteSelectionError(ValueError):
    """Raised when no valid pipette can be chosen (bad config or bad volume)."""


# ── Specs ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PipetteSpec:
    """Volume envelope + channel count for one OT-2 pipette model."""

    name: str
    min_volume_ul: float
    max_volume_ul: float
    recommended_min_volume_ul: float
    recommended_max_volume_ul: float
    channels: int
    mounts_allowed: tuple[str, ...] = ("left", "right")

    @property
    def is_multichannel(self) -> bool:
        return self.channels > 1

    def covers(self, volume_ul: float) -> bool:
        """True if the volume is within the pipette's *hard* min/max range."""
        return self.min_volume_ul <= volume_ul <= self.max_volume_ul

    def is_accurate(self, volume_ul: float) -> bool:
        """True if the volume is within the *recommended* (accurate) range."""
        return self.recommended_min_volume_ul <= volume_ul <= self.recommended_max_volume_ul


def _channels_from_name(name: str) -> int:
    """OT-2 8-channel pipette names contain 'multi'; everything else is single."""
    return 8 if "multi" in name.lower() else 1


# Built-in mirror of configs/constraints/pipette_constraints.yaml. Authoritative
# fallback so the selector works in isolation (and unit tests need no config tree).
_BUILTIN_SPECS: dict[str, PipetteSpec] = {
    "p20_single_gen2": PipetteSpec("p20_single_gen2", 1, 20, 2, 20, 1),
    "p20_multi_gen2": PipetteSpec("p20_multi_gen2", 1, 20, 2, 20, 8),
    "p300_single_gen2": PipetteSpec("p300_single_gen2", 20, 300, 30, 300, 1),
    "p300_multi_gen2": PipetteSpec("p300_multi_gen2", 20, 300, 30, 300, 8),
    "p1000_single_gen2": PipetteSpec("p1000_single_gen2", 100, 1000, 100, 1000, 1),
}

_DEFAULT_CONSTRAINTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "configs" / "constraints" / "pipette_constraints.yaml"
)


def load_pipette_specs(constraints_path: str | Path | None = None) -> dict[str, PipetteSpec]:
    """Return {name: PipetteSpec}, merging the built-in table with the YAML file.

    YAML entries override/extend the built-ins. Missing or unreadable file -> just
    the built-ins (a warning is not raised; callers can pass an explicit path).
    """
    specs = dict(_BUILTIN_SPECS)
    path = Path(constraints_path) if constraints_path else _DEFAULT_CONSTRAINTS_PATH
    try:
        import yaml  # local import so the module has no hard yaml dependency

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ImportError, ValueError):
        return specs

    for name, body in (raw.get("pipettes") or {}).items():
        if not isinstance(body, Mapping):
            continue
        try:
            specs[name] = PipetteSpec(
                name=name,
                min_volume_ul=float(body["min_volume_ul"]),
                max_volume_ul=float(body["max_volume_ul"]),
                recommended_min_volume_ul=float(
                    body.get("recommended_min_volume_ul", body["min_volume_ul"])
                ),
                recommended_max_volume_ul=float(
                    body.get("recommended_max_volume_ul", body["max_volume_ul"])
                ),
                channels=int(body.get("channels", _channels_from_name(name))),
                mounts_allowed=tuple(body.get("mounts_allowed", ("left", "right"))),
            )
        except (KeyError, TypeError, ValueError):
            # Skip malformed entries rather than breaking the whole load.
            continue
    return specs


# ── Mounted set ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MountedPipette:
    name: str
    mount: str
    spec: PipetteSpec


def parse_mounted_pipettes(
    config: Mapping[str, Any],
    specs: Mapping[str, PipetteSpec] | None = None,
) -> list[MountedPipette]:
    """Read the physically-mounted pipettes from a workflow config.

    Accepts either the new multi-pipette form::

        pipettes:
          - {name: p300_multi_gen2, mount: right}
          - {name: p20_single_gen2, mount: left}

    or the legacy single-pipette form::

        pipette: {name: p300_multi_gen2, mount: right}

    Raises PipetteSelectionError on unknown pipette names, illegal mounts, or two
    pipettes sharing a mount.
    """
    specs = specs or load_pipette_specs()

    entries: list[Mapping[str, Any]] = []
    if isinstance(config.get("pipettes"), Sequence) and not isinstance(config.get("pipettes"), (str, bytes)):
        entries = [e for e in config["pipettes"] if isinstance(e, Mapping)]
    elif isinstance(config.get("pipette"), Mapping):
        entries = [config["pipette"]]
    else:
        raise PipetteSelectionError(
            "No pipettes configured: expected a 'pipettes' list or a 'pipette' mapping."
        )

    mounted: list[MountedPipette] = []
    seen_mounts: dict[str, str] = {}
    for entry in entries:
        name = entry.get("name")
        mount = str(entry.get("mount", "")).lower()
        if name not in specs:
            raise PipetteSelectionError(
                f"Unknown pipette '{name}'. Known: {sorted(specs)}."
            )
        spec = specs[name]
        if mount not in ("left", "right"):
            raise PipetteSelectionError(
                f"Pipette '{name}' has invalid mount '{mount}' (must be left/right)."
            )
        if mount not in spec.mounts_allowed:
            raise PipetteSelectionError(
                f"Pipette '{name}' cannot mount on '{mount}' "
                f"(allowed: {list(spec.mounts_allowed)})."
            )
        if mount in seen_mounts:
            raise PipetteSelectionError(
                f"Mount '{mount}' assigned to both '{seen_mounts[mount]}' and '{name}'."
            )
        seen_mounts[mount] = name
        mounted.append(MountedPipette(name=name, mount=mount, spec=spec))
    return mounted


# ── Selection ─────────────────────────────────────────────────────────────────

@dataclass
class PipetteChoice:
    name: str
    mount: str
    channels: int
    spec: PipetteSpec
    reason: str
    warnings: list[str] = field(default_factory=list)


def _fmt_mounted(mounted: Iterable[MountedPipette]) -> str:
    return (
        ", ".join(
            f"{m.name}@{m.mount}[{m.spec.min_volume_ul:g}-{m.spec.max_volume_ul:g}µL]"
            for m in mounted
        )
        or "(none)"
    )


def select_pipette(
    volume_ul: float,
    mounted: Sequence[MountedPipette],
    explicit: str | None = None,
) -> PipetteChoice:
    """Choose the pipette for a single transfer volume.

    Parameters
    ----------
    volume_ul : the per-transfer volume (droplet size), must be > 0.
    mounted   : the pipettes physically available (from parse_mounted_pipettes).
    explicit  : a pipette name to force, or None/"auto" for automatic selection.

    Auto rule: among mounted pipettes whose hard range covers the volume, pick the
    one with the smallest max capacity (P20 before P300). A volume below a
    pipette's *recommended* min is allowed but flagged with an accuracy warning.
    """
    if not mounted:
        raise PipetteSelectionError("No pipettes are mounted.")
    if volume_ul <= 0:
        raise PipetteSelectionError(f"Transfer volume must be > 0 µL (got {volume_ul}).")

    explicit = None if (explicit is None or str(explicit).lower() == "auto") else str(explicit)

    if explicit is not None:
        match = next((m for m in mounted if m.name == explicit), None)
        if match is None:
            raise PipetteSelectionError(
                f"Requested pipette '{explicit}' is not mounted. "
                f"Mounted: {_fmt_mounted(mounted)}."
            )
        if not match.spec.covers(volume_ul):
            raise PipetteSelectionError(
                f"{volume_ul:g} µL is outside {explicit}'s range "
                f"[{match.spec.min_volume_ul:g}-{match.spec.max_volume_ul:g} µL]. "
                f"Choose a volume in range or a different pipette."
            )
        choice = PipetteChoice(
            name=match.name, mount=match.mount, channels=match.spec.channels,
            spec=match.spec, reason=f"explicitly configured pipette '{explicit}'",
        )
        _add_accuracy_warning(choice, volume_ul)
        return choice

    # Auto: candidates whose hard range covers the volume.
    candidates = [m for m in mounted if m.spec.covers(volume_ul)]
    if not candidates:
        raise PipetteSelectionError(
            f"No mounted pipette can handle {volume_ul:g} µL. "
            f"Mounted: {_fmt_mounted(mounted)}. "
            f"Mount a pipette whose range includes {volume_ul:g} µL, "
            f"or change the droplet volume."
        )
    # Prefer accurate candidates; among those (or all, if none accurate) pick the
    # smallest capacity that fits -> P20 before P300 for small volumes.
    accurate = [m for m in candidates if m.spec.is_accurate(volume_ul)]
    pool = accurate or candidates
    best = min(pool, key=lambda m: m.spec.max_volume_ul)
    choice = PipetteChoice(
        name=best.name, mount=best.mount, channels=best.spec.channels,
        spec=best.spec,
        reason=(
            f"auto-selected smallest-capacity pipette covering {volume_ul:g} µL "
            f"(candidates: {', '.join(m.name for m in candidates)})"
        ),
    )
    _add_accuracy_warning(choice, volume_ul)
    return choice


def _add_accuracy_warning(choice: PipetteChoice, volume_ul: float) -> None:
    spec = choice.spec
    if volume_ul < spec.recommended_min_volume_ul:
        choice.warnings.append(
            f"{volume_ul:g} µL is below {spec.name}'s recommended minimum "
            f"({spec.recommended_min_volume_ul:g} µL); accuracy may be reduced."
        )


def assert_layout_supported(spec: PipetteSpec, wells_per_dispense: int) -> None:
    """Guard against incompatible multichannel layouts.

    A single-channel pipette can only address one well per dispense. Asking it to
    hit N>1 wells simultaneously (e.g. an 8-well plate column in one shot) is a
    config error — with a single-channel P20 those must be done one spot at a time.
    """
    if wells_per_dispense > 1 and spec.channels == 1:
        raise PipetteSelectionError(
            f"Single-channel '{spec.name}' cannot dispense to {wells_per_dispense} "
            f"wells simultaneously; use a multichannel pipette or set the group to "
            f"single-spot (one destination per dispense)."
        )
    if wells_per_dispense > spec.channels:
        raise PipetteSelectionError(
            f"'{spec.name}' has {spec.channels} channels but the layout needs "
            f"{wells_per_dispense} simultaneous wells."
        )
