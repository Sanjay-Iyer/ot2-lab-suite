"""
Per-peak audit figures for the local-baseline height method - exp 4 (083126).

WHY THIS EXISTS
---------------
Every quantitative number in the exp-4 report - height_cps in band_metrics.csv,
and therefore every bar in figure 13, every gain in sers_gain.csv and every SNR
- is one point read off one trace against one straight line:

    height = smooth_cps_raw(centre) - line(centre)

where `line` is fitted through the MEDIAN of the band's two shoulders and
`centre` is located separately, on the arPLS-corrected trace. On this paper
that is a small difference of two large numbers, so the line does most of the
work and the line is worth looking at. This script draws it: one figure per
scan per band, showing the two shoulder regions, the two median anchors, the
fitted line, where the height was read and what it came to.

Nothing here re-derives the method. The anchors are recomputed with the same
arithmetic as exp3_timeseries_report.local_baseline_height, and every figure is
cross-checked against that function AND against the height the report's own
band table carries, so a drawing that disagrees with the pipeline is an error,
not a difference of opinion. --strict turns that check into a hard failure.

This script is READ-ONLY with respect to every existing figure. It writes only
into figures/band_fit_audit/.

USAGE (from the repo root, ai env active)
-----------------------------------------
    python raman/exp4_band_fit_audit.py                     # all bands, mapped scans
    python raman/exp4_band_fit_audit.py --bands cv_1620     # one band
    python raman/exp4_band_fit_audit.py --active-only       # 2x/5x/10x only
    python raman/exp4_band_fit_audit.py --scans 828-830,847
    python raman/exp4_band_fit_audit.py --paper white --key w-6
    python raman/exp4_band_fit_audit.py --contact-sheets    # add per-band grids
    python raman/exp4_band_fit_audit.py --flat             # one folder per band

Panels are filed as <band>/<paper>/<stars|bipyramids|cv_only>/, which is how
they get reviewed; --flat restores one directory per band. Both nanostar arms
share the stars/ directory - stock and 1:5 are the same particle, and the
filename separates them.

VARIANT SHOULDERS
-----------------
--left-shoulder / --right-shoulder replace an anchor window, which is how a
proposed change to the method gets looked at before anything is changed. Every
panel then draws BOTH lines and prints both heights and the shift between them;
the shipped number is still cross-checked against band_metrics.csv, so the
comparison cannot drift. Send these to their own --outdir - a variant height is
not the reported height and must not land in the same folder as one.

    python raman/exp4_band_fit_audit.py --bands cv_1620 \n        --left-shoulder 1490 1550 --contact-sheets \n        --outdir raman/results/exp4_stars/figures/band_fit_audit_L1550
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import exp3_timeseries_report as ts                          # noqa: E402
import exp4_stars_report as stars                            # noqa: E402
from exp3_timeseries_report import ALL_BANDS                 # noqa: E402

PAD = 60.0            # same default band_metrics passes to local_baseline_height
HEIGHT_TOL = 1e-6     # counts/s; the redraw must agree with the pipeline exactly

C_RAW = "#b9bec6"
C_SMOOTH = "#16181c"
C_SHOULDER = "#4a90c4"
C_SEARCH = "#d9a441"
C_LINE = "#b53229"
C_PEAK = "#2e7d47"
C_NEIGHBOUR = "#7b4ea8"

# Panels are filed the way they get reviewed: paper first, then what was on the
# spot. Both nanostar arms share one directory - stock and 1:5 are the same
# particle, and the filename already separates them - so the three directories
# under a paper are the three things being compared, not the four config
# conditions.
PARTICLE_DIR = {"stars": "stars", "stars5x": "stars", "bp": "bipyramids",
                "dyeonly": "cv_only"}

# What was ON the spot. Distinct from rec["particle"], which is the PANEL's
# particle and is stamped on that panel's dye-only controls too - scan 860 is
# `bp-w-6 / dyeonly`, a control with no nanoparticles on it at all, sitting in
# the bipyramid rerun panel because that is the print it belongs to. Naming a
# file after the panel key alone reads as though the control had bipyramids in
# it, so the filename says the SAMPLE and marks the panel as a panel.
SAMPLE_TAG = {"stars": "stars-stock", "stars5x": "stars-1to5",
              "bp": "bipyramids", "dyeonly": "cv-only"}


def sample_label(cfg: dict, rec: dict) -> str:
    """Human name for what was on the spot, taken from the config labels."""
    cond = rec.get("condition")
    if cond is None:
        return "unassigned scan"
    label = cfg.get("conditions", {}).get(cond, {}).get("label", cond)
    if cond == cfg.get("control_condition"):
        return "%s only - NO nanoparticles" % label
    return label


def panel_relpath(rec: dict, flat: bool = False) -> Path:
    """Where one panel is filed under its band directory."""
    name = "scan_%04d_%s_%s_%gs_panel-%s.png" % (
        rec["scan_number"],
        SAMPLE_TAG.get(rec["condition"], "unassigned"),
        rec["dilution"] or "na", float(rec["int_time_s"]), rec["key"])
    if flat:
        return Path(name)
    paper = rec["paper"] if rec["paper"] in ("white", "offwhite") else "other_paper"
    return Path(paper) / PARTICLE_DIR.get(rec["condition"], "unassigned") / name


def neighbours(band: str, x0: float, x1: float) -> list:
    """Other known bands whose search window falls in the plotted range.

    The whole method rests on the two shoulders being signal-free. They are not
    guaranteed to be: the 1620 left shoulder (1546-1606) contains the cv_1587
    window, and the paper_1095 right shoulder (1115-1175) contains cv_1175. Any
    real intensity there lifts that anchor, tilts the line and QUIETLY LOWERS
    the reported height. Every band in ALL_BANDS is checked, not just the ones
    this config scores, because a peak contaminates a shoulder whether or not
    the report happens to be measuring it.
    """
    out = []
    for name, meta in ALL_BANDS.items():
        if name == band:
            continue
        lo, hi = meta["search"]
        if hi >= x0 and lo <= x1:
            out.append((name, float(lo), float(hi), int(meta["nominal"])))
    return sorted(out, key=lambda t: t[1])


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[1] > b[0] and a[0] < b[1]


# ---------------------------------------------------------------------------
# The method, re-expressed so the intermediate quantities can be drawn.
# ---------------------------------------------------------------------------
def baseline_anchors(x, y, lo, hi, pad=PAD, left_win=None, right_win=None):
    """The two shoulder anchors and the line through them.

    Deliberately a transcription of ts.local_baseline_height, not a
    reimplementation of the idea: medians for y, means for x, no fit weights,
    no outlier rejection beyond what a median already gives. `fallback` is True
    when a shoulder is too short to use - the pipeline then abandons the local
    baseline for that band and takes a plain window maximum instead, and a
    figure that says so is more useful than one that quietly draws a line the
    report never used.

    `left_win` / `right_win` replace the pad-derived shoulder with an explicit
    (lo, hi) window. Everything downstream is unchanged, so a variant shoulder
    is measured by exactly the same estimator as the reported one - which is the
    only way the two heights are comparable.
    """
    lwin = tuple(left_win) if left_win else (lo - pad, lo)
    rwin = tuple(right_win) if right_win else (hi, hi + pad)
    # Half-open the same way the pipeline is: the band's own window is excluded
    # from both shoulders, and a point is never counted twice.
    left = (x >= lwin[0]) & (x < lwin[1])
    right = (x > rwin[0]) & (x <= rwin[1])
    info = {
        "n_left": int(left.sum()), "n_right": int(right.sum()),
        "left_lo": lwin[0], "left_hi": lwin[1],
        "right_lo": rwin[0], "right_hi": rwin[1],
        "fallback": bool(left.sum() < 5 or right.sum() < 5),
    }
    if info["fallback"]:
        return info
    xl, yl = float(x[left].mean()), float(np.median(y[left]))
    xr, yr = float(x[right].mean()), float(np.median(y[right]))
    slope = (yr - yl) / (xr - xl) if xr != xl else 0.0
    info.update({"xl": xl, "yl": yl, "xr": xr, "yr": yr, "slope": slope,
                 "line": lambda g: yl + slope * (np.asarray(g, dtype=float) - xl)})
    return info


def measure(rec: dict, band: str, pad=PAD, left_win=None, right_win=None) -> dict:
    """Everything one panel needs, plus the agreement checks.

    With a shoulder override in force the panel carries BOTH heights - the
    variant and the shipped one - because the only useful question about a
    variant baseline is how far it moves the number, and a figure that shows
    one height without the other cannot answer it.
    """
    meta = ALL_BANDS[band]
    lo, hi = meta["search"]
    x = rec["x_fit"]
    y = rec["smooth_cps_raw"]                 # the trace the height is read from
    variant = bool(left_win or right_win)

    # Identification stays on the arPLS trace - exactly as band_metrics does it.
    # It does NOT move with the shoulders: the centre is a property of the peak,
    # and re-locating it per variant would confound "the baseline moved" with
    # "the height was read somewhere else".
    centre, arpls_height, at_edge = ts.window_peak(x, rec["smooth_cps"], lo, hi)
    j = int(np.argmin(np.abs(x - centre)))

    m = baseline_anchors(x, y, lo, hi, pad, left_win, right_win)
    if m["fallback"]:
        c_fb, h_fb, edge_fb = ts.window_peak(x, y, lo, hi)
        m.update({"centre": c_fb, "height": h_fb, "at_edge": edge_fb,
                  "base_at_centre": np.nan,
                  "j": int(np.argmin(np.abs(x - c_fb)))})
    else:
        base_c = float(m["line"](x[j]))
        m.update({"centre": float(centre), "height": float(y[j] - base_c),
                  "at_edge": at_edge, "base_at_centre": base_c, "j": j})

    # Check: against the function the pipeline actually calls. With a variant
    # shoulder the two are MEANT to differ, so the same call becomes the
    # shipped-height reference instead of an assertion.
    _, ref_height, _ = ts.local_baseline_height(x, y, lo, hi, pad, at_cm1=centre)
    m["variant"] = variant
    m["height_default"] = float(ref_height)
    m["height_pipeline"] = float(ref_height)
    m["delta_pipeline"] = (np.nan if variant
                           else abs(m["height"] - float(ref_height)))
    m["shift_vs_default"] = float(m["height"] - float(ref_height))
    m["shift_pct"] = (100.0 * m["shift_vs_default"] / abs(float(ref_height))
                      if ref_height else np.nan)
    if variant:
        d = baseline_anchors(x, y, lo, hi, pad)
        m["default_line"] = d.get("line")
        m["default_left"] = (d["left_lo"], d["left_hi"])
        m["default_right"] = (d["right_lo"], d["right_hi"])

    # sigma stays on the DEFAULT shoulders. It is the SNR denominator the whole
    # report is calibrated on, and moving it with the baseline would change two
    # things at once.
    shoulder = ts._shoulders(x, lo, hi, pad)
    corr = rec["corr_cps"]
    sigma = (ts._sigma_from(corr[shoulder]) if shoulder.sum() > 20
             else ts._sigma_from(corr))
    m.update({
        "band": band, "nominal": meta["nominal"], "assign": meta["assign"],
        "tier": meta["tier"], "lo": lo, "hi": hi, "lit": tuple(meta["lit"]),
        "in_lit": bool(meta["lit"][0] <= m["centre"] <= meta["lit"][1]),
        "arpls_height": float(arpls_height), "sigma": float(sigma),
        "snr": (float(m["height"] / sigma)
                if sigma and np.isfinite(sigma) else np.nan),
    })
    return m


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
def _info_text(rec, m, screen_row, band_row, cfg) -> str:
    def yn(v):
        return "yes" if v else "no"

    lines = ["SCAN %d" % rec["scan_number"], "  SAMPLE"]
    lines += ["    " + ln for ln in textwrap.wrap(sample_label(cfg, rec), 32)]
    lines += [
        "  dilution   %s" % (rec["dilution"] or "-"),
        "  paper      %s" % rec["paper"],
        "  IntTime    %.2f s  x%d avg" % (rec["int_time_s"], rec.get("averages", 1)),
        "  AutoInt    %s" % rec.get("auto_int", "?"),
        # The panel is the PRINT this spot came from, not what was on it. A
        # dye-only control lives in the panel whose gains it anchors, so a
        # bipyramid panel legitimately contains nanoparticle-free scans.
        "  panel      %s" % rec["key"],
    ]
    plabel = cfg.get("panel_label", {}).get(rec["key"])
    if plabel:
        lines += ["    " + ln for ln in textwrap.wrap(plabel, 32)]
    if screen_row is not None:
        lines += [
            "  screen     %s (scan %s/band %s)"
            % (screen_row["verdict"], screen_row["verdict_scan"],
               screen_row["verdict_band"]),
            "  median     %.0f counts" % screen_row["median_counts"],
        ]
        if bool(screen_row.get("band_above_fit_ceiling", False)):
            lines.append("  ** band above fit ceiling **")
    lines += [
        "",
        "BAND  %s  (nominal %d)" % (m["band"], m["nominal"]),
    ] + ["  " + ln for ln in textwrap.wrap(m["assign"], 34)] + [
        "  tier       %s" % m["tier"],
        "  search     %g - %g" % (m["lo"], m["hi"]),
        "  lit        %g - %g" % m["lit"],
        "  pad        %g" % PAD,
        "  L shoulder %g - %g (n=%d)" % (m["left_lo"], m["left_hi"], m["n_left"]),
        "  R shoulder %g - %g (n=%d)" % (m["right_lo"], m["right_hi"], m["n_right"]),
        "",
    ]
    if m["fallback"]:
        lines += [
            "LOCAL BASELINE  NOT USED",
            "  a shoulder held <5 points, so the",
            "  pipeline fell back to a plain",
            "  window maximum. The height below",
            "  is NOT baseline-corrected.",
            "",
        ]
    else:
        lines += [
            "LOCAL BASELINE (%s)"
            % ("VARIANT shoulder" if m.get("variant") else "reported method"),
            "  line through the MEDIAN of each",
            "  shoulder, at its mean position",
            "  L anchor  %8.1f , %9.1f" % (m["xl"], m["yl"]),
            "  R anchor  %8.1f , %9.1f" % (m["xr"], m["yr"]),
            "  slope     %+.4f cps/cm-1" % m["slope"],
            "  base(pk)  %9.1f cps" % m["base_at_centre"],
            "",
        ]
    if m.get("variant"):
        lines += [
            "SHIPPED METHOD, for comparison",
            "  L shoulder %g - %g" % m["default_left"],
            "  R shoulder %g - %g" % m["default_right"],
            "  height     %.2f counts/s" % m["height_default"],
            "  shift      %+.2f cps (%+.1f%%)"
            % (m["shift_vs_default"], m["shift_pct"]),
            "",
        ]
    lines += [
        "PEAK",
        "  centre     %.1f cm-1" % m["centre"],
        "             (located on arPLS trace)",
        "  in lit win %s" % yn(m["in_lit"]),
        "  at edge    %s" % yn(m["at_edge"]),
        "",
        "  HEIGHT     %.2f counts/s" % m["height"],
        "             %.1f counts (x %.2f s)"
        % (m["height"] * rec["int_time_s"], rec["int_time_s"]),
        "  sigma      %.2f counts/s" % m["sigma"],
        "  SNR        %.1f" % m["snr"],
        "  detected   %s" % yn(
            np.isfinite(m["snr"]) and m["snr"] >= cfg["quality"]["min_snr"]
            and m["in_lit"]),
        "",
        "arPLS height at this band, for",
        "scale only: %.2f cps (QC)" % m["arpls_height"],
        "",
    ]
    hits = []
    for name, lo, hi, _nom in neighbours(m["band"], m["left_lo"], m["right_hi"]):
        for side, span in (("L", (m["left_lo"], m["left_hi"])),
                           ("R", (m["right_lo"], m["right_hi"]))):
            if _overlaps((lo, hi), span):
                hits.append("  %s in %s shoulder (%g-%g)" % (name, side, lo, hi))
    if hits:
        lines += ["SHOULDER SITS ON ANOTHER BAND"] + hits + [
            "  -> if that band carries real",
            "     intensity the anchor is lifted",
            "     and this height reads LOW",
            "",
        ]
    lines += [
        "CHECKS",
    ]
    if m.get("variant"):
        # A variant is not supposed to reproduce the shipped number; what must
        # still hold is that the shipped number itself is reproduced from this
        # same trace, which is what makes the shift meaningful.
        d = (abs(float(band_row["height_cps"]) - m["height_default"])
             if band_row is not None else np.nan)
        lines += [
            "  variant shoulder in force -",
            "  this is NOT the reported number",
            "  shipped height reproduced from",
            "  band_metrics.csv: %s"
            % ("n/a" if band_row is None
               else "OK" if d <= HEIGHT_TOL else "OFF %.3g" % d),
        ]
        return "\n".join(lines)
    lines.append("  vs local_baseline_height  %s" % (
        "OK" if m["delta_pipeline"] <= HEIGHT_TOL
        else "OFF %.3g" % m["delta_pipeline"]))
    if band_row is not None:
        d = abs(float(band_row["height_cps"]) - m["height"])
        lines.append("  vs band_metrics.csv       %s"
                     % ("OK" if d <= HEIGHT_TOL else "OFF %.3g" % d))
    else:
        lines.append("  vs band_metrics.csv       n/a")
    return "\n".join(lines)


def panel(rec, m, screen_row, band_row, cfg, path, dpi=200, margin=40.0):
    x = rec["x_fit"]
    y = rec["smooth_cps_raw"]
    raw = rec["y_cps"]
    x0, x1 = m["left_lo"] - margin, m["right_hi"] + margin
    view = (x >= x0) & (x <= x1)

    fig = plt.figure(figsize=(12.4, 7.4))
    # Explicit margins rather than tight_layout: the info column is a switched-off
    # axis carrying one long text block, which tight_layout cannot measure.
    gs = fig.add_gridspec(2, 2, width_ratios=[3.05, 1.15],
                          height_ratios=[2.15, 1.0], hspace=0.09, wspace=0.02,
                          left=0.085, right=0.995, top=0.925, bottom=0.085)
    ax = fig.add_subplot(gs[0, 0])
    axd = fig.add_subplot(gs[1, 0], sharex=ax)
    axi = fig.add_subplot(gs[:, 1])
    axi.axis("off")

    for a in (ax, axd):
        a.axvspan(m["left_lo"], m["left_hi"], color=C_SHOULDER, alpha=0.16, lw=0)
        a.axvspan(m["right_lo"], m["right_hi"], color=C_SHOULDER, alpha=0.16, lw=0)
        a.axvspan(m["lo"], m["hi"], color=C_SEARCH, alpha=0.15, lw=0)
        for v in m["lit"]:
            a.axvline(v, color="#8c8c94", ls=":", lw=0.9, zorder=1)
        a.grid(alpha=0.25)
        a.set_axisbelow(True)

    if m.get("variant"):
        # Outline where the shipped shoulders were, unfilled so the variant
        # shading stays the thing being read.
        for lo_d, hi_d in (m["default_left"], m["default_right"]):
            for a in (ax, axd):
                a.axvspan(lo_d, hi_d, facecolor="none", edgecolor="#8c8c94",
                          lw=0.9, ls=(0, (2, 2)), zorder=1)

    # Where the other known bands land. Drawn as a strip along the bottom of the
    # top axis so a shoulder that is really sitting on a neighbouring peak is
    # visible without reading the info panel.
    for name, nlo, nhi, nom in neighbours(m["band"], x0, x1):
        ax.axvspan(max(nlo, x0), min(nhi, x1), ymin=0.0, ymax=0.035,
                   color=C_NEIGHBOUR, alpha=0.75, lw=0, zorder=11)
        ax.annotate(name, xy=(np.clip((nlo + nhi) / 2.0, x0, x1), 0.045),
                    xycoords=("data", "axes fraction"), ha="center", va="bottom",
                    fontsize=7.5, color=C_NEIGHBOUR, zorder=11)

    # --- top: the spectrum as measured, with the line that is subtracted -----
    ax.plot(x[view], raw[view], color=C_RAW, lw=0.8, zorder=2,
            label="counts/s, unsmoothed")
    ax.plot(x[view], y[view], color=C_SMOOTH, lw=1.7, zorder=4,
            label="smoothed counts/s - height is read from this")

    peak_y = float(y[m["j"]])
    if m["fallback"]:
        base_c = peak_y - m["height"]
        ax.text(0.5, 0.94, "shoulder too short - the pipeline fell back to a "
                "plain window maximum; no local baseline was used",
                transform=ax.transAxes, ha="center", fontsize=9, color=C_LINE)
    else:
        base_c = m["base_at_centre"]
        grid = np.array([x0, x1])
        if m.get("variant") and m.get("default_line") is not None:
            # The shipped line, so the two can be read against each other in one
            # look. Drawn first and thinner: this panel is about the new one.
            ax.plot(grid, m["default_line"](grid), color="#8c8c94", ls=(0, (1, 2)),
                    lw=1.5, zorder=4,
                    label="shipped baseline (%.1f cps)" % m["height_default"])
            ax.plot([m["centre"]], [float(m["default_line"](x[m["j"]]))],
                    marker="_", ms=14, mew=2.0, color="#8c8c94", ls="none",
                    zorder=6)
        ax.plot(grid, m["line"](grid), color=C_LINE, ls="--", lw=1.7, zorder=5,
                label=("variant baseline (subtracted)" if m.get("variant")
                       else "local baseline (subtracted)"))
        # The anchors are medians of a whole shoulder, so show the span each one
        # summarises - a median drawn as a bare point hides how noisy it was.
        for lo_s, hi_s, ya in ((m["left_lo"], m["left_hi"], m["yl"]),
                               (m["right_lo"], m["right_hi"], m["yr"])):
            ax.plot([lo_s, hi_s], [ya, ya], color=C_LINE, lw=2.6, alpha=0.45,
                    solid_capstyle="butt", zorder=6)
        ax.plot([m["xl"], m["xr"]], [m["yl"], m["yr"]], ls="none", marker="D",
                ms=9, mfc=C_LINE, mec="white", mew=1.4, zorder=7,
                label="shoulder median anchors")

    ax.annotate("", xy=(m["centre"], peak_y), xytext=(m["centre"], base_c),
                arrowprops=dict(arrowstyle="<->", color=C_PEAK, lw=1.8), zorder=8)
    ax.plot([m["centre"]], [peak_y], marker="o", ms=8, mfc=C_PEAK, mec="white",
            mew=1.4, ls="none", zorder=9, label="height read here")
    ax.annotate(" %.1f counts/s" % m["height"],
                xy=(m["centre"], (peak_y + base_c) / 2.0),
                ha="left", va="center", fontsize=11, fontweight="bold",
                color=C_PEAK, zorder=10,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=C_PEAK,
                          alpha=0.85, lw=0.8))
    ax.set_ylabel("counts s$^{-1}$")
    ax.tick_params(labelbottom=False)
    # Headroom so the legend clears the peak, which sits mid-window by design.
    seg = np.concatenate([y[view], raw[view]])
    ylo, yhi = float(np.nanmin(seg)), float(np.nanmax(seg))
    span = max(yhi - ylo, 1e-9)
    ax.set_ylim(ylo - 0.10 * span, yhi + 0.34 * span)
    ax.legend(fontsize=8, frameon=False, loc="upper left", ncol=1)

    # --- bottom: what is left once the line comes off ------------------------
    if m["fallback"]:
        corr_s, corr_r = y[view], raw[view]
        axd.set_ylabel("counts s$^{-1}$\n(no baseline)")
    else:
        line_v = m["line"](x[view])
        corr_s, corr_r = y[view] - line_v, raw[view] - line_v
        axd.set_ylabel("minus local baseline\n(counts s$^{-1}$)")
    axd.plot(x[view], corr_r, color=C_RAW, lw=0.8, zorder=2)
    axd.plot(x[view], corr_s, color=C_SMOOTH, lw=1.6, zorder=4)
    axd.axhline(0.0, color=C_LINE, ls="--", lw=1.4, zorder=3)
    if np.isfinite(m["sigma"]) and m["sigma"] > 0:
        # sigma is the SNR denominator: second-difference MAD over these same
        # shoulders. It is measured on the arPLS-corrected trace, not on this
        # one, but a second difference is blind to any smooth background, so the
        # number transfers. Drawn because SNR is what decides `detected`, and a
        # band that fails to clear 3 sigma should look like it fails.
        for k, alpha in ((1, 0.20), (3, 0.12)):
            axd.axhspan(-k * m["sigma"], k * m["sigma"], color="#8c8c94",
                        alpha=alpha, lw=0, zorder=1)
        axd.axhline(3 * m["sigma"], color="#8c8c94", lw=0.8, ls="-.", zorder=3)
    axd.annotate("", xy=(m["centre"], m["height"]), xytext=(m["centre"], 0.0),
                 arrowprops=dict(arrowstyle="<->", color=C_PEAK, lw=1.8), zorder=8)
    axd.plot([m["centre"]], [m["height"]], marker="o", ms=7, mfc=C_PEAK,
             mec="white", mew=1.3, ls="none", zorder=9)
    axd.set_xlabel("Raman shift (cm$^{-1}$)")
    axd.set_xlim(x0, x1)

    dseg = np.concatenate([corr_s, corr_r])
    dlo, dhi = float(np.nanmin(dseg)), float(np.nanmax(dseg))
    dspan = max(dhi - dlo, 1e-9)
    axd.set_ylim(dlo - 0.12 * dspan, dhi + 0.38 * dspan)

    handles = [
        Patch(facecolor=C_SHOULDER, alpha=0.16, label="shoulder (baseline anchors)"),
        Patch(facecolor=C_SEARCH, alpha=0.15, label="search window"),
        Line2D([], [], color="#8c8c94", ls=":", label="literature window"),
        Line2D([], [], color="#8c8c94", ls="-.", lw=0.8, label="$\\pm$1, 3 $\\sigma$"),
        Patch(facecolor=C_NEIGHBOUR, alpha=0.75, label="other known band"),
    ]
    axd.legend(handles=handles, fontsize=7.5, frameon=False, loc="upper left",
               ncol=1, labelspacing=0.28)

    axi.text(0.0, 1.0, _info_text(rec, m, screen_row, band_row, cfg),
             transform=axi.transAxes, ha="left", va="top", fontsize=7.3,
             family="monospace", linespacing=1.30)

    fig.suptitle(
        "scan %d - %s @ %s - %s paper, %g s - panel %s - %d cm$^{-1}$ %s"
        % (rec["scan_number"], sample_label(cfg, rec), rec["dilution"] or "-",
           rec["paper"], float(rec["int_time_s"]), rec["key"], m["nominal"],
           ("VARIANT baseline, L shoulder %g-%g" % (m["left_lo"], m["left_hi"])
            if m.get("variant") else "local-baseline height")),
        x=0.012, y=0.972, ha="left", fontsize=12.5,
        color=(C_LINE if m.get("variant") else "black"))
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def contact_sheet(items, band, path, dpi=160, ncol=6):
    """One band, every audited scan - a flip-through view.

    Same line, same anchors, same read point as the per-scan figures; the
    labels come off so a row can be scanned for a baseline that is obviously
    tilted or an anchor that has landed on a neighbouring feature.
    """
    n = len(items)
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.55 * ncol, 2.2 * nrow),
                             squeeze=False)
    for a in axes.ravel()[n:]:
        a.axis("off")
    for a, (rec, m) in zip(axes.ravel(), items):
        x, y = rec["x_fit"], rec["smooth_cps_raw"]
        x0, x1 = m["left_lo"] - 20, m["right_hi"] + 20
        view = (x >= x0) & (x <= x1)
        a.axvspan(m["left_lo"], m["left_hi"], color=C_SHOULDER, alpha=0.16, lw=0)
        a.axvspan(m["right_lo"], m["right_hi"], color=C_SHOULDER, alpha=0.16, lw=0)
        a.axvspan(m["lo"], m["hi"], color=C_SEARCH, alpha=0.15, lw=0)
        a.plot(x[view], y[view], color=C_SMOOTH, lw=1.1)
        if not m["fallback"]:
            grid = np.array([x0, x1])
            a.plot(grid, m["line"](grid), color=C_LINE, ls="--", lw=1.1)
            a.plot([m["xl"], m["xr"]], [m["yl"], m["yr"]], ls="none", marker="D",
                   ms=4, mfc=C_LINE, mec="white", mew=0.7)
            a.annotate("", xy=(m["centre"], float(y[m["j"]])),
                       xytext=(m["centre"], m["base_at_centre"]),
                       arrowprops=dict(arrowstyle="<->", color=C_PEAK, lw=1.2))
        a.set_title("%d  %s %s\n%.1f cps  SNR %.0f"
                    % (rec["scan_number"],
                       SAMPLE_TAG.get(rec["condition"], "-"),
                       rec["dilution"] or "-", m["height"], m["snr"]),
                    fontsize=7.5)
        a.set_xlim(x0, x1)
        a.tick_params(labelsize=6)
    m0 = items[0][1]
    fig.suptitle("%s (%d cm$^{-1}$) - %s on every audited scan"
                 % (band, ALL_BANDS[band]["nominal"],
                    ("VARIANT baseline, L shoulder %g-%g"
                     % (m0["left_lo"], m0["left_hi"]) if m0.get("variant")
                     else "local baseline")),
                 x=0.008, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def parse_scans(spec: str) -> set:
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def select(scans, cfg, args):
    active = cfg.get("plotting", {}).get("active_dilutions")
    wanted = parse_scans(args.scans) if args.scans else None
    keep = []
    for rec in scans:
        if wanted is not None and rec["scan_number"] not in wanted:
            continue
        if rec["condition"] is None and not args.include_unassigned:
            continue
        if args.paper and rec["paper"] != args.paper:
            continue
        if args.key and rec["key"] != args.key:
            continue
        if args.condition and rec["condition"] != args.condition:
            continue
        if args.active_only and active and rec["dilution"] not in active:
            continue
        keep.append(rec)
    return keep


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Per-peak audit figures for the local-baseline height method.")
    p.add_argument("--bands", nargs="+", default=None,
                   help="band names (default: every band in stars_exp4.yaml)")
    p.add_argument("--scans", default=None, help="e.g. 803-810,828,847")
    p.add_argument("--paper", default=None, choices=["offwhite", "white"])
    p.add_argument("--key", default=None, help="panel key, e.g. w-6")
    p.add_argument("--condition", default=None,
                   help="stars | stars5x | bp | dyeonly")
    p.add_argument("--active-only", action="store_true",
                   help="restrict to plotting.active_dilutions (2x/5x/10x)")
    p.add_argument("--include-unassigned", action="store_true",
                   help="also audit scans that map to no series")
    p.add_argument("--contact-sheets", action="store_true",
                   help="also write one overview grid per band")
    p.add_argument("--flat", action="store_true",
                   help="write every panel straight into <band>/ instead of "
                        "<band>/<paper>/<stars|bipyramids|cv_only>/")
    p.add_argument("--left-shoulder", nargs=2, type=float, metavar=("LO", "HI"),
                   default=None,
                   help="replace the left baseline anchor window, e.g. 1490 1550 "
                        "to clear the cv_1587 band out of the cv_1620 shoulder. "
                        "Panels then show BOTH this height and the shipped one.")
    p.add_argument("--right-shoulder", nargs=2, type=float, metavar=("LO", "HI"),
                   default=None, help="same, for the right anchor window")
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--outdir", default=None)
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero if any redrawn height disagrees with the "
                        "pipeline or with band_metrics.csv")
    args = p.parse_args(argv)

    cfg = stars.load_config()
    out_root = (Path(args.outdir) if args.outdir else
                ROOT / cfg["io"]["output_dir"] / "figures" / "band_fit_audit")
    out_root.mkdir(parents=True, exist_ok=True)
    dpi = args.dpi or cfg["plotting"]["dpi"]

    bands = args.bands or list(cfg["bands"])
    unknown = [b for b in bands if b not in ALL_BANDS]
    if unknown:
        raise SystemExit("[error] unknown band(s): %s" % unknown)

    scans = stars.load_scans(cfg)
    chosen = select(scans, cfg, args)
    if not chosen:
        raise SystemExit("[error] no scans matched the selection")
    print("[audit] %d scan(s) x %d band(s) = %d figure(s)"
          % (len(chosen), len(bands), len(chosen) * len(bands)))

    # The report's own tables, purely as an independent check on the drawings.
    tcfg = ts.load_config(ROOT / "configs" / cfg["screen"]["timeseries_config"])
    model = stars.load_noise_model(cfg)
    screen = stars.scan_screen(scans, model, cfg, tcfg)
    table = stars.band_table(scans, screen, cfg)
    by_scan_band = {(int(r["scan_number"]), r["peak_name"]): r
                    for _, r in table.iterrows()}
    by_scan = {int(r["scan_number"]): r for _, r in screen.iterrows()}

    left_win = tuple(args.left_shoulder) if args.left_shoulder else None
    right_win = tuple(args.right_shoulder) if args.right_shoulder else None
    variant = bool(left_win or right_win)
    if variant:
        print("[audit] VARIANT shoulders in force - these heights are NOT the "
              "reported numbers")
        print("[audit]   left  %s" % ("%g - %g" % left_win if left_win
                                      else "(default: search lo - %g)" % PAD))
        print("[audit]   right %s" % ("%g - %g" % right_win if right_win
                                      else "(default: search hi + %g)" % PAD))

    rows, mismatches, per_band = [], [], {}
    for band in bands:
        band_dir = out_root / band
        band_dir.mkdir(parents=True, exist_ok=True)
        per_band[band] = []
        for rec in chosen:
            m = measure(rec, band, left_win=left_win, right_win=right_win)
            band_row = by_scan_band.get((int(rec["scan_number"]), band))
            screen_row = by_scan.get(int(rec["scan_number"]))
            rel = panel_relpath(rec, args.flat)
            dest = band_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            panel(rec, m, screen_row, band_row, cfg, dest, dpi=dpi)
            per_band[band].append((rec, m))

            # With a variant shoulder the height is MEANT to differ from
            # band_metrics; what is checked instead is that the shipped height
            # is still reproduced exactly from this same trace.
            ref = m["height_default"] if variant else m["height"]
            d_table = (abs(float(band_row["height_cps"]) - ref)
                       if band_row is not None else np.nan)
            d_pipe = 0.0 if variant else m["delta_pipeline"]
            if d_pipe > HEIGHT_TOL or (np.isfinite(d_table)
                                       and d_table > HEIGHT_TOL):
                mismatches.append((rec["scan_number"], band, d_pipe, d_table))
            rows.append({
                "scan_number": rec["scan_number"], "key": rec["key"],
                "paper": rec["paper"], "particle": rec["particle"],
                "condition": rec["condition"],
                "sample": SAMPLE_TAG.get(rec["condition"], "unassigned"),
                "dilution": rec["dilution"],
                "int_time_s": rec["int_time_s"], "peak_name": band,
                "nominal": m["nominal"],
                "search_lo": m["lo"], "search_hi": m["hi"], "pad_cm1": PAD,
                "left_lo": m["left_lo"], "left_hi": m["left_hi"],
                "right_lo": m["right_lo"], "right_hi": m["right_hi"],
                "n_left": m["n_left"], "n_right": m["n_right"],
                "fallback_no_local_baseline": m["fallback"],
                "anchor_left_cm1": m.get("xl"), "anchor_left_cps": m.get("yl"),
                "anchor_right_cm1": m.get("xr"), "anchor_right_cps": m.get("yr"),
                "baseline_slope_cps_per_cm1": m.get("slope"),
                "baseline_at_centre_cps": m.get("base_at_centre"),
                "center_cm1": m["centre"], "in_lit_window": m["in_lit"],
                "at_window_edge": m["at_edge"],
                "height_cps": m["height"],
                "height_counts": m["height"] * rec["int_time_s"],
                "variant_shoulder": variant,
                "height_cps_shipped": m["height_default"],
                "shift_vs_shipped_cps": m["shift_vs_default"],
                "shift_vs_shipped_pct": m["shift_pct"],
                "sigma_cps": m["sigma"], "snr": m["snr"],
                "arpls_height_cps": m["arpls_height"],
                "shoulder_overlaps_bands": ";".join(
                    "%s@%s" % (name, side)
                    for name, nlo, nhi, _ in neighbours(
                        band, m["left_lo"], m["right_hi"])
                    for side, span in (("L", (m["left_lo"], m["left_hi"])),
                                       ("R", (m["right_lo"], m["right_hi"])))
                    if _overlaps((nlo, nhi), span)),
                "height_minus_pipeline": m["delta_pipeline"],
                "height_minus_band_metrics": d_table,
                "figure": dest.relative_to(out_root).as_posix(),
            })
        print("[audit]   %s: %d figure(s) -> %s" % (band, len(chosen), band_dir))

    if args.contact_sheets:
        for band, items in per_band.items():
            path = out_root / ("contact_%s.png" % band)
            contact_sheet(items, band, path)
            print("[audit]   contact sheet -> %s" % path)

    # A run narrowed to some bands or some scans must not wipe the rest of the
    # table: rows this run did not produce are carried over from the existing
    # file, and rows it did produce replace their old versions. Without this a
    # `--bands cv_1620` run silently deletes every other band's audit record.
    csv_path = out_root / "band_fit_audit.csv"
    fresh = pd.DataFrame(rows)
    kept = 0
    if csv_path.is_file():
        try:
            prior = pd.read_csv(csv_path)
        except Exception as exc:                       # unreadable -> start clean
            print("[audit] NOTE: could not read %s (%s); writing fresh rows only"
                  % (csv_path.name, exc))
            prior = None
        if prior is not None and {"scan_number", "peak_name"} <= set(prior.columns):
            done = set(zip(fresh["scan_number"], fresh["peak_name"]))
            carry = prior[~prior.apply(
                lambda r: (r["scan_number"], r["peak_name"]) in done, axis=1)]
            kept = len(carry)
            fresh = pd.concat([fresh, carry], ignore_index=True)
    fresh = fresh.sort_values(["peak_name", "scan_number"], kind="stable")
    fresh.to_csv(csv_path, index=False)
    print("[audit] %d row(s) written, %d carried over -> %s"
          % (len(rows), kept, csv_path))

    if mismatches:
        print("[audit] *** %d height mismatch(es) - the drawings do NOT match "
              "the pipeline ***" % len(mismatches))
        for scan, band, dp, dt in mismatches[:20]:
            print("        scan %s %s: vs local_baseline_height %.3g, "
                  "vs band_metrics %.3g" % (scan, band, dp, dt))
        if args.strict:
            return 1
    else:
        print("[audit] every redrawn height matches local_baseline_height and "
              "band_metrics.csv to within %g counts/s" % HEIGHT_TOL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
