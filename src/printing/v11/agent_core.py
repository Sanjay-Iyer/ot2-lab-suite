"""Version 11 agent core - pure Python, no LLM and no GCloud.

This module holds ALL the agent's actual thinking:

    structured intent  ->  evolving experiment state  ->  config update
                       ->  validation  ->  resolved plan

The only thing it does NOT do is turn English into a structured intent; that is
the job of the LLM adapter (src/printing/v11/llm_adapter.py). Keeping the split
here means the home laptop can exercise every one of these functions with
scripted intents and no cloud credentials at all.

Defaults live in the 11_ templates, never in this file and never in a prompt.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .labware import LABWARE, BY_LOAD_NAME, V11ConfigError, normalize_well

REPO = Path(__file__).resolve().parents[3]

WORKFLOWS = {
    "dilution": {
        "template": REPO / "configs" / "templates" / "11_dilution_template.yaml",
        "output": REPO / "configs" / "generated" / "11_current_dilution.yaml",
        "runner": "v11-dilution",
    },
    "standard_print": {
        "template": REPO / "configs" / "templates" / "11_standard_print_template.yaml",
        "output": REPO / "configs" / "generated" / "11_current_standard_print.yaml",
        "runner": "v11-standard-print",
    },
    "clover_print": {
        "template": REPO / "configs" / "templates" / "11_clover_print_template.yaml",
        "output": REPO / "configs" / "generated" / "11_current_clover_print.yaml",
        "runner": "v11-clover-print",
    },
}

#: Aliases the agent may receive for a workflow name.
WORKFLOW_ALIASES = {
    "dilution": "dilution", "dilute": "dilution", "serial_dilution": "dilution",
    "standard": "standard_print", "standard_print": "standard_print",
    "standard-print": "standard_print", "print": "standard_print",
    "clover": "clover_print", "clover_print": "clover_print",
    "clover-print": "clover_print",
}


class AgentError(ValueError):
    """The agent cannot act on this intent."""


def _set(block: dict, key: str, value: Any) -> None:
    """Assign only when the intent actually carried a value."""
    if value is not None:
        block[key] = value


def _labware_key(value: Any, *, label: str) -> str:
    """Accept a registry key or a literal load name; never invent labware."""
    text = str(value)
    if text in LABWARE:
        return text
    if text in BY_LOAD_NAME:
        return BY_LOAD_NAME[text]
    raise AgentError(
        f"{label}: {value!r} is not a registered labware. Available: "
        f"{', '.join(sorted(LABWARE))}"
    )


def normalize_workflow(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "_")
    return WORKFLOW_ALIASES.get(key)


class ExperimentState:
    """One evolving experiment across many conversation turns."""

    def __init__(self) -> None:
        self.workflow: str | None = None
        self.config: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.missing: list[str] = []

    # -- lifecycle ---------------------------------------------------------
    def start(self, workflow: str) -> None:
        """Load the template for a workflow. Existing work is replaced."""
        workflow = normalize_workflow(workflow) or workflow
        if workflow not in WORKFLOWS:
            raise AgentError(
                f"unknown workflow {workflow!r}; choose dilution, standard_print "
                "or clover_print"
            )
        template = WORKFLOWS[workflow]["template"]
        if not template.is_file():
            raise AgentError(f"template not found: {template}")
        self.workflow = workflow
        self.config = yaml.safe_load(template.read_text(encoding="utf-8")) or {}

    def apply(self, intent: dict[str, Any]) -> "ExperimentState":
        """Fold one structured intent into the running config."""
        if not isinstance(intent, dict):
            raise AgentError("intent must be a mapping")
        workflow = normalize_workflow(intent.get("workflow"))
        if workflow and workflow != self.workflow:
            self.start(workflow)
        if self.workflow is None:
            raise AgentError(
                "no workflow chosen yet - dilution, standard_print or clover_print?"
            )
        if self.config is None:
            self.start(self.workflow)

        handler = {
            "dilution": self._apply_dilution,
            "standard_print": self._apply_standard,
            "clover_print": self._apply_clover,
        }[self.workflow]
        handler(intent)
        self.history.append(copy.deepcopy(intent))
        return self

    # -- shared blocks -----------------------------------------------------
    def _apply_shared(self, intent: dict[str, Any]) -> None:
        config = self.config
        if intent.get("dry_run") is not None:
            config.setdefault("run_modes", {})["dry_run"] = bool(intent["dry_run"])

        tips = config.setdefault("tips", {})
        _set(tips, "pipette_tip_reuse",
             None if intent.get("pipette_tip_reuse") is None
             else bool(intent["pipette_tip_reuse"]))
        _set(tips, "return_tips",
             None if intent.get("return_tips") is None else bool(intent["return_tips"]))
        if intent.get("start_tip"):
            tips["start_tip"] = normalize_well(intent["start_tip"], label="start_tip")

        pipetting = config.setdefault("pipetting", {})
        for key in ("air_gap_ul", "air_gap_height_mm", "aspirate_flow_rate_ul_s",
                    "dispense_flow_rate_ul_s", "post_aspirate_delay_s",
                    "post_dispense_delay_s"):
            _set(pipetting, key, intent.get(key))

    def _apply_source(self, block: dict[str, Any], intent: dict[str, Any],
                      prefix: str, *, multi: bool = False) -> None:
        """Apply a source-shaped block from `<prefix>_*` intent keys."""
        kind = intent.get(f"{prefix}_type") or intent.get(f"{prefix}_labware")
        if kind:
            key = _labware_key(kind, label=f"{prefix}_type")
            block["type"] = key
            block.pop("labware", None)
            # A new labware implies its usual slot unless one was given.
            block["slot"] = int(
                intent.get(f"{prefix}_slot", LABWARE[key]["usual_slot"])
            )
            # Switching labware must REPLACE the previous labware's volumes and
            # heights, not keep them: a 5000 uL vial figure is impossible in a
            # 350 uL plate well and would fail validation later.
            if intent.get(f"{prefix}_aspirate_height_mm") is None:
                block["aspirate_height_mm"] = LABWARE[key]["default_aspirate_height_mm"]
            # Volume defaults come from the declared registry, never from
            # literals here (see labware.LABWARE).
            if intent.get(f"{prefix}_loaded_volume_ul") is None:
                block["loaded_volume_ul"] = LABWARE[key]["default_loaded_volume_ul"]
            if intent.get(f"{prefix}_minimum_remaining_ul") is None:
                block["minimum_remaining_ul"] = (
                    LABWARE[key]["default_minimum_remaining_ul"]
                )
        elif intent.get(f"{prefix}_slot") is not None:
            block["slot"] = int(intent[f"{prefix}_slot"])

        wells = intent.get(f"{prefix}_wells") or intent.get(f"{prefix}_well")
        if wells:
            if isinstance(wells, str):
                wells = [wells]
            wells = [normalize_well(w, label=f"{prefix} well") for w in wells]
            if multi:
                block["wells"] = wells
                block.pop("well", None)
            else:
                block["well"] = wells[0]
                block.pop("wells", None)
        for key, target in (
            (f"{prefix}_loaded_volume_ul", "loaded_volume_ul"),
            (f"{prefix}_minimum_remaining_ul", "minimum_remaining_ul"),
            (f"{prefix}_aspirate_height_mm", "aspirate_height_mm"),
            (f"{prefix}_material", "material"),
        ):
            _set(block, target, intent.get(key))

    @staticmethod
    def _retarget_groups(config: dict[str, Any]) -> None:
        """Keep print groups pointing at wells the source still offers.

        Changing the source labware/well must not require hand-editing every
        group: any group naming a well that is no longer available follows the
        new first source well.
        """
        wells = config.get("source", {}).get("wells") or []
        if not wells:
            return
        for group in config.get("groups") or []:
            if group.get("source_well") and group["source_well"] not in wells:
                group["source_well"] = wells[0]

    def _apply_paper(self, intent: dict[str, Any]) -> None:
        paper = self.config.setdefault("paper", {})
        _set(paper, "slot",
             None if intent.get("paper_slot") is None else int(intent["paper_slot"]))
        _set(paper, "print_height_mm",
             None if intent.get("print_height_mm") is None
             else float(intent["print_height_mm"]))

    # -- dilution ----------------------------------------------------------
    def _apply_dilution(self, intent: dict[str, Any]) -> None:
        config = self.config
        self._apply_shared(intent)
        self._apply_source(config.setdefault("stock_source", {}), intent, "stock")
        self._apply_source(config.setdefault("diluent_source", {}), intent, "diluent")

        destination = config.setdefault("destination", {})
        self._apply_source(destination, intent, "destination", multi=True)
        for key, target in (("destination_dispense_height_mm", "dispense_height_mm"),):
            _set(destination, target, intent.get(key))

        if intent.get("mode"):
            mode = str(intent["mode"]).lower()
            if mode not in ("single", "series"):
                raise AgentError(f"mode must be single or series, got {mode!r}")
            config["mode"] = mode

        single = config.setdefault("single", {})
        for key in ("stock_volume_ul", "diluent_volume_ul", "final_volume_ul",
                    "dilution_factor"):
            _set(single, key, intent.get(key))
        # An explicit factor/final pair supersedes stale resolved volumes.
        if intent.get("dilution_factor") is not None:
            single.pop("stock_volume_ul", None)
            single.pop("diluent_volume_ul", None)

        series = config.setdefault("series", {})
        if intent.get("series_wells"):
            series["wells"] = [
                normalize_well(w, label="series well") for w in intent["series_wells"]
            ]
            for stale in ("start_well", "direction", "steps"):
                series.pop(stale, None)
        for key, target in (("series_start_well", "start_well"),
                            ("series_direction", "direction"),
                            ("series_steps", "steps"),
                            ("transfer_volume_ul", "transfer_volume_ul"),
                            ("series_diluent_volume_ul", "diluent_volume_ul")):
            if intent.get(key) is not None:
                series[target] = intent[key]
                if key in ("series_start_well", "series_direction", "series_steps"):
                    series.pop("wells", None)

        transfer = config.setdefault("transfer", {})
        _set(transfer, "max_chunk_ul", intent.get("max_chunk_ul"))
        _set(transfer, "air_gap_ul", intent.get("air_gap_ul"))

        mix = config.setdefault("mix", {})
        _set(mix, "enabled",
             None if intent.get("mix_enabled") is None else bool(intent["mix_enabled"]))
        _set(mix, "cycles", intent.get("mix_cycles"))
        _set(mix, "volume_ul", intent.get("mix_volume_ul"))

        delays = config.setdefault("delays", {})
        _set(delays, "after_transfer_s", intent.get("after_transfer_s"))
        _set(delays, "after_mix_s", intent.get("after_mix_s"))

    # -- standard print ----------------------------------------------------
    def _apply_standard(self, intent: dict[str, Any]) -> None:
        config = self.config
        self._apply_shared(intent)
        self._apply_source(config.setdefault("source", {}), intent, "source", multi=True)
        self._retarget_groups(config)
        self._apply_paper(intent)

        printing = config.setdefault("printing", {})
        _set(printing, "droplet_volume_ul", intent.get("droplet_volume_ul"))
        if intent.get("order"):
            order = str(intent["order"]).lower()
            if order not in ("layer_major", "target_major"):
                raise AgentError(
                    f"order must be layer_major or target_major, got {order!r}"
                )
            printing["order"] = order

        if intent.get("groups"):
            groups = []
            for raw in intent["groups"]:
                group: dict[str, Any] = {}
                if raw.get("source_well"):
                    group["source_well"] = normalize_well(
                        raw["source_well"], label="group source_well"
                    )
                if raw.get("targets"):
                    group["targets"] = [
                        normalize_well(t, label="target") for t in raw["targets"]
                    ]
                elif raw.get("target_selection"):
                    group["target_selection"] = raw["target_selection"]
                group["droplets"] = int(raw.get("droplets", 1))
                groups.append(group)
            config["groups"] = groups
            config.pop("targets", None)
            config.pop("target_selection", None)
        elif intent.get("targets") or intent.get("target_selection"):
            if intent.get("targets"):
                new_targets = {
                    "targets": [normalize_well(t, label="target") for t in intent["targets"]]
                }
            else:
                new_targets = {"target_selection": intent["target_selection"]}
            existing = config.get("groups") or []
            if len(existing) == 1:
                # Retarget the standing group so an earlier droplet count and
                # source well survive the user naming new targets. The stale
                # top-level targets MUST go too: leaving them writes a config
                # whose visible targets disagree with what actually runs, and
                # the human reviewing the YAML would be reading a lie.
                existing[0].pop("targets", None)
                existing[0].pop("target_selection", None)
                existing[0].update(new_targets)
                config.pop("targets", None)
                config.pop("target_selection", None)
            else:
                config.pop("groups", None)
                config.pop("targets", None)
                config.pop("target_selection", None)
                config.update(new_targets)

        if intent.get("droplets_per_target") is not None:
            count = int(intent["droplets_per_target"])
            if config.get("groups"):
                for group in config["groups"]:
                    group["droplets"] = count
            else:
                # No groups yet: fold the standing targets into one group rather
                # than inventing a top-level key the loader does not accept.
                group = {"droplets": count}
                if config.get("targets"):
                    group["targets"] = config.pop("targets")
                elif config.get("target_selection"):
                    group["target_selection"] = config.pop("target_selection")
                wells = config.get("source", {}).get("wells") or []
                if wells:
                    group["source_well"] = wells[0]
                config["groups"] = [group]
        _set(config, "replicates",
             None if intent.get("replicates") is None else int(intent["replicates"]))

        timing = config.setdefault("timing", {})
        for key in ("inter_drop_delay_s", "inter_layer_delay_s", "inter_target_delay_s"):
            _set(timing, key, intent.get(key))

    # -- clover print ------------------------------------------------------
    def _apply_clover(self, intent: dict[str, Any]) -> None:
        config = self.config
        self._apply_shared(intent)
        self._apply_source(config.setdefault("source", {}), intent, "source", multi=True)
        self._apply_paper(intent)

        printing = config.setdefault("printing", {})
        _set(printing, "droplet_volume_ul", intent.get("droplet_volume_ul"))

        geometry = config.setdefault("geometry", {})
        # Actual separation wins over half-values and clears the other form so a
        # stale half_width can never silently survive.
        if intent.get("separation_x_mm") is not None:
            geometry["separation_x_mm"] = float(intent["separation_x_mm"])
            geometry.pop("half_width_mm", None)
        if intent.get("separation_y_mm") is not None:
            geometry["separation_y_mm"] = float(intent["separation_y_mm"])
            geometry.pop("half_height_mm", None)
        if intent.get("half_width_mm") is not None:
            geometry["half_width_mm"] = float(intent["half_width_mm"])
            geometry.pop("separation_x_mm", None)
        if intent.get("half_height_mm") is not None:
            geometry["half_height_mm"] = float(intent["half_height_mm"])
            geometry.pop("separation_y_mm", None)
        for key in ("x_offset_mm", "y_offset_mm", "rotation_deg"):
            _set(geometry, key, intent.get(key))

        # "make it tighter" / "twice as wide" - scale whatever is configured.
        scale = intent.get("scale_separation")
        if scale is not None:
            factor = float(scale)
            if factor <= 0:
                raise AgentError("scale_separation must be > 0")
            for actual, half in (("separation_x_mm", "half_width_mm"),
                                 ("separation_y_mm", "half_height_mm")):
                if geometry.get(actual) is not None:
                    geometry[actual] = float(geometry[actual]) * factor
                elif geometry.get(half) is not None:
                    geometry[half] = float(geometry[half]) * factor

        if intent.get("clover_references"):
            references = [
                normalize_well(r, label="clover reference")
                for r in intent["clover_references"]
            ]
            config["clovers"] = [{"reference": r} for r in references]
        if intent.get("clovers"):
            clovers = []
            for raw in intent["clovers"]:
                entry: dict[str, Any] = {
                    "reference": normalize_well(
                        raw["reference"], label="clover reference"
                    )
                }
                if raw.get("source_well"):
                    entry["source_well"] = normalize_well(
                        raw["source_well"], label="clover source_well"
                    )
                if raw.get("geometry"):
                    entry["geometry"] = raw["geometry"]
                if raw.get("layers") is not None:
                    entry["layers"] = int(raw["layers"])
                clovers.append(entry)
            config["clovers"] = clovers

        _set(config, "layers",
             None if intent.get("layers") is None else int(intent["layers"]))

        timing = config.setdefault("timing", {})
        for key in ("inter_drop_delay_s", "inter_layer_delay_s", "inter_clover_delay_s"):
            _set(timing, key, intent.get(key))

    # -- output ------------------------------------------------------------
    def write(self, path: Path | None = None) -> Path:
        if self.workflow is None or self.config is None:
            raise AgentError("nothing configured yet")
        out = Path(path) if path else WORKFLOWS[self.workflow]["output"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "# VERSION 11 - AI-GENERATED. Review before any physical run.\n"
            + yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return out

    def validate(self) -> tuple[bool, str, dict[str, Any] | None]:
        """Run the config through the real V11 loader for this workflow.

        Returns ``(ok, message, resolved_config)``. Imported lazily so the agent
        core stays importable even while a loader is being worked on.
        """
        if self.workflow is None or self.config is None:
            return False, "nothing configured yet", None
        try:
            if self.workflow == "dilution":
                from .dilution_loader import load_dilution_config as load
            elif self.workflow == "standard_print":
                from .standard_loader import load_standard_print_config as load
            else:
                from .clover_loader import load_clover_config as load
        except ImportError as exc:  # pragma: no cover - loader not present yet
            return False, f"loader unavailable: {exc}", None

        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".yaml", delete=False,
                dir=str(REPO / ".test_tmp"), encoding="utf-8",
            ) as handle:
                yaml.safe_dump(self.config, handle, sort_keys=False)
                temp_path = Path(handle.name)
            try:
                resolved, _ = load(temp_path)
            finally:
                temp_path.unlink(missing_ok=True)
        except (V11ConfigError, ValueError, KeyError, TypeError) as exc:
            return False, str(exc), None
        return True, "ok", resolved
