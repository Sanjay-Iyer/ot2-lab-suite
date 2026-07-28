"""Configuration loading and validation for the YAML-only Raman workflows."""
from __future__ import annotations

import copy
import string
from pathlib import Path
from typing import Any

import yaml

from .naming import CANONICAL_FILENAME_PATTERN, slug


class WorkflowConfigError(ValueError):
    """Raised when a Raman workflow configuration is invalid."""


ANALYSIS_DEFAULTS: dict[str, Any] = {
    "analysis": {
        "name": "initial_1080_peak_test",
        "input_root": "raw",
        "output_root": "results",
        "timestamped_run_directory": True,
    },
    "discovery": {
        "enabled": False,
        "file_glob": "*.csv",
        "recursive": False,
        "canonical_filenames_only": True,
        "include": {
            "columns": [],
            "rows": [],
            "sample_types": [],
        },
        "exclude": {
            "columns": [],
            "rows": [],
            "sample_types": [],
        },
        "include_in_overlay": False,
        "include_in_groups": True,
    },
    "spectra": [],
    "csv": {
        "has_header": None,
        "delimiter": ",",
        "comment": None,
        "raman_shift_column": 0,
        "intensity_column": 1,
        "raman_shift_units": "cm^-1",
    },
    "preprocessing": {
        "sort_axis": True,
        "remove_nonfinite": True,
        "merge_duplicate_shifts": True,
    },
    "smoothing": {
        "enabled": False,
        "method": "savitzky_golay",
        "window_length": 7,
        "polynomial_order": 2,
    },
    "baseline": {
        "enabled": True,
        "method": "arpls",
        "lambda": 100000.0,
        "asymmetry": 0.001,
        "convergence_ratio": 1.0e-6,
        "iterations": 50,
    },
    "target_peak": {
        "name": "band_1080",
        "expected_position_cm1": 1080.0,
        "search_window_cm1": [1060.0, 1100.0],
        "integration_window_cm1": [1065.0, 1095.0],
        "minimum_snr": 3.0,
        "minimum_prominence": None,
        "minimum_prominence_to_noise": 3.0,
        "minimum_points_for_width": 5,
        "minimum_width_cm1": 3.0,
        "maximum_width_cm1": 40.0,
        "maximum_shift_cm1": 20.0,
        "require_resolved_width": True,
    },
    "noise": {
        "method": "sidebands",
        "region_cm1": None,
        "sidebands_cm1": [[1020.0, 1055.0], [1105.0, 1140.0]],
        "minimum_points": 10,
    },
    "sampling": {
        "require_uniform_spacing": True,
        "maximum_relative_spacing_deviation": 0.05,
    },
    "normalization": {
        "method": "target_peak",
        "target_value": 1.0,
        "allow_invalid_target": False,
    },
    "plots": {
        "individual": {
            "enabled": True,
            "title_template": None,
            "x_range_cm1": [1000.0, 1160.0],
            "y_range": None,
            "mark_expected_peak": True,
            "mark_detected_peak": True,
            "show_grid": True,
            "consistent_y_range": False,
        },
        "overlay": {
            "enabled": False,
            "title": "Selected spectra",
            "x_range_cm1": [1000.0, 1160.0],
            "y_range": None,
            "vertical_offset": 0.0,
            "mark_expected_peak": True,
            "mark_detected_peaks": False,
        },
        "groups": {
            "enabled": False,
            "x_range_cm1": [1000.0, 1160.0],
            "y_range": None,
            "vertical_offset": 0.0,
            "selections": [],
        },
        "diagnostics": {
            "enabled": True,
            "title_template": None,
            "full_spectrum": True,
            "baseline_plot": True,
            "target_region_baseline_plot": True,
        },
        "output": {
            "dpi": 300,
            "formats": ["png"],
        },
    },
    "outputs": {
        "save_processed_csv": True,
        "save_peak_metrics_csv": True,
        "save_overlay_data": True,
        "save_run_summary_json": True,
        "copy_plots_to_collections": True,
    },
}


RENAME_DEFAULTS: dict[str, Any] = {
    "rename": {
        "input_root": "raw",
        "output_root": "raw/renamed",
        "file_glob": "*.csv",
        "operation": "copy",
        "dry_run": True,
        "overwrite": False,
        "sort_by": "scan_number",
        "scan_regex": r"(?i)scan[ _-]?0*(\d+)",
        "start_sequence": 1,
        "zero_padding": 4,
        "sample_type": "sample",
        "columns": ["A"],
        "rows": [1],
        "order": "column_major",
        "position_mapping": "scan_number_offset",
        "start_scan_number": 1,
        "allow_scan_gaps": False,
        "filename_pattern": (
            CANONICAL_FILENAME_PATTERN
        ),
        "on_unmapped": "error",
        "manifest_file": "rename_manifest.csv",
    }
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path)
    if not config_path.is_file():
        raise WorkflowConfigError(f"Configuration file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise WorkflowConfigError("Configuration root must be a YAML mapping.")
    return config_path, raw


def load_analysis_config(path: str | Path) -> dict[str, Any]:
    """Load, default, and validate the main Raman analysis YAML."""
    config_path, raw = _read_yaml(path)
    _reject_unknown_analysis_keys(raw)
    cfg = _deep_merge(ANALYSIS_DEFAULTS, raw)
    cfg["_source_path"] = str(config_path.resolve())
    _validate_analysis(cfg)
    return cfg


def load_rename_config(path: str | Path) -> dict[str, Any]:
    """Load, default, and validate the Raman filename-migration YAML."""
    config_path, raw = _read_yaml(path)
    _reject_unknown_rename_keys(raw)
    cfg = _deep_merge(RENAME_DEFAULTS, raw)
    cfg["_source_path"] = str(config_path.resolve())
    _validate_rename(cfg)
    return cfg


def _pair(value: Any, field: str, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, list) or len(value) != 2:
        raise WorkflowConfigError(f"{field} must be a two-item YAML list.")
    if value[0] is None or value[1] is None or float(value[0]) >= float(value[1]):
        raise WorkflowConfigError(f"{field} must be ordered [minimum, maximum].")


def _reject_unknown(mapping: dict[str, Any], template: dict[str, Any], path: str) -> None:
    for key, value in mapping.items():
        if key not in template:
            dotted = f"{path}.{key}" if path else key
            raise WorkflowConfigError(f"Unknown configuration key: {dotted}")
        if isinstance(value, dict) and isinstance(template[key], dict):
            _reject_unknown(value, template[key], f"{path}.{key}" if path else key)


def _reject_unknown_analysis_keys(raw: dict[str, Any]) -> None:
    top = {key: value for key, value in ANALYSIS_DEFAULTS.items() if key != "spectra"}
    for key in raw:
        if key not in ANALYSIS_DEFAULTS:
            raise WorkflowConfigError(f"Unknown configuration key: {key}")
    _reject_unknown({key: raw[key] for key in raw if key != "spectra"}, top, "")
    allowed_spectrum = {
        "file", "label", "sample_type", "column", "row", "include",
        "include_in_overlay", "include_in_groups",
    }
    for index, item in enumerate(raw.get("spectra", [])):
        if isinstance(item, dict):
            unknown = set(item) - allowed_spectrum
            if unknown:
                raise WorkflowConfigError(
                    f"Unknown configuration key: spectra[{index}].{sorted(unknown)[0]}"
                )
    allowed_selection = {
        "name", "title", "group_by", "columns", "rows", "sample_types",
        "x_range_cm1", "y_range", "vertical_offset", "mark_expected_peak",
        "mark_detected_peaks",
    }
    selections = raw.get("plots", {}).get("groups", {}).get("selections", [])
    for index, selection in enumerate(selections):
        if isinstance(selection, dict):
            unknown = set(selection) - allowed_selection
            if unknown:
                raise WorkflowConfigError(
                    "Unknown configuration key: "
                    f"plots.groups.selections[{index}].{sorted(unknown)[0]}"
                )


def _reject_unknown_rename_keys(raw: dict[str, Any]) -> None:
    _reject_unknown(raw, RENAME_DEFAULTS, "")


def _validate_analysis(cfg: dict[str, Any]) -> None:
    analysis = cfg["analysis"]
    for key in ("name", "input_root", "output_root"):
        if not str(analysis.get(key, "")).strip():
            raise WorkflowConfigError(f"analysis.{key} cannot be empty.")
    if not isinstance(analysis["timestamped_run_directory"], bool):
        raise WorkflowConfigError(
            "analysis.timestamped_run_directory must be true or false."
        )

    discovery = cfg["discovery"]
    for key in (
        "enabled",
        "recursive",
        "canonical_filenames_only",
        "include_in_overlay",
        "include_in_groups",
    ):
        if not isinstance(discovery[key], bool):
            raise WorkflowConfigError(f"discovery.{key} must be true or false.")
    pattern = discovery["file_glob"]
    if not isinstance(pattern, str) or not pattern.strip():
        raise WorkflowConfigError("discovery.file_glob cannot be empty.")
    if pattern != pattern.strip():
        raise WorkflowConfigError(
            "discovery.file_glob cannot have leading or trailing whitespace."
        )
    pattern_path = Path(pattern)
    if (
        pattern_path.is_absolute()
        or pattern_path.drive
        or pattern_path.anchor
        or (len(pattern) >= 2 and pattern[0].isalpha() and pattern[1] == ":")
        or ".." in pattern_path.parts
    ):
        raise WorkflowConfigError(
            "discovery.file_glob must be a relative pattern without '..'."
        )
    if not discovery["recursive"] and (
        "**" in pattern or "/" in pattern or "\\" in pattern
    ):
        raise WorkflowConfigError(
            "discovery.file_glob cannot contain directories or '**' when "
            "discovery.recursive is false."
        )
    for section in ("include", "exclude"):
        for key in ("columns", "rows", "sample_types"):
            values = discovery[section][key]
            if not isinstance(values, list):
                raise WorkflowConfigError(
                    f"discovery.{section}.{key} must be a YAML list."
                )
            if any(
                isinstance(item, bool)
                or not isinstance(item, (str, int))
                or not str(item).strip()
                for item in values
            ):
                raise WorkflowConfigError(
                    f"discovery.{section}.{key} values must be nonempty text "
                    "or integers."
                )

    spectra = cfg["spectra"]
    if not isinstance(spectra, list):
        raise WorkflowConfigError("spectra must be a YAML list.")
    selected = 0
    selected_labels: set[str] = set()
    selected_slugs: set[str] = set()
    for index, item in enumerate(spectra):
        if not isinstance(item, dict) or not str(item.get("file", "")).strip():
            raise WorkflowConfigError(f"spectra[{index}].file is required.")
        if item.get("include", True):
            selected += 1
            label = str(item.get("label") or Path(item["file"]).stem).casefold()
            if label in selected_labels:
                raise WorkflowConfigError(
                    f"spectra[{index}] duplicates a selected display label; labels "
                    "must be unique so outputs cannot overwrite each other."
                )
            selected_labels.add(label)
            output_slug = slug(item.get("label") or Path(item["file"]).stem).casefold()
            if output_slug in selected_slugs:
                raise WorkflowConfigError(
                    f"spectra[{index}] creates a duplicate filesystem output slug."
                )
            selected_slugs.add(output_slug)
        for key in ("include", "include_in_overlay", "include_in_groups"):
            if key in item and not isinstance(item[key], bool):
                raise WorkflowConfigError(f"spectra[{index}].{key} must be true or false.")
    if selected == 0 and not discovery["enabled"]:
        raise WorkflowConfigError(
            "Enable discovery or provide at least one spectrum with include: true."
        )

    csv_cfg = cfg["csv"]
    if csv_cfg["raman_shift_units"] not in {"cm^-1", "cm-1", "1/cm"}:
        raise WorkflowConfigError(
            "csv.raman_shift_units must explicitly describe Raman shift in cm^-1."
        )
    for key in ("sort_axis", "remove_nonfinite", "merge_duplicate_shifts"):
        if not isinstance(cfg["preprocessing"][key], bool):
            raise WorkflowConfigError(f"preprocessing.{key} must be true or false.")

    smoothing = cfg["smoothing"]
    if not isinstance(smoothing["enabled"], bool):
        raise WorkflowConfigError("smoothing.enabled must be true or false.")
    if smoothing["method"] not in {"savitzky_golay", "none"}:
        raise WorkflowConfigError("smoothing.method must be savitzky_golay or none.")
    window = int(smoothing["window_length"])
    order = int(smoothing["polynomial_order"])
    if window < 3 or window % 2 == 0:
        raise WorkflowConfigError("smoothing.window_length must be an odd integer >= 3.")
    if order < 0 or order >= window:
        raise WorkflowConfigError(
            "smoothing.polynomial_order must be >= 0 and smaller than window_length."
        )

    baseline = cfg["baseline"]
    if not isinstance(baseline["enabled"], bool):
        raise WorkflowConfigError("baseline.enabled must be true or false.")
    if baseline["method"] not in {"arpls", "als", "none"}:
        raise WorkflowConfigError("baseline.method must be arpls, als, or none.")
    if float(baseline["lambda"]) <= 0:
        raise WorkflowConfigError("baseline.lambda must be positive.")
    if int(baseline["iterations"]) < 1:
        raise WorkflowConfigError("baseline.iterations must be >= 1.")
    if not 0 < float(baseline["convergence_ratio"]) < 1:
        raise WorkflowConfigError(
            "baseline.convergence_ratio must be between 0 and 1."
        )
    if baseline["method"] == "als":
        asymmetry = float(baseline["asymmetry"])
        if not 0 < asymmetry < 1:
            raise WorkflowConfigError("baseline.asymmetry must be between 0 and 1.")

    target = cfg["target_peak"]
    expected = float(target["expected_position_cm1"])
    _pair(target["search_window_cm1"], "target_peak.search_window_cm1")
    _pair(target["integration_window_cm1"], "target_peak.integration_window_cm1")
    search_min, search_max = map(float, target["search_window_cm1"])
    integration_min, integration_max = map(float, target["integration_window_cm1"])
    if not search_min <= expected <= search_max:
        raise WorkflowConfigError(
            "target_peak.expected_position_cm1 must be inside search_window_cm1."
        )
    if not integration_min <= expected <= integration_max:
        raise WorkflowConfigError(
            "target_peak.expected_position_cm1 must be inside integration_window_cm1."
        )
    if target["minimum_snr"] is not None and float(target["minimum_snr"]) < 0:
        raise WorkflowConfigError("target_peak.minimum_snr cannot be negative.")
    for key in (
        "minimum_prominence_to_noise",
        "minimum_width_cm1",
        "maximum_width_cm1",
        "maximum_shift_cm1",
    ):
        if target[key] is not None and float(target[key]) < 0:
            raise WorkflowConfigError(f"target_peak.{key} cannot be negative.")
    if int(target["minimum_points_for_width"]) < 2:
        raise WorkflowConfigError("target_peak.minimum_points_for_width must be >= 2.")
    if not isinstance(target["require_resolved_width"], bool):
        raise WorkflowConfigError("target_peak.require_resolved_width must be true or false.")

    noise = cfg["noise"]
    if noise["method"] not in {"sidebands", "mad_derivative", "region"}:
        raise WorkflowConfigError(
            "noise.method must be sidebands, mad_derivative, or region."
        )
    if noise["method"] == "region":
        _pair(noise["region_cm1"], "noise.region_cm1")
    if noise["method"] == "sidebands":
        if not isinstance(noise["sidebands_cm1"], list) or not noise["sidebands_cm1"]:
            raise WorkflowConfigError("noise.sidebands_cm1 must be a non-empty list.")
        for index, sideband in enumerate(noise["sidebands_cm1"]):
            _pair(sideband, f"noise.sidebands_cm1[{index}]")
    if int(noise["minimum_points"]) < 3:
        raise WorkflowConfigError("noise.minimum_points must be >= 3.")

    sampling = cfg["sampling"]
    if not isinstance(sampling["require_uniform_spacing"], bool):
        raise WorkflowConfigError("sampling.require_uniform_spacing must be true or false.")
    if float(sampling["maximum_relative_spacing_deviation"]) < 0:
        raise WorkflowConfigError(
            "sampling.maximum_relative_spacing_deviation cannot be negative."
        )

    normalization = cfg["normalization"]
    if normalization["method"] not in {
        "none",
        "global_max",
        "target_peak",
        "vector_norm",
        "area",
    }:
        raise WorkflowConfigError(
            "normalization.method must be none, global_max, target_peak, "
            "vector_norm, or area."
        )
    if float(normalization["target_value"]) <= 0:
        raise WorkflowConfigError("normalization.target_value must be positive.")
    if not isinstance(normalization["allow_invalid_target"], bool):
        raise WorkflowConfigError(
            "normalization.allow_invalid_target must be true or false."
        )

    formats = cfg["plots"]["output"]["formats"]
    if not isinstance(formats, list) or not formats:
        raise WorkflowConfigError("plots.output.formats must be a non-empty list.")
    unsupported = set(formats) - {"png", "pdf", "svg"}
    if unsupported:
        raise WorkflowConfigError(
            f"Unsupported plot formats: {', '.join(sorted(unsupported))}."
        )
    if int(cfg["plots"]["output"]["dpi"]) < 72:
        raise WorkflowConfigError("plots.output.dpi must be at least 72.")

    for name in ("individual", "overlay", "groups"):
        plot_cfg = cfg["plots"][name]
        if not isinstance(plot_cfg["enabled"], bool):
            raise WorkflowConfigError(f"plots.{name}.enabled must be true or false.")
        _pair(plot_cfg["x_range_cm1"], f"plots.{name}.x_range_cm1")
        _pair(plot_cfg.get("y_range"), f"plots.{name}.y_range", allow_none=True)
        if name != "individual" and float(plot_cfg.get("vertical_offset", 0.0)) < 0:
            raise WorkflowConfigError(f"plots.{name}.vertical_offset cannot be negative.")
    for name in ("individual", "diagnostics"):
        template = cfg["plots"][name].get("title_template")
        if template is not None and not isinstance(template, str):
            raise WorkflowConfigError(f"plots.{name}.title_template must be text or null.")
        if template:
            allowed_fields = {
                "label", "sample_type", "column", "row", "spot", "source_file",
            }
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template)
                if field_name
            }
            unknown = fields - allowed_fields
            if unknown:
                raise WorkflowConfigError(
                    f"plots.{name}.title_template has unknown placeholder "
                    f"{sorted(unknown)[0]!r}."
                )
    diagnostics = cfg["plots"]["diagnostics"]
    for key in (
        "enabled",
        "full_spectrum",
        "baseline_plot",
        "target_region_baseline_plot",
    ):
        if not isinstance(diagnostics[key], bool):
            raise WorkflowConfigError(f"plots.diagnostics.{key} must be true or false.")
    for section, keys in {
        "individual": (
            "mark_expected_peak", "mark_detected_peak", "show_grid",
            "consistent_y_range",
        ),
        "overlay": ("mark_expected_peak", "mark_detected_peaks"),
    }.items():
        for key in keys:
            if not isinstance(cfg["plots"][section][key], bool):
                raise WorkflowConfigError(
                    f"plots.{section}.{key} must be true or false."
                )
    for key, value in cfg["outputs"].items():
        if not isinstance(value, bool):
            raise WorkflowConfigError(f"outputs.{key} must be true or false.")

    selections = cfg["plots"]["groups"]["selections"]
    if not isinstance(selections, list):
        raise WorkflowConfigError("plots.groups.selections must be a YAML list.")
    selection_names: set[str] = set()
    selection_slugs: set[str] = set()
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict) or not selection.get("name"):
            raise WorkflowConfigError(
                f"plots.groups.selections[{index}] needs a unique name."
            )
        normalized_name = str(selection["name"]).casefold()
        if normalized_name in selection_names:
            raise WorkflowConfigError("plots.groups.selections names must be unique.")
        selection_names.add(normalized_name)
        selection_slug = slug(selection["name"]).casefold()
        if selection_slug in selection_slugs:
            raise WorkflowConfigError(
                "plots.groups.selections names create duplicate output slugs."
            )
        selection_slugs.add(selection_slug)
        if selection.get("group_by", "none") not in {
            "none",
            "column",
            "row",
            "sample_type",
        }:
            raise WorkflowConfigError(
                f"plots.groups.selections[{index}].group_by is invalid."
            )


def _validate_rename(cfg: dict[str, Any]) -> None:
    rename = cfg["rename"]
    for key in ("input_root", "output_root", "file_glob", "filename_pattern"):
        if not str(rename.get(key, "")).strip():
            raise WorkflowConfigError(f"rename.{key} cannot be empty.")
    if rename["operation"] not in {"copy", "move"}:
        raise WorkflowConfigError("rename.operation must be copy or move.")
    if rename["sort_by"] not in {"filename", "scan_number"}:
        raise WorkflowConfigError("rename.sort_by must be filename or scan_number.")
    if rename["order"] not in {"column_major", "row_major"}:
        raise WorkflowConfigError("rename.order must be column_major or row_major.")
    if rename["on_unmapped"] not in {"error", "skip"}:
        raise WorkflowConfigError("rename.on_unmapped must be error or skip.")
    if int(rename["start_sequence"]) < 0 or int(rename["zero_padding"]) < 1:
        raise WorkflowConfigError(
            "rename.start_sequence must be nonnegative and zero_padding must be >= 1."
        )
    if not isinstance(rename["columns"], list) or not rename["columns"]:
        raise WorkflowConfigError("rename.columns must be a non-empty list.")
    if not isinstance(rename["rows"], list) or not rename["rows"]:
        raise WorkflowConfigError("rename.rows must be a non-empty list.")
    if rename["position_mapping"] not in {"scan_number_offset", "sorted_order"}:
        raise WorkflowConfigError(
            "rename.position_mapping must be scan_number_offset or sorted_order."
        )
    if rename["position_mapping"] == "scan_number_offset":
        if rename["sort_by"] != "scan_number":
            raise WorkflowConfigError(
                "scan_number_offset position mapping requires sort_by: scan_number."
            )
        if rename["start_scan_number"] is None:
            raise WorkflowConfigError(
                "rename.start_scan_number is required for scan_number_offset mapping."
            )
        int(rename["start_scan_number"])
    if not isinstance(rename["allow_scan_gaps"], bool):
        raise WorkflowConfigError("rename.allow_scan_gaps must be true or false.")
    required = {"{sequence}", "{sample_type}", "{column}", "{row}"}
    missing = [token for token in required if token not in rename["filename_pattern"]]
    if missing:
        raise WorkflowConfigError(
            "rename.filename_pattern is missing required tokens: " + ", ".join(missing)
        )
    if rename["filename_pattern"] != CANONICAL_FILENAME_PATTERN:
        raise WorkflowConfigError(
            "rename.filename_pattern must use the canonical analyzer-compatible "
            f"pattern: {CANONICAL_FILENAME_PATTERN}"
        )
