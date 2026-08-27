#!/usr/bin/env python
"""exp1_cv_report.py - group-level report for experiment 1 (crystal violet on paper).

Reads the per-spectrum output that `process_raman.py --config peaks_cv_sers
--plate exp1_cv` already wrote, joins it to the acquisition metadata in
raw/exp1/exp1_full (integration time varied because AutoInt was On), and
answers the experimental question: which paper / concentration combination
gives the best crystal-violet SERS signal?

Run (from raman/):
    python exp1_cv_report.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "exp1_cv"
RUNS = RESULTS / "runs"
FULL_META = ROOT / "raw" / "exp1" / "exp1_full"
OUT = RESULTS / "report"
FIG = OUT / "figures"

# ---------------------------------------------------------------------------
# Literature CV band windows (from the assignment table for this experiment).
# `lit` is the acceptance window for calling an observed maximum a CV match;
# `tier` marks the six high-consistency "peaks to watch".
# ---------------------------------------------------------------------------
BANDS = {
    "cv_430":  dict(nominal=430,  lit=(420, 440),   search=(415, 448),   tier="secondary", assign="phenyl-C-phenyl deformation"),
    "cv_526":  dict(nominal=526,  lit=(522, 532),   search=(514, 540),   tier="secondary", assign="aromatic ring skeletal"),
    "cv_806":  dict(nominal=806,  lit=(795, 810),   search=(793, 815),   tier="watch",     assign="aromatic C-H deformation"),
    "cv_914":  dict(nominal=914,  lit=(905, 916),   search=(902, 922),   tier="watch",     assign="ring skeletal / C-H deformation"),
    "cv_1175": dict(nominal=1175, lit=(1171, 1180), search=(1163, 1188), tier="watch",     assign="aromatic C-H in-plane bending"),
    "cv_1375": dict(nominal=1375, lit=(1363, 1385), search=(1358, 1392), tier="watch",     assign="N-phenyl / C-N"),
    "cv_1587": dict(nominal=1587, lit=(1582, 1597), search=(1576, 1602), tier="watch",     assign="aromatic C=C / C-C stretching"),
    "cv_1620": dict(nominal=1620, lit=(1614, 1621), search=(1606, 1632), tier="watch",     assign="aromatic ring C-C stretching"),
}
BAND_ORDER = list(BANDS)
WATCH = [b for b, m in BANDS.items() if m["tier"] == "watch"]

# The five-band panel the experiment is actually scored on. Same physics and the
# same literature windows as BANDS, but 914 is labelled 915 to match the source
# table, 1587/430/526 are dropped, and each band carries its consistency rating.
# Figures 07-12 are the 01-06 set re-rendered against exactly these bands.
FOCUS_BANDS = {
    "cv_806":  dict(BANDS["cv_806"],  nominal=806,  stars="★★★★☆"),
    "cv_914":  dict(BANDS["cv_914"],  nominal=915,  stars="★" * 5),
    "cv_1175": dict(BANDS["cv_1175"], nominal=1175, stars="★" * 5),
    "cv_1375": dict(BANDS["cv_1375"], nominal=1375, stars="★" * 5),
    "cv_1620": dict(BANDS["cv_1620"], nominal=1620, stars="★" * 5),
}
FOCUS_ORDER = list(FOCUS_BANDS)

GROUPS = ["ow-dye", "ow-dilute", "ow-conc", "dw-dye", "dw-dilute", "dw-conc"]
GROUP_LABEL = {
    "ow-dye":    "Offwhite - CV dye ref",
    "ow-dilute": "Offwhite - dilute (1a-c)",
    "ow-conc":   "Offwhite - concentrated (2a-c)",
    "dw-dye":    "Desktop white - CV dye ref",
    "dw-dilute": "Desktop white - dilute (1a-c)",
    "dw-conc":   "Desktop white - concentrated (2a-c)",
}
# Each test group is compared against the dye reference on the SAME paper.
CONTROL_OF = {"ow-dilute": "ow-dye", "ow-conc": "ow-dye",
              "dw-dilute": "dw-dye", "dw-conc": "dw-dye"}
COLORS = {"ow-dye": "#9e9e9e", "ow-dilute": "#4fa3d1", "ow-conc": "#12507b",
          "dw-dye": "#c0a080", "dw-dilute": "#e08a3c", "dw-conc": "#a83232"}
# Two encoding channels, used consistently in every figure:
#   colour     -> condition (grey reference, green dilute, red concentrated)
#   dash/hatch -> paper     (offwhite dashed or hatched, desktop white solid)
COLORS_BY_CONDITION = {"dye": "#8c8c94", "dilute": "#1e8b57", "conc": "#c02f28"}
LINESTYLE_BY_PAPER = {"ow": (0, (5, 2)), "dw": "solid"}
HATCH_BY_PAPER = {"ow": "///", "dw": ""}


def style(group):
    """(colour, linestyle) for a group key like 'ow-conc'."""
    paper, cond = group.split("-")
    return COLORS_BY_CONDITION[cond], LINESTYLE_BY_PAPER[paper]


def bar_style(group):
    """(colour, hatch) for a group key - the bar-chart equivalent of style()."""
    paper, cond = group.split("-")
    return COLORS_BY_CONDITION[cond], HATCH_BY_PAPER[paper]

SNR_MIN = 3.0


# ---------------------------------------------------------------------------
# Acquisition metadata (IntTime varies -> compare counts per second, not counts)
# ---------------------------------------------------------------------------
def load_acquisition_meta() -> pd.DataFrame:
    rows = []
    for path in sorted(FULL_META.glob("*.csv")):
        text = path.read_text(encoding="utf-8", errors="replace")
        marker = '"Intensities"'
        head = text[: text.find(marker)] if marker in text else text[:20000]

        def field(key, default=None):
            m = re.search('"' + key + '","([^"]*)"', head)
            return m.group(1) if m else default

        scan = re.search(r"Scan[ _-]?0*(\d+)", path.name)
        matches = re.findall(r'"MixtureResults_\d+/Name","([^"]*)"', head)
        contrib = re.findall(r'"MixtureResults_\d+/RamanContribution","([^"]*)"', head)
        rows.append({
            "scan_number": int(scan.group(1)) if scan else None,
            "int_time_s": float(field("IntTime", "nan")),
            "auto_int": field("AutoInt"),
            "averages": int(field("Averages", "0") or 0),
            "laser_power": field("LaserPower"),
            "laser_nm": field("Wavelength"),
            "device_match": " + ".join(n + " (" + c + ")" for n, c in zip(matches, contrib)) or "(no match)",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Per-spectrum band table
# ---------------------------------------------------------------------------
def load_band_table(meta: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / "master_log.csv")
    df = df[df["config"] == "cv_sers"].copy()
    # keep only the newest run per (scan, band) in case the processor is re-run
    df = df.sort_values("timestamp").drop_duplicates(["scan_number", "peak_name"], keep="last")
    df = df.merge(meta, on="scan_number", how="left")

    df["group"] = df["column"]
    df["paper"] = np.where(df["group"].str.startswith("ow"), "offwhite", "desktop-white")
    df["condition"] = df["group"].str.split("-").str[1]
    # Prefer the sub-pixel Lorentzian centre, but fall back to the discrete
    # argmax when the fit ran outside its own search window - a Lorentzian
    # sitting on a shoulder can drift several cm^-1 past the window edge and
    # would otherwise be scored as an out-of-window (or in-window) match on the
    # strength of an extrapolation.
    s_lo = df["peak_name"].map(lambda b: BANDS[b]["search"][0])
    s_hi = df["peak_name"].map(lambda b: BANDS[b]["search"][1])
    fit_ok = df["fit_center_cm1"].between(s_lo, s_hi)
    df["observed_cm1"] = df["fit_center_cm1"].where(fit_ok, df["center_cm1"])
    df["lit_min"] = df["peak_name"].map(lambda b: BANDS[b]["lit"][0])
    df["lit_max"] = df["peak_name"].map(lambda b: BANDS[b]["lit"][1])
    df["tier"] = df["peak_name"].map(lambda b: BANDS[b]["tier"])
    df["assignment"] = df["peak_name"].map(lambda b: BANDS[b]["assign"])

    # counts/second - the only fair cross-scan intensity because IntTime varied
    df["height_cps"] = df["height"] / df["int_time_s"]
    df["area_cps"] = df["area"] / df["int_time_s"]

    df["in_window"] = df["observed_cm1"].between(df["lit_min"], df["lit_max"])
    df["snr_ok"] = df["snr"] >= SNR_MIN
    df["matched"] = df["in_window"] & df["snr_ok"] & (df["prominence"] > 0)
    df["offset_cm1"] = np.where(
        df["in_window"], 0.0,
        np.where(df["observed_cm1"] < df["lit_min"],
                 df["observed_cm1"] - df["lit_min"], df["observed_cm1"] - df["lit_max"]))
    return df


# ---------------------------------------------------------------------------
# Mean spectra per group (counts/second, baseline-corrected)
# ---------------------------------------------------------------------------
def _run_dir(label: str) -> Path:
    """Newest run directory for a label (the processor suffixes reruns)."""
    cands = sorted(RUNS.glob(label + "__cv_sers*"))
    if not cands:
        raise FileNotFoundError("no run directory for " + label)
    return cands[-1]


def load_group_spectra(df, per_second=True):
    """Per-spot and per-group baseline-corrected spectra.

    per_second=True divides by integration time (the fair cross-scan scale);
    per_second=False leaves raw detector counts, the conventional Raman axis.
    """
    per_scan = df.drop_duplicates("scan_number")[
        ["scan_number", "label", "group", "row", "int_time_s"]]
    grid = None
    spots, stacks, raws = {}, {g: [] for g in GROUPS}, {g: [] for g in GROUPS}
    for _, r in per_scan.iterrows():
        s = pd.read_csv(_run_dir(r["label"]) / (r["label"] + "_processed_spectrum.csv"))
        if grid is None:
            grid = s["wavelength_cm1"].to_numpy(float)
        scale = r["int_time_s"] if per_second else 1.0
        y = np.interp(grid, s["wavelength_cm1"], s["smoothed"]) / scale
        yr = np.interp(grid, s["wavelength_cm1"], s["raw"]) / scale
        spots[int(r["scan_number"])] = {"group": r["group"], "row": r["row"], "y": y}
        stacks[r["group"]].append(y)
        raws[r["group"]].append(yr)
    means = {g: np.mean(v, axis=0) for g, v in stacks.items() if v}
    raw_means = {g: np.mean(v, axis=0) for g, v in raws.items() if v}
    return grid, spots, means, raw_means


def _mad_sigma(y: np.ndarray) -> float:
    d = np.diff(y)
    return float(np.median(np.abs(d - np.median(d))) * 1.4826 / np.sqrt(2.0))


def delta_table(grid, spots, means) -> pd.DataFrame:
    """Control-subtracted band gains, one row per (spot, band).

    Every test spot is differenced against the mean CV-dye reference spectrum
    of the SAME paper. Bands that belong to the paper (cellulose, carbonate
    filler, optical brightener) cancel; only what the coating actually added
    survives. This is the metric the substrate comparison rests on, because the
    absolute band heights are contaminated by substrate bands that sit inside
    several of the CV windows.
    """
    rows = []
    for scan, rec in spots.items():
        g = rec["group"]
        if g not in CONTROL_OF:
            continue
        d = rec["y"] - means[CONTROL_OF[g]]
        sigma = _mad_sigma(d[(grid >= 400) & (grid <= 1800)])
        for band, m in BANDS.items():
            # Search the WIDE window so the true band centre is located even
            # when it sits just outside the literature range, then report how
            # far off it is. Searching only the literature window would pin the
            # maximum to a boundary and hide the offset.
            lo, hi = m["search"]
            sel = np.where((grid >= lo) & (grid <= hi))[0]
            local = d[sel]
            # Require a genuine interior maximum; a bare argmax on a monotonic
            # slope just reports the window edge.
            loc, _ = find_peaks(local)
            if loc.size:
                j, at_edge = int(loc[int(np.argmax(local[loc]))]), False
            else:
                j = int(np.argmax(local))
                at_edge = j in (0, local.size - 1)
            centre = float(grid[sel][j])
            lit_lo, lit_hi = m["lit"]
            in_lit = lit_lo <= centre <= lit_hi
            offset = 0.0 if in_lit else (centre - lit_lo if centre < lit_lo else centre - lit_hi)
            rows.append({
                "scan_number": scan, "group": g, "row": rec["row"], "peak_name": band,
                "tier": m["tier"], "nominal_cm1": m["nominal"],
                "delta_center_cm1": centre,
                "in_lit_window": in_lit,
                "offset_from_lit_cm1": offset,
                "delta_cps": float(local[j]),
                "delta_sigma_cps": sigma,
                "delta_snr": float(local[j] / sigma) if sigma else np.nan,
                "at_window_edge": at_edge,
            })
    out = pd.DataFrame(rows)
    # A band counts as gained when the coating added a genuine, >=3 sigma
    # interior maximum; it counts as a CV match when that maximum also lands
    # inside the published range for the assignment.
    out["delta_detected"] = (out["delta_snr"] >= SNR_MIN) & ~out["at_window_edge"]
    out["cv_match"] = out["delta_detected"] & out["in_lit_window"]
    return out


def delta_summary(dl: pd.DataFrame) -> pd.DataFrame:
    g = dl.groupby(["group", "peak_name"], as_index=False).agg(
        n=("scan_number", "size"),
        n_delta_detected=("delta_detected", "sum"),
        n_cv_match=("cv_match", "sum"),
        mean_offset_cm1=("offset_from_lit_cm1", "mean"),
        mean_delta_cps=("delta_cps", "mean"),
        sd_delta_cps=("delta_cps", "std"),
        mean_delta_snr=("delta_snr", "mean"),
        mean_delta_center=("delta_center_cm1", "mean"),
        sd_delta_center=("delta_center_cm1", "std"),
    )
    g["band_order"] = g["peak_name"].map(BAND_ORDER.index)
    g["group_order"] = g["group"].map(GROUPS.index)
    return g.sort_values(["group_order", "band_order"]).drop(columns=["band_order", "group_order"])


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def group_band_summary(df: pd.DataFrame, per_second=True) -> pd.DataFrame:
    # The mean_height_cps / mean_area_cps columns carry whichever scale was
    # requested; only the per_second=True result is written to CSV under that
    # name, so the published files stay unambiguous.
    hcol = "height_cps" if per_second else "height"
    acol = "area_cps" if per_second else "area"
    g = df.groupby(["group", "peak_name"], as_index=False).agg(
        n=("scan_number", "size"),
        n_matched=("matched", "sum"),
        mean_center=("observed_cm1", "mean"),
        sd_center=("observed_cm1", "std"),
        mean_height_cps=(hcol, "mean"),
        sd_height_cps=(hcol, "std"),
        mean_area_cps=(acol, "mean"),
        mean_snr=("snr", "mean"),
        mean_fwhm=("fwhm_cm1", "mean"),
    )
    ctrl = g[["group", "peak_name", "mean_height_cps"]].rename(
        columns={"group": "control", "mean_height_cps": "control_height_cps"})
    g["control"] = g["group"].map(CONTROL_OF)
    g = g.merge(ctrl, on=["control", "peak_name"], how="left")
    g["gain_vs_control"] = g["mean_height_cps"] / g["control_height_cps"]
    g["band_order"] = g["peak_name"].map(BAND_ORDER.index)
    g["group_order"] = g["group"].map(GROUPS.index)
    return g.sort_values(["group_order", "band_order"]).drop(columns=["band_order", "group_order"])


def group_overall(df: pd.DataFrame) -> pd.DataFrame:
    w = df[df["peak_name"].isin(WATCH)]
    per_scan = w.groupby(["group", "scan_number"], as_index=False).agg(
        n_watch_matched=("matched", "sum"), watch_cps=("height_cps", "sum"))
    out = per_scan.groupby("group", as_index=False).agg(
        spots=("scan_number", "size"),
        watch_matched_mean=("n_watch_matched", "mean"),
        watch_matched_min=("n_watch_matched", "min"),
        watch_matched_max=("n_watch_matched", "max"),
        summed_watch_cps=("watch_cps", "mean"),
        sd_watch_cps=("watch_cps", "std"),
    )
    out["rel_sd_pct"] = 100 * out["sd_watch_cps"] / out["summed_watch_cps"]
    ctrl = out.set_index("group")["summed_watch_cps"]
    out["gain_vs_same_paper_dye"] = out.apply(
        lambda r: r["summed_watch_cps"] / ctrl[CONTROL_OF[r["group"]]]
        if r["group"] in CONTROL_OF else np.nan, axis=1)
    out["group_order"] = out["group"].map(GROUPS.index)
    return out.sort_values("group_order").drop(columns="group_order")


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _tick(meta, show_stars=True):
    """X tick label: wavenumber, plus the consistency rating when the band set
    carries one, plus a secondary marker when it does not."""
    if show_stars and meta.get("stars"):
        return "%d\n%s" % (meta["nominal"], meta["stars"])
    return str(meta["nominal"]) + ("" if meta["tier"] == "watch" else "*")


def _xnote(bands, show_stars=True):
    if show_stars and any(m.get("stars") for m in bands.values()):
        return "   (stars = literature consistency across studies)"
    if any(m["tier"] != "watch" for m in bands.values()):
        return "   * = secondary band"
    return ""


def fig_mean_spectra(grid, means, path, bands=BANDS, subtitle="",
                     ylabel="counts / s", title_unit="counts/s"):
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    panels = ((axes[0], ["ow-dye", "ow-dilute", "ow-conc"], "Offwhite paper"),
              (axes[1], ["dw-dye", "dw-dilute", "dw-conc"], "Desktop white paper"))
    for ax, papers, title in panels:
        for g in papers:
            c, ls = style(g)
            ax.plot(grid, means[g], lw=1.3, color=c, linestyle=ls, label=GROUP_LABEL[g])
        for b, m in bands.items():
            ax.axvspan(m["lit"][0], m["lit"][1],
                       color="tab:green" if m["tier"] == "watch" else "tab:olive",
                       alpha=0.13, lw=0)
            ax.annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                        xycoords=("data", "axes fraction"), ha="center",
                        fontsize=7, color="dimgray")
        ax.set_title(title + " - mean baseline-corrected spectrum (n=3 spots), "
                     + title_unit + subtitle)
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(alpha=0.25)
    axes[1].set_xlabel("Wavenumber (cm$^{-1}$)")
    axes[1].set_xlim(400, 1800)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_band_zoom(grid, means, path, bands=BANDS, unit="counts/s"):
    order = list(bands)
    ncols = len(order) if len(order) <= 5 else 4
    nrows = int(np.ceil(len(order) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.3 * ncols, 3.6 * nrows + 0.4),
                             squeeze=False)
    flat = axes.ravel()
    for spare in flat[len(order):]:
        spare.set_visible(False)
    for ax, band in zip(flat, order):
        m = bands[band]
        lo, hi = m["lit"][0] - 30, m["lit"][1] + 30
        sel = (grid >= lo) & (grid <= hi)
        for g in GROUPS:
            c, ls = style(g)
            ax.plot(grid[sel], means[g][sel], lw=1.4, color=c, linestyle=ls,
                    label=GROUP_LABEL[g])
        ax.axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.15, lw=0)
        tag = m.get("stars") or ("" if m["tier"] == "watch" else "(secondary)")
        ax.set_title("%d cm$^{-1}$ - %s\nlit %d-%d  %s"
                     % (m["nominal"], m["assign"], m["lit"][0], m["lit"][1], tag), fontsize=9)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
    flat[0].legend(fontsize=7)
    fig.suptitle("CV band windows - group mean baseline-corrected intensity (%s)" % unit,
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_band_bars(summ, path, bands=BANDS, show_stars=True,
                  ylabel="peak height (counts/s), mean $\\pm$ sd of 3 spots"):
    order = list(bands)
    fig, ax = plt.subplots(figsize=(max(9.5, 1.65 * len(order) + 4), 6))
    n = len(GROUPS)
    width = 0.8 / n
    xs = np.arange(len(order))
    for i, g in enumerate(GROUPS):
        sub = summ[summ["group"] == g].set_index("peak_name").reindex(order)
        c, hatch = bar_style(g)
        ax.bar(xs + i * width - 0.4 + width / 2, sub["mean_height_cps"], width,
               yerr=sub["sd_height_cps"], capsize=2, color=c, hatch=hatch,
               edgecolor="white", linewidth=.5, label=GROUP_LABEL[g])
    ax.set_xticks(xs)
    ax.set_xticklabels([_tick(bands[b], show_stars) for b in order])
    ax.set_xlabel("Wavenumber (cm$^{-1}$)" + _xnote(bands, show_stars))
    ax.set_ylabel(ylabel)
    ax.set_title("Baseline-corrected CV band intensity by sample group")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_difference(grid, means, path, bands=BANDS,
                   ylabel="$\\Delta$ counts / s"):
    fig, ax = plt.subplots(figsize=(14, 6))
    for g, ctrl in CONTROL_OF.items():
        c, ls = style(g)
        ax.plot(grid, means[g] - means[ctrl], lw=1.3, color=c, linestyle=ls,
                label=GROUP_LABEL[g] + "  -  " + GROUP_LABEL[ctrl])
    for b, m in bands.items():
        ax.axvspan(m["lit"][0], m["lit"][1],
                   color="tab:green" if m["tier"] == "watch" else "tab:olive", alpha=0.13, lw=0)
        ax.annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                    xycoords=("data", "axes fraction"), ha="center", fontsize=7, color="dimgray")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlim(400, 1800)
    ax.set_xlabel("Wavenumber (cm$^{-1}$)")
    ax.set_ylabel(ylabel)
    ax.set_title("Difference spectra: each test group minus the CV-dye reference on the same paper")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_delta_bars(dsum, path, bands=BANDS,
                   ylabel="$\\Delta$ counts/s vs same-paper CV-dye reference"):
    order = list(bands)
    test_groups = [g for g in GROUPS if g in CONTROL_OF]
    fig, ax = plt.subplots(figsize=(max(9.5, 1.65 * len(order) + 4), 6))
    width = 0.8 / len(test_groups)
    xs = np.arange(len(order))
    for i, g in enumerate(test_groups):
        sub = dsum[dsum["group"] == g].set_index("peak_name").reindex(order)
        c, hatch = bar_style(g)
        ax.bar(xs + i * width - 0.4 + width / 2, sub["mean_delta_cps"], width,
               yerr=sub["sd_delta_cps"], capsize=2, color=c, hatch=hatch,
               edgecolor="white", linewidth=.5, label=GROUP_LABEL[g])
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(xs)
    ax.set_xticklabels([_tick(bands[b]) for b in order])
    ax.set_xlabel("Wavenumber (cm$^{-1}$)" + _xnote(bands))
    ax.set_ylabel(ylabel)
    ax.set_title("Substrate-corrected CV band gain (mean $\\pm$ sd of 3 spots)")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_scale_explainer(label, int_time, path, bands=FOCUS_BANDS):
    """Why the instrument screen reads ~40k and these figures read ~1,500.

    MIRA Cal DS plots the raw detector trace, which is dominated by a
    fluorescence background running from ~70k down to ~17k counts. Every figure
    in this report plots what is left after that background is subtracted. Same
    data, two vertical scales roughly 40x apart.
    """
    s = pd.read_csv(_run_dir(label) / (label + "_processed_spectrum.csv"))
    x = s["wavelength_cm1"].to_numpy()
    raw, base, corr = s["raw"].to_numpy(), s["baseline"].to_numpy(), s["corrected"].to_numpy()
    i = int(np.argmin(np.abs(x - 1617)))
    peak = raw[i] - base[i]

    fig, axes = plt.subplots(2, 1, figsize=(13, 8.5), sharex=True)

    axes[0].plot(x, raw, lw=1.2, color="#3b3b45", label="raw detector signal")
    axes[0].plot(x, base, lw=1.4, ls=(0, (5, 2)), color="#c02f28",
                 label="fitted fluorescence baseline (arPLS)")
    axes[0].set_ylim(0, raw.max() * 1.05)
    axes[0].set_ylabel("Intensity (counts)")
    axes[0].set_title("What MIRA Cal DS plots - raw signal, dominated by fluorescence", fontsize=11)
    axes[0].legend(fontsize=9, loc="upper right")
    axes[0].grid(alpha=0.25)
    axes[0].annotate(
        "the 1620 CV band is here:\n"
        "{:,.0f} counts on a {:,.0f} count background\n"
        "(a {:.1f}% wiggle on this axis)".format(
            peak, base[i], 100 * peak / raw.max()),
        xy=(x[i], raw[i]), xytext=(x[i] - 620, raw.max() * 0.62), fontsize=9,
        ha="left", arrowprops=dict(arrowstyle="->", color="#1e8b57", lw=1.4), color="#1e8b57")

    axes[1].plot(x, corr, lw=1.2, color="#1e8b57", label="baseline-corrected signal")
    axes[1].axhline(0, color="k", lw=0.8)
    for m in bands.values():
        axes[1].axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.15, lw=0)
        axes[1].annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                         xycoords=("data", "axes fraction"), ha="center",
                         fontsize=7, color="dimgray")
    axes[1].set_ylabel("Intensity (counts)")
    axes[1].set_xlabel("Wavenumber (cm$^{-1}$)")
    axes[1].set_title("What every figure in this report plots - the same data, background removed",
                      fontsize=11)
    axes[1].legend(fontsize=9, loc="upper left")
    axes[1].grid(alpha=0.25)
    axes[1].annotate("{:,.0f} counts".format(peak), xy=(x[i], corr[i]),
                     xytext=(x[i] - 380, corr.max() * 0.88), fontsize=9, color="#c02f28",
                     arrowprops=dict(arrowstyle="->", color="#c02f28", lw=1.4))
    axes[1].set_xlim(400, 1800)

    fig.suptitle("Raw vs baseline-corrected scale - %s, integration %.2f s"
                 % (label, int_time), fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_background(grid, raw_means, df, path, bands=None,
                   ylabel="raw counts / s"):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    # Only the focus variant passes bands: it marks where the scored windows sit
    # relative to the fluorescence background they have to be dug out of.
    for m in (bands or {}).values():
        axes[0].axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.16, lw=0)
        axes[0].annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                         xycoords=("data", "axes fraction"), ha="center",
                         fontsize=7, color="dimgray")
    for g in GROUPS:
        c, ls = style(g)
        axes[0].plot(grid, raw_means[g], lw=1.2, color=c, linestyle=ls, label=GROUP_LABEL[g])
    axes[0].set_xlabel("Wavenumber (cm$^{-1}$)")
    axes[0].set_ylabel(ylabel)
    axes[0].set_title("Raw signal (fluorescence background dominates)")
    axes[0].legend(fontsize=7)
    axes[0].grid(alpha=0.25)

    per_scan = df.drop_duplicates("scan_number")
    for g in GROUPS:
        sub = per_scan[per_scan["group"] == g]
        c, _ = style(g)
        ow = g.startswith("ow")
        axes[1].scatter(sub["scan_number"], sub["int_time_s"], s=55,
                        facecolor="none" if ow else c, edgecolor=c,
                        linewidth=1.6, label=GROUP_LABEL[g])
    axes[1].set_xlabel("scan number")
    axes[1].set_ylabel("integration time (s)")
    axes[1].set_title("Auto-integration time per scan\n(shorter = instrument saw a brighter sample)")
    axes[1].grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)

    meta = load_acquisition_meta()
    df = load_band_table(meta)
    summ = group_band_summary(df)
    overall = group_overall(df)
    grid, spots, means, raw_means = load_group_spectra(df)
    dl = delta_table(grid, spots, means)
    dsum = delta_summary(dl)

    def ranking(band_keys):
        """Substrate-corrected ranking over an arbitrary band panel."""
        w = dl[dl["peak_name"].isin(band_keys)]
        per_spot = w.groupby(["group", "scan_number"], as_index=False).agg(
            n_delta_detected=("delta_detected", "sum"),
            n_cv_match=("cv_match", "sum"),
            delta_cps=("delta_cps", "sum"))
        out = per_spot.groupby("group", as_index=False).agg(
            spots=("scan_number", "size"),
            bands_gained_mean=("n_delta_detected", "mean"),
            bands_gained_min=("n_delta_detected", "min"),
            cv_matches_mean=("n_cv_match", "mean"),
            summed_delta_cps=("delta_cps", "mean"),
            sd_delta_cps=("delta_cps", "std"))
        out["rel_sd_pct"] = 100 * out["sd_delta_cps"] / out["summed_delta_cps"]
        out["n_bands"] = len(band_keys)
        return out.sort_values("summed_delta_cps", ascending=False)

    # Ranking over the six high-consistency bands, and over the five scored ones.
    rank = ranking(WATCH)
    focus_rank = ranking(FOCUS_ORDER)

    cols = ["scan_number", "group", "paper", "condition", "row", "peak_name", "assignment", "tier",
            "observed_cm1", "lit_min", "lit_max", "in_window", "offset_cm1", "snr", "matched",
            "height", "height_cps", "area_cps", "fwhm_cm1", "prominence", "int_time_s"]
    df[cols].to_csv(OUT / "per_spectrum_bands.csv", index=False)
    summ.to_csv(OUT / "group_band_summary.csv", index=False)
    overall.to_csv(OUT / "group_overall_summary.csv", index=False)
    dl.to_csv(OUT / "per_spot_band_gain.csv", index=False)
    dsum.to_csv(OUT / "group_band_gain_summary.csv", index=False)
    rank.to_csv(OUT / "substrate_ranking.csv", index=False)
    focus_rank.to_csv(OUT / "substrate_ranking_focus5.csv", index=False)
    meta.to_csv(OUT / "acquisition_metadata.csv", index=False)
    spec_out = {"wavenumber_cm1": grid}
    spec_out.update({g: means[g] for g in GROUPS})
    pd.DataFrame(spec_out).to_csv(OUT / "group_mean_spectra_cps.csv", index=False)

    # 01-06: the full eight-band set.
    fig_mean_spectra(grid, means, FIG / "01_group_mean_spectra.png")
    fig_band_zoom(grid, means, FIG / "02_band_windows_zoom.png")
    fig_band_bars(summ, FIG / "03_band_intensity_by_group.png")
    fig_difference(grid, means, FIG / "04_difference_vs_dye_reference.png")
    fig_background(grid, raw_means, df, FIG / "05_background_and_integration_time.png")
    fig_delta_bars(dsum, FIG / "06_substrate_corrected_band_gain.png")

    # 07-12: the same six views restricted to the five scored bands.
    focus_note = "   |   focus: 806 / 915 / 1175 / 1375 / 1620 cm-1"
    fig_mean_spectra(grid, means, FIG / "07_focus_group_mean_spectra.png",
                     bands=FOCUS_BANDS, subtitle=focus_note)
    fig_band_zoom(grid, means, FIG / "08_focus_band_windows_zoom.png", bands=FOCUS_BANDS)
    # 09 shows uncorrected heights, which say nothing about literature
    # consistency - the star ratings would only invite a false connection.
    fig_band_bars(summ, FIG / "09_focus_band_intensity_by_group.png",
                  bands=FOCUS_BANDS, show_stars=False)
    fig_difference(grid, means, FIG / "10_focus_difference_vs_dye_reference.png",
                   bands=FOCUS_BANDS)
    fig_background(grid, raw_means, df, FIG / "11_focus_background_and_integration_time.png",
                   bands=FOCUS_BANDS)
    fig_delta_bars(dsum, FIG / "12_focus_substrate_corrected_band_gain.png", bands=FOCUS_BANDS)

    # ---------------------------------------------------------------------
    # 13-24: the same twelve views on the conventional Raman axis - raw
    # detector counts, exposure NOT divided out. Directly comparable to how a
    # spectrum is normally published, but remember that integration time varied
    # across this run, so cross-sample intensity here is not exposure-corrected.
    # ---------------------------------------------------------------------
    c_grid, c_spots, c_means, c_raw_means = load_group_spectra(df, per_second=False)
    c_dl = delta_table(c_grid, c_spots, c_means)
    c_dsum = delta_summary(c_dl)
    c_summ = group_band_summary(df, per_second=False)

    Y_SPEC = "Intensity (counts)"
    Y_BARS = "Peak intensity (counts), mean $\\pm$ sd of 3 spots"
    Y_DIFF = "$\\Delta$ Intensity (counts)"
    Y_GAIN = "$\\Delta$ Intensity (counts) vs same-paper CV-dye reference"
    Y_RAW = "Raw intensity (counts)"

    for tag, bset, star in ((13, BANDS, True), (19, FOCUS_BANDS, True)):
        pre = "" if bset is BANDS else "focus_"
        note = "" if bset is BANDS else "   |   focus: 806 / 915 / 1175 / 1375 / 1620 cm-1"
        fig_mean_spectra(c_grid, c_means, FIG / ("%02d_%scounts_group_mean_spectra.png" % (tag, pre)),
                         bands=bset, subtitle=note, ylabel=Y_SPEC, title_unit="counts")
        fig_band_zoom(c_grid, c_means, FIG / ("%02d_%scounts_band_windows_zoom.png" % (tag + 1, pre)),
                      bands=bset, unit="counts")
        fig_band_bars(c_summ, FIG / ("%02d_%scounts_band_intensity_by_group.png" % (tag + 2, pre)),
                      bands=bset, show_stars=(bset is not FOCUS_BANDS), ylabel=Y_BARS)
        fig_difference(c_grid, c_means,
                       FIG / ("%02d_%scounts_difference_vs_dye_reference.png" % (tag + 3, pre)),
                       bands=bset, ylabel=Y_DIFF)
        fig_background(c_grid, c_raw_means, df,
                       FIG / ("%02d_%scounts_background_and_integration_time.png" % (tag + 4, pre)),
                       bands=(bset if bset is FOCUS_BANDS else None), ylabel=Y_RAW)
        fig_delta_bars(c_dsum,
                       FIG / ("%02d_%scounts_substrate_corrected_band_gain.png" % (tag + 5, pre)),
                       bands=bset, ylabel=Y_GAIN)

    c_summ.to_csv(OUT / "group_band_summary_counts.csv", index=False)
    c_dsum.to_csv(OUT / "group_band_gain_summary_counts.csv", index=False)

    # 25: reconciles the instrument screen (~40k) with these figures (~1,500).
    ref = df[df["scan_number"] == 734].iloc[0]
    fig_scale_explainer(ref["label"], float(ref["int_time_s"]),
                        FIG / "25_raw_vs_baseline_corrected_scale.png")

    (OUT / "run_context.json").write_text(json.dumps({
        "config": "configs/peaks_cv_sers.yaml",
        "plate": "plates/exp1_cv.yaml",
        "input": "raw/exp1/exp1",
        "snr_threshold": SNR_MIN,
        "bands": {k: dict(nominal=v["nominal"], lit=list(v["lit"]),
                          tier=v["tier"], assign=v["assign"]) for k, v in BANDS.items()},
        "groups": GROUP_LABEL,
        "controls": CONTROL_OF,
    }, indent=2), encoding="utf-8")

    pd.set_option("display.width", 240)
    fmt = lambda v: "%.2f" % v
    print("\n=== SUBSTRATE RANKING (six watch bands, substrate-corrected) ===")
    print(rank.to_string(index=False, float_format=fmt))
    print("\n=== SUBSTRATE RANKING (focus five: 806/915/1175/1375/1620) ===")
    print(focus_rank.to_string(index=False, float_format=fmt))
    print("\n=== SUBSTRATE-CORRECTED BAND GAIN ===")
    print(dsum.to_string(index=False, float_format=fmt))
    print("\n=== GROUP OVERALL (absolute, six watch bands) ===")
    print(overall.to_string(index=False, float_format=fmt))
    print("\n=== PER-BAND GROUP SUMMARY (absolute) ===")
    print(summ.to_string(index=False, float_format=fmt))
    print("\nWrote " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
