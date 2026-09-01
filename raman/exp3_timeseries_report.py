#!/usr/bin/env python
"""exp3_timeseries_report.py - integration-time sweep, 083126.

Experiment 3 asks a different question from experiments 1 and 2. There the
sample varied and the acquisition was fixed; here ONE SPOT is measured again
and again while the exposure falls 20 s -> 2 s. The deliverable is a single
number per paper: the integration time every future scan should use.

Four sweeps:

    ow-bp    offwhite / bipyramids   scans 785-790   20,10,8,6,4,2 s
    ow-star  offwhite / stars        scans 791-796   20,10,8,6,4,2 s
    w-bp     white    / bipyramids   scans 797-802   20,10,8,6,4,2 s
    w-star   white    / stars        scans 869-873      10,8,6,4,2 s

WHY THE OBVIOUS METRICS DO NOT WORK
-----------------------------------
"Longer exposure = more counts = better" holds right up until it doesn't, and
raw counts cannot tell you where that is: they rise monotonically on both sides
of the failure. Absolute noise is no better, because noise grows with signal
even in a perfectly healthy detector. Nor does the spectrum flat-top - these
exports never clip, so nothing looks obviously wrong.

What does work is to fit the detector's own noise law,

    sigma^2 = read^2 + gain * N,

on data that cannot be saturated (every sliding window in the dump below
30k counts), and then ask each scan how far its measured noise sits ABOVE that
law at its own count level. That ratio - the noise excess - is ~1 for a healthy
scan at any exposure, and rises to 3-28 for a saturating one. It is
dimensionless, needs no reference scan, and carries the read-noise floor that
explains the short-exposure end for free.

The noise on this instrument is never white: it is correlated over ~10 cm-1,
because a ~10 cm-1 optical resolution is exported onto a 1 cm-1 grid. That is
true of healthy scans too - the measured colour of the noise does not change
when a scan saturates, only its amplitude does, by two orders of magnitude.
The consequence is the dangerous part: noise correlated over 10 cm-1 has the
width of a narrow Raman band, so it survives Savitzky-Golay smoothing and gets
found and fitted as a peak. A saturated scan still LOOKS like a spectrum and
still yields peaks. That is why the noise excess, and not the eye, has to
decide.

Band windows are imported from exp1_cv_report so all three experiments score
against identical definitions. Everything tunable lives in
configs/timeseries_exp3.yaml.

Run (from raman/):
    python exp3_timeseries_report.py
"""
from __future__ import annotations

import csv
import json
import re
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
from scipy.signal import find_peaks, savgol_filter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from raman_lib.preprocessing import baseline_arpls          # noqa: E402
from exp1_cv_report import BANDS as CV_BANDS                # noqa: E402

CONFIG_PATH = ROOT / "configs" / "timeseries_exp3.yaml"

# The carbonate filler band. It is a property of the PAPER, not of the dye. It
# is NOT the reference band - that is the dye band, cv_1620 - but it is carried
# alongside it, because its true intensity cannot move with the dye, with
# photobleaching or with how much analyte landed on the spot. That makes it the
# one trace that responds to exposure and to nothing else, so it says whether a
# change in the dye band is the exposure or the sample. See the paper-SERS
# confound notes: ~1095 on these sheets is filler, and must never be read as a
# dye band.
PAPER_BANDS = {
    "paper_1095": dict(nominal=1095, lit=(1080, 1110), search=(1075, 1115),
                       tier="substrate",
                       assign="carbonate filler in the sheet (NOT analyte)"),
}
ALL_BANDS = {**CV_BANDS, **PAPER_BANDS}

# colour = paper, dash = particle. Same two-channel encoding as exp 1 and 2.
COLOR_BY_PAPER = {"offwhite": "#c07a2a", "white": "#2f6ea8"}
DASH_BY_PARTICLE = {"bipyramids": (0, (5, 2)), "stars": "solid"}
MARKER_BY_PARTICLE = {"bipyramids": "s", "stars": "o"}
VERDICT_COLOR = {"pass": "#2e7d47", "warn": "#d99b1c", "fail": "#b53229"}
VERDICT_RANK = {"pass": 0, "warn": 1, "fail": 2}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    named = set(cfg["bands"]) | set(cfg["quality"]["snr_bands"])
    unknown = sorted(b for b in named if b not in ALL_BANDS)
    if unknown:
        raise SystemExit("[error] %s names unknown bands: %s" % (path.name, unknown))
    if cfg["reference_band"] not in cfg["bands"]:
        raise SystemExit("[error] reference_band must also appear in bands:")
    missing = sorted(set(cfg["quality"]["snr_bands"]) - set(cfg["bands"]))
    if missing:
        raise SystemExit("[error] snr_bands not in bands: %s" % missing)
    return cfg


# ---------------------------------------------------------------------------
# Loading. The metadata export is the only file read: it carries the exposure
# in its header AND the spectrum in its "Intensities" row, so exposure and
# spectrum cannot be mismatched by a filename-pairing mistake.
# ---------------------------------------------------------------------------
def read_metadata_csv(path: Path) -> dict | None:
    """Parse one metadata export. Returns None for the bare two-column export."""
    fields: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        if not fh.readline().startswith('"Name"'):
            return None                       # the two-column export, not this one
        fh.seek(0)
        for row in csv.reader(fh):
            if len(row) >= 2:
                fields[row[0]] = row[1]
    if "Intensities" not in fields:
        return None

    y = np.asarray([float(v) for v in fields["Intensities"].split(",")], dtype=float)
    first_cm1 = float(fields["Firstwavenumber"])
    last_cm1 = float(fields["LastWavenumber"])
    # The export gives no axis, only its endpoints; the instrument writes one
    # point per cm-1, which this length check confirms rather than assumes.
    if len(y) != int(round(last_cm1 - first_cm1)) + 1:
        raise SystemExit("[error] %s: %d points for range %g-%g cm-1"
                         % (path.name, len(y), first_cm1, last_cm1))
    x = first_cm1 + np.arange(len(y), dtype=float)

    scan = re.search(r"(?i)scan[ _-]?0*(\d+)", path.name)
    return {
        "scan_number": int(scan.group(1)) if scan else None,
        "int_time_s": float(fields["IntTime"]),
        "auto_int": fields.get("AutoInt"),
        "averages": int(fields.get("Averages", "1") or 1),
        "laser_power": fields.get("LaserPower"),
        "laser_nm": fields.get("Wavelength"),
        "created": fields.get("CreatedDate"),
        "op_name": fields.get("OpName"),
        "x": x, "y_raw": y, "path": path,
    }


def load_scans(cfg: dict) -> tuple[list[dict], list[dict]]:
    """(sweep scans, every scan in the dump). The second only widens the fit."""
    raw_root = ROOT / cfg["io"]["raw_root"]
    if not raw_root.is_dir():
        raise SystemExit("[error] raw root not found: %s" % raw_root)

    by_folder = {s["folder"]: s for s in cfg["sweeps"]}
    sweep_scans, all_scans = [], []
    for path in sorted(raw_root.rglob("*.csv")):
        rec = read_metadata_csv(path)
        if rec is None:
            continue
        rel = path.parent.relative_to(raw_root).as_posix()
        rec["folder"] = rel
        rec["total_time_s"] = rec["int_time_s"] * rec["averages"]
        all_scans.append(rec)
        spec = by_folder.get(rel)
        if spec is not None:
            rec.update({k: spec[k] for k in ("key", "paper", "particle", "label")})
            sweep_scans.append(rec)

    missing = set(by_folder) - {r["folder"] for r in sweep_scans}
    if missing:
        raise SystemExit("[error] sweep folders not found under %s: %s"
                         % (raw_root, sorted(missing)))
    if any(r["auto_int"] != "Off" for r in sweep_scans):
        raise SystemExit("[error] AutoInt fired inside a sweep - exposure is no longer "
                         "the controlled variable and the sweep cannot be interpreted.")
    return sweep_scans, all_scans


# ---------------------------------------------------------------------------
# Per-scan processing
# ---------------------------------------------------------------------------
def process(rec: dict, cfg: dict) -> dict:
    """Trim, convert to counts/s, baseline, smooth."""
    rng, pre = cfg["analysis_range"], cfg["preprocessing"]
    x, y = rec["x"], rec["y_raw"]
    if rng.get("enabled", True):
        m = (x >= rng["min"]) & (x <= rng["max"])
        x, y = x[m], y[m]

    cps = y / rec["int_time_s"]                    # exposure divided out
    arp = pre["baseline"]["arpls"]
    base = baseline_arpls(cps, lam=arp["lam"], ratio=arp["ratio"], niter=arp["niter"])
    corr = cps - base
    sg = pre["smoothing"]["savgol"]
    smooth = savgol_filter(corr, sg["window"], sg["polyorder"])

    rec = dict(rec)
    rec.update({"x_fit": x, "y_counts": y, "y_cps": cps,
                "baseline_cps": base, "corr_cps": corr, "smooth_cps": smooth})
    return rec


def _sigma_from(seg: np.ndarray) -> float:
    """Point-to-point noise from the SECOND difference.

    The first difference is the usual choice, but on these spectra the
    fluorescence background is so steep at low wavenumber that its slope leaks
    into it. The second difference is blind to any linear trend, so what comes
    back is noise and only noise.  var(d2) = 6 var(y) for white noise.
    """
    d2 = np.diff(seg, 2)
    if d2.size < 3:
        return float("nan")
    mad = np.median(np.abs(d2 - np.median(d2)))
    return float(mad * 1.4826 / np.sqrt(6.0))


def noise_profile(x: np.ndarray, y_counts: np.ndarray, width: float) -> pd.DataFrame:
    """Sliding-window local noise and local level, in RAW COUNTS.

    Never in counts/second: sigma^2 = read^2 + gain*N is a statement about
    photons actually collected, and dividing by the exposure first would scale
    the two sides differently and destroy the test.
    """
    rows = []
    for lo in np.arange(x[0], x[-1] - width + 1, width):
        m = (x >= lo) & (x < lo + width)
        if m.sum() < 20:
            continue
        seg = y_counts[m]
        rows.append({"center_cm1": float(lo + width / 2),
                     "level_counts": float(np.median(seg)),
                     "sigma_counts": _sigma_from(seg)})
    return pd.DataFrame(rows)


def _detrend(seg: np.ndarray, window: int = 15) -> np.ndarray:
    """Remove everything broader than a real Raman band, keep the fine ripple."""
    return seg - np.convolve(seg, np.ones(window) / window, mode="same")


# ---------------------------------------------------------------------------
# The detector's noise law, fitted rather than assumed
# ---------------------------------------------------------------------------
def fit_noise_model(windows: pd.DataFrame, cfg: dict) -> dict:
    """Robust fit of sigma^2 = read^2 + gain * N over unsaturable windows.

    Fitted only below fit_max_counts, so no saturated point can pull the law it
    is about to be judged against. Re-weighted a few times with 3-sigma
    trimming, because a handful of cosmic-ray windows would otherwise dominate
    an ordinary least-squares fit of a variance.
    """
    nm = cfg["noise_model"]
    d = windows.dropna(subset=["sigma_counts", "level_counts"])
    d = d[(d["level_counts"] > 0) & (d["level_counts"] < nm["fit_max_counts"])]
    if len(d) < 20:
        raise SystemExit("[error] only %d windows below %d counts - too few to fit "
                         "the noise model" % (len(d), nm["fit_max_counts"]))

    A = np.column_stack([np.ones(len(d)), d["level_counts"].to_numpy()])
    var = d["sigma_counts"].to_numpy() ** 2
    keep = np.ones(len(d), dtype=bool)
    coef = np.array([var.mean(), 0.0])
    for _ in range(int(nm["fit_iterations"])):
        coef, *_ = np.linalg.lstsq(A[keep], var[keep], rcond=None)
        resid = var - A @ coef
        scale = np.median(np.abs(resid - np.median(resid))) * 1.4826
        keep = np.abs(resid) < 3 * scale if scale > 0 else keep
    read_var, gain = float(coef[0]), float(coef[1])
    return {"read_var": read_var, "gain": gain,
            "read_counts": float(np.sqrt(max(read_var, 0.0))),
            "n_fit_windows": int(keep.sum()), "n_candidate_windows": int(len(d)),
            "fit_max_counts": nm["fit_max_counts"]}


def model_sigma(level_counts, model: dict) -> np.ndarray:
    return np.sqrt(np.maximum(model["read_var"] + model["gain"] * np.asarray(
        level_counts, dtype=float), 1e-12))


def add_excess(windows: pd.DataFrame, model: dict) -> pd.DataFrame:
    out = windows.copy()
    out["sigma_model"] = model_sigma(out["level_counts"], model)
    out["excess"] = out["sigma_counts"] / out["sigma_model"]
    return out


# ---------------------------------------------------------------------------
# The excess noise is band-limited, so measure WHERE in period it lives
# ---------------------------------------------------------------------------
def _periodogram(x, y_counts, cfg):
    lo, hi = cfg["excess_noise"]["measure_window_cm1"]
    m = (x >= lo) & (x <= hi)
    seg = y_counts[m]
    if seg.size < 128:
        return None, None
    res = _detrend(seg)[10:-10]
    amp = np.abs(np.fft.rfft(res * np.hanning(res.size))) / res.size * 2
    freq = np.fft.rfftfreq(res.size, d=1.0)
    period = np.divide(1.0, freq, out=np.full_like(freq, np.inf), where=freq > 0)
    return period, amp


def excess_noise_spectrum(x, y_counts, cfg) -> dict:
    """Amplitude of the fine structure per ripple-period band.

    White noise would be flat across every band. It is not: the power piles up
    around 10 cm-1 and is essentially absent below 4 cm-1, because a ~10 cm-1
    optical resolution is being exported on a 1 cm-1 grid.

    `colour_ratio` tests whether SATURATION changes that shape. It does not -
    the ratio is flat across every scan in every sweep - which is a useful null
    result: saturation does not introduce a new kind of noise, it multiplies
    the noise the instrument always had. That is why the amplitude, and not the
    shape, is the thing to threshold on.
    """
    en = cfg["excess_noise"]
    period, amp = _periodogram(x, y_counts, cfg)
    if period is None:
        return {}
    out = {}
    for lo, hi in en["period_bands_cm1"]:
        sel = (period >= lo) & (period < hi)
        out["amp_%g_%g" % (lo, hi)] = float(amp[sel].mean()) if sel.any() else np.nan
    slo, shi = en["signature_band_cm1"]
    wlo, whi = en["white_band_cm1"]
    sig = amp[(period >= slo) & (period < shi)].mean()
    white = amp[(period >= wlo) & (period < whi)].mean()
    out["signature_amp"] = float(sig)
    out["white_amp"] = float(white)
    out["colour_ratio"] = float(sig / white) if white > 0 else np.nan
    return out


def window_peak(x: np.ndarray, y: np.ndarray, lo: float,
                hi: float) -> tuple[float, float, bool]:
    """(centre, height, at_edge) of the band inside [lo, hi].

    The highest INTERIOR local maximum, not the plain maximum of the window.
    A search window usually clips the shoulder of a neighbouring feature, and a
    plain max would return the value at that edge - which is not this band, is
    systematically too high, and would inflate every ratio built from it.
    Falls back to the plain argmax only when the window holds no local maximum
    at all, and says so via at_edge.

    Anything comparing two spectra band by band must call this, so the numbers
    in a difference or a ratio come from the same estimator as the per-scan
    table they will be checked against.
    """
    sel = np.where((x >= lo) & (x <= hi))[0]
    local = y[sel]
    loc, _ = find_peaks(local)
    if loc.size:
        j, at_edge = int(loc[int(np.argmax(local[loc]))]), False
    else:
        j = int(np.argmax(local))
        at_edge = j in (0, local.size - 1)
    return float(x[sel][j]), float(local[j]), at_edge


def band_metrics(rec: dict, cfg: dict) -> list[dict]:
    """Height and SNR of each configured band on the corrected counts/s trace."""
    x, corr, smooth = rec["x_fit"], rec["corr_cps"], rec["smooth_cps"]
    out = []
    for band in cfg["bands"]:
        meta = ALL_BANDS[band]
        lo, hi = meta["search"]
        centre, height, at_edge = window_peak(x, smooth, lo, hi)
        # The SNR denominator comes from a signal-free shoulder just outside the
        # band, on the UNSMOOTHED trace - smoothing suppresses noise without
        # suppressing signal, so an SNR taken after it is inflated.
        pad = 60.0
        shoulder = ((x >= lo - pad) & (x < lo)) | ((x > hi) & (x <= hi + pad))
        sigma = _sigma_from(corr[shoulder]) if shoulder.sum() > 20 else _sigma_from(corr)
        lit_lo, lit_hi = meta["lit"]
        out.append({
            "scan_number": rec["scan_number"], "key": rec["key"],
            "int_time_s": rec["int_time_s"], "peak_name": band,
            "nominal": meta["nominal"], "center_cm1": centre,
            "in_lit_window": lit_lo <= centre <= lit_hi, "at_window_edge": at_edge,
            "height_cps": height, "sigma_cps": sigma,
            "snr": height / sigma if sigma and np.isfinite(sigma) else np.nan,
        })
    return out


# ---------------------------------------------------------------------------
# Sweep-level tables
# ---------------------------------------------------------------------------
def window_table(scans: list[dict], cfg: dict) -> pd.DataFrame:
    """Every sliding window of every scan - the noise model's training set."""
    rng = cfg["analysis_range"]
    width = cfg["noise_model"]["window_cm1"]
    rows = []
    for r in scans:
        x, y = r["x"], r["y_raw"]
        m = (x >= rng["min"]) & (x <= rng["max"])
        prof = noise_profile(x[m], y[m], width)
        prof["scan_number"] = r["scan_number"]
        prof["int_time_s"] = r["int_time_s"]
        prof["folder"] = r["folder"]
        rows.append(prof)
    return pd.concat(rows, ignore_index=True).dropna(
        subset=["sigma_counts", "level_counts"])


def scan_table(scans: list[dict], model: dict, cfg: dict) -> pd.DataFrame:
    width = cfg["noise_model"]["window_cm1"]
    rows = []
    for r in scans:
        prof = add_excess(noise_profile(r["x_fit"], r["y_counts"], width), model)
        row = {
            "key": r["key"], "label": r["label"], "paper": r["paper"],
            "particle": r["particle"], "scan_number": r["scan_number"],
            "int_time_s": r["int_time_s"], "averages": r["averages"],
            "total_time_s": r["total_time_s"], "auto_int": r["auto_int"],
            "laser_power": r["laser_power"],
            "max_counts": float(r["y_counts"].max()),
            "median_counts": float(np.median(r["y_counts"])),
            "noise_excess_median": float(prof["excess"].median()),
            "noise_excess_p90": float(prof["excess"].quantile(0.90)),
            "noise_excess_max": float(prof["excess"].max()),
        }
        row.update(excess_noise_spectrum(r["x_fit"], r["y_counts"], cfg))
        for p in cfg["quality"]["probe_windows"]:
            m = (r["x_fit"] >= p["min"]) & (r["x_fit"] <= p["max"])
            row["I%s_counts" % p["name"]] = float(r["y_counts"][m].mean())
            row["I%s_cps" % p["name"]] = float(r["y_cps"][m].mean())
        rows.append(row)
    return (pd.DataFrame(rows)
            .sort_values(["key", "int_time_s"], ascending=[True, False])
            .reset_index(drop=True))


def add_linearity(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Departure of counts/s from the SHORT-EXPOSURE extrapolation, per probe.

    The reference is built from the two shortest exposures in each sweep, not
    from a fit through all of them. A fit through all of them would let the
    very scans under test drag the reference toward themselves and hide the
    failure; the shortest exposures have the least detector fill and so are the
    ones least able to be wrong.
    """
    names = [p["name"] for p in cfg["quality"]["probe_windows"]]
    out = []
    for _, g in df.groupby("key", sort=False):
        g = g.sort_values("int_time_s").copy()
        for n in names:
            ref = g["I%s_cps" % n].head(2).mean()
            g["dev%s_pct" % n] = 100.0 * (g["I%s_cps" % n] - ref) / ref
        g["dev_worst_pct"] = g[["dev%s_pct" % n for n in names]].abs().max(axis=1)
        out.append(g)
    return (pd.concat(out)
            .sort_values(["key", "int_time_s"], ascending=[True, False])
            .reset_index(drop=True))


def add_fidelity(df: pd.DataFrame, scans: list[dict]) -> pd.DataFrame:
    """Correlation of each corrected spectrum with its sweep's shortest scan.

    Restricted to 800-1800 cm-1. Below 800 the fluorescence shoulder dominates
    and would carry the correlation to ~1 for every scan including the broken
    ones; the bands that matter all sit above it.
    """
    by_key: dict[str, list[dict]] = {}
    for r in scans:
        by_key.setdefault(r["key"], []).append(r)
    vals = {}
    for key, recs in by_key.items():
        recs = sorted(recs, key=lambda r: r["int_time_s"])
        ref = recs[0]
        m = (ref["x_fit"] >= 800) & (ref["x_fit"] <= 1800)
        a = ref["smooth_cps"][m]
        for r in recs:
            vals[(key, r["scan_number"])] = float(
                np.corrcoef(a, r["smooth_cps"][m])[0, 1])
    df = df.copy()
    df["fidelity_vs_shortest"] = [vals[(k, s)] for k, s in
                                  zip(df["key"], df["scan_number"])]
    return df


def screen_all(scans: list[dict], model: dict, cfg: dict) -> pd.DataFrame:
    """Noise-excess verdict for every scan in the dump, sweeps included.

    The sweeps alone give a flattering picture of how well raw counts predict
    failure, because within a sweep the count level only moves one way. The
    other 61 scans cover the same count range at fixed exposure, so they are
    what shows whether a count threshold actually generalises. It does not
    fully - which is the point, and why the noise excess is the real test and
    the count level only a screening proxy.
    """
    q = cfg["quality"]
    width = cfg["noise_model"]["window_cm1"]
    rng = cfg["analysis_range"]
    rows = []
    for r in scans:
        x, y = r["x"], r["y_raw"]
        m = (x >= rng["min"]) & (x <= rng["max"])
        prof = add_excess(noise_profile(x[m], y[m], width), model)
        p90 = float(prof["excess"].quantile(0.90))
        rows.append({
            "folder": r["folder"], "scan_number": r["scan_number"],
            "int_time_s": r["int_time_s"],
            "median_counts": float(np.median(y[m])), "max_counts": float(y[m].max()),
            "noise_excess_p90": p90,
            "verdict": ("fail" if p90 >= q["noise_excess_fail"]
                        else "warn" if p90 >= q["noise_excess_warn"] else "pass"),
        })
    return pd.DataFrame(rows).sort_values("scan_number").reset_index(drop=True)


def verdict_table(df: pd.DataFrame, bands: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Traffic light per criterion, then the recommended exposure per sweep.

    A criterion is a VETO. The recommendation is the longest exposure that is
    fully clean; a warn-level exposure is only picked when nothing at all is
    clean. Warns still appear as caveats on whatever is chosen.
    """
    q = cfg["quality"]
    med_snr = (bands[bands["peak_name"].isin(q["snr_bands"])]
               .groupby(["key", "scan_number"])["snr"].median().to_dict())

    def light(value, warn, fail, higher_is_worse=True):
        if not np.isfinite(value):
            return "fail"
        if higher_is_worse:
            return "fail" if value >= fail else ("warn" if value >= warn else "pass")
        return "fail" if value <= fail else ("warn" if value <= warn else "pass")

    rows = []
    for _, r in df.iterrows():
        s = med_snr.get((r["key"], r["scan_number"]), np.nan)
        crit = {
            "noise_excess": light(r["noise_excess_p90"],
                                  q["noise_excess_warn"], q["noise_excess_fail"]),
            "count_headroom": light(r["median_counts"],
                                    q["median_counts_warn"], q["median_counts_fail"]),
            "linearity": light(r["dev_worst_pct"],
                               q["linearity_warn_pct"], q["linearity_fail_pct"]),
            "band_snr": light(s, q["snr_warn"], q["snr_fail"], higher_is_worse=False),
        }
        rows.append({
            "key": r["key"], "label": r["label"], "scan_number": r["scan_number"],
            "int_time_s": r["int_time_s"], "total_time_s": r["total_time_s"],
            **{"c_" + k: v for k, v in crit.items()},
            "median_cv_snr": s,
            "overall": max(crit.values(), key=lambda v: VERDICT_RANK[v]),
            "caveats": ", ".join(k.replace("_", " ") for k, v in crit.items()
                                 if v == "warn"),
        })
    out = pd.DataFrame(rows)

    # Longest exposure that is CLEAN, not merely longest that does not fail.
    #
    # The earlier rule ignored warns, and on white bipyramids that picked 10 s
    # (amber on linearity, dye SNR 63) over 8 s (all-pass, dye SNR 79) - worse
    # on both counts. A warn is evidence, so it is used: prefer an all-pass
    # exposure, take the longest of those, and only fall back to the longest
    # warn-level exposure when nothing is fully clean.
    rec = {}
    for key, g in out.groupby("key", sort=False):
        clean = g[g["overall"] == "pass"].sort_values("int_time_s")
        usable = g[g["overall"] != "fail"].sort_values("int_time_s")
        pick = clean if len(clean) else usable
        rec[key] = float(pick["int_time_s"].iloc[-1]) if len(pick) else np.nan
    out["recommended"] = [t == rec[k] for k, t in zip(out["key"], out["int_time_s"])]
    return out


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _exposure_colors(times):
    cm = plt.get_cmap("viridis")
    ts = sorted(set(float(t) for t in times))
    return {t: cm(i / max(len(ts) - 1, 1)) for i, t in enumerate(ts)}


def _band_marks(ax, cfg):
    for b in cfg["bands"]:
        ax.axvline(ALL_BANDS[b]["nominal"], color="0.55", lw=0.6, ls=":", zorder=0)


def _sweep_recs(scans, key):
    return sorted([r for r in scans if r["key"] == key], key=lambda r: -r["int_time_s"])


def _style(spec):
    return dict(color=COLOR_BY_PAPER[spec["paper"]],
                ls=DASH_BY_PARTICLE[spec["particle"]],
                marker=MARKER_BY_PARTICLE[spec["particle"]], ms=5, lw=1.3)


def fig01_raw_overlay(scans, cfg, path):
    fig, axes = plt.subplots(len(cfg["sweeps"]), 1, figsize=(14, 12), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, spec in zip(axes, cfg["sweeps"]):
        recs = _sweep_recs(scans, spec["key"])
        cols = _exposure_colors([r["int_time_s"] for r in recs])
        for r in recs:
            ax.plot(r["x_fit"], r["y_counts"], lw=0.8, color=cols[r["int_time_s"]],
                    label="%g s (#%d)" % (r["int_time_s"], r["scan_number"]))
        _band_marks(ax, cfg)
        ax.set_title(spec["label"] + "  -  raw detector counts", fontsize=10, loc="left")
        ax.set_ylabel("counts")
        ax.legend(fontsize=7, ncol=6, loc="upper right", frameon=False)
    axes[-1].set_xlabel("Raman shift (cm$^{-1}$)")
    axes[0].set_xlim(cfg["analysis_range"]["min"], cfg["analysis_range"]["max"])
    fig.suptitle("01  Raw counts rise with exposure, and nothing ever clips - which is "
                 "why the eye cannot pick the right exposure", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig02_cps_overlay(scans, cfg, path):
    fig, axes = plt.subplots(len(cfg["sweeps"]), 1, figsize=(14, 12), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, spec in zip(axes, cfg["sweeps"]):
        recs = _sweep_recs(scans, spec["key"])
        cols = _exposure_colors([r["int_time_s"] for r in recs])
        for r in recs:
            ax.plot(r["x_fit"], r["y_cps"], lw=0.8, color=cols[r["int_time_s"]],
                    label="%g s" % r["int_time_s"])
        _band_marks(ax, cfg)
        ax.set_title(spec["label"] + "  -  counts s$^{-1}$", fontsize=10, loc="left")
        ax.set_ylabel("counts s$^{-1}$")
        ax.legend(fontsize=7, ncol=6, loc="upper right", frameon=False)
    axes[-1].set_xlabel("Raman shift (cm$^{-1}$)")
    axes[0].set_xlim(cfg["analysis_range"]["min"], cfg["analysis_range"]["max"])
    fig.suptitle("02  Divide the exposure out and healthy scans collapse onto one curve. "
                 "The ones that do not are the failures", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig03_linearity(df, cfg, path):
    q = cfg["quality"]
    probes = [p["name"] for p in q["probe_windows"]]
    fig, axes = plt.subplots(2, len(probes), figsize=(15, 8), sharex=True)
    for j, n in enumerate(probes):
        top, bot = axes[0, j], axes[1, j]
        for spec in cfg["sweeps"]:
            g = df[df["key"] == spec["key"]].sort_values("int_time_s")
            st = _style(spec)
            top.plot(g["int_time_s"], g["I%s_counts" % n], label=spec["label"], **st)
            ref = g["I%s_cps" % n].head(2).mean()
            tt = np.linspace(0, g["int_time_s"].max() * 1.05, 50)
            top.plot(tt, ref * tt, color=st["color"], lw=0.6, alpha=0.35, zorder=0)
            bot.plot(g["int_time_s"], g["dev%s_pct" % n], **st)
        top.set_title("%s cm$^{-1}$" % n, fontsize=10)
        bot.axhline(0, color="0.4", lw=0.8)
        for lim, col in ((q["linearity_warn_pct"], VERDICT_COLOR["warn"]),
                         (q["linearity_fail_pct"], VERDICT_COLOR["fail"])):
            for s in (-1, 1):
                bot.axhline(s * lim, color=col, lw=0.7, ls="--")
        bot.set_xlabel("Integration time (s)")
        if j == 0:
            top.set_ylabel("counts")
            bot.set_ylabel("departure from short-exposure\nextrapolation (%)")
    axes[0, 0].legend(fontsize=7, frameon=False)
    fig.suptitle("03  Counts should be proportional to exposure (thin lines = ideal). "
                 "The gentle POSITIVE drift is real and physical; the collapse at 20 s "
                 "on white is not", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig04_noise_model(windows, model, cfg, path):
    q = cfg["quality"]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    ax = axes[0]
    sc = ax.scatter(windows["level_counts"], windows["sigma_counts"], s=8, alpha=0.35,
                    c=windows["int_time_s"], cmap="viridis")
    nn = np.linspace(1, windows["level_counts"].max(), 400)
    ax.plot(nn, model_sigma(nn, model), color="k", lw=2,
            label=r"fit  $\sigma=\sqrt{%.0f + %.4f\,N}$" % (model["read_var"], model["gain"]))
    ax.axvline(model["fit_max_counts"], color="0.3", lw=1.0, ls=":")
    ax.text(model["fit_max_counts"], ax.get_ylim()[1], " fitted below here",
            fontsize=7, rotation=90, va="top")
    ax.set_xlabel("local level, N (raw counts)")
    ax.set_ylabel(r"local $\sigma$ (counts)")
    ax.set_yscale("log")
    ax.set_title("The detector's noise law, fitted on %d windows that\ncannot be "
                 "saturated" % model["n_fit_windows"], fontsize=10, loc="left")
    ax.legend(fontsize=9, frameon=False, loc="upper left")
    plt.colorbar(sc, ax=ax, label="integration time (s)")

    ax = axes[1]
    ax.scatter(windows["level_counts"], windows["excess"], s=8, alpha=0.3, color="0.45")
    edges = np.arange(0, windows["level_counts"].max() + 5000, 5000)
    mid = 0.5 * (edges[:-1] + edges[1:])
    grp = windows.groupby(pd.cut(windows["level_counts"], edges), observed=False)["excess"]
    ax.plot(mid, grp.median().to_numpy(), color="k", lw=2, marker="o", ms=4,
            label="median")
    ax.plot(mid, grp.quantile(0.90).to_numpy(), color="k", lw=1.2, ls="--",
            label="90th percentile")
    ax.axhline(1.0, color=VERDICT_COLOR["pass"], lw=1.0)
    ax.axhline(q["noise_excess_warn"], color=VERDICT_COLOR["warn"], lw=1.0, ls="--")
    ax.axhline(q["noise_excess_fail"], color=VERDICT_COLOR["fail"], lw=1.0, ls="--")
    ax.set_yscale("log")
    ax.set_xlabel("local level, N (raw counts)")
    ax.set_ylabel(r"noise excess  $\sigma_{\rm meas}/\sigma_{\rm model}$")
    ax.set_title("Excess over that law. Flat at 1 while the detector is\nlinear, then it "
                 "runs away", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False)
    fig.suptitle("04  Fit the detector's own noise law, then measure how far each scan "
                 "sits above it", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig05_excess_per_scan(scans, df, model, cfg, path):
    q = cfg["quality"]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    ax = axes[0]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("int_time_s")
        ax.plot(g["int_time_s"], g["noise_excess_p90"], label=spec["label"], **_style(spec))
    ax.axhspan(0, q["noise_excess_warn"], color=VERDICT_COLOR["pass"], alpha=0.08)
    ax.axhspan(q["noise_excess_warn"], q["noise_excess_fail"],
               color=VERDICT_COLOR["warn"], alpha=0.12)
    ax.axhspan(q["noise_excess_fail"], 100, color=VERDICT_COLOR["fail"], alpha=0.12)
    ax.set_yscale("log")
    ax.set_xlabel("Integration time (s)")
    ax.set_ylabel("noise excess (90th pct of windows)")
    ax.set_title("Per scan vs exposure", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False, loc="upper left")

    ax = axes[1]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("median_counts")
        ax.plot(g["median_counts"], g["noise_excess_p90"], **_style(spec))
    ax.axvline(q["median_counts_warn"], color=VERDICT_COLOR["warn"], lw=1.0, ls="--")
    ax.axvline(q["median_counts_fail"], color=VERDICT_COLOR["fail"], lw=1.0, ls="--")
    ax.axhline(q["noise_excess_fail"], color=VERDICT_COLOR["fail"], lw=0.8, ls=":")
    ax.set_yscale("log")
    ax.set_xlabel("median level of the spectrum (raw counts)")
    ax.set_ylabel("noise excess")
    ax.set_title("Against the MEDIAN level - every failure above\n%s counts, every pass "
                 "below %s" % ("{:,}".format(q["median_counts_fail"]),
                               "{:,}".format(q["median_counts_warn"])),
                 fontsize=10, loc="left")

    ax = axes[2]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("max_counts")
        ax.plot(g["max_counts"], g["noise_excess_p90"], **_style(spec))
    ax.axhline(q["noise_excess_fail"], color=VERDICT_COLOR["fail"], lw=0.8, ls=":")
    ax.set_yscale("log")
    ax.set_xlabel("MAXIMUM of the spectrum (raw counts)")
    ax.set_ylabel("noise excess")
    ax.set_title("Against the maximum - passes and failures\ninterleave, so peak height "
                 "is the wrong gauge", fontsize=10, loc="left")
    fig.suptitle("05  What actually predicts failure is how much of the detector is "
                 "full, not how tall the tallest peak is", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig06_noise_colour(scans, df, cfg, path):
    en = cfg["excess_noise"]
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))

    ax = axes[0, 0]
    worst = df.sort_values("noise_excess_p90").iloc[-1]
    best = df[df["key"] == worst["key"]].sort_values("noise_excess_p90").iloc[0]
    for scan_no, col, tag in ((int(best["scan_number"]), VERDICT_COLOR["pass"], "healthy"),
                              (int(worst["scan_number"]), VERDICT_COLOR["fail"], "saturating")):
        rec = [s for s in scans if s["scan_number"] == scan_no][0]
        m = (rec["x_fit"] >= 430) & (rec["x_fit"] <= 620)
        # Trim the same 10 points the metric trims: a boxcar detrend run with
        # mode="same" fabricates a huge step at each end, and plotting it would
        # show an artifact the analysis never sees.
        ax.plot(rec["x_fit"][m][10:-10], _detrend(rec["y_counts"][m])[10:-10],
                lw=1.0, color=col,
                label="%g s (#%d), %s" % (rec["int_time_s"], scan_no, tag))
    ax.set_xlim(440, 610)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("residual (counts)")
    ax.set_title("%s - detrended residual" % worst["label"], fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False)

    ax = axes[0, 1]
    for spec in cfg["sweeps"]:
        for rec in _sweep_recs(scans, spec["key"]):
            period, amp = _periodogram(rec["x_fit"], rec["y_counts"], cfg)
            keep = (period >= 2) & (period <= 60)
            ax.plot(period[keep], amp[keep], lw=0.8, alpha=0.7,
                    color=COLOR_BY_PAPER[spec["paper"]],
                    ls=DASH_BY_PARTICLE[spec["particle"]])
    ax.axvspan(*en["signature_band_cm1"], color="0.85", zorder=0)
    ax.axvspan(*en["white_band_cm1"], color="#cfe3f2", zorder=0)
    ax.set_xlim(2, 60)
    ax.set_yscale("log")
    ax.set_xlabel("ripple period (cm$^{-1}$)")
    ax.set_ylabel("amplitude (counts)")
    ax.set_title("Grey = signature band, blue = white-noise reference.\nWhite noise "
                 "would be a flat line across both", fontsize=10, loc="left")

    ax = axes[1, 0]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("int_time_s")
        ax.plot(g["int_time_s"], g["colour_ratio"], label=spec["label"], **_style(spec))
    ax.axhline(1.0, color="0.4", lw=1.0, ls=":")
    ax.text(ax.get_xlim()[0], 1.0, " white noise would sit here", fontsize=7, va="bottom")
    ax.set_yscale("log")
    ax.set_ylim(0.5, 2000)
    ax.set_xlabel("Integration time (s)")
    ax.set_ylabel("signature / white-band amplitude")
    ax.set_title("The COLOUR of the noise does not change with exposure\n"
                 "- saturation multiplies the noise, it does not replace it",
                 fontsize=10, loc="left")
    ax.legend(fontsize=7, frameon=False)

    ax = axes[1, 1]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("median_counts")
        ax.plot(g["median_counts"], g["signature_amp"], label=spec["label"], **_style(spec))
    ax.axvline(cfg["quality"]["median_counts_fail"], color=VERDICT_COLOR["fail"],
               lw=1.0, ls="--")
    ax.set_yscale("log")
    ax.set_xlabel("median level of the spectrum (raw counts)")
    ax.set_ylabel("signature-band amplitude (counts)")
    ax.set_title("It explodes with detector fill - two orders of\nmagnitude across "
                 "the sweep", fontsize=10, loc="left")
    fig.suptitle("06  The noise always sits at %g-%g cm$^{-1}$ - the width of a narrow "
                 "Raman band. Saturation does not change that, it just multiplies the "
                 "amplitude ~100x" % tuple(en["signature_band_cm1"]), fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig07_band_snr(bands, cfg, path):
    q = cfg["quality"]
    sel = list(cfg["bands"])
    fig, axes = plt.subplots(2, len(sel), figsize=(3.1 * len(sel), 8), sharex=True)
    for j, b in enumerate(sel):
        top, bot = axes[0, j], axes[1, j]
        for spec in cfg["sweeps"]:
            g = (bands[(bands["key"] == spec["key"]) & (bands["peak_name"] == b)]
                 .sort_values("int_time_s"))
            st = _style(spec)
            st["ms"] = 4
            top.plot(g["int_time_s"], g["snr"], label=spec["label"], **st)
            bot.plot(g["int_time_s"], g["height_cps"], **st)
        t0 = bands["int_time_s"].min()
        ref = bands[(bands["peak_name"] == b) & (bands["int_time_s"] == t0)]["snr"].median()
        tt = np.linspace(t0, bands["int_time_s"].max(), 40)
        top.plot(tt, ref * np.sqrt(tt / t0), color="0.35", lw=0.9, ls=":",
                 label=r"$\sqrt{t}$ ideal" if j == 0 else None)
        top.axhline(q["snr_fail"], color=VERDICT_COLOR["fail"], lw=0.8, ls="--")
        top.set_title("%s\n%d cm$^{-1}$" % (b, ALL_BANDS[b]["nominal"]), fontsize=9)
        top.set_yscale("log")
        bot.set_xlabel("Integration time (s)")
        if j == 0:
            top.set_ylabel("band SNR")
            bot.set_ylabel("band height (counts s$^{-1}$)")
            top.legend(fontsize=6, frameon=False)
    fig.suptitle("07  The 'too short' side barely bites: even at 2 s the strong CV bands "
                 "sit far above the SNR = %g floor. Bottom - height in counts s$^{-1}$, "
                 "which should not depend on exposure at all" % q["snr_fail"], fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig08_fidelity_and_cost(df, bands, cfg, path):
    std = cfg["reference_band"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    ax = axes[0]
    for spec in cfg["sweeps"]:
        g = df[df["key"] == spec["key"]].sort_values("int_time_s")
        ax.plot(g["int_time_s"], 1 - g["fidelity_vs_shortest"],
                label=spec["label"], **_style(spec))
    ax.set_yscale("log")
    ax.set_xlabel("Integration time (s)")
    ax.set_ylabel(r"$1-r$ vs the shortest scan")
    ax.set_title("Spectral fidelity, 800-1800 cm$^{-1}$\n(lower = same spectrum)",
                 fontsize=10, loc="left")
    ax.legend(fontsize=7, frameon=False)

    m = bands[bands["peak_name"] == std].merge(
        df[["key", "scan_number", "total_time_s"]], on=["key", "scan_number"])
    ax = axes[1]
    for spec in cfg["sweeps"]:
        g = m[m["key"] == spec["key"]].sort_values("int_time_s")
        ax.plot(g["total_time_s"], g["snr"], **_style(spec))
        for _, r in g.iterrows():
            ax.annotate("%gs" % r["int_time_s"], (r["total_time_s"], r["snr"]),
                        fontsize=6, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("wall-clock s per spot (exposure x averages)")
    ax.set_ylabel("SNR of the %d cm$^{-1}$ dye band" % ALL_BANDS[std]["nominal"])
    ax.set_title("What the extra time buys you", fontsize=10, loc="left")

    ax = axes[2]
    g = m.copy()
    g["snr_per_root_sec"] = g["snr"] / np.sqrt(g["total_time_s"])
    for spec in cfg["sweeps"]:
        h = g[g["key"] == spec["key"]].sort_values("int_time_s")
        ax.plot(h["int_time_s"], h["snr_per_root_sec"], **_style(spec))
    ax.set_xlabel("Integration time (s)")
    ax.set_ylabel(r"SNR / $\sqrt{\mathrm{seconds}}$")
    ax.set_title("Efficiency - flat means the time is\nbeing spent well",
                 fontsize=10, loc="left")
    fig.suptitle("08  Is a longer exposure worth its wall-clock cost?", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig09_verdict(verdict, cfg, path):
    crit = [c for c in verdict.columns if c.startswith("c_")]
    names = {"c_noise_excess": "noise excess", "c_count_headroom": "count headroom",
             "c_linearity": "cps linearity", "c_band_snr": "band SNR"}
    keys = [s["key"] for s in cfg["sweeps"]]
    labels = {s["key"]: s["label"] for s in cfg["sweeps"]}
    # NOT sharey: the sweeps do not all have the same number of exposures, and a
    # shared axis silently clips the extra row off the longer ones and relabels
    # it with the shorter sweep's ticks.
    fig, axes = plt.subplots(1, len(keys), figsize=(4.0 * len(keys), 5.0))
    for ax, key in zip(np.atleast_1d(axes), keys):
        g = verdict[verdict["key"] == key].sort_values("int_time_s", ascending=False)
        ts = g["int_time_s"].to_numpy()
        for i, c in enumerate(crit):
            for j, (_, r) in enumerate(g.iterrows()):
                ax.add_patch(plt.Rectangle((i - 0.46, j - 0.46), 0.92, 0.92,
                                           color=VERDICT_COLOR[r[c]], alpha=0.85))
        for j, (_, r) in enumerate(g.iterrows()):
            if r["recommended"]:
                ax.add_patch(plt.Rectangle((-0.5, j - 0.5), len(crit), 1.0,
                                           fill=False, ec="k", lw=2.4, zorder=5))
                ax.text(len(crit) - 0.4, j, "  USE THIS", fontsize=9, weight="bold",
                        va="center")
        ax.set_xticks(range(len(crit)))
        ax.set_xticklabels([names[c] for c in crit], rotation=35, ha="right", fontsize=8)
        ax.set_yticks(range(len(ts)))
        ax.set_yticklabels(["%g s" % t for t in ts], fontsize=9)
        ax.set_xlim(-0.5, len(crit) + 1.4)
        ax.set_ylim(len(ts) - 0.5, -0.5)
        ax.set_title(labels[key], fontsize=10)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0)
    fig.legend(handles=[Patch(color=v, label=k) for k, v in VERDICT_COLOR.items()],
               loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle("09  Verdict - a red cell is a veto, so the pick is the LONGEST exposure "
                 "with no red", fontsize=12)
    fig.tight_layout(rect=(0, 0.06, 1, 0.94))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def calibration_table(bands, df, verdict, model, cfg) -> pd.DataFrame:
    """SNR vs exposure per sweep and band, against the detector's own prediction.

    The textbook reference for "SNR should grow as sqrt(t)" only holds once shot
    noise dominates. At the short end of this sweep it does not: at 2 s the
    median level is ~2 500 counts, where the fitted read term (%s counts) is
    still a large part of sigma. So the honest reference curve is the detector
    model itself,

        SNR(t) proportional to  t / sqrt(read^2 + gain * N(t))

    which grows like t while read-limited and like sqrt(t) once shot-limited.
    The bend between those two regimes IS the plateau - past it, doubling the
    exposure buys only 40%% more SNR, and eventually nothing at all because
    saturation takes it back.

    The scale factor is fitted per sweep and band on the exposures that PASSED,
    so saturated points cannot drag the curve down to meet themselves.
    """
    q = cfg["quality"]
    ok_scans = set(verdict[verdict["overall"] != "fail"]["scan_number"])
    counts = df.set_index("scan_number")["median_counts"].to_dict()
    rows = []
    for band in cfg["calibration_bands"]:
        for spec in cfg["sweeps"]:
            g = (bands[(bands["key"] == spec["key"]) & (bands["peak_name"] == band)]
                 .sort_values("int_time_s"))
            if not len(g):
                continue
            t = g["int_time_s"].to_numpy(float)
            snr = g["snr"].to_numpy(float)
            n = np.array([counts[s] for s in g["scan_number"]], float)
            shape = t / np.sqrt(model["read_var"] + model["gain"] * n)
            healthy = np.array([s in ok_scans for s in g["scan_number"]])
            fit = healthy & np.isfinite(snr)
            scale = (float(np.sum(snr[fit] * shape[fit]) / np.sum(shape[fit] ** 2))
                     if fit.any() else np.nan)
            # Fraction of the total SNR gain available across this sweep that is
            # already in hand at each exposure. This is the number the choice of
            # exposure actually turns on: "6 s gets you 90% of what 10 s gets".
            best = np.nanmax(np.where(healthy, snr, np.nan)) if healthy.any() else np.nan
            first = snr[0]
            span = best - first
            for i in range(len(t)):
                rows.append({
                    "peak_name": band, "key": spec["key"], "label": spec["label"],
                    "paper": spec["paper"], "particle": spec["particle"],
                    "scan_number": int(g["scan_number"].iloc[i]),
                    "int_time_s": t[i], "median_counts": n[i],
                    "snr": snr[i], "sigma_cps": float(g["sigma_cps"].iloc[i]),
                    "height_cps": float(g["height_cps"].iloc[i]),
                    "model_snr": scale * shape[i],
                    "snr_vs_2s": snr[i] / first if first else np.nan,
                    "frac_of_best": (snr[i] - first) / span if span > 0 else np.nan,
                    "healthy": bool(healthy[i]),
                })
    return pd.DataFrame(rows)


def fig11_calibration(cal, cfg, model, path):
    """The calibration curve: SNR vs exposure, with the plateau marked."""
    knee = cfg["calibration_knee_fraction"]
    bandlist = cfg["calibration_bands"]
    fig, axes = plt.subplots(len(bandlist), 3, figsize=(16, 4.6 * len(bandlist)),
                             squeeze=False)
    for i, band in enumerate(bandlist):
        sub = cal[cal["peak_name"] == band]

        ax = axes[i, 0]
        for spec in cfg["sweeps"]:
            g = sub[sub["key"] == spec["key"]].sort_values("int_time_s")
            if not len(g):
                continue
            st = _style(spec)
            ok = g["healthy"]
            ax.plot(g["int_time_s"], g["snr"], color=st["color"], ls=st["ls"],
                    lw=1.2, alpha=0.55)
            ax.scatter(g["int_time_s"][ok], g["snr"][ok], color=st["color"],
                       marker=st["marker"], s=34, label=spec["label"], zorder=3)
            ax.scatter(g["int_time_s"][~ok], g["snr"][~ok], facecolor="none",
                       edgecolor=st["color"], marker=st["marker"], s=52,
                       linewidth=1.5, zorder=3)
            ax.plot(g["int_time_s"], g["model_snr"], color=st["color"], lw=0.9,
                    ls=":", alpha=0.9)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Integration time (s)")
        ax.set_ylabel("band SNR")
        ax.set_title("%s (%d cm$^{-1}$) - measured vs the detector model\n"
                     "(dotted). Hollow = failed the screen"
                     % (band, ALL_BANDS[band]["nominal"]), fontsize=10, loc="left")
        ax.legend(fontsize=7, frameon=False)

        ax = axes[i, 1]
        tt = np.logspace(np.log10(2), np.log10(20), 60)
        for spec in cfg["sweeps"]:
            g = sub[sub["key"] == spec["key"]].sort_values("int_time_s")
            if not len(g):
                continue
            st = _style(spec)
            ok = g["healthy"]
            ax.plot(g["int_time_s"], g["snr_vs_2s"], color=st["color"], ls=st["ls"],
                    lw=1.2, alpha=0.55)
            ax.scatter(g["int_time_s"][ok], g["snr_vs_2s"][ok], color=st["color"],
                       marker=st["marker"], s=34, zorder=3)
            ax.scatter(g["int_time_s"][~ok], g["snr_vs_2s"][~ok], facecolor="none",
                       edgecolor=st["color"], marker=st["marker"], s=52,
                       linewidth=1.5, zorder=3)
        ax.plot(tt, tt / 2.0, color="0.25", lw=1.0, ls="--",
                label=r"$\propto t$  (read-noise limited)")
        ax.plot(tt, np.sqrt(tt / 2.0), color="0.25", lw=1.0, ls=":",
                label=r"$\propto\sqrt{t}$  (shot-noise limited)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Integration time (s)")
        ax.set_ylabel("SNR relative to the 2 s scan")
        ax.set_title("Normalised, so the four sweeps are comparable.\nThe curves bend "
                     "off $t$ onto $\\sqrt{t}$ - that bend is the plateau",
                     fontsize=10, loc="left")
        ax.legend(fontsize=8, frameon=False)

        ax = axes[i, 2]
        for spec in cfg["sweeps"]:
            g = sub[sub["key"] == spec["key"]].sort_values("int_time_s")
            if not len(g):
                continue
            st = _style(spec)
            ok = g["healthy"]
            ax.plot(g["int_time_s"], 100 * g["frac_of_best"], color=st["color"],
                    ls=st["ls"], lw=1.2, alpha=0.55)
            ax.scatter(g["int_time_s"][ok], 100 * g["frac_of_best"][ok],
                       color=st["color"], marker=st["marker"], s=34, zorder=3)
            ax.scatter(g["int_time_s"][~ok], 100 * g["frac_of_best"][~ok],
                       facecolor="none", edgecolor=st["color"], marker=st["marker"],
                       s=52, linewidth=1.5, zorder=3)
        ax.axhline(100 * knee, color=VERDICT_COLOR["pass"], lw=1.2, ls="--")
        ax.text(2.05, 100 * knee + 2, "%d%% of the achievable gain" % (100 * knee),
                fontsize=8, color=VERDICT_COLOR["pass"])
        ax.set_xscale("log")
        ax.set_ylim(-15, 115)
        ax.set_xlabel("Integration time (s)")
        ax.set_ylabel("% of the 2 s $\\rightarrow$ best SNR gain captured")
        ax.set_title("The decision view: where you stop gaining.\nPoints that drop back "
                     "are losing to saturation", fontsize=10, loc="left")
    fig.suptitle("11  Calibration curve - SNR against integration time. SNR grows like "
                 "$t$ while read noise dominates, flattens to $\\sqrt{t}$ once shot "
                 "noise does, then turns over when the detector fills", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


# ---------------------------------------------------------------------------
# The plain set. One figure, one axes, one question: how does the dye signal
# vary with integration time on this paper. Figures 01-11 are for interrogating
# the data; these are for reading the answer off, so they carry axes, units, a
# title and nothing else.
# ---------------------------------------------------------------------------
PARTICLE_COLOR = {"bipyramids": "#2f6ea8", "stars": "#a8322d"}


def _snr_series(bands, verdict, cfg, key):
    """(times, snr, usable-mask) for one sweep, ordered by exposure."""
    band = cfg["reference_band"]
    v = (verdict[verdict["key"] == key].sort_values("int_time_s")
         .set_index("int_time_s"))
    b = (bands[(bands["key"] == key) & (bands["peak_name"] == band)]
         .sort_values("int_time_s").set_index("int_time_s"))
    t = np.array(list(v.index), dtype=float)
    return t, b.loc[t, "snr"].to_numpy(float), (v.loc[t, "overall"] != "fail").to_numpy()


def _plain_axes(ax, cfg):
    ax.set_xlabel("Integration time (s)")
    ax.set_ylabel("SNR at %d cm$^{-1}$" % ALL_BANDS[cfg["reference_band"]]["nominal"])
    ax.set_ylim(bottom=0)
    ax.spines[["top", "right"]].set_visible(False)


def fig_plain_one_sweep(bands, verdict, cfg, spec, path):
    t, snr, ok = _snr_series(bands, verdict, cfg, spec["key"])
    col = PARTICLE_COLOR[spec["particle"]]
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ax.plot(t, snr, color=col, lw=1.6, zorder=1)
    ax.scatter(t[ok], snr[ok], color=col, s=70, zorder=2, label="usable")
    if (~ok).any():
        # Open markers, not a second colour: saturated points are the same
        # measurement, just not trustworthy, and a new colour would read as a
        # new series.
        ax.scatter(t[~ok], snr[~ok], facecolor="white", edgecolor=col, s=70,
                   linewidth=1.6, zorder=2, label="saturated")
        ax.legend(frameon=False)
    ax.set_xticks(t)
    ax.set_xticklabels(["%g" % v for v in t])
    _plain_axes(ax, cfg)
    ax.set_title("%s · %s" % (spec["paper"].capitalize(), spec["particle"]))
    fig.tight_layout()
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_plain_spectra(scans, verdict, cfg, spec, path, offset=False):
    """Every exposure of one sweep, overlaid in counts/s.

    Counts per second, not raw counts: dividing the exposure out is what makes
    the traces comparable at all - in raw counts a 10 s scan is simply five
    times a 2 s scan and the plot says nothing. On this axis healthy exposures
    should land on top of one another, so any trace that does not is the
    finding.
    """
    v = (verdict[verdict["key"] == spec["key"]].sort_values("int_time_s")
         .set_index("int_time_s"))
    recs = sorted([r for r in scans if r["key"] == spec["key"]],
                  key=lambda r: r["int_time_s"])
    cm = plt.get_cmap("viridis")
    n = max(len(recs) - 1, 1)
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    step = (float(np.percentile([np.ptp(r["smooth_cps"]) for r in recs], 70)) * 0.6
            if offset else 0.0)
    for i, r in enumerate(recs):
        t = r["int_time_s"]
        bad = v.loc[t, "overall"] == "fail"
        ax.plot(r["x_fit"], r["smooth_cps"] + i * step, lw=1.0, color=cm(i / n),
                label="%g s" % t + (" (saturated)" if bad else ""))
    ax.set_xlim(cfg["analysis_range"]["min"], cfg["analysis_range"]["max"])
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    if offset:
        ax.set_yticks([])
        ax.set_ylabel("Intensity (counts s$^{-1}$, offset)")
    else:
        ax.set_ylabel("Intensity (counts s$^{-1}$)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=9)
    ax.set_title("%s · %s" % (spec["paper"].capitalize(), spec["particle"]))
    fig.tight_layout()
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig_plain_paper(bands, verdict, cfg, paper, path):
    specs = [s for s in cfg["sweeps"] if s["paper"] == paper]
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ticks = set()
    for spec in specs:
        t, snr, ok = _snr_series(bands, verdict, cfg, spec["key"])
        ticks.update(t.tolist())
        col = PARTICLE_COLOR[spec["particle"]]
        ax.plot(t, snr, color=col, lw=1.6, zorder=1)
        ax.scatter(t[ok], snr[ok], color=col, s=70, zorder=2, label=spec["particle"])
        if (~ok).any():
            ax.scatter(t[~ok], snr[~ok], facecolor="white", edgecolor=col, s=70,
                       linewidth=1.6, zorder=2)
    t = sorted(ticks)
    ax.set_xticks(t)
    ax.set_xticklabels(["%g" % v for v in t])
    _plain_axes(ax, cfg)
    ax.legend(frameon=False)
    ax.set_title(paper.capitalize())
    fig.tight_layout()
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def fig10_recommended_spectra(scans, verdict, cfg, path):
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
    ax = axes[0]
    for spec in cfg["sweeps"]:
        row = verdict[(verdict["key"] == spec["key"]) & verdict["recommended"]]
        if not len(row):
            continue
        rec = [r for r in scans
               if r["scan_number"] == int(row["scan_number"].iloc[0])][0]
        ax.plot(rec["x_fit"], rec["smooth_cps"], lw=1.1,
                color=COLOR_BY_PAPER[spec["paper"]],
                ls=DASH_BY_PARTICLE[spec["particle"]],
                label="%s  -  %g s (#%d)" % (spec["label"], rec["int_time_s"],
                                             rec["scan_number"]))
    top = ax.get_ylim()[1]
    for b in cfg["bands"]:
        c = ALL_BANDS[b]["nominal"]
        ax.axvline(c, color="0.6", lw=0.6, ls=":")
        ax.text(c, top, " %d" % c, rotation=90, fontsize=7, va="top")
    ax.set_ylabel("baseline-corrected counts s$^{-1}$")
    ax.legend(fontsize=8, frameon=False)
    ax.set_title("Keep: each sweep at its recommended exposure", fontsize=10, loc="left")

    ax = axes[1]
    worst = verdict[verdict["overall"] == "fail"].sort_values("int_time_s")
    for _, r in worst.iterrows():
        rec = [s for s in scans if s["scan_number"] == int(r["scan_number"])][0]
        ax.plot(rec["x_fit"], rec["smooth_cps"], lw=0.9,
                color=COLOR_BY_PAPER[rec["paper"]], ls=DASH_BY_PARTICLE[rec["particle"]],
                label="%s  -  %g s (#%d)" % (rec["label"], rec["int_time_s"],
                                             rec["scan_number"]))
    for b in cfg["bands"]:
        ax.axvline(ALL_BANDS[b]["nominal"], color="0.6", lw=0.6, ls=":")
    ax.set_xlim(cfg["analysis_range"]["min"], cfg["analysis_range"]["max"])
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("baseline-corrected counts s$^{-1}$")
    ax.legend(fontsize=8, frameon=False)
    ax.set_title("Discard: the scans that failed. Note they still look like spectra - "
                 "the structure is the excess noise, not the sample",
                 fontsize=10, loc="left")
    fig.suptitle("10  Keep vs discard, on the same axes", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


# ---------------------------------------------------------------------------
def _fmt(v):
    return "{:,.0f}".format(v)


def _ok(df, q):
    return df[df["noise_excess_p90"] < q["noise_excess_fail"]]


def _bad(df, q):
    return df[df["noise_excess_p90"] >= q["noise_excess_fail"]]


def write_findings(df, verdict, allscreen, model, cfg, path):
    q = cfg["quality"]
    lines = [
        "# Experiment 3 - integration-time sweep (083126)", "",
        "One spot per sweep, exposure swept while everything else was held fixed. "
        "AutoInt was Off on every scan and Averages was %d throughout, so exposure "
        "really is the only variable." % int(df["averages"].mode().iloc[0]), "",
        "## Answer", "",
        "| sweep | use | wall clock per spot | why not longer | caveats |",
        "|---|---|---|---|---|",
    ]
    for spec in cfg["sweeps"]:
        g = verdict[verdict["key"] == spec["key"]].sort_values("int_time_s",
                                                              ascending=False)
        r = g[g["recommended"]]
        if not len(r):
            lines.append("| %s | nothing usable | - | - | - |" % spec["label"])
            continue
        r = r.iloc[0]
        longer = g[g["int_time_s"] > r["int_time_s"]].sort_values("int_time_s")
        why = "nothing longer was tested"
        if len(longer):
            nxt = longer.iloc[0]
            broke = [k[2:].replace("_", " ") for k in g.columns
                     if k.startswith("c_") and nxt[k] == "fail"]
            soft = [k[2:].replace("_", " ") for k in g.columns
                    if k.startswith("c_") and nxt[k] == "warn"]
            why = ("%g s fails %s" % (nxt["int_time_s"], ", ".join(broke)) if broke
                   else "%g s is only borderline (%s)"
                   % (nxt["int_time_s"], ", ".join(soft)))
        lines.append("| %s | **%g s** | %g s | %s | %s |"
                     % (spec["label"], r["int_time_s"], r["total_time_s"], why,
                        r["caveats"] or "none"))

    lines += [
        "", "## How the call was made", "",
        "Four vetoes, any one of which disqualifies an exposure.", "",
        "1. **Noise excess** - measured sigma over the sigma the detector's own noise "
        "law predicts at that count level. Fitted here as "
        "`sigma^2 = %.0f + %.4f N` on %d sliding windows below %s counts, i.e. on data "
        "that cannot itself be saturated. Read noise comes out at %.1f counts."
        % (model["read_var"], model["gain"], model["n_fit_windows"],
           "{:,}".format(model["fit_max_counts"]), model["read_counts"]),
        "2. **Count headroom** - the MEDIAN level of the spectrum, not its maximum.",
        "3. **Linearity** - departure of counts/second from the short-exposure "
        "extrapolation.",
        "4. **Band SNR** - median SNR across the five CV bands, the 'too short' guard.",
        "",
        "The reference band throughout is the dye band at %d cm-1 (observed 1616-1617). "
        "The 1095 cm-1 paper filler band is carried beside it as a control, not as the "
        "reference: it cannot move with the dye, so where the two agree the dye curve "
        "is reading exposure, and where they diverge something sample-side is moving."
        % ALL_BANDS[cfg["reference_band"]]["nominal"],
        "",
        "## Setting the exposure in future runs", "",
        "Exposure is the wrong thing to standardise on, because the right exposure "
        "depends on how bright the paper is - white fluoresces far harder than offwhite "
        "and fills the detector sooner. The count level travels better, and the level "
        "that travels best is the MEDIAN of the spectrum, not the tallest peak.", "",
        "Across the 6 exposures of the 4 sweeps:", "",
        "| | median level | max level |", "|---|---|---|",
        "| passed | up to %s | up to %s |"
        % (_fmt(_ok(df, q)["median_counts"].max()), _fmt(_ok(df, q)["max_counts"].max())),
        "| failed | from %s | from %s |"
        % (_fmt(_bad(df, q)["median_counts"].min()), _fmt(_bad(df, q)["max_counts"].min())),
        "",
        "Within the sweeps the median separates the two groups cleanly and the maximum "
        "does not - the maxima overlap, so a scan can hold the tallest peak in the dump "
        "and still be fine.", "",
        "**But the median is a screening proxy, not the test.** Across all %d scans in "
        "the dump the two distributions overlap as well: a scan passed at a median of "
        "%s counts while another failed at %s. Counts tell you when to be suspicious; "
        "the noise excess is what actually decides."
        % (len(allscreen), _fmt(allscreen[allscreen["verdict"] == "pass"]["median_counts"].max()),
           _fmt(allscreen[allscreen["verdict"] == "fail"]["median_counts"].min())), "",
        "**Working rule: keep the median below ~%s counts** - take one 2 s scan, read "
        "its median, and scale the exposure to land there - then screen the result on "
        "the noise excess rather than trusting the count alone."
        % "{:,}".format(q["median_counts_warn"]), "",
        "## What saturation looks like on this instrument", "",
        "Not a clipped flat top - these exports never clip. What the failing scans gain "
        "is amplitude, not a new kind of noise. The noise on this instrument always sits "
        "at ripple periods of %g-%g cm-1 and is essentially absent below %g cm-1, "
        "because a ~10 cm-1 optical resolution is exported onto a 1 cm-1 grid. That "
        "shape is the same on healthy and saturated scans alike - the signature-to-white "
        "ratio holds at ~%.0f across every scan in every sweep. Only the amplitude "
        "moves, and it moves by two orders of magnitude."
        % (cfg["excess_noise"]["signature_band_cm1"][0],
           cfg["excess_noise"]["signature_band_cm1"][1],
           cfg["excess_noise"]["white_band_cm1"][1],
           df["colour_ratio"].median()), "",
        "That period range is the width of a narrow Raman band. So the noise survives "
        "Savitzky-Golay smoothing, gets picked up by peak finding, and gets fitted. "
        "**A saturated scan still looks like a spectrum and still yields peaks** - it "
        "will pass a visual check. The noise excess is the only reliable tell, which is "
        "why it drives the verdict.", "",
        "## Two things worth knowing separately", "",
        "* **counts/second is not transferable between exposures.** Even among healthy "
        "scans, counts/s drifts upward with exposure by up to %.0f%% across these "
        "sweeps. It is smooth and monotonic, so it is not the detector failing - but it "
        "does mean intensities taken at different integration times cannot be compared "
        "at better than the ~10-15%% level without a standard."
        % df[df["noise_excess_p90"] < q["noise_excess_fail"]]["dev_worst_pct"].max(),
        "* **'too short' never actually bit.** Down to 2 s the strong CV bands stay far "
        "above the SNR floor. The reason not to run at 2 s is precision on the WEAK "
        "bands, not detection of the strong ones.", "",
        "## Files", "",
        "* `scan_quality.csv` - one row per scan, every metric behind the verdict",
        "* `band_metrics.csv` - band height and SNR per scan",
        "* `verdict.csv` - the traffic lights, caveats and the recommendation",
        "* `noise_windows.csv` - every sliding window, with model sigma and excess",
        "* `all_scans_screen.csv` - the same saturation verdict for all %d scans in the "
        "dump, not just the sweeps" % len(allscreen),
        "* `calibration_curve.csv` - SNR vs exposure per sweep and band, with the "
        "model prediction and the fraction of the achievable gain captured",
        "* `noise_model.json` - the fitted noise law", "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    out = ROOT / cfg["io"]["output_dir"]
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    sweep_scans, all_scans = load_scans(cfg)
    sweep_scans = [process(r, cfg) for r in sweep_scans]
    print("[exp3] %d sweep scans across %d sweeps; %d scans total in the dump"
          % (len(sweep_scans), len(cfg["sweeps"]), len(all_scans)))

    pool = all_scans if cfg["pool_all_scans"] else sweep_scans
    windows = window_table(pool, cfg)
    model = fit_noise_model(windows, cfg)
    windows = add_excess(windows, model)
    print("[exp3] noise law: sigma^2 = %.1f + %.5f N   (read %.1f counts, "
          "%d/%d windows kept)" % (model["read_var"], model["gain"],
                                   model["read_counts"], model["n_fit_windows"],
                                   model["n_candidate_windows"]))

    df = add_fidelity(add_linearity(scan_table(sweep_scans, model, cfg), cfg),
                      sweep_scans)
    bands = pd.DataFrame([m for r in sweep_scans for m in band_metrics(r, cfg)])
    verdict = verdict_table(df, bands, cfg)
    allscreen = screen_all(pool, model, cfg)

    allscreen.to_csv(out / "all_scans_screen.csv", index=False)
    df.to_csv(out / "scan_quality.csv", index=False)
    bands.to_csv(out / "band_metrics.csv", index=False)
    verdict.to_csv(out / "verdict.csv", index=False)
    windows.to_csv(out / "noise_windows.csv", index=False)
    (out / "noise_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")

    fig01_raw_overlay(sweep_scans, cfg, fig_dir / "01_raw_counts_overlay.png")
    fig02_cps_overlay(sweep_scans, cfg, fig_dir / "02_counts_per_second_overlay.png")
    fig03_linearity(df, cfg, fig_dir / "03_linearity_vs_exposure.png")
    fig04_noise_model(windows, model, cfg, fig_dir / "04_noise_model_and_excess.png")
    fig05_excess_per_scan(sweep_scans, df, model, cfg,
                          fig_dir / "05_what_predicts_failure.png")
    fig06_noise_colour(sweep_scans, df, cfg,
                       fig_dir / "06_noise_colour_and_amplitude.png")
    fig07_band_snr(bands, cfg, fig_dir / "07_band_snr_vs_exposure.png")
    fig08_fidelity_and_cost(df, bands, cfg, fig_dir / "08_fidelity_and_time_cost.png")
    fig09_verdict(verdict, cfg, fig_dir / "09_verdict_grid.png")
    fig10_recommended_spectra(sweep_scans, verdict, cfg,
                              fig_dir / "10_keep_vs_discard.png")
    plain_dir = out / "figures_plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    for spec in cfg["sweeps"]:
        stem = "%s_%s" % (spec["paper"], spec["particle"])
        fig_plain_one_sweep(bands, verdict, cfg, spec,
                            plain_dir / (stem + "_snr.png"))
        fig_plain_spectra(sweep_scans, verdict, cfg, spec,
                          plain_dir / (stem + "_spectra.png"))
        fig_plain_spectra(sweep_scans, verdict, cfg, spec,
                          plain_dir / (stem + "_spectra_stacked.png"), offset=True)
    for paper in dict.fromkeys(w["paper"] for w in cfg["sweeps"]):
        fig_plain_paper(bands, verdict, cfg, paper, plain_dir / ("%s.png" % paper))

    cal = calibration_table(bands, df, verdict, model, cfg)
    cal.to_csv(out / "calibration_curve.csv", index=False)
    fig11_calibration(cal, cfg, model, fig_dir / "11_calibration_curve.png")

    write_findings(df, verdict, allscreen, model, cfg, out / "FINDINGS.md")
    (out / "run_context.json").write_text(json.dumps({
        "config": CONFIG_PATH.name,
        "raw_root": cfg["io"]["raw_root"],
        "sweeps": {s["key"]: s["folder"] for s in cfg["sweeps"]},
        "n_sweep_scans": len(sweep_scans), "n_pooled_scans": len(pool),
        "noise_model": model,
        "thresholds": cfg["quality"],
        "band_source": "exp1_cv_report.BANDS + paper_1095",
    }, indent=2, default=str), encoding="utf-8")

    pd.set_option("display.width", 240)
    print("\n=== SCAN QUALITY ===")
    print(df[["key", "scan_number", "int_time_s", "median_counts", "max_counts",
              "noise_excess_p90", "colour_ratio", "dev_worst_pct",
              "fidelity_vs_shortest"]].round(2).to_string(index=False))
    print("\n=== VERDICT ===")
    print(verdict[["key", "int_time_s", "c_noise_excess", "c_count_headroom",
                   "c_linearity", "c_band_snr", "median_cv_snr", "overall",
                   "recommended"]].round(1).to_string(index=False))
    print("\n=== RECOMMENDED ===")
    print(verdict[verdict["recommended"]][
        ["label", "int_time_s", "total_time_s", "caveats"]].to_string(index=False))
    print("\nWrote " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
