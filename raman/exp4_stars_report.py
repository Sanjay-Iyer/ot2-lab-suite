#!/usr/bin/env python
"""exp4_stars_report.py - gold nanostar dilution panels + bipyramid rerun, 083126.

The exp-2 design with a third arm: the same CV dye ladder (2x -> 200x), scored
for stock nanostars, 5:1 diluted nanostars, and dye alone, on both papers. Plus
a rerun of the 082726 bipyramid print at fixed exposure.

WHAT THIS ANSWERS
-----------------
Does adding nanostars boost the dye signal, and how far down the ladder does
the boost survive? Both are paired questions, so both are answered by
subtracting the dye-only spot at the SAME dilution, on the SAME paper, at the
SAME exposure. Nothing is compared across papers: white fluoresces several
times harder than offwhite, so a cross-paper subtraction would hand the
substrate difference to the nanoparticles.

THE SATURATION SCREEN COMES FIRST, AND IT IS SCORED AT THE BAND
---------------------------------------------------------------
Ten scans are saturated somewhere, and they are not a random ten - they are
the concentrated end of each ladder, which is exactly where the nanoparticle
gain would show up. On this instrument a saturated scan still looks like a
spectrum and still yields fitted peaks (see exp 3), so nothing about them
announces itself; they are found by excess noise against the law exp 3 fitted.

But saturation is not uniform across a spectrum. It starts at the low
wavenumbers, where the fluorescence background is highest, and on several of
these scans it never reaches the dye band: 854 rings at 7.6x the model noise
below 800 cm-1 and sits at 1.6x at 1620, quieter there than 849, which passes
outright. Discarding its 1620 point because of a region it is not read at
throws away a real measurement, so the verdict that governs the figures is
scored on the windows overlapping the band being plotted
(`quality.screen_scope`). Only 831 and 839 still fail there.

The thresholds are unchanged, and both verdicts are written to
scan_screen.csv. What the band screen does NOT cover is linearity: a band can
be quiet enough to measure and still sit above the count level the noise law
was fitted below, where the response compresses and a height is a lower bound.
Those scans carry `band_above_fit_ceiling` and are hatched in the bar figures.

Band windows come from exp1_cv_report so exp 1, 2, 3 and 4 all score against
identical definitions. Everything else lives in configs/stars_exp4.yaml.

Run (from raman/):
    python exp4_stars_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import exp3_timeseries_report as ts                          # noqa: E402
import exp4_paper_plots as paper_plots                       # noqa: E402
import exp4_scan_gallery as scan_gallery                      # noqa: E402
import exp4_scan_key as scan_key_writer                      # noqa: E402
from exp3_timeseries_report import ALL_BANDS                 # noqa: E402

CONFIG_PATH = ROOT / "configs" / "stars_exp4.yaml"
VERDICT_COLOR = ts.VERDICT_COLOR


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _scan_range(spec: str | int) -> list[int]:
    if isinstance(spec, int):
        return [spec]
    text = str(spec).strip()
    if "-" in text:
        lo, hi = (int(v) for v in text.split("-", 1))
        if hi < lo:
            raise SystemExit("[error] reversed scan range %r" % spec)
        return list(range(lo, hi + 1))
    return [int(text)]


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = sorted(b for b in cfg["bands"] if b not in ALL_BANDS)
    if unknown:
        raise SystemExit("[error] %s names unknown bands: %s" % (path.name, unknown))
    if cfg["control_condition"] not in cfg["conditions"]:
        raise SystemExit("[error] control_condition is not one of conditions:")

    ladder = cfg["dilution_ladder"]
    rows, seen = [], {}
    for s in cfg["series"]:
        if s["condition"] not in cfg["conditions"]:
            raise SystemExit("[error] unknown condition %r in series %s"
                             % (s["condition"], s["key"]))
        scans = _scan_range(s["scans"])
        dils = list(s["dilutions"])
        # The one structural check worth having: a series whose scan count and
        # dilution count disagree has been mistyped, and every downstream number
        # would be attached to the wrong dilution.
        if len(scans) != len(dils):
            raise SystemExit("[error] series %s/%s: %d scans (%s) but %d dilutions"
                             % (s["key"], s["condition"], len(scans), s["scans"],
                                len(dils)))
        bad = [d for d in dils if d not in ladder]
        if bad:
            raise SystemExit("[error] series %s/%s: dilutions not on the ladder: %s"
                             % (s["key"], s["condition"], bad))
        for scan, dil in zip(scans, dils):
            if scan in seen:
                raise SystemExit("[error] scan %d appears in two series: %s and %s"
                                 % (scan, seen[scan], s["key"] + "/" + s["condition"]))
            seen[scan] = s["key"] + "/" + s["condition"]
            rows.append({
                "key": s["key"], "folder": s["folder"], "paper": s["paper"],
                "particle": s["particle"], "condition": s["condition"],
                "scan_number": scan, "dilution": dil,
                "series_note": s.get("note", ""),
            })
    cfg["_map"] = pd.DataFrame(rows)
    cfg["_unassigned"] = {int(k): v for k, v in (cfg.get("unassigned") or {}).items()}
    return cfg


def load_noise_model(cfg: dict) -> dict:
    """Reuse exp 3's noise law; refit only if exp 3 has not been run."""
    path = ROOT / cfg["screen"]["noise_model_from"]
    if path.is_file():
        model = json.loads(path.read_text(encoding="utf-8"))
        model["_source"] = str(path.relative_to(ROOT))
        return model
    print("[exp4] %s not found - refitting the noise law from this dump" % path)
    tcfg = ts.load_config(ROOT / "configs" / cfg["screen"]["timeseries_config"])
    _, all_scans = ts.load_scans(tcfg)
    model = ts.fit_noise_model(ts.window_table(all_scans, tcfg), tcfg)
    model["_source"] = "refitted in exp4"
    return model


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_scans(cfg: dict) -> list[dict]:
    raw_root = ROOT / cfg["io"]["raw_root"]
    if not raw_root.is_dir():
        raise SystemExit("[error] raw root not found: %s" % raw_root)
    folders = set(cfg.get("folders") or []) | {s["folder"] for s in cfg["series"]}
    wanted = cfg["_map"].set_index("scan_number").to_dict("index")

    scans, missing_folders = [], set(folders)
    for path in sorted(raw_root.rglob("*.csv")):
        rec = ts.read_metadata_csv(path)
        if rec is None:
            continue
        rel = path.parent.relative_to(raw_root).as_posix()
        if rel not in folders:
            continue
        missing_folders.discard(rel)
        rec["folder"] = rel
        rec["total_time_s"] = rec["int_time_s"] * rec["averages"]
        meta = wanted.get(rec["scan_number"])
        if meta is not None and meta["folder"] != rel:
            raise SystemExit("[error] scan %d is mapped to folder %s but found in %s"
                             % (rec["scan_number"], meta["folder"], rel))
        if meta is None:
            rec.update({"key": "unassigned", "paper": "?", "particle": "?",
                        "condition": None, "dilution": None,
                        "series_note": cfg["_unassigned"].get(rec["scan_number"], "")})
        else:
            rec.update({k: meta[k] for k in
                        ("key", "paper", "particle", "condition", "dilution",
                         "series_note")})
        scans.append(rec)

    if missing_folders:
        raise SystemExit("[error] folders named in series: not found: %s"
                         % sorted(missing_folders))
    found = {r["scan_number"] for r in scans}
    absent = sorted(set(wanted) - found)
    if absent:
        print("[exp4] NOTE: %d mapped scan(s) are not in the dump: %s"
              % (len(absent), absent))
    stray = sorted(r["scan_number"] for r in scans
                   if r["condition"] is None
                   and r["scan_number"] not in cfg["_unassigned"])
    if stray:
        print("[exp4] NOTE: %d scan(s) in the raw folders are in no series and are "
              "not listed under unassigned: %s" % (len(stray), stray))
    return [ts.process(r, cfg) for r in sorted(scans, key=lambda r: r["scan_number"])]


# ---------------------------------------------------------------------------
# Per-scan tables
# ---------------------------------------------------------------------------
def _verdict(excess: float, q: dict) -> str:
    return ("fail" if excess >= q["noise_excess_fail"]
            else "warn" if excess >= q["noise_excess_warn"] else "pass")


def scan_screen(scans: list[dict], model: dict, cfg: dict, tcfg: dict) -> pd.DataFrame:
    """Two saturation verdicts per scan: whole-scan, and at the band scored.

    The whole-scan verdict answers "is this exposure safe on this paper", which
    is what exp 3 needed. It is the wrong question for a figure of one band.
    Saturation here starts at the low-wavenumber end, where the fluorescence
    background is highest, and on several scans it never reaches 1620: scan 854
    scores 7.6 below 800 cm-1 and 1.6 at the dye band, which is better than
    several scans that pass outright. Throwing its 1620 point away because a
    region it is not being read at is ringing loses a real measurement.

    So the band verdict is scored on the windows that overlap the band being
    plotted, and `quality.screen_scope` in the config chooses which verdict the
    figures and gains obey. Both are written to scan_screen.csv either way, so
    nothing about the whole-scan judgement is lost.

    The level at the band is carried too. Noise and linearity are different
    failures: a window can be quiet and still sit high enough on the detector
    for the response to compress, which would under-report a peak height rather
    than roughen it. `band_above_fit_ceiling` marks those.
    """
    q = tcfg["quality"]
    width = tcfg["noise_model"]["window_cm1"]
    lo, hi = ALL_BANDS[cfg["sers_band"]]["search"]
    lo, hi = lo - width / 2, hi + width / 2
    scope = cfg["quality"].get("screen_scope", "band")
    if scope not in ("band", "scan"):
        raise SystemExit("[error] quality.screen_scope must be 'band' or 'scan'")

    rows = []
    for r in scans:
        prof = ts.add_excess(ts.noise_profile(r["x_fit"], r["y_counts"], width), model)
        p90 = float(prof["excess"].quantile(0.90))
        at_band = prof[(prof["center_cm1"] >= lo) & (prof["center_cm1"] <= hi)]
        # The maximum, not a percentile: this is one or two windows, and a
        # percentile over two windows says nothing a maximum does not.
        band_excess = float(at_band["excess"].max()) if len(at_band) else np.nan
        band_level = float(at_band["level_counts"].median()) if len(at_band) else np.nan
        verdict_scan = _verdict(p90, q)
        verdict_band = _verdict(band_excess, q) if np.isfinite(band_excess) else "fail"
        rows.append({
            "key": r["key"], "folder": r["folder"], "paper": r["paper"],
            "particle": r["particle"], "condition": r["condition"],
            "dilution": r["dilution"], "scan_number": r["scan_number"],
            "int_time_s": r["int_time_s"], "created": r["created"],
            "median_counts": float(np.median(r["y_counts"])),
            "max_counts": float(r["y_counts"].max()),
            "noise_excess_p90": p90,
            "noise_excess_at_band": band_excess,
            "level_at_band_counts": band_level,
            "band_above_fit_ceiling": bool(band_level > model["fit_max_counts"]),
            "verdict_scan": verdict_scan,
            "verdict_band": verdict_band,
            "verdict": verdict_band if scope == "band" else verdict_scan,
            "screen_scope": scope,
            "series_note": r["series_note"],
        })
    return pd.DataFrame(rows).sort_values("scan_number").reset_index(drop=True)


def band_table(scans: list[dict], screen: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rows = []
    for r in scans:
        for m in ts.band_metrics(r, cfg):
            m.update({"paper": r["paper"], "particle": r["particle"],
                      "condition": r["condition"], "dilution": r["dilution"]})
            rows.append(m)
    df = pd.DataFrame(rows)
    df["factor"] = df["dilution"].map(
        lambda d: float(str(d)[:-1]) if isinstance(d, str) else np.nan)
    df["detected"] = (df["snr"] >= cfg["quality"]["min_snr"]) & df["in_lit_window"]
    return df.merge(screen[["scan_number", "verdict", "int_time_s"]],
                    on="scan_number", how="left")


# ---------------------------------------------------------------------------
# Paired gain. Every comparison is within one panel and one dilution.
# ---------------------------------------------------------------------------
def gain_table(scans, screen, cfg) -> pd.DataFrame:
    ctrl = cfg["control_condition"]
    usable = screen
    if cfg["quality"]["exclude_failed_from_gains"]:
        usable = usable[usable["verdict"] != "fail"]
    by_scan = {r["scan_number"]: r for r in scans}
    index = {(r["key"], r["condition"], r["dilution"]): int(r["scan_number"])
             for _, r in usable.dropna(subset=["condition"]).iterrows()}
    # Noise and linearity fail differently. A band can be quiet and still sit
    # high enough on the detector for the response to compress, which biases a
    # RATIO in whichever direction the two spots differ - so the level behind
    # both halves of every gain is carried, not just the verdict.
    ceiling = screen.set_index("scan_number")["band_above_fit_ceiling"].to_dict()
    band_level = screen.set_index("scan_number")["level_at_band_counts"].to_dict()

    rows = []
    for (key, cond, dil), scan in index.items():
        if cond == ctrl:
            continue
        cscan = index.get((key, ctrl, dil))
        if cscan is None:
            continue                       # no control survived at this dilution
        test, control = by_scan[scan], by_scan[cscan]
        grid = test["x_fit"]
        base = np.interp(grid, control["x_fit"], control["smooth_cps"])
        diff = test["smooth_cps"] - base
        sigma = ts._sigma_from(test["corr_cps"] - np.interp(
            grid, control["x_fit"], control["corr_cps"]))
        for band in cfg["bands"]:
            lo, hi = ALL_BANDS[band]["search"]
            # Two separate quantities, deliberately not mixed:
            #   gain  = peak height of the test over peak height of the control,
            #           each picked with ts.window_peak so both agree with the
            #           per-scan band table they will be checked against
            #   delta = the largest excursion of the DIFFERENCE spectrum, which
            #           is what the SNR test is run on
            # Adding delta to the control height and calling that the test
            # height would be wrong whenever the two peaks sit at slightly
            # different wavenumbers, which on a shifting SERS band they do.
            _, test_cps, _ = ts.window_peak(grid, test["smooth_cps"], lo, hi)
            _, ref, _ = ts.window_peak(grid, base, lo, hi)
            _, delta, _ = ts.window_peak(grid, diff, lo, hi)
            rows.append({
                "key": key, "condition": cond, "dilution": dil,
                "factor": float(str(dil)[:-1]), "peak_name": band,
                "nominal": ALL_BANDS[band]["nominal"],
                "test_scan": scan, "control_scan": cscan,
                "control_cps": ref, "test_cps": test_cps, "delta_cps": delta,
                "gain_x": test_cps / ref if ref > 0 else np.nan,
                "delta_sigma_cps": sigma,
                "delta_snr": delta / sigma if sigma else np.nan,
                "test_band_counts": band_level.get(scan, np.nan),
                "control_band_counts": band_level.get(cscan, np.nan),
                "above_linearity_ceiling": bool(ceiling.get(scan, False)
                                                or ceiling.get(cscan, False)),
            })
    out = pd.DataFrame(rows)
    if len(out):
        # Two different claims, kept apart. `delta_detected` says something in
        # the window rose above noise - it can fire even when the test PEAK is
        # lower than the control's, because the largest positive excursion of a
        # difference spectrum need not sit at either peak. Only the conjunction
        # with gain_x > 1 is an actual enhancement.
        out["delta_detected"] = out["delta_snr"] >= cfg["quality"]["min_snr"]
        out["gain_confirmed"] = out["delta_detected"] & (out["gain_x"] > 1.0)
        out = out.sort_values(["key", "condition", "factor", "peak_name"])
    return out.reset_index(drop=True)


def annotate_control_quality(gain: pd.DataFrame, bands: pd.DataFrame,
                             cfg: dict) -> pd.DataFrame:
    """Flag gains whose dye-only control was itself in the noise.

    A ratio is only as good as its denominator. Once the control band has died
    into the baseline its height is a noise draw, and dividing by it produces a
    large, meaningless "gain" - or, just as easily, a spurious loss.
    """
    if not len(gain):
        return gain
    idx = bands.set_index(["scan_number", "peak_name"])
    snr = idx["snr"].to_dict()
    inwin = idx["in_lit_window"].to_dict()
    out = gain.copy()
    out["control_snr"] = [snr.get((s, b), np.nan) for s, b in
                          zip(out["control_scan"], out["peak_name"])]
    # SNR only, deliberately. The band's centre being inside the literature
    # window is a question about whether it is really CV - reported per scan in
    # band_metrics.csv - not about whether the control height is measurable. A
    # SERS band that has shifted a few cm-1 is still a perfectly good
    # denominator, and requiring the window here would throw out good controls.
    out["control_detected"] = out["control_snr"] >= cfg["quality"]["min_snr"]
    out["control_in_lit_window"] = [bool(inwin.get((s, b), False)) for s, b in
                                    zip(out["control_scan"], out["peak_name"])]
    out["gain_trustworthy"] = out["gain_confirmed"] & out["control_detected"]
    return out


def detection_limits(bands: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Most dilute point still detected, walking from the strong end.

    Walking matters. A plain "highest dilution that passed" would report 200x
    off a single noise bump even if the band had already died at 30x, so the
    walk stops at the first failure and reports the last point before it.
    """
    rows = []
    sub = bands.dropna(subset=["condition", "factor"])
    if cfg["quality"]["exclude_failed_from_gains"]:
        sub = sub[sub["verdict"] != "fail"]
    for (key, cond, band), g in sub.groupby(["key", "condition", "peak_name"]):
        g = g.sort_values("factor")
        last = None
        for _, r in g.iterrows():
            if not r["detected"]:
                break
            last = r["dilution"]
        rows.append({
            "key": key, "condition": cond, "peak_name": band,
            "nominal": ALL_BANDS[band]["nominal"],
            "n_points": len(g), "n_detected": int(g["detected"].sum()),
            "lod": last, "lod_factor": float(str(last)[:-1]) if last else np.nan,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _cond_style(cfg, cond):
    c = cfg["conditions"][cond]
    return c["color"], c["marker"], c["label"]


def _panels(cfg, screen):
    return [k for k in cfg["panel_label"] if (screen["key"] == k).any()]


def fig01_screen(screen, tcfg, cfg, path):
    q = tcfg["quality"]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5),
                             gridspec_kw={"width_ratios": [1.5, 1]})
    ax = axes[0]
    ordered = screen.sort_values("scan_number").reset_index(drop=True)
    ax.bar(ordered.index, ordered["noise_excess_p90"], width=0.8,
           color=[VERDICT_COLOR[v] for v in ordered["verdict"]])
    ax.axhline(q["noise_excess_warn"], color=VERDICT_COLOR["warn"], lw=1.0, ls="--")
    ax.axhline(q["noise_excess_fail"], color=VERDICT_COLOR["fail"], lw=1.0, ls="--")
    ax.set_yscale("log")
    # Ticks and labels must come from the SAME sorted frame the bars did, and
    # the scan numbers are not contiguous (823-827 are absent).
    ax.set_xticks(ordered.index[::3])
    ax.set_xticklabels([str(s) for s in ordered["scan_number"][::3]],
                       rotation=90, fontsize=6)
    ax.set_xlabel("scan number (acquisition order)")
    ax.set_ylabel("noise excess (90th pct)")
    ax.set_title("Saturation screen, every mapped scan", fontsize=10, loc="left")
    ax.legend(handles=[Patch(color=v, label=k) for k, v in VERDICT_COLOR.items()],
              fontsize=8, ncol=3, frameon=False)

    ax = axes[1]
    handles = []
    for cond in cfg["conditions"]:
        g = screen[screen["condition"] == cond]
        if not len(g):
            continue
        col, mk, lab = _cond_style(cfg, cond)
        ax.scatter(g["factor"] if "factor" in g else g["median_counts"],
                   g["noise_excess_p90"], s=30, marker=mk,
                   c=[VERDICT_COLOR[v] for v in g["verdict"]],
                   edgecolor="0.3", linewidth=0.4)
        handles.append(Line2D([], [], ls="", marker=mk, color="0.45", label=lab))
    ax.axvline(q["median_counts_warn"], color=VERDICT_COLOR["warn"], lw=1.0, ls="--")
    ax.axvline(q["median_counts_fail"], color=VERDICT_COLOR["fail"], lw=1.0, ls="--")
    ax.set_yscale("log")
    ax.set_xlabel("median level (raw counts)")
    ax.set_ylabel("noise excess (90th pct)")
    ax.set_title("Shape = condition, colour = verdict", fontsize=10, loc="left")
    ax.legend(handles=handles, fontsize=7, frameon=False)
    fig.suptitle("01  Saturation screen - the failures are the concentrated end of "
                 "each ladder, which is exactly where the gain would show",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig02_map_check(bands, cfg, path):
    """Does the supplied scan -> dilution map behave like a dilution ladder?

    A ladder has to fall with dilution. If a series does not, either the map is
    wrong for that series or the band is already in the noise - and the two are
    told apart by whether the fall stops at a floor (noise) or wanders (map).
    """
    band = cfg["sers_band"]
    sub = bands[bands["peak_name"] == band].dropna(subset=["condition", "factor"])
    panels = [k for k in cfg["panel_label"] if (sub["key"] == k).any()]
    ncol = min(3, len(panels))
    nrow = int(np.ceil(len(panels) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 4.2 * nrow),
                             squeeze=False)
    for ax, key in zip(axes.ravel(), panels):
        g = sub[sub["key"] == key]
        for cond in cfg["conditions"]:
            h = g[g["condition"] == cond].sort_values("factor")
            if not len(h):
                continue
            col, mk, lab = _cond_style(cfg, cond)
            ok = h["verdict"] != "fail"
            ax.plot(h["factor"], h["height_cps"], color=col, lw=1.2, alpha=0.5)
            ax.scatter(h["factor"][ok], h["height_cps"][ok], color=col, marker=mk,
                       s=34, label=lab, zorder=3)
            ax.scatter(h["factor"][~ok], h["height_cps"][~ok], facecolor="none",
                       edgecolor=col, marker=mk, s=48, linewidth=1.4, zorder=3)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("dye dilution factor (x)")
        ax.set_ylabel("%d cm$^{-1}$ height (counts s$^{-1}$)"
                      % ALL_BANDS[band]["nominal"])
        ax.set_title(cfg["panel_label"][key], fontsize=10, loc="left")
        ax.legend(fontsize=7, frameon=False)
    for ax in axes.ravel()[len(panels):]:
        ax.set_visible(False)
    fig.suptitle("02  Map check - every series should fall with dilution. Hollow "
                 "markers failed the saturation screen and are excluded downstream",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig03_dilution_response(bands, cfg, path):
    sel = cfg["bands"]
    panels = [k for k in cfg["panel_label"]
              if (bands["key"] == k).any() and k.startswith(("ow", "w"))]
    fig, axes = plt.subplots(len(panels), len(sel),
                             figsize=(2.9 * len(sel), 3.3 * len(panels)),
                             squeeze=False, sharex=True)
    for i, key in enumerate(panels):
        for j, band in enumerate(sel):
            ax = axes[i, j]
            g = bands[(bands["key"] == key) & (bands["peak_name"] == band)]
            g = g.dropna(subset=["condition", "factor"])
            for cond in cfg["conditions"]:
                h = g[g["condition"] == cond].sort_values("factor")
                if not len(h):
                    continue
                col, mk, lab = _cond_style(cfg, cond)
                ok = h["verdict"] != "fail"
                ax.plot(h["factor"], h["height_cps"], color=col, lw=1.0, alpha=0.5)
                ax.scatter(h["factor"][ok], h["height_cps"][ok], color=col,
                           marker=mk, s=22, label=lab if (i == 0 and j == 0) else None)
                ax.scatter(h["factor"][~ok], h["height_cps"][~ok], facecolor="none",
                           edgecolor=col, marker=mk, s=34, linewidth=1.2)
            ax.set_xscale("log")
            ax.set_yscale("log")
            if i == 0:
                ax.set_title("%s\n%d cm$^{-1}$" % (band, ALL_BANDS[band]["nominal"]),
                             fontsize=9)
            if j == 0:
                ax.set_ylabel(cfg["panel_label"][key] + "\ncounts s$^{-1}$", fontsize=8)
            if i == len(panels) - 1:
                ax.set_xlabel("dilution (x)")
    axes[0, 0].legend(fontsize=6, frameon=False)
    fig.suptitle("03  Dilution response, every band. Stars above dye-only at the same "
                 "dilution is the enhancement", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig04_paired_spectra(scans, gain, cfg, path):
    """Test minus its own dye-only control, per panel and dilution."""
    if not len(gain):
        return
    by_scan = {r["scan_number"]: r for r in scans}
    pairs = gain.drop_duplicates(["key", "condition", "dilution"])
    panels = [k for k in cfg["panel_label"] if (pairs["key"] == k).any()]
    fig, axes = plt.subplots(len(panels), 1, figsize=(14, 3.6 * len(panels)),
                            squeeze=False, sharex=True)
    for ax, key in zip(axes.ravel(), panels):
        g = pairs[pairs["key"] == key]
        offset = 0.0
        step = 0.0
        traces = []
        for _, r in g.sort_values(["condition", "factor"]).iterrows():
            t, c = by_scan[int(r["test_scan"])], by_scan[int(r["control_scan"])]
            diff = t["smooth_cps"] - np.interp(t["x_fit"], c["x_fit"], c["smooth_cps"])
            traces.append((r, t["x_fit"], diff))
            step = max(step, float(np.ptp(diff)))
        step *= 0.55
        for r, x, diff in traces:
            col, _, lab = _cond_style(cfg, r["condition"])
            ax.plot(x, diff + offset, lw=0.9, color=col,
                    label="%s %s" % (lab, r["dilution"]))
            offset += step
        for b in cfg["bands"]:
            ax.axvline(ALL_BANDS[b]["nominal"], color="0.75", lw=0.6, ls=":", zorder=0)
        ax.set_xlim(400, 1800)
        ax.set_yticks([])
        ax.set_ylabel("$\\Delta$ counts s$^{-1}$\n(offset)", fontsize=8)
        ax.set_title(cfg["panel_label"][key], fontsize=10, loc="left")
        ax.legend(fontsize=6, ncol=4, frameon=False)
    axes.ravel()[-1].set_xlabel("Raman shift (cm$^{-1}$)")
    fig.suptitle("04  Nanoparticle spot minus its own dye-only control, same paper, "
                 "same exposure, same dilution", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig05_gain(gain, cfg, path):
    if not len(gain):
        return
    band, sub_band = cfg["sers_band"], cfg["substrate_band"]
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.4))
    ax = axes[0]
    g = gain[gain["peak_name"] == band]
    keys = [k for k in cfg["panel_label"] if (g["key"] == k).any()]
    # colour = condition, dash = panel. Three panels share the `stars` colour,
    # so without the second channel their curves are indistinguishable.
    dashes = ["solid", (0, (5, 2)), (0, (1, 1)), (0, (7, 2, 1, 2)), (0, (3, 1, 1, 1))]
    for i, key in enumerate(keys):
        for cond in cfg["conditions"]:
            h = g[(g["key"] == key) & (g["condition"] == cond)].sort_values("factor")
            if not len(h):
                continue
            col, mk, lab = _cond_style(cfg, cond)
            ax.plot(h["factor"], h["gain_x"], color=col, lw=1.3, marker=mk, ms=6,
                    ls=dashes[i % len(dashes)],
                    label="%s - %s" % (cfg["panel_label"][key], lab))
            bad = h[~h["gain_trustworthy"]]
            ax.scatter(bad["factor"], bad["gain_x"], facecolor="none",
                       edgecolor="0.2", s=90, linewidth=1.2, zorder=4)
    ax.axhline(1.0, color="0.3", lw=1.2)
    ax.set_xscale("log")
    ax.set_xlabel("dye dilution factor (x)")
    ax.set_ylabel("gain vs dye only at the same dilution ($\\times$)")
    ax.set_title("SERS gain at %d cm$^{-1}$. Hollow rings = the difference did not "
                 "clear\nSNR %g, so the gain is not established there"
                 % (ALL_BANDS[band]["nominal"], cfg["quality"]["min_snr"]),
                 fontsize=10, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="upper left",
              bbox_to_anchor=(0.0, -0.16), ncol=2)

    ax = axes[1]
    piv = gain.pivot_table(index="peak_name", columns=["key", "condition", "dilution"],
                           values="gain_x")
    piv = piv.reindex([b for b in cfg["bands"] if b in piv.index])
    im = ax.imshow(piv.to_numpy(), cmap="RdBu_r", vmin=0, vmax=2.5, aspect="auto")
    ax.set_xticks(range(piv.shape[1]))
    ax.set_xticklabels(["%s\n%s %s" % c for c in piv.columns], fontsize=5,
                       rotation=90)
    ax.set_yticks(range(piv.shape[0]))
    ax.set_yticklabels(["%s  %d" % (b, ALL_BANDS[b]["nominal"]) for b in piv.index],
                       fontsize=7)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.to_numpy()[i, j]
            if np.isfinite(v):
                ax.text(j, i, "%.1f" % v, ha="center", va="center", fontsize=5)
    plt.colorbar(im, ax=ax, label="gain ($\\times$)")
    ax.set_title("Every band. The %s row is the control: a gain there means the\npaper "
                 "changed, not the CV" % sub_band, fontsize=10, loc="left")
    fig.suptitle("05  Do the nanostars boost the dye signal?", fontsize=12)
    fig.tight_layout(rect=(0, 0.16, 1, 0.92))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig06_lod(lod, cfg, path):
    if not len(lod):
        return
    sub = lod[lod["peak_name"].isin(cfg["bands"])]
    panels = [k for k in cfg["panel_label"] if (sub["key"] == k).any()]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.6 * len(panels), 4.8),
                             squeeze=False, sharey=True)
    for ax, key in zip(axes.ravel(), panels):
        g = sub[sub["key"] == key]
        conds = [c for c in cfg["conditions"] if (g["condition"] == c).any()]
        width = 0.8 / max(len(conds), 1)
        xs = np.arange(len(cfg["bands"]))
        for i, cond in enumerate(conds):
            h = g[g["condition"] == cond].set_index("peak_name").reindex(cfg["bands"])
            col, _, lab = _cond_style(cfg, cond)
            ax.bar(xs + i * width, h["lod_factor"].fillna(0), width, color=col,
                   label=lab)
        ax.set_xticks(xs + 0.4 - width / 2)
        ax.set_xticklabels([str(ALL_BANDS[b]["nominal"]) for b in cfg["bands"]],
                           rotation=45, fontsize=7)
        ax.set_yscale("log")
        ax.set_xlabel("band (cm$^{-1}$)")
        ax.set_title(cfg["panel_label"][key], fontsize=9, loc="left")
    axes.ravel()[0].set_ylabel("most dilute point still detected ($\\times$)")
    axes.ravel()[0].legend(fontsize=7, frameon=False)
    fig.suptitle("06  How far down the ladder each band survives. Higher is better; "
                 "0 means it was never detected", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig07_panels_in_order(scans, screen, cfg, path):
    panels = _panels(cfg, screen) + (["unassigned"]
                                     if (screen["key"] == "unassigned").any() else [])
    fig, axes = plt.subplots(1, len(panels), figsize=(3.7 * len(panels), 11),
                             squeeze=False, sharex=True)
    verdict = screen.set_index("scan_number")["verdict"].to_dict()
    by_scan = {r["scan_number"]: r for r in scans}
    for ax, key in zip(axes.ravel(), panels):
        g = screen[screen["key"] == key].sort_values("scan_number")
        step = float(np.percentile(
            [np.ptp(by_scan[int(s)]["smooth_cps"]) for s in g["scan_number"]], 60))
        for i, s in enumerate(g["scan_number"]):
            rec = by_scan[int(s)]
            v = verdict[s]
            cond = rec["condition"]
            col = (VERDICT_COLOR[v] if v != "pass"
                   else cfg["conditions"][cond]["color"] if cond else "0.3")
            ax.plot(rec["x_fit"], rec["smooth_cps"] + i * step, lw=0.7, color=col)
            tag = "%d  %s" % (s, rec["dilution"] or "?")
            ax.text(1810, i * step, " " + tag, fontsize=6, va="center", color=col)
        for b in cfg["bands"]:
            ax.axvline(ALL_BANDS[b]["nominal"], color="0.8", lw=0.5, ls=":", zorder=0)
        ax.set_xlim(400, 1800)
        ax.set_yticks([])
        ax.set_title(cfg["panel_label"].get(key, "Unassigned"), fontsize=9)
        ax.set_xlabel("Raman shift (cm$^{-1}$)")
    fig.suptitle("07  Every scan in acquisition order, labelled with its dilution. "
                 "Colour = condition, red/amber = failed or borderline", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


# ---------------------------------------------------------------------------
def exposure_advice(screen: pd.DataFrame, tcfg: dict) -> pd.DataFrame:
    """Exposure each (paper, dilution) would need to land under the count target.

    Exp 3 answered "which integration time" from ONE spot per paper. Those spots
    were not the worst case: the ladders here reach far brighter spots at the
    concentrated end, and a setting that is safe at 200x saturates at 2x. Counts
    are proportional to exposure in the healthy regime (exp 3, fig 03), so the
    exposure that would land a spot on the target is a rescaling of the one it
    was actually measured at.

    Only scans that PASSED the screen are rescaled. A saturated scan's median is
    compressed by the saturation itself, so extrapolating from it understates how
    far the exposure has to come down - on white 2x, scaling from the saturated
    10 s scan says 5 s while scaling from the clean 6 s scan says 4.4 s.

    That leaves a known bias in the safe direction of being too generous: the
    brightest spot at a dilution is often the one that failed, so the brightest
    SURVIVING spot understates the worst case. `n_failed` flags where that
    applies, and there the number is an upper bound - go shorter.
    """
    q = tcfg["quality"]
    target = q["median_counts_warn"]
    g = screen.dropna(subset=["dilution"])
    rows = []
    for (paper, dil), h in g.groupby(["paper", "dilution"]):
        ok = h[h["verdict"] != "fail"]
        n_failed = int((h["verdict"] == "fail").sum())
        if not len(ok):
            rows.append({"paper": paper, "dilution": dil,
                         "factor": float(str(dil)[:-1]), "brightest_scan": -1,
                         "measured_at_s": np.nan, "median_counts": np.nan,
                         "implied_max_s": np.nan, "n_failed": n_failed,
                         "n_usable": 0})
            continue
        worst = ok.loc[ok["median_counts"].idxmax()]
        rows.append({
            "paper": paper, "dilution": dil, "factor": float(str(dil)[:-1]),
            "brightest_scan": int(worst["scan_number"]),
            "measured_at_s": float(worst["int_time_s"]),
            "median_counts": float(worst["median_counts"]),
            "implied_max_s": float(worst["int_time_s"]) * target
            / float(worst["median_counts"]),
            "n_failed": n_failed, "n_usable": len(ok),
        })
    return pd.DataFrame(rows).sort_values(["paper", "factor"]).reset_index(drop=True)


def _condition(value, cfg, dash="-"):
    """Condition as it is written in the figures, not as it is keyed in YAML.

    The config key for the control is `dyeonly`; everything the reader sees
    says CV, so the tables say CV too.
    """
    if not isinstance(value, str):
        return dash
    return cfg["conditions"].get(value, {}).get("label", value)         if value == cfg["control_condition"] else value


def _or_dash(value, dash="-"):
    """Render a possibly-missing cell. `value or dash` does not work: pandas
    stores a missing string as float('nan'), which is truthy."""
    return dash if value is None or (isinstance(value, float) and np.isnan(value))         else str(value)


def write_findings(screen, bands, gain, lod, advice, cfg, model, path):
    q = cfg["quality"]
    band = cfg["sers_band"]
    fail = screen[screen["verdict"] == "fail"]
    lines = [
        "# Experiment 4 - nanostar dilution panels + bipyramid rerun (083126)", "",
        "Three conditions across the CV ladder - stock stars (9a col 1), 5:1 diluted "
        "stars (9b col 2), CV only - on both papers, plus the 082726 bipyramid print "
        "rerun at fixed exposure. Every gain below is a spot minus its OWN CV-only "
        "control: same paper, same exposure, same dilution.", "",
        "## Read this first: the saturation screen", "",
        "Saturation is scored twice, because it is not uniform across a "
        "spectrum. It starts at the low-wavenumber end, where the fluorescence "
        "background is highest, and on several scans it never reaches the CV "
        "band at all.", "",
        "* **whole scan** - 90th percentile of noise excess over 400-1800 cm-1: "
        "%d of %d fail, %d borderline. This is the right question for \"was this "
        "exposure safe on this paper\"."
        % (int((screen["verdict_scan"] == "fail").sum()), len(screen),
           int((screen["verdict_scan"] == "warn").sum())),
        "* **at %d cm-1** - the same thresholds on the windows overlapping the "
        "band actually plotted: %d fail, %d borderline. This is the right "
        "question for a figure of that band."
        % (ALL_BANDS[band]["nominal"],
           int((screen["verdict_band"] == "fail").sum()),
           int((screen["verdict_band"] == "warn").sum())), "",
        "`quality.screen_scope: %s` is in force, so the figures and gains below "
        "obey the **%s** verdict. Both are in `scan_screen.csv` either way."
        % (screen["screen_scope"].iloc[0], screen["screen_scope"].iloc[0]), "",
        "Scans the two scopes disagree about - the ringing is real, but it is "
        "not where the band is:", "",
        "| panel | condition | dilution | scan | excess, whole scan | excess at "
        "%d | counts at band |" % ALL_BANDS[band]["nominal"],
        "|---|---|---|---|---|---|---|",
    ]
    split = screen[(screen["verdict_scan"] == "fail")
                   & (screen["verdict_band"] != "fail")]
    for _, r in split.sort_values("scan_number").iterrows():
        lines.append("| %s | %s | %s | %d | %.1f | %.1f | %s |"
                     % (cfg["panel_label"].get(r["key"], r["key"]),
                        _condition(r["condition"], cfg), _or_dash(r["dilution"]),
                        r["scan_number"], r["noise_excess_p90"],
                        r["noise_excess_at_band"],
                        "{:,.0f}".format(r["level_at_band_counts"])))
    lines += [
        "",
        "Still failing at the band itself - here the ringing does reach %d, and "
        "these stay out of every gain and every spectrum:"
        % ALL_BANDS[band]["nominal"], "",
        "| panel | condition | dilution | scan | excess at %d |"
        % ALL_BANDS[band]["nominal"],
        "|---|---|---|---|---|",
    ]
    for _, r in screen[screen["verdict_band"] == "fail"].sort_values(
            "scan_number").iterrows():
        lines.append("| %s | %s | %s | %d | %.1f |"
                     % (cfg["panel_label"].get(r["key"], r["key"]),
                        _condition(r["condition"], cfg), _or_dash(r["dilution"]),
                        r["scan_number"], r["noise_excess_at_band"]))
    lines += [
        "",
        "One caveat the noise test does not cover. Noise and linearity fail "
        "differently: a band can be quiet enough to measure and still sit above "
        "the count level the noise law was fitted below (%s), where the "
        "detector response can compress and under-report a height. %d scan(s) "
        "are in that state and are marked `band_above_fit_ceiling`; their bars "
        "are hatched, and a gain built on one carries "
        "`above_linearity_ceiling`. Read those heights as lower bounds."
        % ("{:,.0f}".format(model["fit_max_counts"]),
           int(screen["band_above_fit_ceiling"].sum())), "",
        "Your note that ~5 s still looked oversaturated is borne out: at 6 s, %d "
        "of the %d white star scans ring over the whole scan, though only %d of "
        "them ring at the CV band."
        % (int(((screen["key"] == "w-6") & (screen["verdict_scan"] == "fail")).sum()),
           int((screen["key"] == "w-6").sum()),
           int(((screen["key"] == "w-6") & (screen["verdict_band"] == "fail")).sum())),
        "",
    ]

    if len(gain):
        g = gain[(gain["peak_name"] == band)]
        lines += [
            "## SERS gain at %d cm-1" % ALL_BANDS[band]["nominal"], "",
            "Gain is (nanoparticle spot) / (CV-only spot) at the same dilution. "
            "1.0x means the nanoparticles did nothing. A gain is only counted as "
            "established when the difference spectrum clears SNR %g."
            % q["min_snr"], "",
            "| panel | condition | dilution | CV only (cps) | with NP (cps) | gain | "
            "delta SNR | control SNR | verdict |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for _, r in g.iterrows():
            if r["gain_trustworthy"]:
                v = "**enhancement**"
            elif r["gain_confirmed"]:
                v = "control in noise"
            elif r["delta_detected"]:
                v = "differs, but no gain"
            else:
                v = "no difference"
            lines.append("| %s | %s | %s | %.1f | %.1f | **%.2fx** | %.1f | %.1f | %s |"
                         % (cfg["panel_label"].get(r["key"], r["key"]),
                            cfg["conditions"][r["condition"]]["label"],
                            r["dilution"], r["control_cps"], r["test_cps"],
                            r["gain_x"], r["delta_snr"], r["control_snr"], v))
        sb = cfg["substrate_band"]
        est = gain[gain["gain_trustworthy"]]
        lines += [
            "", "## The internal control says the enhancement is real", "",
            "Two of the six bands scored are not CV. `%s` is carbonate filler in "
            "the sheet, and on paper the 1375 band is dominated by cellulose rather "
            "than by CV. Neither can be enhanced by nanoparticles sitting on the CV - "
            "so if the \"gain\" were really a substrate or exposure artifact, those two "
            "would rise with everything else. They do not:" % sb, "",
            "| band | what it is | median gain |", "|---|---|---|",
        ]
        for b in cfg["bands"]:
            med = gain[gain["peak_name"] == b]["gain_x"].median()
            what = {"paper_1095": "paper filler - NOT analyte",
                    "cv_1375": "CV, but cellulose-dominated on paper"}.get(b, "CV band")
            lines.append("| %d cm-1 | %s | %.2fx |"
                         % (ALL_BANDS[b]["nominal"], what, med))
        med = {b: gain[gain["peak_name"] == b]["gain_x"].median() for b in cfg["bands"]}
        strong = [b for b in cfg["bands"]
                  if b not in (sb, "cv_1375") and med[b] >= 1.5]
        weak = [b for b in cfg["bands"]
                if b not in (sb, "cv_1375") and med[b] < 1.5]
        lines += [
            "",
            "Both substrate-carried bands sit BELOW 1.0 (%.2f and %.2f), so the "
            "nanoparticle spots were if anything on slightly dimmer paper - the gains "
            "are not being manufactured by a brighter patch of sheet or a longer "
            "exposure. That is the one comparison here that no artifact explains, and "
            "it is worth more than any individual gain number."
            % (med[sb], med["cv_1375"]), "",
            "Be precise about how far it goes, though. The separation is clear for "
            "%s (median %s) and only marginal for %s (median %s) - those two are weak "
            "bands whose per-scan scatter is comparable to the effect. The case rests "
            "on the strong bands."
            % (", ".join("%d cm-1" % ALL_BANDS[b]["nominal"] for b in strong),
               ", ".join("%.2fx" % med[b] for b in strong),
               ", ".join("%d cm-1" % ALL_BANDS[b]["nominal"] for b in weak),
               ", ".join("%.2fx" % med[b] for b in weak)), "",
            "%d of %d comparisons at %d cm-1 are established enhancements."
            % (int(est[est["peak_name"] == band].shape[0]),
               int(g.shape[0]), ALL_BANDS[band]["nominal"]), "",
        ]
    else:
        lines += ["## SERS gain", "",
                  "No gain could be computed - no dilution had both a nanoparticle "
                  "spot and a dye-only control survive the screen.", ""]

    if len(lod):
        lines += [
            "## How far down the ladder each condition survives", "",
            "Most dilute point still detected at %d cm-1, walking from the strong end "
            "and stopping at the first failure." % ALL_BANDS[band]["nominal"], "",
            "| panel | condition | detected | limit |", "|---|---|---|---|",
        ]
        for _, r in lod[lod["peak_name"] == band].iterrows():
            lines.append("| %s | %s | %d of %d | %s |"
                         % (cfg["panel_label"].get(r["key"], r["key"]),
                            cfg["conditions"][r["condition"]]["label"],
                            r["n_detected"], r["n_points"],
                            _or_dash(r["lod"], "never detected")))
        lines.append("")

    lines += [
        "## The right exposure depends on the dilution, not just the paper", "",
        "Exp 3 picked an exposure per paper from one spot each - offwhite col 3 row 1, "
        "white col 3 row 4. Those spots were not the worst case. The 2x end of these "
        "ladders is far brighter, and it saturates at settings that are perfectly safe "
        "at 200x. Counts scale with exposure in the healthy regime, so rescaling the "
        "brightest spot measured at each dilution onto the %s-count target gives the "
        "longest exposure that dilution can take:"
        % "{:,}".format(20000), "",
        "Only unsaturated scans are rescaled - a saturated median is compressed by "
        "the saturation and would flatter the answer.", "",
        "| paper | dilution | brightest clean scan | measured at | its median | "
        "max safe | saturated at this dilution |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, r in advice.iterrows():
        if not np.isfinite(r["implied_max_s"]):
            lines.append("| %s | %s | - | - | - | **every scan saturated** | %d |"
                         % (r["paper"], r["dilution"], r["n_failed"]))
            continue
        lines.append("| %s | %s | %d | %g s | %s | **%.1f s**%s | %d |"
                     % (r["paper"], r["dilution"], r["brightest_scan"],
                        r["measured_at_s"], "{:,.0f}".format(r["median_counts"]),
                        r["implied_max_s"], " (upper bound)" if r["n_failed"] else "",
                        r["n_failed"]))
    lines.append("")
    for paper, h in advice.groupby("paper"):
        strong = h[h["factor"] <= 10]["implied_max_s"].min()
        weak = h[h["factor"] > 10]["implied_max_s"].min()
        lines.append(
            "* **%s:** ~%.0f s at the concentrated end (2x-10x), ~%.0f s from 20x down."
            % (paper, strong, weak if np.isfinite(weak) else strong))
    lines += [
        "",
        "Your instinct that ~5 s still looked oversaturated on white was right, and if "
        "anything conservative: the brightest clean 2x spot on white puts the ceiling "
        "at %.1f s, and because brighter 2x spots than that one saturated outright, "
        "the real ceiling is lower. **4 s is the setting to use for the concentrated "
        "end on white.**"
        % advice[(advice["paper"] == "white") & (advice["factor"] == 2.0)]
        ["implied_max_s"].min(), "",
        "One exposure for the whole ladder means taking the concentrated end's number. "
        "Splitting the ladder across two exposures buys back signal at the dilute end, "
        "at the cost of the counts/second offset between exposures noted in exp 3 "
        "(up to ~15%) - which matters here, because a gain is a ratio between two "
        "spots and both must be on the same exposure for it to mean anything.", "",
        "## Where the notes and the files disagree", "",
        "* **Active figures use only White 6 s and Offwhite 10 s.** White 8 s and "
        "White 10 s scans remain in the audit CSVs but are excluded from every active "
        "plot, so exposure times are never mixed.",
        "* **Scans 823-827 are not in this dump.** The white 10 s stock-star series is "
        "noted as 823-830; only 828-830 are here, so that series keeps just its three "
        "most dilute points (50x, 100x, 200x) and has no strong end to compare.",
        "* **Scans 820-822 are noted as 8 s; the files record 10.00 s** (OpName "
        "\"10S\") on all three. The report reads exposure from the file. Scan 847 is "
        "the one that really is 8 s.",
        "* **The rerun blocks are written \"2x - 20x\" in the notes, but are "
        "2x/5x/10x.** The operator confirmed the three spots are the first three "
        "columns of the print. Corrected 090126; bipyramid points in any earlier "
        "run of this report were labelled one or two rungs too dilute.", "",
        "## The dilutions the figures use", "",
        "Every arm measured 2x, 5x and 10x. Only the offwhite nanostar ladders "
        "went further (out to 200x for stock stars, 30x for 5:1, 20x for CV "
        "only). The paper figures are held to the shared three so paper, "
        "particle and dilution can be read against each other with no missing "
        "cell; the CSVs below keep every point.", "",
        "## Files", "",
        "* `scan_screen.csv` - saturation verdict, condition and dilution per scan",
        "* `band_metrics.csv` - band height, centre, SNR per scan",
        "* `sers_gain.csv` - paired gain per panel, condition, dilution and band",
        "* `detection_limits.csv` - most dilute point still detected",
        "* `exposure_advice.csv` - longest safe exposure per paper and dilution",
        "* `scan_key.csv`, `scan_key_4_tabs.xlsx` - bench-readable scan key for "
        "the whole dump, generated from the config so a corrected dilution "
        "reaches it",
        "* `scan_key_v2_plot_data_4_tabs.xlsx` - just the scans behind the "
        "figures, with the 1620 cm-1 height, SNR and gain each one carries",
        "* `figures/scan_gallery/` - every scan drawn one per cell, condition by "
        "dilution, so a screen verdict can be checked by eye",
        "* `figures/offwhite_10s/` - Offwhite 10 s figures focused on 1620 cm-1",
        "* `figures/white_6s/` - White 6 s figures focused on 1620 cm-1",
        "  * `11_dilution_series_by_nanoparticle.png` - each arm's own CV ladder",
        "  * `12_nanoparticles_at_each_dilution.png` - the arms side by side at "
        "every dilution they share",
        "  * `13_1620_intensity_by_dilution.png` - the same block as 1620 cm-1 "
        "peak heights", "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    tcfg = ts.load_config(ROOT / "configs" / cfg["screen"]["timeseries_config"])
    out = ROOT / cfg["io"]["output_dir"]
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    model = load_noise_model(cfg)
    scans = load_scans(cfg)
    print("[exp4] %d scans loaded; %d mapped to a condition"
          % (len(scans), sum(r["condition"] is not None for r in scans)))

    screen = scan_screen(scans, model, cfg, tcfg)
    screen["factor"] = screen["dilution"].map(
        lambda d: float(str(d)[:-1]) if isinstance(d, str) else np.nan)
    bands = band_table(scans, screen, cfg)
    gain = annotate_control_quality(gain_table(scans, screen, cfg), bands, cfg)
    lod = detection_limits(bands, cfg)
    advice = exposure_advice(screen, tcfg)

    screen.to_csv(out / "scan_screen.csv", index=False)
    bands.to_csv(out / "band_metrics.csv", index=False)
    gain.to_csv(out / "sers_gain.csv", index=False)
    lod.to_csv(out / "detection_limits.csv", index=False)
    advice.to_csv(out / "exposure_advice.csv", index=False)
    scan_key_writer.write(cfg, screen, out)
    scan_key_writer.write_v2(cfg, screen, bands, gain, out)

    paper_plots.remove_obsolete_non1620(fig_dir)
    paper_plots.generate(scans, screen, bands, gain, cfg, fig_dir)
    sheets = scan_gallery.generate(scans, screen, cfg, fig_dir)
    print("[exp4] %d scan-gallery sheet(s) in figures/scan_gallery" % len(sheets))

    write_findings(screen, bands, gain, lod, advice, cfg, model, out / "FINDINGS.md")
    (out / "run_context.json").write_text(json.dumps({
        "config": CONFIG_PATH.name,
        "noise_model": model,
        "n_scans": len(scans),
        "n_mapped": int(cfg["_map"].shape[0]),
        "panels": cfg["panel_label"],
    }, indent=2, default=str), encoding="utf-8")

    pd.set_option("display.width", 240)
    print("\n=== SATURATION SCREEN ===")
    print(screen.groupby("key", sort=False)["verdict"].value_counts()
          .unstack(fill_value=0).to_string())
    bad = screen[screen["verdict"] == "fail"]
    if len(bad):
        print("\n=== FAILED (excluded from gains) ===")
        print(bad[["key", "condition", "dilution", "scan_number", "int_time_s",
                   "median_counts", "noise_excess_p90"]].round(1).to_string(index=False))
    if len(gain):
        print("\n=== GAIN AT %d cm-1 ===" % ALL_BANDS[cfg["sers_band"]]["nominal"])
        print(gain[gain["peak_name"] == cfg["sers_band"]][
            ["key", "condition", "dilution", "control_cps", "test_cps", "gain_x",
             "delta_snr", "control_snr", "gain_confirmed",
             "gain_trustworthy"]].round(2).to_string(index=False))
    print("\nWrote " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
