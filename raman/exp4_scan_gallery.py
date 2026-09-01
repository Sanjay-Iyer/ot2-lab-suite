"""exp4_scan_gallery.py - every scan in the 083126 dump, drawn one per cell.

The report's other figures show conclusions. This one shows the evidence: one
contact sheet per panel, laid out condition down the side and dye dilution
across the top, so a scan the saturation screen threw out can be looked at
next to the scans it was thrown out for resembling.

WHAT YOU ARE LOOKING AT, AND WHY IT IS THE CORRECTED TRACE
----------------------------------------------------------
Saturation on this instrument does not clip. Scan 854 - a fail - peaks at
90,569 counts with exactly one point at its maximum, the same as scan 855,
which passes: there is no flat top to see in the raw trace, and the
fluorescence background swamps everything else at that scale. What does show
is the residual after the baseline comes off, where a saturated scan rings -
a large, fast oscillation running the whole width of the spectrum, quite
unlike the sparse bands of a good scan. That is the trace drawn here, and it
is also what the noise-excess screen measures.

Every cell on a sheet shares one y axis, deliberately: the ringing is only
damning next to what a clean scan of the same block looks like.

Called by exp4_stars_report.main().
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from exp3_timeseries_report import ALL_BANDS, VERDICT_COLOR

CONDITION_ORDER = ("stars", "stars5x", "bp", "dyeonly")
CELL_W, CELL_H = 3.05, 2.6


def _factor(dilution):
    return float(str(dilution).rstrip("x"))


def _missing_index(cfg):
    """(key, condition, dilution) -> scan number, for scans absent from the dump."""
    out = {}
    for scan, m in (cfg.get("missing_scans") or {}).items():
        out[(m["key"], m["condition"], m["dilution"])] = int(scan)
    return out


def _sheet_layout(rows, cfg):
    """Conditions down the side, dilutions across the top, in ladder order."""
    conditions = [c for c in CONDITION_ORDER if (rows["condition"] == c).any()]
    dilutions = sorted(
        {d for d in rows["dilution"].dropna()}, key=_factor
    )
    return conditions, dilutions


def _draw_cell(ax, rec, row, cfg):
    # Two verdicts, two channels. The trace takes the one in force - scored at
    # the band being plotted - while the frame keeps the whole-scan verdict, so
    # a scan that rings below 800 cm-1 and is clean at 1620 reads as exactly
    # that, rather than as either fact alone.
    colour = VERDICT_COLOR[row["verdict_band"]]
    frame = VERDICT_COLOR[row["verdict_scan"]]
    # Unsmoothed behind, smoothed in front: the first carries the noise the
    # screen scores, the second the band shape the report scores.
    ax.plot(rec["x_fit"], rec["corr_cps"], color=colour, lw=0.45, alpha=0.35)
    ax.plot(rec["x_fit"], rec["smooth_cps"], color=colour, lw=1.0)
    ax.axvline(ALL_BANDS[cfg["sers_band"]]["nominal"], color="0.6", lw=0.6, ls=":")
    ax.axhline(0, color="0.4", lw=0.5)
    ax.set_xlim(400, 1800)
    ax.set_title(
        "%d  %s\nexcess %.1f at band | %.1f whole scan"
        % (int(row["scan_number"]), row["verdict_band"],
           row["noise_excess_at_band"], row["noise_excess_p90"]),
        fontsize=8, color=colour, loc="left",
    )
    for spine in ax.spines.values():
        spine.set_color(frame)
        spine.set_linewidth(1.1 if row["verdict_scan"] != "pass" else 0.7)
    ax.tick_params(labelsize=7)


def _draw_blank(ax, text):
    ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=8, color="0.55",
            transform=ax.transAxes)
    # Hide this cell's ticks through tick_params, not set_xticks/set_yticks:
    # the axes share a locator, so clearing ticks here would strip the scale
    # off every populated cell on the sheet as well.
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _sheet(scans, rows, cfg, key, title, path, missing):
    by_scan = {r["scan_number"]: r for r in scans}
    conditions, dilutions = _sheet_layout(rows, cfg)
    if not conditions or not dilutions:
        return None
    indexed = rows.set_index(["condition", "dilution"])

    fig, axes = plt.subplots(
        len(conditions), len(dilutions),
        figsize=(CELL_W * len(dilutions), CELL_H * len(conditions) + 0.9),
        squeeze=False, sharey=True, sharex=True,
    )
    for r, condition in enumerate(conditions):
        for c, dilution in enumerate(dilutions):
            ax = axes[r][c]
            if (condition, dilution) in indexed.index:
                row = indexed.loc[(condition, dilution)]
                _draw_cell(ax, by_scan[int(row["scan_number"])], row, cfg)
            elif (key, condition, dilution) in missing:
                _draw_blank(ax, "scan %d\nnot in dump"
                            % missing[(key, condition, dilution)])
            else:
                _draw_blank(ax, "not acquired")
            if r == 0:
                ax.annotate(dilution, xy=(0.5, 1.30), xycoords="axes fraction",
                            ha="center", fontsize=11)
            if c == 0:
                label = cfg["conditions"][condition]["label"]
                ax.annotate(label, xy=(-0.28, 0.5), xycoords="axes fraction",
                            rotation=90, va="center", ha="center", fontsize=9)
    for ax in axes[-1]:
        ax.set_xlabel("Raman shift (cm$^{-1}$)", fontsize=8)
    fig.supylabel("Baseline-corrected counts s$^{-1}$", fontsize=9)

    fig.suptitle(
        "%s\ntrace colour = verdict at the CV band, frame = "
        "whole-scan verdict" % title,
        fontsize=12,
    )
    fig.tight_layout(rect=(0.015, 0, 1, 0.93 if len(conditions) > 1 else 0.86))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)
    return path


def generate(scans, screen, cfg, figure_dir: Path) -> list[Path]:
    out = Path(figure_dir) / "scan_gallery"
    out.mkdir(parents=True, exist_ok=True)
    missing = _missing_index(cfg)
    written = []
    for key, title in cfg["panel_label"].items():
        rows = screen[(screen["key"] == key) & screen["condition"].notna()]
        if not len(rows):
            continue
        # panel_label already names the paper, particle and exposure.
        path = _sheet(scans, rows, cfg, key, title, out / ("%s.png" % key), missing)
        if path is not None:
            written.append(path)
    return written
