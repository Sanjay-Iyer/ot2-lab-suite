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
