#!/usr/bin/env python
"""exp2_dilution_report.py - CV dye dilution series, NP+dye vs dye-only.

Experiment 2 (082726): 32 spectra, two papers x two series x eight dilutions.
Column 3 = 5 uL nanoparticles + dye, column 4 = dye only, both run 2x -> 200x.

  offwhite  scans 747-754 (NP)   755-762 (dye only)
  white     scans 763-770 (NP)   771-778 (dye only)

The design differs from exp 1 in two ways that drive the analysis:

  * The control is PAIRED. Each NP spot has a dye-only spot at the same
    dilution on the same paper, so NP - dye-only isolates the nanoparticle
    enhancement directly, with no need for a separate substrate reference.
  * There is ONE spot per cell. No replicates means no error bars; scatter is
    judged from each spectrum's own noise sigma instead, and the dye-only
    series doubles as a visible estimate of spot-to-spot variability.

Exposure differs between the papers: offwhite ran a flat 10.00 s, but AutoInt
fired on white (4.49-10.00 s) because it fluoresces far harder. Every figure is
therefore rendered at three intensity scales, into three sibling folders, so
they can be compared without overwriting one another - see SCALES below.

Band definitions are imported from exp1_cv_report so both experiments score
against exactly the same windows.

Run (from raman/):
    python exp2_dilution_report.py
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
from matplotlib.lines import Line2D
from scipy.signal import find_peaks

from exp1_cv_report import BANDS, FOCUS_BANDS, FOCUS_ORDER, BAND_ORDER, _tick

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "exp2_dilution"
RUNS = RESULTS / "runs"
RAW = ROOT / "raw" / "082726_dyedilutioncomparison" / "082726_dyedilutioncomparison"
OUT = RESULTS / "report"

SNR_MIN = 3.0

# ---------------------------------------------------------------------------
# Intensity scales. Every figure is rendered once per scale into its own
# folder, so the three can be compared side by side without overwriting.
#
#   cps     I / t_i            counts per second - exposure divided out
#   counts  I                  raw detector counts, the conventional Raman axis
#   tmin    I * (t_min / t_i)  equivalent counts at the SHORTEST exposure in
#                              the run; t_min is computed from the loaded
#                              metadata, not hardcoded
#
# Note that `tmin` is `cps` multiplied by the constant t_min, so the two produce
# identical curve SHAPES and differ only in the numbers on the y-axis. The scale
# that differs in shape is `counts`, because there the exposure is left in.
# ---------------------------------------------------------------------------
SCALES = {
    "cps": dict(folder="figures_cps", kind="cps",
                unit="counts s$^{-1}$",
                blurb="rate - measured counts divided by each scan's exposure"),
    "counts": dict(folder="figures_counts", kind="counts",
                   unit="counts",
                   blurb="raw detector counts - exposure NOT divided out"),
    "tmin": dict(folder="figures_tmin", kind="tmin",
                 unit=None,   # filled in once t_min is known
                 blurb="equivalent counts at the shortest exposure in the run"),
}

T_MIN = None          # set in main() from the acquisition metadata
Y_INT = Y_PEAK = Y_DELTA = Y_RAW = None


def scale_factor(int_time, kind):
    """Multiplier applied to a measured intensity for the chosen scale."""
    if kind == "cps":
        return 1.0 / int_time
    if kind == "tmin":
        return T_MIN / int_time
    return 1.0


def set_scale(name):
    """Point the module-level axis labels at one scale. Called once per pass."""
    global Y_INT, Y_PEAK, Y_DELTA, Y_RAW
    spec = SCALES[name]
    unit = spec["unit"]
    if unit is None:                      # tmin, which needs the measured t_min
        unit = "counts, normalised to %.2f s" % T_MIN
    Y_INT = "Intensity (%s)" % unit
    Y_PEAK = "Peak intensity (%s)" % unit
    Y_DELTA = "$\\Delta$ Intensity (%s)" % unit
    Y_RAW = "Raw intensity (%s)" % unit
    return spec


DILUTIONS = ["2x", "5x", "10x", "20x", "30x", "50x", "100x", "200x"]
FACTOR = {d: float(d[:-1]) for d in DILUTIONS}

# The two exports sit side by side in each raw folder; these globs pick the
# metadata half, whose timestamps differ per acquisition session.
PAPERS = {
    "offwhite": dict(label="Offwhite", short="Offwhite", raw=RAW / "offwhite",
                     meta_glob="*_11_30_44_*.csv", config="cv_sers_exp2",
                     np_col="ow-np", dye_col="ow-dyeonly"),
    "white": dict(label="White", short="White", raw=RAW / "white",
                  meta_glob="*_13_53_1*.csv", config="cv_sers_exp2_white",
                  np_col="w-np", dye_col="w-dyeonly"),
}

SERIES = ["np", "dyeonly"]
SERIES_LABEL = {"np": "NP + dye (5 uL nanoparticles)", "dyeonly": "Dye only"}
# Same convention as exp 1: grey is the unenhanced control, red the treatment.
SERIES_COLOR = {"np": "#c02f28", "dyeonly": "#8c8c94"}
SERIES_STYLE = {"np": "solid", "dyeonly": (0, (5, 2))}
SERIES_CMAP = {"np": "Reds", "dyeonly": "Greys"}
# Paper is the second contrast, used only in the cross-paper figures.
PAPER_COLOR = {"offwhite": "#1e8b57", "white": "#7a1f8f"}
PAPER_STYLE = {"offwhite": (0, (5, 2)), "white": "solid"}


def dilution_colors(series):
    cmap = plt.get_cmap(SERIES_CMAP[series])
    return {d: cmap(0.92 - 0.62 * i / (len(DILUTIONS) - 1)) for i, d in enumerate(DILUTIONS)}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_acquisition_meta(paper: str) -> pd.DataFrame:
    spec = PAPERS[paper]
    rows = []
    for path in sorted(spec["raw"].glob(spec["meta_glob"])):
        head = path.read_text(encoding="utf-8", errors="replace")
        marker = '"Intensities"'
        head = head[: head.find(marker)] if marker in head else head[:20000]

        def field(key, default=None):
            m = re.search('"' + key + '","([^"]*)"', head)
            return m.group(1) if m else default

        scan = re.search(r"Scan[ _-]?0*(\d+)", path.name)
        names = re.findall(r'"MixtureResults_\d+/Name","([^"]*)"', head)
        contrib = re.findall(r'"MixtureResults_\d+/RamanContribution","([^"]*)"', head)
        rows.append({
            "scan_number": int(scan.group(1)) if scan else None,
            "paper": paper,
            "int_time_s": float(field("IntTime", "nan")),
            "auto_int": field("AutoInt"),
            "averages": int(field("Averages", "0") or 0),
            "laser_power": field("LaserPower"),
            "device_match": " + ".join(n + " (" + c + ")" for n, c in zip(names, contrib))
                            or "(no match)",
        })
    return pd.DataFrame(rows)


def load_band_table(meta: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / "master_log.csv")
    df = df.sort_values("timestamp").drop_duplicates(["scan_number", "peak_name"], keep="last")
    df = df.merge(meta.drop(columns=["paper"]), on="scan_number", how="left")

    col2paper = {v["np_col"]: k for k, v in PAPERS.items()}
    col2paper.update({v["dye_col"]: k for k, v in PAPERS.items()})
    col2series = {v["np_col"]: "np" for v in PAPERS.values()}
    col2series.update({v["dye_col"]: "dyeonly" for v in PAPERS.values()})

    df["paper"] = df["column"].map(col2paper)
    df["series"] = df["column"].map(col2series)
    df["dilution"] = df["row"]
    df["factor"] = df["dilution"].map(FACTOR)

    s_lo = df["peak_name"].map(lambda b: BANDS[b]["search"][0])
    s_hi = df["peak_name"].map(lambda b: BANDS[b]["search"][1])
    fit_ok = df["fit_center_cm1"].between(s_lo, s_hi)
    df["observed_cm1"] = df["fit_center_cm1"].where(fit_ok, df["center_cm1"])
    df["lit_min"] = df["peak_name"].map(lambda b: BANDS[b]["lit"][0])
    df["lit_max"] = df["peak_name"].map(lambda b: BANDS[b]["lit"][1])
    df["nominal"] = df["peak_name"].map(lambda b: BANDS[b]["nominal"])

    df["height_cps"] = df["height"] / df["int_time_s"]
    df["area_cps"] = df["area"] / df["int_time_s"]

    df["in_window"] = df["observed_cm1"].between(df["lit_min"], df["lit_max"])
    df["snr_ok"] = df["snr"] >= SNR_MIN
    df["detected"] = df["in_window"] & df["snr_ok"] & (df["prominence"] > 0)
    return df


def apply_scale(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Add the `intensity` column every figure reads, for one scale."""
    out = df.copy()
    out["intensity"] = out["height"] * out["int_time_s"].map(
        lambda ti: scale_factor(ti, kind))
    return out


def _run_dir(label: str, config: str) -> Path:
    cands = sorted(RUNS.glob(label + "__" + config + "*"))
    if not cands:
        raise FileNotFoundError("no run directory for " + label)
    return cands[-1]


def load_spectra(df, paper, kind="cps"):
    """(grid, {(series,dilution): corrected}, {...: raw}) for one paper.

    Each spectrum is scaled by its OWN integration time before it is stored, so
    any later averaging of replicates operates on already-normalised traces.
    (This dataset has one spot per cell, so there is nothing to average here -
    the ordering matters for designs that do have replicates.)
    """
    cfg = PAPERS[paper]["config"]
    sub = df[df["paper"] == paper].drop_duplicates("scan_number")[
        ["scan_number", "label", "series", "dilution", "int_time_s"]]
    grid, corr, raw = None, {}, {}
    for _, r in sub.iterrows():
        s = pd.read_csv(_run_dir(r["label"], cfg) / (r["label"] + "_processed_spectrum.csv"))
        if grid is None:
            grid = s["wavelength_cm1"].to_numpy(float)
        key = (r["series"], r["dilution"])
        f = scale_factor(r["int_time_s"], kind)
        corr[key] = np.interp(grid, s["wavelength_cm1"], s["smoothed"]) * f
        raw[key] = np.interp(grid, s["wavelength_cm1"], s["raw"]) * f
    return grid, corr, raw


def _mad_sigma(y):
    d = np.diff(y)
    return float(np.median(np.abs(d - np.median(d))) * 1.4826 / np.sqrt(2.0))


# ---------------------------------------------------------------------------
# Paired enhancement + detection limits
# ---------------------------------------------------------------------------
def enhancement_table(grid, corr, paper) -> pd.DataFrame:
    rows = []
    for d in DILUTIONS:
        diff = corr[("np", d)] - corr[("dyeonly", d)]
        sigma = _mad_sigma(diff[(grid >= 400) & (grid <= 1800)])
        for band, m in BANDS.items():
            lo, hi = m["search"]
            sel = np.where((grid >= lo) & (grid <= hi))[0]
            local = diff[sel]
            loc, _ = find_peaks(local)
            if loc.size:
                j, at_edge = int(loc[int(np.argmax(local[loc]))]), False
            else:
                j = int(np.argmax(local))
                at_edge = j in (0, local.size - 1)
            centre = float(grid[sel][j])
            lit_lo, lit_hi = m["lit"]
            rows.append({
                "paper": paper, "dilution": d, "factor": FACTOR[d], "peak_name": band,
                "nominal": m["nominal"], "tier": m["tier"],
                "delta_center_cm1": centre, "in_lit_window": lit_lo <= centre <= lit_hi,
                "delta_cps": float(local[j]), "delta_sigma_cps": sigma,
                "delta_snr": float(local[j] / sigma) if sigma else np.nan,
                "at_window_edge": at_edge,
            })
    out = pd.DataFrame(rows)
    out["delta_detected"] = (out["delta_snr"] >= SNR_MIN) & ~out["at_window_edge"]
    out["np_gain_confirmed"] = out["delta_detected"] & out["in_lit_window"]
    return out


def _contiguous_limit(flags) -> str | None:
    """Last dilution before the first failure, walking from least dilute.

    A plain "highest dilution that passed" is not a detection limit: a noise
    bump at 200x would report 200x even though the band vanished at 30x.
    """
    last = None
    for d, ok in zip(DILUTIONS, flags):
        if not ok:
            break
        last = d
    return last


def detection_limits(df: pd.DataFrame, paper: str) -> pd.DataFrame:
    """Two limits per band, which fail differently.

    `lod_confirmed` asks whether the band is still demonstrably the CV band -
    right wavenumber AND above noise. `lod_snr_only` asks merely whether
    anything rises above noise in that window. Where they diverge, signal is
    present but its centre has drifted off the literature position, which is
    what happens when a fading CV band is overtaken by a paper feature.
    """
    rows = []
    for series in SERIES:
        for band in BAND_ORDER:
            sub = (df[(df["paper"] == paper) & (df["series"] == series)
                      & (df["peak_name"] == band)]
                   .set_index("dilution").reindex(DILUTIONS))
            conf = _contiguous_limit(sub["detected"].fillna(False))
            snr = _contiguous_limit(sub["snr_ok"].fillna(False))
            rows.append({
                "paper": paper, "series": series, "peak_name": band,
                "nominal": BANDS[band]["nominal"], "tier": BANDS[band]["tier"],
                "n_detected_of_8": int(sub["detected"].sum()),
                "lod_confirmed": conf,
                "lod_confirmed_factor": FACTOR[conf] if conf else np.nan,
                "lod_snr_only": snr,
                "lod_snr_only_factor": FACTOR[snr] if snr else np.nan,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Per-paper figures
# ---------------------------------------------------------------------------
def fig01_series_spectra(grid, corr, path, title, bands=BANDS):
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for ax, series in zip(axes, SERIES):
        cols = dilution_colors(series)
        for d in DILUTIONS:
            ax.plot(grid, corr[(series, d)], lw=1.1, color=cols[d],
                    linestyle=SERIES_STYLE[series], label=d)
        for m in bands.values():
            ax.axvspan(m["lit"][0], m["lit"][1],
                       color="tab:green" if m["tier"] == "watch" else "tab:olive",
                       alpha=0.13, lw=0)
            ax.annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                        xycoords=("data", "axes fraction"), ha="center",
                        fontsize=7, color="dimgray")
        ax.set_title(SERIES_LABEL[series] + " - dilution series 2x to 200x", fontsize=11)
        ax.set_ylabel(Y_INT)
        ax.legend(fontsize=8, ncol=8, title="dilution", title_fontsize=8, loc="upper left")
        ax.grid(alpha=0.25)
    axes[1].set_xlabel("Wavenumber (cm$^{-1}$)")
    axes[1].set_xlim(400, 1800)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig02_band_zoom(grid, corr, path, title, bands=FOCUS_BANDS):
    order = list(bands)
    ncols = len(order) if len(order) <= 5 else 4
    nrows = int(np.ceil(len(order) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.3 * ncols, 3.6 * nrows + 0.6),
                             squeeze=False)
    flat = axes.ravel()
    for spare in flat[len(order):]:
        spare.set_visible(False)
    for ax, band in zip(flat, order):
        m = bands[band]
        lo, hi = m["lit"][0] - 30, m["lit"][1] + 30
        sel = (grid >= lo) & (grid <= hi)
        for series in SERIES:
            cols = dilution_colors(series)
            for d in DILUTIONS:
                ax.plot(grid[sel], corr[(series, d)][sel], lw=1.2, color=cols[d],
                        linestyle=SERIES_STYLE[series])
        ax.axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.15, lw=0)
        ax.set_title("%d cm$^{-1}$ - %s\nlit %d-%d  %s"
                     % (m["nominal"], m["assign"], m["lit"][0], m["lit"][1],
                        m.get("stars") or ""), fontsize=9)
        ax.set_xlabel("Wavenumber (cm$^{-1}$)", fontsize=8)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
    flat[0].set_ylabel(Y_INT, fontsize=8)
    flat[0].legend(handles=[
        Line2D([], [], color=SERIES_COLOR[s], linestyle=SERIES_STYLE[s], lw=1.6,
               label=SERIES_LABEL[s]) for s in SERIES]
        + [Line2D([], [], color="0.5", lw=1.6, label="darker = less dilute")], fontsize=7)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig03_band_bars(df, path, title, bands=FOCUS_BANDS):
    order = list(bands)
    fig, axes = plt.subplots(1, len(order), figsize=(3.4 * len(order), 4.8), squeeze=False)
    xs = np.arange(len(DILUTIONS))
    width = 0.38
    for ax, band in zip(axes.ravel(), order):
        sub = df[df["peak_name"] == band]
        for i, series in enumerate(SERIES):
            v = (sub[sub["series"] == series].set_index("dilution")
                 .reindex(DILUTIONS)["intensity"])
            ax.bar(xs + (i - 0.5) * width, v, width, color=SERIES_COLOR[series],
                   hatch="" if series == "np" else "///", edgecolor="white",
                   linewidth=.5, label=SERIES_LABEL[series])
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(xs)
        ax.set_xticklabels(DILUTIONS, rotation=45, fontsize=8)
        ax.set_title(str(bands[band]["nominal"]) + " cm$^{-1}$", fontsize=10)
        ax.grid(axis="y", alpha=0.3)
        ax.tick_params(labelsize=8)
    axes[0, 0].set_ylabel(Y_PEAK)
    axes[0, 0].legend(fontsize=7)
    fig.suptitle(title, fontsize=12)
    fig.supxlabel("Dilution", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig04_difference(grid, corr, path, title, bands=BANDS):
    fig, ax = plt.subplots(figsize=(14, 6))
    cols = dilution_colors("np")
    for d in DILUTIONS:
        ax.plot(grid, corr[("np", d)] - corr[("dyeonly", d)], lw=1.1, color=cols[d], label=d)
    for m in bands.values():
        ax.axvspan(m["lit"][0], m["lit"][1],
                   color="tab:green" if m["tier"] == "watch" else "tab:olive", alpha=0.13, lw=0)
        ax.annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                    xycoords=("data", "axes fraction"), ha="center", fontsize=7, color="dimgray")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlim(400, 1800)
    ax.set_xlabel("Wavenumber (cm$^{-1}$)")
    ax.set_ylabel(Y_DELTA)
    ax.set_title(title)
    ax.legend(fontsize=8, ncol=8, title="dilution", title_fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig05_background(grid, raw, df, path, title, bands=FOCUS_BANDS):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for m in (bands or {}).values():
        axes[0].axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.16, lw=0)
    for series in SERIES:
        cols = dilution_colors(series)
        for d in DILUTIONS:
            axes[0].plot(grid, raw[(series, d)], lw=1.0, color=cols[d],
                         linestyle=SERIES_STYLE[series])
    axes[0].set_xlabel("Wavenumber (cm$^{-1}$)")
    axes[0].set_ylabel(Y_RAW)
    axes[0].set_title("Raw signal - fluorescence background")
    axes[0].legend(handles=[
        Line2D([], [], color=SERIES_COLOR[s], linestyle=SERIES_STYLE[s], lw=1.6,
               label=SERIES_LABEL[s]) for s in SERIES], fontsize=7)
    axes[0].grid(alpha=0.25)

    per_scan = df.drop_duplicates("scan_number")
    for series in SERIES:
        sub = per_scan[per_scan["series"] == series]
        axes[1].scatter(sub["scan_number"], sub["int_time_s"], s=55,
                        facecolor="none" if series == "dyeonly" else SERIES_COLOR[series],
                        edgecolor=SERIES_COLOR[series], linewidth=1.6,
                        label=SERIES_LABEL[series])
    axes[1].set_xlabel("Scan number")
    axes[1].set_ylabel("Integration time (s)")
    axes[1].set_ylim(0, 12)
    axes[1].set_title("Auto-integration time per scan")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig06_dilution_response(df, enh, path, title, bands=FOCUS_BANDS):
    order = list(bands)
    cmap = plt.get_cmap("viridis")
    bandcol = {b: cmap(0.08 + 0.82 * i / max(len(order) - 1, 1)) for i, b in enumerate(order)}
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))

    for ax, series in zip(axes[:2], SERIES):
        for b in order:
            sub = (df[(df["series"] == series) & (df["peak_name"] == b)]
                   .set_index("dilution").reindex(DILUTIONS))
            ax.plot(sub["factor"], sub["intensity"], marker="o", ms=5, lw=1.6,
                    color=bandcol[b], linestyle=SERIES_STYLE[series],
                    label="%d cm$^{-1}$" % bands[b]["nominal"])
            det = sub[sub["detected"]]
            ax.scatter(det["factor"], det["intensity"], s=95, facecolor="none",
                       edgecolor=bandcol[b], linewidth=1.6, zorder=5)
        ax.set_xscale("log")
        ax.set_xticks([FACTOR[d] for d in DILUTIONS])
        ax.set_xticklabels(DILUTIONS, fontsize=8)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xlabel("Dilution (log scale)")
        ax.set_title(SERIES_LABEL[series], fontsize=11)
        ax.grid(alpha=0.25)
    axes[0].set_ylabel(Y_PEAK)
    axes[0].legend(fontsize=8, title="ringed = confirmed detection", title_fontsize=7)
    lo = min(axes[0].get_ylim()[0], axes[1].get_ylim()[0])
    hi = max(axes[0].get_ylim()[1], axes[1].get_ylim()[1])
    for ax in axes[:2]:
        ax.set_ylim(lo, hi)

    ax = axes[2]
    for b in order:
        sub = enh[enh["peak_name"] == b].set_index("dilution").reindex(DILUTIONS)
        ax.plot(sub["factor"], sub["delta_cps"], marker="o", ms=5, lw=1.6,
                color=bandcol[b], label="%d cm$^{-1}$" % bands[b]["nominal"])
        det = sub[sub["np_gain_confirmed"]]
        ax.scatter(det["factor"], det["delta_cps"], s=95, facecolor="none",
                   edgecolor=bandcol[b], linewidth=1.6, zorder=5)
    ax.set_xscale("log")
    ax.set_xticks([FACTOR[d] for d in DILUTIONS])
    ax.set_xticklabels(DILUTIONS, fontsize=8)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("Dilution (log scale)")
    ax.set_ylabel(Y_DELTA)
    ax.set_title("Nanoparticle gain (NP minus dye only)", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig07_centre_drift(df, path, title, bands=FOCUS_BANDS):
    order = list(bands)
    fig, axes = plt.subplots(1, len(order), figsize=(3.4 * len(order), 4.8), squeeze=False)
    for ax, band in zip(axes.ravel(), order):
        m = bands[band]
        ax.axhspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.18, lw=0,
                   label="literature window")
        for series in SERIES:
            sub = (df[(df["series"] == series) & (df["peak_name"] == band)]
                   .set_index("dilution").reindex(DILUTIONS))
            ax.plot(sub["factor"], sub["observed_cm1"], marker="o", ms=5, lw=1.5,
                    color=SERIES_COLOR[series], linestyle=SERIES_STYLE[series],
                    label=SERIES_LABEL[series])
            det = sub[sub["detected"]]
            ax.scatter(det["factor"], det["observed_cm1"], s=95, facecolor="none",
                       edgecolor=SERIES_COLOR[series], linewidth=1.6, zorder=5)
        ax.set_xscale("log")
        ax.set_xticks([FACTOR[d] for d in DILUTIONS])
        ax.set_xticklabels(DILUTIONS, rotation=45, fontsize=8)
        ax.set_title("%d cm$^{-1}$" % m["nominal"], fontsize=10)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
    axes[0, 0].set_ylabel("Observed band centre (cm$^{-1}$)")
    axes[0, 0].legend(fontsize=6.5, loc="lower left")
    fig.suptitle(title + " - ringed markers are confirmed detections", fontsize=12)
    fig.supxlabel("Dilution", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# --- the paired per-dilution views ----------------------------------------
def fig08_paired_spectra(grid, corr, path, title, bands=FOCUS_BANDS):
    """One panel per dilution: NP vs dye-only at that dilution, head to head.

    Figure 01 stacks every dilution to show the trend; this asks the narrower
    question the experiment was designed around - at 5x, does adding
    nanoparticles change the spectrum?
    """
    fig, axes = plt.subplots(2, 4, figsize=(19, 8.5), sharex=True)
    for ax, d in zip(axes.ravel(), DILUTIONS):
        for series in SERIES:
            ax.plot(grid, corr[(series, d)], lw=1.15, color=SERIES_COLOR[series],
                    linestyle=SERIES_STYLE[series], label=SERIES_LABEL[series])
        for m in bands.values():
            ax.axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.15, lw=0)
            ax.annotate(str(m["nominal"]), xy=(m["nominal"], 1.005),
                        xycoords=("data", "axes fraction"), ha="center",
                        fontsize=6.5, color="dimgray")
        ax.axhline(0, color="k", lw=0.7)
        ax.set_title(d + " dilution", fontsize=11)
        ax.set_xlim(400, 1800)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel(Y_INT, fontsize=9)
    for ax in axes[1, :]:
        ax.set_xlabel("Wavenumber (cm$^{-1}$)", fontsize=9)
    axes[0, 0].legend(fontsize=8, loc="upper left")
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig09_paired_band_grid(grid, corr, path, title, bands=FOCUS_BANDS):
    """Small multiples: every band (rows) at every dilution (columns), paired.

    The most direct answer to "5x with nanoparticles vs 5x without" for each
    scored band at once. Rows share a y-axis so band strength stays comparable
    across dilutions.
    """
    order = list(bands)
    nrows, ncols = len(order), len(DILUTIONS)
    fig, axes = plt.subplots(nrows, ncols, figsize=(2.35 * ncols, 2.35 * nrows),
                             squeeze=False, sharey="row")
    for r, band in enumerate(order):
        m = bands[band]
        lo, hi = m["lit"][0] - 25, m["lit"][1] + 25
        sel = (grid >= lo) & (grid <= hi)
        for c, d in enumerate(DILUTIONS):
            ax = axes[r][c]
            ax.axvspan(m["lit"][0], m["lit"][1], color="tab:green", alpha=0.16, lw=0)
            for series in SERIES:
                ax.plot(grid[sel], corr[(series, d)][sel], lw=1.3,
                        color=SERIES_COLOR[series], linestyle=SERIES_STYLE[series],
                        label=SERIES_LABEL[series])
            ax.axhline(0, color="k", lw=0.6)
            ax.grid(alpha=0.22)
            ax.tick_params(labelsize=7)
            if r == 0:
                ax.set_title(d, fontsize=11)
            if c == 0:
                ax.set_ylabel("%d cm$^{-1}$\n%s" % (m["nominal"], Y_INT),
                              fontsize=8.5)
            else:
                ax.tick_params(labelleft=False)
            # Every row spans a different window, so each row keeps its own
            # x tick labels - hiding them would imply a shared axis.
            ax.tick_params(labelbottom=True)
    axes[0][0].legend(fontsize=6.5, loc="upper left")
    fig.supxlabel("Wavenumber (cm$^{-1}$)  -  each column is one dilution, each row one band",
                  fontsize=10)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Cross-paper figures
# ---------------------------------------------------------------------------
def fig10_paper_comparison(df, enh_all, path, bands=FOCUS_BANDS):
    order = list(bands)
    fig, axes = plt.subplots(2, len(order), figsize=(3.4 * len(order), 8.6), squeeze=False)
    for c, band in enumerate(order):
        ax = axes[0][c]
        for paper in PAPERS:
            sub = (df[(df["paper"] == paper) & (df["series"] == "np")
                      & (df["peak_name"] == band)]
                   .set_index("dilution").reindex(DILUTIONS))
            ax.plot(sub["factor"], sub["intensity"], marker="o", ms=5, lw=1.7,
                    color=PAPER_COLOR[paper], linestyle=PAPER_STYLE[paper],
                    label=PAPERS[paper]["label"])
            det = sub[sub["detected"]]
            ax.scatter(det["factor"], det["intensity"], s=95, facecolor="none",
                       edgecolor=PAPER_COLOR[paper], linewidth=1.6, zorder=5)
        ax.set_xscale("log")
        ax.set_xticks([FACTOR[d] for d in DILUTIONS])
        ax.set_xticklabels(DILUTIONS, rotation=45, fontsize=8)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_title("%d cm$^{-1}$" % bands[band]["nominal"], fontsize=10)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)

        ax = axes[1][c]
        for paper in PAPERS:
            sub = (enh_all[(enh_all["paper"] == paper) & (enh_all["peak_name"] == band)]
                   .set_index("dilution").reindex(DILUTIONS))
            ax.plot(sub["factor"], sub["delta_cps"], marker="o", ms=5, lw=1.7,
                    color=PAPER_COLOR[paper], linestyle=PAPER_STYLE[paper],
                    label=PAPERS[paper]["label"])
            det = sub[sub["np_gain_confirmed"]]
            ax.scatter(det["factor"], det["delta_cps"], s=95, facecolor="none",
                       edgecolor=PAPER_COLOR[paper], linewidth=1.6, zorder=5)
        ax.set_xscale("log")
        ax.set_xticks([FACTOR[d] for d in DILUTIONS])
        ax.set_xticklabels(DILUTIONS, rotation=45, fontsize=8)
        ax.axhline(0, color="k", lw=0.8)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
    axes[0][0].set_ylabel("NP + dye " + Y_PEAK.lower())
    axes[1][0].set_ylabel(Y_DELTA.replace("Intensity", "NP gain"))
    axes[0][0].legend(fontsize=8)
    fig.suptitle("Offwhite vs white paper - NP series (top) and nanoparticle gain (bottom)",
                 fontsize=12)
    fig.supxlabel("Dilution", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def sers_gain_table(df, band="cv_1620") -> pd.DataFrame:
    """NP / dye-only intensity ratio at one band, per paper per dilution.

    Uses counts/SECOND regardless of the figure scale in force. The paired NP
    and dye-only spots did not always get the same exposure on white (e.g. 7.63
    vs 9.11 s at 10x), so a ratio of raw counts would carry that difference into
    the answer. Dividing each side by its own integration time removes it.

    `dye_at_floor` marks points where the denominator sat at the noise floor;
    there the ratio is a lower bound, not a measurement.
    """
    rows = []
    for paper in PAPERS:
        sub = df[(df["paper"] == paper) & (df["peak_name"] == band)]
        # Noise proxy: spread of the dye-only 1175 window, which holds no real
        # feature in either series past 20x.
        floor = float(df[(df["paper"] == paper) & (df["peak_name"] == "cv_1175")
                         & (df["series"] == "dyeonly")]["height_cps"].abs().median() * 3)
        for d in DILUTIONS:
            n = sub[(sub["series"] == "np") & (sub["dilution"] == d)]
            y = sub[(sub["series"] == "dyeonly") & (sub["dilution"] == d)]
            if not len(n) or not len(y):
                continue
            npv = float(n["height_cps"].iloc[0])
            dyv = float(y["height_cps"].iloc[0])
            rows.append({
                "paper": paper, "dilution": d, "factor": FACTOR[d],
                "np_cps": npv, "dye_cps": dyv,
                "np_int_time_s": float(n["int_time_s"].iloc[0]),
                "dye_int_time_s": float(y["int_time_s"].iloc[0]),
                "sers_gain": npv / dyv if dyv > 0 else np.nan,
                "dye_at_floor": dyv < floor,
                "noise_floor_cps": floor,
            })
    return pd.DataFrame(rows)


def fig12_sers_gain(gain, path, band_label="1620 cm$^{-1}$"):
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.axhline(1.0, color="k", lw=1.2)
    ax.annotate("1.0 = no enhancement", xy=(0.985, 1.0), xycoords=("axes fraction", "data"),
                ha="right", va="bottom", fontsize=9, color="dimgray")
    for paper in PAPERS:
        s = gain[gain["paper"] == paper].set_index("dilution").reindex(DILUTIONS)
        ax.plot(s["factor"], s["sers_gain"], lw=1.8, color=PAPER_COLOR[paper],
                linestyle=PAPER_STYLE[paper], label=PAPERS[paper]["label"], zorder=3)
        solid = s[~s["dye_at_floor"].astype(bool)]
        hollow = s[s["dye_at_floor"].astype(bool)]
        ax.scatter(solid["factor"], solid["sers_gain"], s=70, zorder=4,
                   color=PAPER_COLOR[paper])
        ax.scatter(hollow["factor"], hollow["sers_gain"], s=80, zorder=4,
                   facecolor="white", edgecolor=PAPER_COLOR[paper], linewidth=1.8)
        for d in DILUTIONS:
            v = s["sers_gain"][d]
            if np.isfinite(v):
                ax.annotate("%.1f" % v, xy=(s["factor"][d], v), xytext=(0, 9),
                            textcoords="offset points", ha="center", fontsize=8.5,
                            color=PAPER_COLOR[paper], fontweight="medium")
    ax.set_xscale("log")
    ax.set_xticks([FACTOR[d] for d in DILUTIONS])
    ax.set_xticklabels(DILUTIONS)
    ax.set_xlabel("Dye dilution (log scale)")
    ax.set_ylabel("SERS gain  (NP + dye) / (dye only)")
    ax.set_title("SERS signal multiplier at %s, exposure-corrected\n"
                 "hollow markers: dye-only at the noise floor, so the ratio is a lower bound"
                 % band_label, fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.28)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig11_paper_background(specs, path):
    fig, ax = plt.subplots(figsize=(13, 5.5))
    for paper, (grid, corr, raw) in specs.items():
        for d in DILUTIONS:
            ax.plot(grid, raw[("np", d)], lw=0.9, color=PAPER_COLOR[paper],
                    linestyle=PAPER_STYLE[paper], alpha=0.75)
    ax.set_xlabel("Wavenumber (cm$^{-1}$)")
    ax.set_ylabel(Y_RAW)
    ax.set_title("Fluorescence background by paper - NP series, all dilutions")
    ax.legend(handles=[Line2D([], [], color=PAPER_COLOR[p], linestyle=PAPER_STYLE[p],
                              lw=1.8, label=PAPERS[p]["label"]) for p in PAPERS], fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main() -> int:
    meta = pd.concat([load_acquisition_meta(p) for p in PAPERS], ignore_index=True)
    df = load_band_table(meta)

    global T_MIN
    T_MIN = float(meta["int_time_s"].min())
    print("t_min across all %d scans = %.2f s" % (len(meta), T_MIN))

    # Detection flags, band centres and SNR are scale-invariant, so they are
    # computed once. Only the intensity column and the plotted traces change.
    enh_all = lod_all = None
    for scale_name, spec in SCALES.items():
        set_scale(scale_name)
        tag = "  [%s]" % spec["blurb"]
        specs, enh_parts, lod_parts = {}, [], []

        for paper, pspec in PAPERS.items():
            pdir = OUT / paper
            fig_dir = pdir / spec["folder"]
            fig_dir.mkdir(parents=True, exist_ok=True)
            sub = apply_scale(df[df["paper"] == paper], spec["kind"])
            grid, corr, raw = load_spectra(df, paper, kind=spec["kind"])
            specs[paper] = (grid, corr, raw)
            enh = enhancement_table(grid, corr, paper)
            lod = detection_limits(df, paper)
            enh_parts.append(enh)
            lod_parts.append(lod)
            name = pspec["label"] + " paper"

            if scale_name == "cps":       # tables are written once, in counts/s
                cols = ["scan_number", "paper", "series", "dilution", "factor",
                        "peak_name", "nominal", "observed_cm1", "lit_min", "lit_max",
                        "in_window", "snr", "detected", "height", "height_cps",
                        "area_cps", "fwhm_cm1", "prominence", "int_time_s"]
                sub[cols].to_csv(pdir / "per_spectrum_bands.csv", index=False)
                lod.to_csv(pdir / "detection_limits.csv", index=False)
            enh.to_csv(pdir / ("np_enhancement_by_dilution_%s.csv" % scale_name),
                       index=False)
            s = {"wavenumber_cm1": grid}
            s.update({"%s_%s" % k: v for k, v in corr.items()})
            pd.DataFrame(s).to_csv(pdir / ("spectra_%s.csv" % scale_name), index=False)

            fig01_series_spectra(grid, corr, fig_dir / "01_series_dilution_spectra.png",
                                 name + tag)
            fig02_band_zoom(grid, corr, fig_dir / "02_band_windows_zoom.png",
                            name + " - CV band windows, every dilution" + tag)
            fig03_band_bars(sub, fig_dir / "03_band_intensity_by_dilution.png",
                            name + " - band intensity by dilution" + tag)
            fig04_difference(grid, corr,
                             fig_dir / "04_np_enhancement_difference_spectra.png",
                             name + " - NP + dye minus dye only, matched dilution" + tag)
            fig05_background(grid, raw, sub,
                             fig_dir / "05_background_and_integration_time.png", name + tag)
            fig06_dilution_response(sub, enh, fig_dir / "06_dilution_response_curves.png",
                                    name + " - dilution response" + tag)
            fig07_centre_drift(sub, fig_dir / "07_band_centre_vs_dilution.png",
                               name + " - band position vs dilution")
            fig08_paired_spectra(grid, corr, fig_dir / "08_paired_by_dilution_spectra.png",
                                 name + " - NP + dye vs dye only, per dilution" + tag)
            fig09_paired_band_grid(grid, corr,
                                   fig_dir / "09_paired_by_dilution_band_grid.png",
                                   name + " - every band at every dilution" + tag)

        enh_all = pd.concat(enh_parts, ignore_index=True)
        lod_all = pd.concat(lod_parts, ignore_index=True)
        cdir = OUT / "combined" / spec["folder"]
        cdir.mkdir(parents=True, exist_ok=True)
        scaled = apply_scale(df, spec["kind"])
        fig10_paper_comparison(scaled, enh_all, cdir / "10_paper_comparison.png")
        fig11_paper_background(specs, cdir / "11_paper_background.png")
        print("  wrote %-14s -> %s" % (scale_name, spec["folder"]))

    cdir = OUT / "combined"
    enh_all.to_csv(cdir / "np_enhancement_by_dilution_all.csv", index=False)
    lod_all.to_csv(cdir / "detection_limits_all.csv", index=False)
    meta.to_csv(cdir / "acquisition_metadata.csv", index=False)

    # SERS gain is a ratio, so it is scale-free and lives outside the three
    # figure folders - but it must be built from counts/s, see sers_gain_table.
    gain = sers_gain_table(df)
    gain.to_csv(cdir / "sers_gain_1620.csv", index=False)
    fig12_sers_gain(gain, cdir / "12_sers_gain_multiplier_1620.png")

    (OUT / "run_context.json").write_text(json.dumps({
        "configs": [v["config"] for v in PAPERS.values()],
        "intensity_scales": {k: v["blurb"] for k, v in SCALES.items()},
        "t_min_s": T_MIN,
        "plate": "plates/exp2_dilution.yaml",
        "snr_threshold": SNR_MIN,
        "dilutions": DILUTIONS,
        "papers": {k: v["label"] for k, v in PAPERS.items()},
        "note": "single spot per cell - no replicates, so no error bars",
    }, indent=2), encoding="utf-8")

    pd.set_option("display.width", 240)
    print("\n=== INTEGRATION TIME BY PAPER ===")
    print(meta.groupby("paper")["int_time_s"].agg(["min", "mean", "max"]).round(2).to_string())
    print("\n=== DETECTION LIMITS (contiguous from 2x) ===")
    show = lod_all[lod_all["peak_name"].isin(FOCUS_ORDER)]
    print(show[["paper", "series", "nominal", "n_detected_of_8",
                "lod_confirmed", "lod_snr_only"]].to_string(index=False))
    print("\n=== NP GAIN AT 1620 (counts, normalised to %.2f s) ===" % T_MIN)
    g = enh_all[enh_all["peak_name"] == "cv_1620"]
    print(g.pivot_table(index="dilution", columns="paper", values="delta_cps")
          .reindex(DILUTIONS).round(1).to_string())
    print("\nWrote " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
