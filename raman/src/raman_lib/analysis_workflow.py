"""YAML-only orchestration for reproducible initial Raman analysis."""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy.signal import find_peaks, peak_prominences, peak_widths

from . import io_utils, naming, plotting, preprocessing

_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")


@dataclass
class SpectrumResult:
    """All data and metadata produced by one shared processing pipeline."""

    source: Path
    spec: dict[str, Any]
    metadata: dict[str, Any]
    x: np.ndarray
    raw: np.ndarray
    smoothed: np.ndarray
    baseline: np.ndarray
    corrected: np.ndarray
    scaled: np.ndarray
    target_region: np.ndarray
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    normalization_method: str = "none"
    normalization_valid: bool = False
    normalization_scale: float | None = None
    load_report: dict[str, Any] = field(default_factory=dict)
    sampling_report: dict[str, Any] = field(default_factory=dict)
    baseline_report: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return naming.slug(self.metadata["label"])

    @property
    def title(self) -> str:
        return naming.display_title(self.metadata)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    return str(value)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _filter_value(value: Any, included: list[Any], excluded: list[Any]) -> bool:
    """Apply case-insensitive include/exclude filters; exclusions win."""
    normalized = "" if value is None else str(value).strip().casefold()
    include_values = {str(item).strip().casefold() for item in included}
    exclude_values = {str(item).strip().casefold() for item in excluded}
    return (not include_values or normalized in include_values) and (
        normalized not in exclude_values
    )


def _validate_resolved_spectra(spectra: list[dict[str, Any]]) -> None:
    """Protect generated outputs from duplicate labels and filesystem slugs."""
    selected = [item for item in spectra if item.get("include", True)]
    if not selected:
        raise RuntimeError(
            "No spectra matched the discovery filters and explicit selections."
        )
    labels: set[str] = set()
    slugs: set[str] = set()
    for item in selected:
        label = str(item.get("label") or Path(item["file"]).stem)
        normalized_label = label.casefold()
        output_slug = naming.slug(label).casefold()
        if normalized_label in labels:
            raise RuntimeError(
                f"Selected spectra create the duplicate display label {label!r}."
            )
        if output_slug in slugs:
            raise RuntimeError(
                f"Selected spectra create the duplicate output slug {output_slug!r}."
            )
        labels.add(normalized_label)
        slugs.add(output_slug)


def resolve_spectra(
    cfg: dict[str, Any],
    input_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover spectra, apply metadata filters, and merge explicit overrides."""
    discovery = cfg["discovery"]
    explicit = [copy.deepcopy(item) for item in cfg["spectra"]]
    resolved: list[dict[str, Any]] = []
    filtered_out: list[str] = []
    skipped_noncanonical: list[str] = []
    rejected: dict[str, tuple[str, str]] = {}
    selection_sources: dict[str, str] = {}
    matched_files = 0

    def identity(value: str | Path) -> str:
        return os.path.normcase(str(_resolve(input_root, value).resolve()))

    if discovery["enabled"]:
        if not input_root.is_dir():
            raise FileNotFoundError(f"Analysis input directory not found: {input_root}")
        pattern = str(discovery["file_glob"])
        iterator = (
            input_root.rglob(pattern)
            if discovery["recursive"]
            else input_root.glob(pattern)
        )
        paths = sorted(
            (path for path in iterator if path.is_file()),
            key=lambda path: path.relative_to(input_root).as_posix().casefold(),
        )
        matched_files = len(paths)
        for path in paths:
            relative = path.relative_to(input_root).as_posix()
            metadata = naming.parse_filename_metadata(path)
            if (
                discovery["canonical_filenames_only"]
                and not metadata["canonical_filename"]
            ):
                skipped_noncanonical.append(relative)
                rejected[identity(path)] = (relative, "noncanonical")
                continue
            if not all(
                (
                    _filter_value(
                        metadata.get("column"),
                        discovery["include"]["columns"],
                        discovery["exclude"]["columns"],
                    ),
                    _filter_value(
                        metadata.get("row"),
                        discovery["include"]["rows"],
                        discovery["exclude"]["rows"],
                    ),
                    _filter_value(
                        metadata.get("sample_type"),
                        discovery["include"]["sample_types"],
                        discovery["exclude"]["sample_types"],
                    ),
                )
            ):
                filtered_out.append(relative)
                rejected[identity(path)] = (relative, "filtered")
                continue
            resolved.append(
                {
                    "file": relative,
                    "include": True,
                    "include_in_overlay": discovery["include_in_overlay"],
                    "include_in_groups": discovery["include_in_groups"],
                }
            )
            selection_sources[identity(path)] = "discovered"

    positions = {
        identity(item["file"]): index
        for index, item in enumerate(resolved)
    }
    explicit_identities: set[str] = set()
    explicitly_reincluded: list[str] = []
    explicitly_excluded: list[str] = []
    for item in explicit:
        key = identity(item["file"])
        if key in explicit_identities:
            raise RuntimeError(
                "Duplicate explicit spectrum paths resolve to the same file: "
                f"{item['file']}"
            )
        explicit_identities.add(key)
        if key in positions:
            discovered_file = resolved[positions[key]]["file"]
            resolved[positions[key]].update(
                {name: value for name, value in item.items() if name != "file"}
            )
            resolved[positions[key]]["file"] = discovered_file
            selection_sources[key] = "discovered+override"
        else:
            positions[key] = len(resolved)
            resolved.append(item)
            if key in rejected and item.get("include", True):
                relative, reason = rejected[key]
                explicitly_reincluded.append(relative)
                if reason == "filtered":
                    filtered_out.remove(relative)
                else:
                    skipped_noncanonical.remove(relative)
                selection_sources[key] = "explicit-reinclude"
            else:
                selection_sources[key] = "explicit"
        if not item.get("include", True):
            explicitly_excluded.append(str(resolved[positions[key]]["file"]))

    _validate_resolved_spectra(resolved)
    selected = []
    for item in resolved:
        if not item.get("include", True):
            continue
        selected.append(
            {
                "file": item["file"],
                "source": selection_sources[identity(item["file"])],
            }
        )
    report = {
        "enabled": discovery["enabled"],
        "file_glob": discovery["file_glob"],
        "matched_files": matched_files,
        "selected_files": len(selected),
        "selected": selected,
        "filtered_out": filtered_out,
        "skipped_noncanonical": skipped_noncanonical,
        "explicit_entries": len(explicit),
        "explicitly_reincluded": explicitly_reincluded,
        "explicitly_excluded": explicitly_excluded,
    }
    return resolved, report


def _invalid_metrics(target: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    return {
        "target_name": target["name"],
        "expected_position_cm1": float(target["expected_position_cm1"]),
        "candidate_found": False,
        "detected_position_cm1": None,
        "peak_shift_cm1": None,
        "detected_index": None,
        "raw_peak_intensity": None,
        "baseline_intensity_at_peak": None,
        "baseline_corrected_peak_height": None,
        "local_peak_area": None,
        "local_noise": None,
        "signal_to_noise_ratio": None,
        "peak_prominence": None,
        "fwhm_cm1": None,
        "width_at_half_prominence_cm1": None,
        "peak_valid": False,
        "warnings": warnings,
    }


def _noise_estimate(
    x: np.ndarray,
    signal: np.ndarray,
    noise_cfg: dict[str, Any],
) -> tuple[float | None, dict[str, Any], list[str]]:
    """Estimate noise with target-local, detrended sidebands by default."""
    warnings: list[str] = []
    method = noise_cfg["method"]
    if method == "sidebands":
        residuals = []
        points = 0
        used_bounds = []
        sideband_stats = []
        for lower, upper in noise_cfg["sidebands_cm1"]:
            mask = (x >= float(lower)) & (x <= float(upper))
            if int(mask.sum()) < 3:
                warnings.append(
                    f"Noise sideband [{lower}, {upper}] contains fewer than three points."
                )
                continue
            coefficients = np.polyfit(x[mask], signal[mask], 1)
            residuals.append(signal[mask] - np.polyval(coefficients, x[mask]))
            points += int(mask.sum())
            used_bounds.append([float(lower), float(upper)])
            sideband_stats.append(
                {
                    "bounds_cm1": [float(lower), float(upper)],
                    "points": int(mask.sum()),
                    "median_corrected_intensity": float(np.median(signal[mask])),
                    "linear_slope_intensity_per_cm1": float(coefficients[0]),
                }
            )
        if points < int(noise_cfg["minimum_points"]) or not residuals:
            warnings.append("Too few valid sideband points for a local noise estimate.")
            return None, {
                "method": method,
                "bounds_cm1": used_bounds,
                "points": points,
                "sideband_stats": sideband_stats,
            }, warnings
        values = np.concatenate(residuals)
        median = float(np.median(values))
        sigma = float(1.4826 * np.median(np.abs(values - median)))
        if not np.isfinite(sigma) or sigma <= 0:
            warnings.append("Local sideband noise estimate is zero or non-finite.")
            return None, {
                "method": method,
                "bounds_cm1": used_bounds,
                "points": points,
            }, warnings
        return sigma, {
            "method": method,
            "bounds_cm1": used_bounds,
            "points": points,
            "detrending": "independent_linear",
            "estimator": "MAD_sigma",
            "sideband_stats": sideband_stats,
        }, warnings
    if method == "region":
        args = {"method": "region", "region": noise_cfg["region_cm1"]}
    else:
        args = {"method": "mad_derivative"}
    sigma = float(preprocessing.estimate_noise(x, signal, args))
    return sigma, {
        "method": method,
        "bounds_cm1": noise_cfg.get("region_cm1"),
        "points": int(x.size),
    }, warnings


def characterize_target_peak(
    x: np.ndarray,
    raw: np.ndarray,
    baseline: np.ndarray,
    corrected: np.ndarray,
    target: dict[str, Any],
    noise_cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Detect and conservatively characterize the strongest local target peak."""
    warnings: list[str] = []
    search_min, search_max = map(float, target["search_window_cm1"])
    search_mask = (x >= search_min) & (x <= search_max)
    if int(search_mask.sum()) < 5:
        warnings.append("Target search window contains fewer than five data points.")
        return _invalid_metrics(target, warnings), warnings

    global_indices = np.flatnonzero(search_mask)
    local_signal = corrected[search_mask]
    local_peaks, _ = find_peaks(local_signal)
    if local_peaks.size == 0:
        warnings.append("No interior local maximum was found in the target search window.")
        return _invalid_metrics(target, warnings), warnings

    best_local = int(local_peaks[np.argmax(local_signal[local_peaks])])
    peak_index = int(global_indices[best_local])
    height = float(corrected[peak_index])
    prominence_values, left_bases, right_bases = peak_prominences(
        local_signal,
        [best_local],
    )
    prominence = float(prominence_values[0])
    left_base_index = int(left_bases[0])
    right_base_index = int(right_bases[0])
    bases_inside = left_base_index > 0 and right_base_index < local_signal.size - 1

    noise, noise_details, noise_warnings = _noise_estimate(
        x,
        raw - baseline,
        noise_cfg,
    )
    warnings.extend(noise_warnings)
    snr = float(height / noise) if noise is not None and noise > 0 else math.nan

    fwhm: float | None = None
    width_points: float | None = None
    left_crossing: float | None = None
    right_crossing: float | None = None
    evaluation_height: float | None = None
    try:
        widths, width_heights, left_ips, right_ips = peak_widths(
            local_signal,
            [best_local],
            rel_height=0.5,
        )
        width_points = float(widths[0])
        minimum_width_points = int(target["minimum_points_for_width"])
        crossings_inside = left_ips[0] > 0 and right_ips[0] < local_signal.size - 1
        if width_points >= minimum_width_points and crossings_inside:
            sample_indices = np.arange(local_signal.size, dtype=float)
            left_crossing = float(np.interp(left_ips[0], sample_indices, x[search_mask]))
            right_crossing = float(np.interp(right_ips[0], sample_indices, x[search_mask]))
            fwhm = right_crossing - left_crossing
            evaluation_height = float(width_heights[0])
        else:
            warnings.append(
                "Half-prominence width omitted because it is under-resolved "
                "or crosses the search-window boundary."
            )
    except (ValueError, IndexError):
        warnings.append(
            "Half-prominence width could not be measured reliably and was omitted."
        )

    valid = True
    if not np.isfinite(height) or height <= 0:
        valid = False
        warnings.append("The baseline-corrected target peak height is not positive.")
    minimum_snr = target.get("minimum_snr")
    if minimum_snr is not None and (not np.isfinite(snr) or snr < float(minimum_snr)):
        valid = False
        warnings.append(
            f"Target peak SNR {snr:.3g} is below the configured minimum "
            f"of {float(minimum_snr):g}."
        )
    minimum_prominence = target.get("minimum_prominence")
    if minimum_prominence is not None and prominence < float(minimum_prominence):
        valid = False
        warnings.append(
            f"Target peak prominence {prominence:.3g} is below the configured "
            f"minimum of {float(minimum_prominence):g}."
        )
    prominence_to_noise = (
        float(prominence / noise)
        if noise is not None and noise > 0
        else math.nan
    )
    minimum_prominence_to_noise = target.get("minimum_prominence_to_noise")
    if (
        minimum_prominence_to_noise is not None
        and (
            not np.isfinite(prominence_to_noise)
            or prominence_to_noise < float(minimum_prominence_to_noise)
        )
    ):
        valid = False
        warnings.append(
            "Target peak prominence-to-noise ratio "
            f"{prominence_to_noise:.3g} is below the configured minimum of "
            f"{float(minimum_prominence_to_noise):g}."
        )
    if not bases_inside:
        warnings.append(
            "Peak prominence bases touch the search-window boundary; "
            "width crossings remain the validation boundary guard."
        )
    if target.get("require_resolved_width", True) and fwhm is None:
        valid = False
        warnings.append("A resolved peak width is required for validation.")
    if fwhm is not None:
        if (
            target.get("minimum_width_cm1") is not None
            and fwhm < float(target["minimum_width_cm1"])
        ):
            valid = False
            warnings.append("Candidate width is below the configured physical minimum.")
        if (
            target.get("maximum_width_cm1") is not None
            and fwhm > float(target["maximum_width_cm1"])
        ):
            valid = False
            warnings.append("Candidate width is above the configured physical maximum.")

    detected = float(x[peak_index])
    shift = detected - float(target["expected_position_cm1"])
    if (
        target.get("maximum_shift_cm1") is not None
        and abs(shift) > float(target["maximum_shift_cm1"])
    ):
        valid = False
        warnings.append("Candidate shift exceeds target_peak.maximum_shift_cm1.")

    integration_min, integration_max = map(float, target["integration_window_cm1"])
    integration_mask = (x >= integration_min) & (x <= integration_max)
    area: float | None = None
    if not integration_min <= detected <= integration_max:
        warnings.append("Detected candidate lies outside the integration window; area omitted.")
    elif int(integration_mask.sum()) < 3:
        warnings.append("Integration window contains fewer than three points; area omitted.")
    elif valid:
        area = float(_trapz(corrected[integration_mask], x[integration_mask]))

    metrics = {
        "target_name": target["name"],
        "expected_position_cm1": float(target["expected_position_cm1"]),
        "candidate_found": True,
        "detected_position_cm1": detected,
        "peak_shift_cm1": shift,
        "detected_index": peak_index,
        "raw_peak_intensity": float(raw[peak_index]),
        "baseline_intensity_at_peak": float(baseline[peak_index]),
        "baseline_corrected_peak_height": height,
        "local_peak_area": area,
        "integration_min_cm1": integration_min,
        "integration_max_cm1": integration_max,
        "integration_points": int(integration_mask.sum()),
        "local_noise": noise if noise is not None and np.isfinite(noise) else None,
        "noise_details": noise_details,
        "signal_to_noise_ratio": snr if np.isfinite(snr) else None,
        "peak_prominence": prominence,
        "prominence_to_noise_ratio": (
            prominence_to_noise if np.isfinite(prominence_to_noise) else None
        ),
        "prominence_left_base_cm1": float(x[search_mask][left_base_index]),
        "prominence_right_base_cm1": float(x[search_mask][right_base_index]),
        "width_at_half_prominence_cm1": fwhm,
        "width_samples": width_points,
        "width_evaluation_intensity": evaluation_height,
        "width_left_crossing_cm1": left_crossing,
        "width_right_crossing_cm1": right_crossing,
        "fwhm_cm1": None,
        "peak_valid": valid,
        "warnings": warnings.copy(),
    }
    return metrics, warnings


def normalize_spectrum(
    x: np.ndarray,
    corrected: np.ndarray,
    metrics: dict[str, Any],
    normalization: dict[str, Any],
) -> tuple[np.ndarray, float | None, bool, list[str]]:
    """Normalize baseline-corrected data using the configured reproducible rule."""
    method = normalization["method"]
    target_value = float(normalization["target_value"])
    warnings: list[str] = []
    if method == "none":
        return corrected.copy(), 1.0, True, warnings

    denominator: float | None
    if method == "target_peak":
        denominator = metrics.get("baseline_corrected_peak_height")
        if not metrics.get("peak_valid") or denominator is None or denominator <= 0:
            warning = (
                "Target-peak normalization was not applied because the target "
                "peak failed validation."
            )
            warnings.append(warning)
            if normalization.get("allow_invalid_target", False):
                warnings.append(
                    "allow_invalid_target is true; scaled_intensity contains the "
                    "unscaled corrected signal."
                )
                return corrected.copy(), 1.0, False, warnings
            return np.full_like(corrected, np.nan), None, False, warnings
    elif method == "global_max":
        denominator = float(np.nanmax(corrected))
    elif method == "vector_norm":
        denominator = float(np.linalg.norm(corrected))
    else:  # area
        denominator = float(_trapz(np.abs(corrected), x))

    if denominator is None or not np.isfinite(denominator) or denominator <= 0:
        warnings.append(
            f"{method} normalization denominator is invalid; scaled data were omitted."
        )
        return np.full_like(corrected, np.nan), None, False, warnings
    scale = denominator / target_value
    return corrected / scale, scale, True, warnings


def process_spectrum(
    path: Path,
    spec: dict[str, Any],
    cfg: dict[str, Any],
) -> SpectrumResult:
    """Run the one shared processing path used by every output and overlay."""
    loader_cfg = {**cfg["csv"], **cfg["preprocessing"]}
    x, raw, load_report = io_utils.load_spectrum(
        path,
        loader_cfg,
        return_report=True,
    )
    spacing = np.diff(x)
    median_spacing = float(np.median(spacing))
    relative_deviation = float(
        np.max(np.abs(spacing - median_spacing)) / max(abs(median_spacing), 1.0e-12)
    )
    sampling_report = {
        "median_spacing_cm1": median_spacing,
        "maximum_relative_spacing_deviation": relative_deviation,
        "uniform_spacing_required": cfg["sampling"]["require_uniform_spacing"],
    }
    if (
        cfg["sampling"]["require_uniform_spacing"]
        and relative_deviation
        > float(cfg["sampling"]["maximum_relative_spacing_deviation"])
    ):
        raise ValueError(
            f"Spectrum {path} has irregular Raman-shift spacing "
            f"(relative deviation {relative_deviation:.3g}) above the configured "
            "limit."
        )
    smoothing_cfg = cfg["smoothing"]
    if smoothing_cfg["enabled"] and smoothing_cfg["method"] != "none":
        smoothed = preprocessing.apply_smoothing(
            raw,
            {
                "method": "savgol",
                "savgol": {
                    "window": smoothing_cfg["window_length"],
                    "polyorder": smoothing_cfg["polynomial_order"],
                },
            },
        )
    else:
        smoothed = raw.copy()
    sampling_report["smoothing_span_cm1"] = (
        float(smoothing_cfg["window_length"]) * median_spacing
        if smoothing_cfg["enabled"] and smoothing_cfg["method"] != "none"
        else 0.0
    )

    baseline_cfg = cfg["baseline"]
    if not baseline_cfg["enabled"] or baseline_cfg["method"] == "none":
        baseline_args = {"method": "none"}
    elif baseline_cfg["method"] == "arpls":
        baseline_args = {
            "method": "arpls",
            "arpls": {
                "lam": baseline_cfg["lambda"],
                "ratio": baseline_cfg["convergence_ratio"],
                "niter": baseline_cfg["iterations"],
            },
        }
    else:
        baseline_args = {
            "method": "als",
            "als": {
                "lam": baseline_cfg["lambda"],
                "p": baseline_cfg["asymmetry"],
                "niter": baseline_cfg["iterations"],
            },
        }
    if baseline_args["method"] == "arpls":
        parameters = baseline_args["arpls"]
        baseline, baseline_report = preprocessing.baseline_arpls_with_diagnostics(
            smoothed,
            lam=parameters["lam"],
            ratio=parameters["ratio"],
            niter=parameters["niter"],
        )
    else:
        baseline = preprocessing.apply_baseline(x, smoothed, baseline_args)
        baseline_report = {
            "method": baseline_args["method"],
            "converged": None,
            "baseline_finite": bool(np.all(np.isfinite(baseline))),
        }
    corrected = smoothed - baseline
    metrics, warnings = characterize_target_peak(
        x,
        raw,
        baseline,
        corrected,
        cfg["target_peak"],
        cfg["noise"],
    )
    noise_details = metrics.get("noise_details") or {}
    noise_value = metrics.get("local_noise")
    baseline_report["sideband_quality"] = noise_details.get("sideband_stats", [])
    if noise_value:
        for sideband in baseline_report["sideband_quality"]:
            lower, upper = sideband["bounds_cm1"]
            median_ratio = abs(sideband["median_corrected_intensity"]) / noise_value
            slope_ratio = (
                abs(sideband["linear_slope_intensity_per_cm1"])
                * (upper - lower)
                / noise_value
            )
            sideband["median_to_noise_ratio"] = float(median_ratio)
            sideband["slope_span_to_noise_ratio"] = float(slope_ratio)
            if median_ratio > 3.0 or slope_ratio > 3.0:
                warnings.append(
                    f"Baseline quality warning in sideband [{lower}, {upper}]: "
                    "residual level or slope exceeds 3x local noise."
                )
    if baseline_report.get("converged") is False:
        warnings.append("arPLS reached its iteration limit before convergence.")
    if not baseline_report.get("baseline_finite", True):
        warnings.append("Estimated baseline contains non-finite values.")
    scaled, scale, normalization_valid, norm_warnings = normalize_spectrum(
        x,
        corrected,
        metrics,
        cfg["normalization"],
    )
    warnings.extend(norm_warnings)
    metrics["warnings"] = warnings.copy()
    search_min, search_max = cfg["target_peak"]["search_window_cm1"]
    target_region = (x >= float(search_min)) & (x <= float(search_max))
    return SpectrumResult(
        source=path,
        spec=spec,
        metadata=naming.spectrum_metadata(path, spec),
        x=x,
        raw=raw,
        smoothed=smoothed,
        baseline=baseline,
        corrected=corrected,
        scaled=scaled,
        target_region=target_region,
        metrics=metrics,
        warnings=warnings,
        normalization_method=cfg["normalization"]["method"],
        normalization_valid=normalization_valid,
        normalization_scale=scale,
        load_report=load_report,
        sampling_report=sampling_report,
        baseline_report=baseline_report,
    )


def _processed_frame(result: SpectrumResult) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "raman_shift_cm1": result.x,
            "raw_intensity": result.raw,
            "smoothed_intensity": result.smoothed,
            "estimated_baseline": result.baseline,
            "baseline_corrected_intensity": result.corrected,
            "scaled_intensity": result.scaled,
            "in_target_region": result.target_region,
        }
    )


def _metric_row(result: SpectrumResult) -> dict[str, Any]:
    row = {
        "source_file": result.source.name,
        "label": result.metadata["label"],
        "sequence": result.metadata.get("sequence"),
        "sample_type": result.metadata.get("sample_type"),
        "column": result.metadata.get("column"),
        "row": result.metadata.get("row"),
        "spot": result.metadata.get("spot"),
        "normalization_method": result.normalization_method,
        "normalization_valid": result.normalization_valid,
        "normalization_scale": result.normalization_scale,
    }
    row.update(result.metrics)
    row["warnings"] = " | ".join(result.warnings)
    return row


def _copy_collection(paths: list[Path], destinations: list[Path]) -> None:
    for source in paths:
        for destination_dir in destinations:
            destination_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination_dir / source.name)


class _BlankFormat(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _configured_title(
    result: SpectrumResult,
    template: str | None,
) -> str:
    """Format an optional YAML title template from filename/YAML metadata."""
    if not template:
        return result.title
    values = _BlankFormat(
        {
            "label": str(result.metadata.get("label") or ""),
            "sample_type": str(result.metadata.get("sample_type") or ""),
            "column": str(result.metadata.get("column") or ""),
            "row": str(result.metadata.get("row") or ""),
            "spot": str(result.metadata.get("spot") or ""),
            "source_file": result.source.name,
        }
    )
    try:
        return template.format_map(values)
    except ValueError as exc:
        raise ValueError(f"Invalid plot title_template {template!r}: {exc}") from exc


def _normalization_plot_descriptor(
    normalization: dict[str, Any],
) -> dict[str, str]:
    """Return scientifically explicit labels for one normalization method."""
    method = str(normalization["method"])
    target_value = float(normalization["target_value"])
    method_label = {
        "target_peak": "target-peak",
        "global_max": "global-maximum",
        "vector_norm": "vector-norm",
        "area": "absolute-area",
    }.get(method, method.replace("_", "-"))
    detail = f"{method_label}; target={target_value:g}"
    return {
        "title_suffix": f"normalized ({detail})",
        "ylabel": f"Normalized intensity ({detail})",
        "series_label": f"{method_label} normalized spectrum",
        "method_label": method_label,
    }


def _write_spectrum_outputs(
    result: SpectrumResult,
    run_dir: Path,
    cfg: dict[str, Any],
) -> list[str]:
    generated: list[str] = []
    if cfg["outputs"]["save_processed_csv"]:
        output = run_dir / "processed" / f"{result.slug}_processed.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        _processed_frame(result).to_csv(output, index=False)
        generated.append(str(output.relative_to(run_dir)))

    output_cfg = cfg["plots"]["output"]
    spectrum_root = run_dir / "plots" / "by_spectrum" / result.slug
    copy_collections = cfg["outputs"]["copy_plots_to_collections"]
    target_slug = naming.slug(cfg["target_peak"]["name"])

    individual = cfg["plots"]["individual"]
    if individual["enabled"]:
        individual_title = _configured_title(
            result,
            individual.get("title_template"),
        )
        target_plots: list[dict[str, Any]] = []
        if individual["baseline_corrected_enabled"]:
            target_plots.append(
                {
                    "kind": "baseline_corrected",
                    "intensity": result.corrected,
                    "title": (
                        f"{individual_title} — baseline-corrected "
                        "(not normalized)"
                    ),
                    "ylabel": "Baseline-corrected intensity (a.u.)",
                    "series_label": "baseline-corrected spectrum",
                    "y_range": individual.get("baseline_corrected_y_range"),
                    "invalid_target_message": (
                        "Target peak not validated"
                        if not result.metrics.get("peak_valid")
                        else None
                    ),
                }
            )
        normalization_method = result.normalization_method
        normalized_ready = (
            normalization_method != "none"
            and result.normalization_valid
            and np.any(np.isfinite(result.scaled))
            and (
                normalization_method != "target_peak"
                or result.metrics.get("peak_valid")
            )
        )
        if (
            individual["normalized_enabled"]
            and normalized_ready
        ):
            descriptor = _normalization_plot_descriptor(cfg["normalization"])
            target_plots.append(
                {
                    "kind": "normalized",
                    "intensity": result.scaled,
                    "title": (
                        f"{individual_title} — {descriptor['title_suffix']}"
                    ),
                    "ylabel": descriptor["ylabel"],
                    "series_label": descriptor["series_label"],
                    "y_range": individual.get("normalized_y_range"),
                    "invalid_target_message": (
                        "Target peak not validated; "
                        f"{descriptor['method_label']} normalization applied"
                        if not result.metrics.get("peak_valid")
                        else None
                    ),
                }
            )
        for target_plot in target_plots:
            plot_kind = target_plot["kind"]
            plot_cfg = {
                **individual,
                "y_range": (
                    target_plot["y_range"]
                    if target_plot["y_range"] is not None
                    else individual.get("y_range")
                ),
            }
            paths = plotting.plot_target_spectrum(
                result.x,
                target_plot["intensity"],
                result.metrics,
                title=target_plot["title"],
                ylabel=target_plot["ylabel"],
                series_label=target_plot["series_label"],
                invalid_target_message=target_plot["invalid_target_message"],
                plot_cfg=plot_cfg,
                output_cfg=output_cfg,
                base_path=(
                    spectrum_root
                    / "individual"
                    / plot_kind
                    / f"{result.slug}_target_{plot_kind}"
                ),
            )
            generated.extend(str(path.relative_to(run_dir)) for path in paths)
            if copy_collections:
                _copy_collection(
                    paths,
                    [
                        run_dir / "plots" / "by_type" / "individual" / plot_kind,
                        run_dir / "plots" / "by_peak" / target_slug / plot_kind,
                    ],
                )

    diagnostics = cfg["plots"]["diagnostics"]
    diagnostic_title = _configured_title(
        result,
        diagnostics.get("title_template"),
    )
    if diagnostics["enabled"] and diagnostics["baseline_plot"]:
        paths = plotting.plot_baseline_diagnostic(
            result.x,
            result.raw,
            result.baseline,
            result.corrected,
            title=diagnostic_title,
            output_cfg=output_cfg,
            base_path=spectrum_root / "diagnostics" / f"{result.slug}_baseline",
        )
        generated.extend(str(path.relative_to(run_dir)) for path in paths)
        if copy_collections:
            _copy_collection(paths, [run_dir / "plots" / "by_type" / "diagnostics"])
    if diagnostics["enabled"] and diagnostics["target_region_baseline_plot"]:
        paths = plotting.plot_baseline_diagnostic(
            result.x,
            result.raw,
            result.baseline,
            result.corrected,
            title=f"{diagnostic_title} — target region",
            output_cfg=output_cfg,
            base_path=(
                spectrum_root
                / "diagnostics"
                / f"{result.slug}_target_region_baseline"
            ),
            x_range=cfg["plots"]["individual"]["x_range_cm1"],
        )
        generated.extend(str(path.relative_to(run_dir)) for path in paths)
        if copy_collections:
            _copy_collection(paths, [run_dir / "plots" / "by_type" / "diagnostics"])
    if diagnostics["enabled"] and diagnostics["full_spectrum"]:
        paths = plotting.plot_full_spectrum(
            result.x,
            result.corrected,
            title=diagnostic_title,
            output_cfg=output_cfg,
            base_path=spectrum_root / "diagnostics" / f"{result.slug}_full_spectrum",
        )
        generated.extend(str(path.relative_to(run_dir)) for path in paths)
        if copy_collections:
            _copy_collection(paths, [run_dir / "plots" / "by_type" / "full_spectrum"])
    return generated


def _overlay_series(results: list[SpectrumResult]) -> list[dict[str, Any]]:
    return [
        {
            "x": result.x,
            "intensity": result.scaled,
            "label": result.metadata["label"],
            "detected_position_cm1": result.metrics.get("detected_position_cm1"),
            "detected_index": result.metrics.get("detected_index"),
        }
        for result in results
        if result.normalization_valid and np.any(np.isfinite(result.scaled))
    ]


def _write_overlay_data(results: list[SpectrumResult], path: Path) -> Path:
    frames = []
    for result in results:
        frames.append(
            pd.DataFrame(
                {
                    "label": result.metadata["label"],
                    "source_file": result.source.name,
                    "sample_type": result.metadata.get("sample_type"),
                    "column": result.metadata.get("column"),
                    "row": result.metadata.get("row"),
                    "raman_shift_cm1": result.x,
                    "scaled_intensity": result.scaled,
                }
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return path


def _matches(value: Any, selected: list[Any] | None) -> bool:
    if not selected:
        return True
    normalized = "" if value is None else str(value).strip().casefold()
    return normalized in {
        str(item).strip().casefold()
        for item in selected
    }


def _group_partitions(
    results: list[SpectrumResult],
    selection: dict[str, Any],
) -> dict[str, list[SpectrumResult]]:
    filtered = [
        result
        for result in results
        if result.spec.get("include_in_groups", False)
        and _matches(result.metadata.get("column"), selection.get("columns"))
        and _matches(result.metadata.get("row"), selection.get("rows"))
        and _matches(result.metadata.get("sample_type"), selection.get("sample_types"))
    ]
    group_by = selection.get("group_by", "none")
    if group_by == "none":
        return {selection["name"]: filtered}
    selected_field = {
        "column": "columns",
        "row": "rows",
        "sample_type": "sample_types",
    }[group_by]
    configured_values = selection.get(selected_field) or []
    configured_display = {
        str(item).strip().casefold(): str(item)
        for item in configured_values
    }
    normalized_partitions: dict[str, list[SpectrumResult]] = {}
    display_values: dict[str, str] = {}
    for result in filtered:
        value = result.metadata.get(group_by)
        if value is None:
            continue
        normalized = str(value).strip().casefold()
        display_values.setdefault(
            normalized,
            configured_display.get(normalized, str(value)),
        )
        normalized_partitions.setdefault(normalized, []).append(result)
    partitions: dict[str, list[SpectrumResult]] = {}
    for normalized, members in normalized_partitions.items():
        display = display_values[normalized]
        key = f"{selection['name']}__{group_by}_{display}"
        partitions[key] = members
    return partitions


def _create_batch_plots(
    results: list[SpectrumResult],
    run_dir: Path,
    cfg: dict[str, Any],
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[dict[str, str]] = []
    expected = float(cfg["target_peak"]["expected_position_cm1"])
    output_cfg = cfg["plots"]["output"]
    normalization_method = cfg["normalization"]["method"]
    overlay_ylabel = (
        f"Intensity normalized to validated {expected:g} cm$^{{-1}}$ peak "
        f"(target = {float(cfg['normalization']['target_value']):g})"
        if normalization_method == "target_peak"
        else (
            "Baseline-corrected intensity (a.u.)"
            if normalization_method == "none"
            else f"Normalized intensity ({normalization_method})"
        )
    )

    overlay_cfg = cfg["plots"]["overlay"]
    if overlay_cfg["enabled"]:
        selected = [
            result for result in results if result.spec.get("include_in_overlay", False)
        ]
        valid = [result for result in selected if result.normalization_valid]
        if len(valid) < 2:
            message = (
                "Overlay skipped: fewer than two selected spectra had valid normalized data."
            )
            warnings.append(message)
            failures.append({"name": "selected_spectra", "message": message})
            logger.warning(message)
        else:
            base = run_dir / "plots" / "overlay" / "selected_spectra"
            paths = plotting.plot_processed_overlay(
                _overlay_series(valid),
                expected,
                title=overlay_cfg["title"],
                plot_cfg=overlay_cfg,
                output_cfg=output_cfg,
                base_path=base,
                ylabel=overlay_ylabel,
            )
            data_path = None
            if cfg["outputs"]["save_overlay_data"]:
                data_path = _write_overlay_data(
                    valid,
                    run_dir / "overlay_data" / "selected_spectra.csv",
                )
            records.append(
                {
                    "name": "selected_spectra",
                    "kind": "overlay",
                    "spectra": [item.metadata["label"] for item in valid],
                    "plots": [str(path.relative_to(run_dir)) for path in paths],
                    "data": str(data_path.relative_to(run_dir)) if data_path else None,
                }
            )

    groups_cfg = cfg["plots"]["groups"]
    if groups_cfg["enabled"]:
        for selection in groups_cfg["selections"]:
            for group_name, members in _group_partitions(results, selection).items():
                valid = [item for item in members if item.normalization_valid]
                if len(valid) < 2:
                    message = (
                        f"Group {group_name!r} skipped: fewer than two matching "
                        "spectra had valid normalized data."
                    )
                    warnings.append(message)
                    failures.append({"name": group_name, "message": message})
                    logger.warning(message)
                    continue
                slug = naming.slug(group_name)
                plot_cfg = {**groups_cfg, **selection}
                title = str(selection.get("title") or group_name)
                if selection.get("group_by", "none") != "none":
                    partition = group_name.split("__")[-1].replace("_", " ")
                    title = f"{title} — {partition}"
                paths = plotting.plot_processed_overlay(
                    _overlay_series(valid),
                    expected,
                    title=title,
                    plot_cfg=plot_cfg,
                    output_cfg=output_cfg,
                    base_path=run_dir / "plots" / "groups" / slug,
                    ylabel=overlay_ylabel,
                )
                data_path = None
                if cfg["outputs"]["save_overlay_data"]:
                    data_path = _write_overlay_data(
                        valid,
                        run_dir / "overlay_data" / f"{slug}.csv",
                    )
                records.append(
                    {
                        "name": group_name,
                        "kind": "group",
                        "spectra": [item.metadata["label"] for item in valid],
                        "plots": [str(path.relative_to(run_dir)) for path in paths],
                        "data": str(data_path.relative_to(run_dir)) if data_path else None,
                    }
                )
    return records, warnings, failures


def _run_dir(output_root: Path, analysis: dict[str, Any]) -> Path:
    name = naming.slug(analysis["name"])
    if analysis["timestamped_run_directory"]:
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
    candidate = output_root / name
    suffix = 2
    while candidate.exists():
        candidate = output_root / f"{name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _set_consistent_individual_ranges(
    results: list[SpectrumResult],
    cfg: dict[str, Any],
) -> None:
    """Resolve separate common y-ranges for corrected and normalized plots."""
    individual = cfg["plots"]["individual"]
    if (
        not individual.get("consistent_y_range", False)
        or individual.get("y_range") is not None
    ):
        return
    x_min, x_max = map(float, individual["x_range_cm1"])

    def combined_range(series: list[tuple[np.ndarray, np.ndarray]]) -> list[float] | None:
        values = []
        for x, intensity in series:
            mask = (x >= x_min) & (x <= x_max)
            visible = intensity[mask]
            values.append(visible[np.isfinite(visible)])
        values = [value for value in values if value.size]
        if not values:
            return None
        combined = np.concatenate(values)
        low = float(np.min(combined))
        high = float(np.max(combined))
        padding = max((high - low) * 0.05, 1.0e-12)
        return [low - padding, high + padding]

    if (
        individual["baseline_corrected_enabled"]
        and individual.get("baseline_corrected_y_range") is None
    ):
        individual["baseline_corrected_y_range"] = combined_range(
            [(result.x, result.corrected) for result in results]
        )
    if (
        individual["normalized_enabled"]
        and cfg["normalization"]["method"] != "none"
        and individual.get("normalized_y_range") is None
    ):
        individual["normalized_y_range"] = combined_range(
            [
                (result.x, result.scaled)
                for result in results
                if result.normalization_valid
                and np.any(np.isfinite(result.scaled))
                and (
                    result.normalization_method != "target_peak"
                    or result.metrics.get("peak_valid")
                )
            ]
        )


def _logger(run_dir: Path) -> logging.Logger:
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"raman.analysis.{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_dir / "analysis.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(stream_handler)
    return logger


def run_analysis(cfg: dict[str, Any], raman_root: Path) -> tuple[Path, dict[str, Any]]:
    """Execute the configured analysis and return its run directory and summary."""
    cfg = copy.deepcopy(cfg)
    input_root = _resolve(raman_root, cfg["analysis"]["input_root"])
    cfg["spectra"], discovery_report = resolve_spectra(cfg, input_root)
    output_root = _resolve(raman_root, cfg["analysis"]["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = _run_dir(output_root, cfg["analysis"])
    logger = _logger(run_dir)

    source_config = Path(cfg["_source_path"])
    shutil.copy2(source_config, run_dir / "config_snapshot.yaml")
    logger.info("Analysis %s", cfg["analysis"]["name"])
    logger.info("Configuration: %s", source_config)
    logger.info("Input root: %s", input_root)
    logger.info(
        "File selection: %d matched, %d selected, %d filtered, "
        "%d noncanonical skipped",
        discovery_report["matched_files"],
        discovery_report["selected_files"],
        len(discovery_report["filtered_out"]),
        len(discovery_report["skipped_noncanonical"]),
    )
    logger.info("Output: %s", run_dir)

    results: list[SpectrumResult] = []
    failures: list[dict[str, str]] = []
    generated: dict[str, list[str]] = {}
    for spec in cfg["spectra"]:
        if not spec.get("include", True):
            continue
        path = _resolve(input_root, spec["file"])
        try:
            result = process_spectrum(path, spec, cfg)
            results.append(result)
            if result.warnings:
                logger.warning(
                    "%s: %s",
                    path.name,
                    " | ".join(result.warnings),
                )
            logger.info(
                "%s -> peak=%s cm^-1, valid=%s",
                path.name,
                result.metrics.get("detected_position_cm1"),
                result.metrics["peak_valid"],
            )
        except Exception as exc:
            logger.exception("Failed to process %s", path)
            failures.append(
                {
                    "file": str(path),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    if not results:
        raise RuntimeError(
            "No spectra were processed successfully. See logs/analysis.log in "
            f"{run_dir}."
        )

    _set_consistent_individual_ranges(results, cfg)
    for result in results:
        generated[result.metadata["label"]] = _write_spectrum_outputs(
            result,
            run_dir,
            cfg,
        )

    if cfg["outputs"]["save_peak_metrics_csv"]:
        pd.DataFrame([_metric_row(item) for item in results]).to_csv(
            run_dir / "peak_metrics.csv",
            index=False,
        )
    batch_plots, batch_warnings, batch_failures = _create_batch_plots(
        results,
        run_dir,
        cfg,
        logger,
    )

    summary = {
        "analysis_name": cfg["analysis"]["name"],
        "run_directory": str(run_dir),
        "started_from_config": str(source_config),
        "raman_shift_units": "cm^-1",
        "processing_order": [
            "load_and_validate",
            "sort_and_merge_duplicate_shifts",
            "optional_smoothing",
            "baseline_estimation",
            "baseline_subtraction",
            "target_peak_detection_and_metrics",
            "normalization",
            "plot_and_output_writing",
        ],
        "parameters": {
            key: value
            for key, value in cfg.items()
            if key not in {"_source_path", "spectra"}
        },
        "n_selected": sum(1 for item in cfg["spectra"] if item.get("include", True)),
        "n_processed": len(results),
        "n_failed": len(failures),
        "discovery": discovery_report,
        "failures": failures,
        "warnings": batch_warnings,
        "batch_failures": batch_failures,
        "run_status": (
            "success"
            if not failures and not batch_failures
            else "degraded"
        ),
        "spectra": [
            {
                "source_file": str(result.source),
                "metadata": result.metadata,
                "metrics": result.metrics,
                "load_report": result.load_report,
                "sampling_report": result.sampling_report,
                "baseline_report": result.baseline_report,
                "normalization_valid": result.normalization_valid,
                "normalization_scale": result.normalization_scale,
                "generated_outputs": generated[result.metadata["label"]],
            }
            for result in results
        ],
        "batch_plots": batch_plots,
    }
    if cfg["outputs"]["save_run_summary_json"]:
        with (run_dir / "run_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=_json_default)
    with (run_dir / "resolved_config.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                key: value
                for key, value in cfg.items()
                if key != "_source_path"
            },
            handle,
            sort_keys=False,
        )
    logger.info("Completed: %d processed, %d failed", len(results), len(failures))
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    return run_dir, summary
