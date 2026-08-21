"""Canonical comparison of resolved experiments at the physical-action level.

Two experiments are equivalent when the robot would do the same physical things
in the same order, regardless of how the configuration was written or which
labels a human or a model happened to choose. Three normalizations are provided,
from strictest to most label-independent:

``canonical``
    The whole resolved plan, including operation ids and tip-group names. Used to
    prove that resolution is deterministic and that an artifact has not drifted.

``physical``
    Ordered physical actions only. Operation ids, condition ids, and delay reason
    text are dropped because they are commentary; tip-group names are replaced by
    a monotonic tip index because *when the tip changes* is physical but what the
    group is called is not. Initial liquid setup is compared separately because
    changing a loaded liquid, location, or volume is physical even when the
    action list is unchanged.

``structural``
    The physical trace with every remaining label removed: labware roles become
    ``load_name@slot``, and liquid ids become ``L1, L2, ...`` in order of first
    appearance. This is a topology-only diagnostic; because it deliberately
    erases chemical identity it must never be the sole A-vs-C success criterion.

Equivalence claims in this repository are made on these traces, never on file
names, YAML formatting, or visual similarity.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from ..canonical import canonical_json_bytes


#: Fields that carry human commentary rather than physical meaning.
LABEL_FIELDS = ("operation_id", "condition_id", "reason")

#: Physical fields compared for each action type, in canonical order.
PHYSICAL_FIELDS: Mapping[str, tuple[str, ...]] = {
    "LOAD_LABWARE": ("role", "slot", "load_name", "namespace", "version"),
    "LOAD_PIPETTE": (
        "pipette",
        "mount",
        "tiprack_role",
        "minimum_volume_ul",
        "maximum_volume_ul",
        "flow_rates",
    ),
    "TRANSFER": (
        "liquid_id",
        "result_liquid_id",
        "source",
        "destination",
        "volume_ul",
        "chunk_index",
        "chunk_count",
        "pipette",
    ),
    "MIX": ("liquid_id", "location", "cycles", "volume_ul", "pipette"),
    "PRINT": (
        "liquid_id",
        "source",
        "destination",
        "volume_ul",
        "air_gap_ul",
        "air_gap_height_mm",
        "piston_dispense_ul",
        "push_out_ul",
        "blow_out",
        "post_dispense_delay_s",
        "pipette",
        "drop_index",
        "repeat_count",
    ),
    "DELAY": ("duration_s",),
}

# Fields whose physical canonical form is floating point. Normalizing them here
# keeps equality and hashing consistent for semantically equal inputs such as 5
# and 5.0 without changing the established trace representation.
PHYSICAL_FLOAT_FIELDS = frozenset(
    {
        "minimum_volume_ul",
        "maximum_volume_ul",
        "aspirate_ul_s",
        "dispense_ul_s",
        "volume_ul",
        "z_mm",
        "air_gap_ul",
        "air_gap_height_mm",
        "piston_dispense_ul",
        "push_out_ul",
        "post_dispense_delay_s",
        "duration_s",
        "loaded_volume_ul",
        "minimum_remaining_ul",
        "scientific_allocation_ul",
    }
)


def _normalize_physical_value(name: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _normalize_physical_value(str(key), item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_physical_value(name, item) for item in value]
    if name in PHYSICAL_FLOAT_FIELDS and isinstance(value, (int, float)) \
            and not isinstance(value, bool):
        return float(value)
    return value


def _actions_of(plan: Any) -> list[dict[str, Any]]:
    """Accept a resolved plan model, a plan mapping, or a bare action list."""
    if hasattr(plan, "action_dicts"):
        return plan.action_dicts()
    if isinstance(plan, Mapping):
        return [dict(action) for action in plan["actions"]]
    return [dict(action) for action in plan]


def _tip_indices(actions: Sequence[Mapping[str, Any]]) -> list[int | None]:
    """Assign a monotonic tip index, mirroring how the executor changes tips."""
    indices: list[int | None] = []
    active: str | None = None
    count = 0
    for action in actions:
        kind = action["action"]
        if kind in {"LOAD_LABWARE", "LOAD_PIPETTE"}:
            indices.append(None)
            continue
        if kind == "DELAY":
            active = None
            indices.append(None)
            continue
        group = action["tip_group"]
        if group != active:
            count += 1
            active = group
        indices.append(count)
    return indices


def physical_trace(plan: Any) -> list[dict[str, Any]]:
    """Ordered physical actions with commentary removed and tips indexed."""
    actions = _actions_of(plan)
    tips = _tip_indices(actions)
    trace: list[dict[str, Any]] = []
    for action, tip_index in zip(actions, tips):
        kind = action["action"]
        fields = PHYSICAL_FIELDS.get(kind)
        if fields is None:
            raise ValueError(f"unknown physical action {kind!r}")
        record: dict[str, Any] = {"action": kind}
        for name in fields:
            record[name] = _normalize_physical_value(name, action[name])
        if tip_index is not None:
            record["tip_index"] = tip_index
        trace.append(record)
    return trace


def setup_trace(plan: Any) -> list[dict[str, Any]]:
    """Canonical initial-liquid setup required before physical actions begin."""
    if hasattr(plan, "model_dump"):
        mapping = plan.model_dump(mode="json")
    elif isinstance(plan, Mapping) and "initial_liquids" in plan:
        mapping = plan
    else:
        raise ValueError(
            "setup comparison requires a complete plan with initial_liquids; "
            "bare action lists support action-only physical diagnostics"
        )

    trace: list[dict[str, Any]] = []
    for source in mapping.get("initial_liquids", []):
        trace.append(
            {
                "action": "SETUP_LIQUID",
                "liquid_id": source["liquid_id"],
                "location": _normalize_physical_value("location", source["location"]),
                "loaded_volume_ul": _normalize_physical_value(
                    "loaded_volume_ul", source["loaded_volume_ul"]
                ),
                "minimum_remaining_ul": _normalize_physical_value(
                    "minimum_remaining_ul", source["minimum_remaining_ul"]
                ),
                "scientific_allocation_ul": _normalize_physical_value(
                    "scientific_allocation_ul", source["scientific_allocation_ul"]
                ),
            }
        )
    return sorted(
        trace,
        key=lambda item: (
            item["location"]["labware"],
            item["location"]["well"],
            item["liquid_id"],
        ),
    )


def structural_trace(plan: Any) -> list[dict[str, Any]]:
    """Physical trace with every identifier replaced by a positional token."""
    actions = _actions_of(plan)
    role_identity: dict[str, str] = {}
    for action in actions:
        if action["action"] == "LOAD_LABWARE":
            role_identity[action["role"]] = (
                f"{action['namespace']}/{action['load_name']}@{action['slot']}"
            )

    liquid_tokens: dict[str, str] = {}

    def liquid_token(liquid_id: str) -> str:
        token = liquid_tokens.get(liquid_id)
        if token is None:
            token = f"L{len(liquid_tokens) + 1}"
            liquid_tokens[liquid_id] = token
        return token

    def normalize_location(location: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "labware": role_identity.get(location["labware"], location["labware"]),
            "well": location["well"],
            "reference": location["reference"],
            "z_mm": location["z_mm"],
        }

    trace: list[dict[str, Any]] = []
    for record in physical_trace(actions):
        item = dict(record)
        if item["action"] == "LOAD_LABWARE":
            item.pop("role")
            item["labware"] = f"{item.pop('namespace')}/{item.pop('load_name')}@{item.pop('slot')}"
        if item["action"] == "LOAD_PIPETTE":
            item["tiprack_role"] = role_identity.get(
                item["tiprack_role"], item["tiprack_role"]
            )
        for key in ("liquid_id", "result_liquid_id"):
            if key in item:
                item[key] = liquid_token(item[key])
        for key in ("source", "destination", "location"):
            if key in item:
                item[key] = normalize_location(item[key])
        trace.append(item)
    return trace


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def physical_sha256(plan: Any) -> str:
    return _digest(physical_trace(plan))


def setup_sha256(plan: Any) -> str:
    return _digest(setup_trace(plan))


def structural_sha256(plan: Any) -> str:
    return _digest(structural_trace(plan))


def trace_fingerprints(plan: Any) -> dict[str, str]:
    """All comparable digests for one resolved plan."""
    fingerprints = {
        "physical_sha256": physical_sha256(plan),
        "setup_sha256": setup_sha256(plan),
        "structural_sha256": structural_sha256(plan),
    }
    if hasattr(plan, "plan_sha256"):
        fingerprints["canonical_sha256"] = plan.plan_sha256()
    return fingerprints


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, out)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, out)
    else:
        out[prefix] = value


def semantic_diff(
    left: Iterable[Mapping[str, Any]],
    right: Iterable[Mapping[str, Any]],
    *,
    left_label: str = "left",
    right_label: str = "right",
    limit: int = 50,
) -> list[str]:
    """Human-readable differences between two normalized traces."""
    left_list = list(left)
    right_list = list(right)
    differences: list[str] = []
    if len(left_list) != len(right_list):
        differences.append(
            f"action count differs: {left_label}={len(left_list)} "
            f"{right_label}={len(right_list)}"
        )
    for index, (a, b) in enumerate(zip(left_list, right_list), start=1):
        if a == b:
            continue
        if a.get("action") != b.get("action"):
            differences.append(
                f"action {index}: type {a.get('action')} != {b.get('action')}"
            )
            continue
        flat_a: dict[str, Any] = {}
        flat_b: dict[str, Any] = {}
        _flatten("", a, flat_a)
        _flatten("", b, flat_b)
        for key in sorted(set(flat_a) | set(flat_b)):
            if flat_a.get(key) != flat_b.get(key):
                differences.append(
                    f"action {index} ({a['action']}) {key}: "
                    f"{left_label}={flat_a.get(key)!r} {right_label}={flat_b.get(key)!r}"
                )
        if len(differences) >= limit:
            differences.append("... further differences suppressed")
            return differences
    return differences


def compare_plans(
    left: Any,
    right: Any,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict[str, Any]:
    """Full comparison report at both the physical and structural levels."""
    left_physical = physical_trace(left)
    right_physical = physical_trace(right)
    left_setup = setup_trace(left)
    right_setup = setup_trace(right)
    left_structural = structural_trace(left)
    right_structural = structural_trace(right)
    physical_match = left_physical == right_physical
    setup_match = left_setup == right_setup
    structural_match = left_structural == right_structural
    left_physical_sha256 = _digest(left_physical)
    right_physical_sha256 = _digest(right_physical)
    left_setup_sha256 = _digest(left_setup)
    right_setup_sha256 = _digest(right_setup)
    if physical_match != (left_physical_sha256 == right_physical_sha256):
        raise AssertionError("physical equality and SHA-256 equality disagree")
    if setup_match != (left_setup_sha256 == right_setup_sha256):
        raise AssertionError("setup equality and SHA-256 equality disagree")
    return {
        "left_label": left_label,
        "right_label": right_label,
        "left_action_count": len(left_physical),
        "right_action_count": len(right_physical),
        "physical_match": physical_match,
        "setup_match": setup_match,
        "execution_match": physical_match and setup_match,
        "structural_match": structural_match,
        "left_physical_sha256": left_physical_sha256,
        "right_physical_sha256": right_physical_sha256,
        "left_setup_sha256": left_setup_sha256,
        "right_setup_sha256": right_setup_sha256,
        "left_structural_sha256": _digest(left_structural),
        "right_structural_sha256": _digest(right_structural),
        "physical_differences": (
            []
            if physical_match
            else semantic_diff(
                left_physical,
                right_physical,
                left_label=left_label,
                right_label=right_label,
            )
        ),
        "setup_differences": (
            []
            if setup_match
            else semantic_diff(
                left_setup,
                right_setup,
                left_label=left_label,
                right_label=right_label,
            )
        ),
        "structural_differences": (
            []
            if structural_match
            else semantic_diff(
                left_structural,
                right_structural,
                left_label=left_label,
                right_label=right_label,
            )
        ),
    }
