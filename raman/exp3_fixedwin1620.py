#!/usr/bin/env python
"""exp3_fixedwin1620.py - parallel 1620 cm-1 analysis on FIXED baseline windows.

METHOD 2, run alongside method 1. Nothing here writes into the existing result
folders; every output lands in results/exp3_timeseries/fixedwin1620/ and every
filename carries the `fixedwin1620` tag. The current analysis
(exp3_timeseries_report.py, exp4_stars_report.py) is untouched and still
reproduces exactly what it did before.

    Method 1  local baseline from a 60 cm-1 pad either side of the band's
              search window -> for 1620 that is 1546-1606 and 1632-1692
    Method 2  local baseline from two FIXED windows, 1500-1550 and 1650-1700,
              the same for every spectrum

Only the 1620 height changes. Peak centres, sigma, the raw-count vetoes
(saturation, headroom, linearity, noise excess) and every other band are the
method-1 values, reused as-is - so any difference in a recommendation or a gain
is attributable to the 1620 baseline and nothing else.

Why this is worth testing: method 1's left shoulder for 1620 is not clean. It
overlaps the cv_1587 band on offwhite, and on white it sits on the rising flank
of 1620 itself (median 15-46 counts/s across that window against ~0 for
1500-1550). The fixed windows are empirically flat on all four sweeps.

Run (from raman/, after the two main reports):
    python exp3_fixedwin1620.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import exp3_timeseries_report as ts                          # noqa: E402
import exp4_stars_report as e4                               # noqa: E402
from exp3_timeseries_report import ALL_BANDS                 # noqa: E402

TAG = "fixedwin1620"
M1, M2 = "current (shoulder pad)", "fixed windows 1500-1550 / 1650-1700"
C1, C2 = "#2f6ea8", "#c07a2a"


# ---------------------------------------------------------------------------
def fixed_windows(cfg) -> tuple[str, tuple, tuple]:
    fw = cfg["fixed_window_1620"]
    pair = fw["pairs"][fw["primary_pair"]]
    return fw["band"], tuple(pair["left"]), tuple(pair["right"])


def substitute_snr(bands: pd.DataFrame, band: str) -> pd.DataFrame:
    """Method-1 band table with ONLY the 1620 SNR/height swapped for method 2.

    Returned frame is fed to the unmodified ts.verdict_table, so the vetoes and
    the plateau logic that run on it are literally the same code paths as the
    official run. The single substituted variable is the 1620 SNR.
    """
    out = bands.copy()
    m = out["peak_name"] == band
    out.loc[m, "height_cps"] = out.loc[m, "height_fixedwin_1620_cps"]
    out.loc[m, "snr"] = out.loc[m, "snr_fixedwin_1620"]
    return out


def gains_both(scans, screen, cfg, band, left, right):
    """exp4 gains twice: method 1, then with the 1620 height read fixed-window.

    e4.gain_table is called unmodified both times. For the second pass the
    height reader it calls is temporarily pointed at the fixed-window
    calculation, and ONLY when the window it is handed is the 1620 window - every
    other band still goes through the method-1 path. The original function is
    restored immediately afterwards.
    """
    lo_hi = ALL_BANDS[band]["search"]
    original = ts.local_baseline_height

    def shim(x, y, lo, hi, pad=60.0, at_cm1=None):
        if (lo, hi) == lo_hi and at_cm1 is not None:
            h, _ = ts.fixed_window_height(x, y, at_cm1, left, right)
            return float(at_cm1), h, False
        return original(x, y, lo, hi, pad, at_cm1=at_cm1)

    g1 = e4.gain_table(scans, screen, cfg)
    try:
        ts.local_baseline_height = shim
        g2 = e4.gain_table(scans, screen, cfg)
    finally:
        ts.local_baseline_height = original
    return g1, g2


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_diagnostic(scans, bands, cfg, band, left, right, path):
    """Exactly how the fixed-window height is built, one panel per condition."""
    nominal = ALL_BANDS[band]["nominal"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.4))
    for ax, spec in zip(axes.ravel(), cfg["sweeps"]):
        g = bands[(bands["key"] == spec["key"]) & (bands["peak_name"] == band)]
        # A representative healthy scan: the sweep's recommended exposure if it
        # is here, otherwise the middle one.
        pick = int(g.sort_values("int_time_s").iloc[len(g) // 2]["scan_number"])
        rec = [r for r in scans if r["scan_number"] == pick][0]
        row = g[g["scan_number"] == pick].iloc[0]
        x, y = rec["x_fit"], rec["smooth_cps_raw"]
        m = (x >= 1400) & (x <= 1800)
        ax.plot(x[m], y[m], color="0.2", lw=1.2, zorder=3)

        for (lo, hi), lbl in ((left, "left window"), (right, "right window")):
            ax.axvspan(lo, hi, color=C2, alpha=0.16, lw=0, zorder=0)
        anchors = []
        for lo, hi in (left, right):
            w = (x >= lo) & (x <= hi)
            anchors.append((float(x[w].mean()), float(np.median(y[w]))))
        (xl, yl), (xr, yr) = anchors
        slope = (yr - yl) / (xr - xl)
        xs = np.array([1400.0, 1800.0])
        ax.plot(xs, yl + slope * (xs - xl), color=C2, lw=1.8, ls="--", zorder=4)
        ax.scatter([xl, xr], [yl, yr], s=90, color=C2, zorder=5,
                   edgecolor="white", linewidth=1.5)

        centre = float(row["center_cm1"])
        j = int(np.argmin(np.abs(x - centre)))
        b = yl + slope * (x[j] - xl)
        ax.plot([centre, centre], [b, y[j]], color="#b53229", lw=2.6, zorder=6)
        ax.annotate("H = %.0f" % row["height_fixedwin_1620_cps"],
                    xy=(centre, (b + y[j]) / 2), xytext=(10, 0),
                    textcoords="offset points", fontsize=10, color="#b53229",
                    va="center", weight="bold")
        ax.axvline(centre, color="0.55", lw=0.8, ls=":", zorder=1)
        ax.set_xlim(1400, 1800)
        ax.set_xlabel("Raman shift (cm$^{-1}$)")
        ax.set_ylabel("Intensity (counts s$^{-1}$)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_title("%s · %s   scan %d, %g s   centre %d cm$^{-1}$"
                     % (spec["paper"].capitalize(), spec["particle"], pick,
                        rec["int_time_s"], centre), fontsize=10, loc="left")
    axes[0, 0].legend(handles=[
        plt.Line2D([], [], color="0.2", lw=1.2, label="measured (smoothed)"),
        plt.Line2D([], [], color=C2, lw=1.8, ls="--", label="fixed-window baseline"),
        plt.Line2D([], [], color=C2, marker="o", ls="", label="window medians"),
        plt.Line2D([], [], color="#b53229", lw=2.6, label="height at arPLS centre"),
    ], fontsize=8, frameon=False)
    fig.suptitle("Fixed-window %d cm$^{-1}$ baseline - shaded spans are the two fixed "
                 "windows (%g-%g and %g-%g cm$^{-1}$)"
                 % (nominal, left[0], left[1], right[0], right[1]), fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_compare(bands, v1, v2, cfg, band, path):
    """Plot A (SNR) and Plot B (height), method 1 vs method 2, per sweep."""
    nominal = ALL_BANDS[band]["nominal"]
    n = len(cfg["sweeps"])
    fig, axes = plt.subplots(2, n, figsize=(4.3 * n, 8.0))
    for col, spec in enumerate(cfg["sweeps"]):
        g = (bands[(bands["key"] == spec["key"]) & (bands["peak_name"] == band)]
             .sort_values("int_time_s"))
        t = g["int_time_s"].to_numpy(float)
        for r, (a, b, lab) in enumerate((
                ("snr", "snr_fixedwin_1620", "SNR at %d cm$^{-1}$" % nominal),
                ("height_local_cps", "height_fixedwin_1620_cps",
                 "height at %d cm$^{-1}$ (counts s$^{-1}$)" % nominal))):
            ax = axes[r, col]
            ax.plot(t, g[a], color=C1, lw=1.5, marker="o", ms=6, label=M1)
            ax.plot(t, g[b], color=C2, lw=1.5, marker="s", ms=6, label=M2)
            ax.set_xticks(t)
            ax.set_xticklabels(["%g" % q for q in t])
            ax.set_ylim(bottom=0)
            ax.spines[["top", "right"]].set_visible(False)
            if col == 0:
                ax.set_ylabel(lab)
            if r == 1:
                ax.set_xlabel("Integration time (s)")
            if r == 0:
                ax.set_title("%s · %s" % (spec["paper"].capitalize(),
                                          spec["particle"]), fontsize=11)
    axes[0, 0].legend(frameon=False, fontsize=8)
    fig.suptitle("Method 1 vs method 2 at %d cm$^{-1}$. Top: SNR. Bottom: peak height."
                 % nominal, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


PARTICLE_COLOR = {"bipyramids": "#2f6ea8", "stars": "#a8322d"}


def _series(bands, verdict, cfg, key, band):
    """(t, snr_fixed, height_fixed, usable-mask) for one sweep."""
    g = (bands[(bands["key"] == key) & (bands["peak_name"] == band)]
         .sort_values("int_time_s"))
    v = verdict[verdict["key"] == key].set_index("int_time_s")["overall"].to_dict()
    t = g["int_time_s"].to_numpy(float)
    ok = np.array([v.get(q) != "fail" for q in t])
    return (t, g["snr_fixedwin_1620"].to_numpy(float),
            g["height_fixedwin_1620_cps"].to_numpy(float), ok)


def _draw(ax, t, y, ok, colour, label):
    """Trend line through the USABLE points only.

    A scan that failed the raw-count vetoes has no measurable band - its
    "height" is whatever the largest noise excursion in the window happened to
    be - so joining it to the trend implies a continuity that is not there.
    It is still drawn, hollow and detached, because knowing the scan exists and
    was rejected is useful; connecting it is not.
    """
    ax.plot(t[ok], y[ok], color=colour, lw=1.6, zorder=1)
    ax.scatter(t[ok], y[ok], color=colour, s=70, zorder=2, label=label)
    if (~ok).any():
        ax.scatter(t[~ok], y[~ok], facecolor="white", edgecolor=colour, s=70,
                   linewidth=1.6, zorder=2)


def fig_fixed_only(bands, verdict, cfg, band, path):
    """Method 2 on its own - the same layout as the comparison, one method."""
    nominal = ALL_BANDS[band]["nominal"]
    n = len(cfg["sweeps"])
    fig, axes = plt.subplots(2, n, figsize=(4.3 * n, 8.0))
    for col, spec in enumerate(cfg["sweeps"]):
        t, snr, h, ok = _series(bands, verdict, cfg, spec["key"], band)
        colour = PARTICLE_COLOR[spec["particle"]]
        for r, (y, lab) in enumerate(((snr, "SNR at %d cm$^{-1}$" % nominal),
                                      (h, "Height at %d cm$^{-1}$ (counts s$^{-1}$)"
                                       % nominal))):
            ax = axes[r, col]
            _draw(ax, t, y, ok, colour, None)
            ax.set_xticks(t)
            ax.set_xticklabels(["%g" % q for q in t])
            ax.set_ylim(bottom=0)
            ax.spines[["top", "right"]].set_visible(False)
            if col == 0:
                ax.set_ylabel(lab)
            if r == 1:
                ax.set_xlabel("Integration time (s)")
            if r == 0:
                ax.set_title("%s · %s" % (spec["paper"].capitalize(),
                                          spec["particle"]), fontsize=11)
    fig.suptitle("Fixed-window %d cm$^{-1}$ baseline. Hollow, detached markers failed "
                 "the raw QC vetoes - their height is a noise excursion, not a band."
                 % nominal, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_paper(bands, verdict, cfg, band, paper, path):
    """One paper, both particle types, so the NPs can be compared directly."""
    nominal = ALL_BANDS[band]["nominal"]
    specs = [s for s in cfg["sweeps"] if s["paper"] == paper]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.8))
    ticks = set()
    for spec in specs:
        t, snr, h, ok = _series(bands, verdict, cfg, spec["key"], band)
        ticks.update(t.tolist())
        colour = PARTICLE_COLOR[spec["particle"]]
        _draw(axes[0], t, snr, ok, colour, spec["particle"])
        _draw(axes[1], t, h, ok, colour, spec["particle"])
    tt = sorted(ticks)
    for ax, lab in ((axes[0], "SNR at %d cm$^{-1}$" % nominal),
                    (axes[1], "Height at %d cm$^{-1}$ (counts s$^{-1}$)" % nominal)):
        ax.set_xticks(tt)
        ax.set_xticklabels(["%g" % q for q in tt])
        ax.set_xlabel("Integration time (s)")
        ax.set_ylabel(lab)
        ax.set_ylim(bottom=0)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle("%s paper - fixed-window %d cm$^{-1}$ baseline.\nHollow, detached "
                 "markers failed the raw QC vetoes: their height is a noise "
                 "excursion, not a band." % (paper.capitalize(), nominal),
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_recommendation(v1, v2, cfg, path):
    """Where each method would put the operating point, side by side."""
    fig, axes = plt.subplots(1, len(cfg["sweeps"]),
                             figsize=(4.0 * len(cfg["sweeps"]), 4.4))
    for ax, spec in zip(np.atleast_1d(axes), cfg["sweeps"]):
        for v, col, lab, off in ((v1, C1, M1, -0.12), (v2, C2, M2, 0.12)):
            g = v[v["key"] == spec["key"]].sort_values("int_time_s")
            t = g["int_time_s"].to_numpy(float)
            ok = (g["overall"] != "fail").to_numpy()
            ax.scatter(t[ok], np.full(ok.sum(), off), s=70, color=col)
            ax.scatter(t[~ok], np.full((~ok).sum(), off), s=70, facecolor="white",
                       edgecolor=col, linewidth=1.5)
            r = g[g["recommended"]]
            if len(r):
                ax.scatter(r["int_time_s"], [off], s=320, marker="*", color=col,
                           zorder=5, label="%s -> %g s"
                           % (lab.split(" (")[0], float(r["int_time_s"].iloc[0])))
        ax.set_yticks([])
        ax.set_ylim(-0.45, 0.45)
        ax.set_xlabel("Integration time (s)")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.set_title("%s · %s" % (spec["paper"].capitalize(), spec["particle"]),
                     fontsize=10)
        ax.legend(frameon=False, fontsize=8, loc="upper center")
    fig.suptitle("Recommended integration time - identical vetoes and plateau logic, "
                 "only the 1620 SNR substituted. Star = pick, open = failed a veto.",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_gain(g1, g2, band, e4cfg, path):
    """SERS gain at 1620, method 1 vs method 2, paired per comparison."""
    a = g1[g1["peak_name"] == band].set_index(["key", "condition", "dilution"])
    b = g2[g2["peak_name"] == band].set_index(["key", "condition", "dilution"])
    j = a[["gain_x", "gain_trustworthy"]].join(
        b[["gain_x", "gain_trustworthy"]], lsuffix="_1", rsuffix="_2").dropna(
        subset=["gain_x_1", "gain_x_2"])
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    ax = axes[0]
    lim = float(np.nanmax([j["gain_x_1"].max(), j["gain_x_2"].max()])) * 1.08
    ax.plot([0, lim], [0, lim], color="0.7", lw=1.0, ls="--")
    for cond, h in j.groupby(level="condition"):
        c = e4cfg["conditions"][cond]
        ax.scatter(h["gain_x_1"], h["gain_x_2"], s=60, color=c["color"],
                   marker=c["marker"], label=c["label"], edgecolor="white",
                   linewidth=0.8)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_xlabel("gain, method 1 ($\\times$)")
    ax.set_ylabel("gain, method 2 ($\\times$)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Every paired comparison. On the dashed line = unchanged.",
                 fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8)

    ax = axes[1]
    med = j.groupby(level="condition")[["gain_x_1", "gain_x_2"]].median()
    xs = np.arange(len(med))
    ax.bar(xs - 0.19, med["gain_x_1"], 0.38, color=C1, label=M1)
    ax.bar(xs + 0.19, med["gain_x_2"], 0.38, color=C2, label=M2)
    ax.axhline(1.0, color="0.3", lw=1.0)
    ax.set_xticks(xs)
    ax.set_xticklabels([e4cfg["conditions"][c]["label"] for c in med.index],
                       rotation=15, ha="right", fontsize=8)
    ax.set_ylabel("median gain ($\\times$)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Condition ranking", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8)
    fig.suptitle("SERS gain at %d cm$^{-1}$ under both baseline methods"
                 % ALL_BANDS[band]["nominal"], fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main() -> int:
    cfg = ts.load_config()
    band, left, right = fixed_windows(cfg)
    out = ROOT / cfg["io"]["output_dir"] / TAG
    out.mkdir(parents=True, exist_ok=True)

    # ---- method 1, rebuilt exactly as the official run builds it -----------
    sweeps, all_scans = ts.load_scans(cfg)
    sweeps = [ts.process(r, cfg) for r in sweeps]
    pool = all_scans if cfg["pool_all_scans"] else sweeps
    model = ts.fit_noise_model(ts.window_table(pool, cfg), cfg)
    df = ts.add_fidelity(ts.add_linearity(ts.scan_table(sweeps, model, cfg), cfg),
                         sweeps)
    bands = pd.DataFrame([m for r in sweeps for m in ts.band_metrics(r, cfg)])
    v1 = ts.verdict_table(df, bands, cfg)
    # ---- method 2: same everything, 1620 SNR substituted -------------------
    v2 = ts.verdict_table(df, substitute_snr(bands, band), cfg)

    for v, name in ((v1, "verdict_method1.csv"), (v2, "verdict_method2.csv")):
        v.to_csv(out / name, index=False)

    g = (bands[bands["peak_name"] == band]
         .merge(v1[["key", "scan_number", "overall", "recommended"]],
                on=["key", "scan_number"])
         .merge(v2[["key", "scan_number", "recommended"]],
                on=["key", "scan_number"], suffixes=("_m1", "_m2")))
    comp = g[["scan_number", "key", "int_time_s", "center_cm1",
              "height_local_cps", "baseline_fixedwin_1620_cps",
              "height_fixedwin_1620_cps", "snr", "snr_fixedwin_1620",
              "height_fixedwin_pct_diff", "overall",
              "recommended_m1", "recommended_m2"]].copy()
    comp = comp.rename(columns={"key": "condition", "snr": "snr_current",
                                "overall": "raw_qc_verdict"})
    comp.to_csv(out / ("comparison_%s.csv" % TAG), index=False)

    # ---- exp4 enhancement, both methods ------------------------------------
    e4cfg = e4.load_config()
    e4scans = e4.load_scans(e4cfg)
    e4tcfg = ts.load_config(ROOT / "configs" / e4cfg["screen"]["timeseries_config"])
    e4screen = e4.scan_screen(e4scans, e4.load_noise_model(e4cfg), e4cfg, e4tcfg)
    e4bands = e4.band_table(e4scans, e4screen, e4cfg)
    g1, g2 = gains_both(e4scans, e4screen, e4cfg, band, left, right)
    g1 = e4.annotate_control_quality(g1, e4bands, e4cfg)
    g2 = e4.annotate_control_quality(g2, e4bands, e4cfg)
    g1.to_csv(out / "sers_gain_method1.csv", index=False)
    g2.to_csv(out / ("sers_gain_%s.csv" % TAG), index=False)

    # ---- figures -----------------------------------------------------------
    fig_diagnostic(sweeps, bands, cfg, band, left, right,
                   out / ("01_baseline_diagnostic_%s.png" % TAG))
    fig_compare(bands, v1, v2, cfg, band,
                out / ("02_snr_and_height_compare_%s.png" % TAG))
    fig_recommendation(v1, v2, cfg, out / ("03_recommendation_%s.png" % TAG))
    fig_gain(g1, g2, band, e4cfg, out / ("04_sers_gain_%s.png" % TAG))
    fig_fixed_only(bands, v2, cfg, band, out / ("05_%s_only.png" % TAG))
    for paper in dict.fromkeys(w["paper"] for w in cfg["sweeps"]):
        fig_paper(bands, v2, cfg, band, paper,
                  out / ("06_%s_NP_compare_%s.png" % (paper, TAG)))

    pd.set_option("display.width", 220)
    print("=== RECOMMENDATION: method 1 vs method 2 ===")
    r = (v1[v1["recommended"]].set_index("label")[["int_time_s", "selection"]]
         .join(v2[v2["recommended"]].set_index("label")[["int_time_s", "selection"]],
               lsuffix="_m1", rsuffix="_m2"))
    print(r.to_string())
    print("\n=== 1620 HEIGHT / SNR ===")
    print(comp[["condition", "int_time_s", "height_local_cps",
                "height_fixedwin_1620_cps", "height_fixedwin_pct_diff",
                "snr_current", "snr_fixedwin_1620"]].round(1).to_string(index=False))
    a = g1[g1["peak_name"] == band].set_index(["key", "condition", "dilution"])
    b = g2[g2["peak_name"] == band].set_index(["key", "condition", "dilution"])
    j = a[["gain_x", "gain_trustworthy"]].join(
        b[["gain_x", "gain_trustworthy"]], lsuffix="_1", rsuffix="_2")
    print("\n=== GAIN at %d ===" % ALL_BANDS[band]["nominal"])
    print(j.round(2).to_string())
    print("\ntrustworthy flips: %d of %d"
          % (int((j["gain_trustworthy_1"] != j["gain_trustworthy_2"]).sum()), len(j)))
    print("\nranking m1:", j.groupby(level="condition")["gain_x_1"].median().round(2).to_dict())
    print("ranking m2:", j.groupby(level="condition")["gain_x_2"].median().round(2).to_dict())
    print("\nWrote " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
