"""Plotting: an overview spectrum plus optional per-peak zoom panels.

Uses the non-interactive Agg backend so it runs headless on any machine
(work laptop, CI, SSH) without a display.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def _found(peaks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in peaks if p.get("found") and "center_cm1" in p]


def plot_overview(x, spectrum: dict[str, np.ndarray], peaks, cfg,
                  out_path: Path, title: str) -> Path:
    pcfg = cfg["plotting"]
    fig, ax = plt.subplots(figsize=tuple(pcfg["figsize"]))

    if pcfg.get("show_raw", True) and "raw" in spectrum:
        ax.plot(x, spectrum["raw"], color="0.75", lw=0.8, label="raw", zorder=1)
    if pcfg.get("show_baseline", True) and "baseline" in spectrum:
        ax.plot(x, spectrum["baseline"], color="tab:orange", lw=1.0, ls="--",
                label="baseline", zorder=2)
    ax.plot(x, spectrum["smoothed"], color="tab:blue", lw=1.1,
            label="corrected (smoothed)", zorder=3)

    for p in _found(peaks):
        cx = p["center_cm1"]
        cy = p["height"]
        ax.axvline(cx, color="tab:red", lw=0.6, alpha=0.35, zorder=2)
        ax.plot([cx], [cy], "v", color="tab:red", ms=7, zorder=5)
        if pcfg.get("annotate", True):
            label = p.get("name", "")
            ax.annotate(f"{label}\n{cx:.1f}", (cx, cy),
                        textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=7, color="tab:red")

    # shade targeted search windows if present
    for p in peaks:
        if "search_min_cm1" in p and p.get("search_max_cm1") is not None:
            ax.axvspan(p["search_min_cm1"], p["search_max_cm1"],
                       color="tab:green", alpha=0.06, zorder=0)

    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Intensity (baseline-corrected)")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    ax.margins(x=0.01)
    fig.tight_layout()
    fig.savefig(out_path, dpi=pcfg["dpi"])
    plt.close(fig)
    return out_path


def plot_zoom(x, spectrum, peak: dict[str, Any], cfg, out_path: Path) -> Path | None:
    """Zoom panel around one targeted peak's search window."""
    lo = peak.get("search_min_cm1")
    hi = peak.get("search_max_cm1")
    if lo is None or hi is None:
        return None
    pad = (hi - lo) * 0.4
    mask = (x >= lo - pad) & (x <= hi + pad)
    if mask.sum() < 3:
        return None

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(x[mask], spectrum["smoothed"][mask], color="tab:blue", lw=1.2, label="corrected (smoothed)")
    ax.axvspan(lo, hi, color="tab:green", alpha=0.10, label="search window")
    if peak.get("target_center_cm1") is not None:
        ax.axvline(peak["target_center_cm1"], color="0.5", ls=":", lw=1, label="target")
    if peak.get("found") and "center_cm1" in peak:
        ax.plot([peak["center_cm1"]], [peak["height"]], "v", color="tab:red", ms=9)
        txt = [f"center {peak['center_cm1']:.1f} cm$^{{-1}}$"]
        if peak.get("fwhm_cm1") == peak.get("fwhm_cm1"):  # not NaN
            txt.append(f"FWHM {peak['fwhm_cm1']:.1f}")
        if peak.get("snr") == peak.get("snr"):
            txt.append(f"SNR {peak['snr']:.1f}")
        ax.annotate("\n".join(txt), (peak["center_cm1"], peak["height"]),
                    textcoords="offset points", xytext=(6, -4), fontsize=8, color="tab:red")

    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Intensity (corrected)")
    ax.set_title(f"{peak.get('name','peak')} zoom")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)
    return out_path


def _save_formats(
    fig: plt.Figure,
    base_path: Path,
    formats: list[str],
    dpi: int,
) -> list[Path]:
    """Save one figure in every configured format and close it."""
    base_path.parent.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for extension in formats:
        output = base_path.with_suffix(f".{extension}")
        fig.savefig(output, dpi=dpi, bbox_inches="tight")
        paths.append(output)
    plt.close(fig)
    return paths


def _ranges(ax: plt.Axes, x_range: list[float], y_range: list[float] | None) -> None:
    ax.set_xlim(float(x_range[0]), float(x_range[1]))
    if y_range is not None:
        ax.set_ylim(float(y_range[0]), float(y_range[1]))


def _visible_y_range(
    x: np.ndarray,
    series: list[np.ndarray],
    x_range: list[float],
) -> list[float] | None:
    mask = (x >= float(x_range[0])) & (x <= float(x_range[1]))
    values = [
        np.asarray(item)[mask]
        for item in series
        if np.asarray(item).shape == x.shape
    ]
    finite = [item[np.isfinite(item)] for item in values if np.any(np.isfinite(item))]
    if not finite:
        return None
    combined = np.concatenate(finite)
    lower = float(np.min(combined))
    upper = float(np.max(combined))
    padding = max((upper - lower) * 0.05, 1.0e-12)
    return [lower - padding, upper + padding]


def plot_target_spectrum(
    x: np.ndarray,
    y: np.ndarray,
    metrics: dict[str, Any],
    *,
    title: str,
    ylabel: str,
    plot_cfg: dict[str, Any],
    output_cfg: dict[str, Any],
    base_path: Path,
) -> list[Path]:
    """Plot one processed spectrum focused on the configured target region."""
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    ax.plot(x, y, color="tab:blue", lw=1.25, label="processed spectrum")
    if plot_cfg.get("mark_expected_peak", True):
        ax.axvline(
            metrics["expected_position_cm1"],
            color="0.45",
            ls=":",
            lw=1,
            label="expected peak",
        )
    detected = metrics.get("detected_position_cm1")
    if plot_cfg.get("mark_detected_peak", True) and detected is not None:
        index = metrics.get("detected_index")
        marker_y = float(y[index]) if index is not None and np.isfinite(y[index]) else None
        if marker_y is not None:
            if metrics.get("peak_valid"):
                ax.plot(
                    [detected],
                    [marker_y],
                    "v",
                    color="tab:green",
                    ms=8,
                    label="validated target peak",
                )
            else:
                ax.plot(
                    [detected],
                    [marker_y],
                    "x",
                    color="tab:red",
                    ms=8,
                    label="unvalidated candidate",
                )
                ax.text(
                    0.02,
                    0.97,
                    "Target not validated; spectrum not normalized",
                    transform=ax.transAxes,
                    va="top",
                    color="tab:red",
                    fontsize=8,
                )
    if plot_cfg.get("show_grid", True):
        ax.grid(True, alpha=0.2)
    y_range = plot_cfg.get("y_range")
    if y_range is None:
        y_range = _visible_y_range(x, [y], plot_cfg["x_range_cm1"])
    _ranges(ax, plot_cfg["x_range_cm1"], y_range)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return _save_formats(
        fig,
        base_path,
        output_cfg["formats"],
        int(output_cfg["dpi"]),
    )


def plot_baseline_diagnostic(
    x: np.ndarray,
    raw: np.ndarray,
    baseline: np.ndarray,
    corrected: np.ndarray,
    *,
    title: str,
    output_cfg: dict[str, Any],
    base_path: Path,
    x_range: list[float] | None = None,
) -> list[Path]:
    """Plot raw/baseline and baseline-corrected traces on separate axes."""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    top.plot(x, raw, color="0.35", lw=0.8, label="raw")
    top.plot(x, baseline, color="tab:orange", lw=1.1, label="estimated baseline")
    top.set_ylabel("Raw intensity (a.u.)")
    top.legend(loc="best", fontsize=8)
    top.grid(True, alpha=0.15)
    bottom.plot(x, corrected, color="tab:blue", lw=0.9, label="baseline-corrected")
    bottom.axhline(0.0, color="0.5", lw=0.7, ls=":")
    bottom.set_xlabel("Raman shift (cm$^{-1}$)")
    bottom.set_ylabel("Corrected intensity (a.u.)")
    bottom.legend(loc="best", fontsize=8)
    bottom.grid(True, alpha=0.15)
    if x_range is not None:
        bottom.set_xlim(float(x_range[0]), float(x_range[1]))
        top_limits = _visible_y_range(x, [raw, baseline], x_range)
        bottom_limits = _visible_y_range(x, [corrected], x_range)
        if top_limits is not None:
            top.set_ylim(*top_limits)
        if bottom_limits is not None:
            bottom.set_ylim(*bottom_limits)
    fig.suptitle(f"{title} — baseline diagnostic")
    fig.tight_layout()
    return _save_formats(
        fig,
        base_path,
        output_cfg["formats"],
        int(output_cfg["dpi"]),
    )


def plot_full_spectrum(
    x: np.ndarray,
    corrected: np.ndarray,
    *,
    title: str,
    output_cfg: dict[str, Any],
    base_path: Path,
) -> list[Path]:
    """Plot the complete baseline-corrected spectrum."""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x, corrected, color="tab:blue", lw=0.9)
    ax.axhline(0.0, color="0.5", lw=0.7, ls=":")
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Baseline-corrected intensity (a.u.)")
    ax.set_title(f"{title} — full spectrum")
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    return _save_formats(
        fig,
        base_path,
        output_cfg["formats"],
        int(output_cfg["dpi"]),
    )


def plot_processed_overlay(
    series: list[dict[str, Any]],
    expected_position_cm1: float,
    *,
    title: str,
    plot_cfg: dict[str, Any],
    output_cfg: dict[str, Any],
    base_path: Path,
    ylabel: str = "Normalized intensity",
) -> list[Path]:
    """Overlay identically processed spectra without offsets by default."""
    if len(series) < 2:
        raise ValueError("An overlay requires at least two valid spectra.")
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    offset = float(plot_cfg.get("vertical_offset", 0.0))
    plotted_series = []
    for index, item in enumerate(series):
        plotted = item["intensity"] + index * offset
        plotted_series.append((item["x"], plotted))
        ax.plot(item["x"], plotted, lw=1.1, label=item["label"])
        if plot_cfg.get("mark_detected_peaks", False):
            detected = item.get("detected_position_cm1")
            detected_index = item.get("detected_index")
            if detected is not None and detected_index is not None:
                ax.plot(
                    [detected],
                    [plotted[detected_index]],
                    "v",
                    ms=5,
                )
    if plot_cfg.get("mark_expected_peak", True):
        ax.axvline(
            expected_position_cm1,
            color="0.35",
            ls=":",
            lw=1,
            label="expected peak",
        )
    y_range = plot_cfg.get("y_range")
    if y_range is None:
        visible_values = []
        for item_x, item_y in plotted_series:
            mask = (
                (item_x >= float(plot_cfg["x_range_cm1"][0]))
                & (item_x <= float(plot_cfg["x_range_cm1"][1]))
            )
            visible_values.append(item_y[mask])
        finite = [
            values[np.isfinite(values)]
            for values in visible_values
            if np.any(np.isfinite(values))
        ]
        if finite:
            combined = np.concatenate(finite)
            lower = float(np.min(combined))
            upper = float(np.max(combined))
            padding = max((upper - lower) * 0.05, 1.0e-12)
            y_range = [lower - padding, upper + padding]
    _ranges(ax, plot_cfg["x_range_cm1"], y_range)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    if offset:
        ylabel += f" (offset {offset:g})"
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.15)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return _save_formats(
        fig,
        base_path,
        output_cfg["formats"],
        int(output_cfg["dpi"]),
    )
