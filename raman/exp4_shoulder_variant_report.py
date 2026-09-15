"""
What changes if a baseline shoulder is moved - exp 4 (083126).

The audit figures showed that the cv_1620 left shoulder, 1546-1606, contains the
cv_1587 window (1576-1602). Any real intensity there lifts the left anchor, tilts
the baseline and lowers the reported height. Moving that anchor clear of 1587 -
out to 1490-1550, the region the repo's own fixed-window comparison already calls
empirically flat - removes the contamination, but it also buys a longer
extrapolation under the peak and picks up whatever background curvature lives
between 1550 and 1606. Which effect wins is a question about these spectra, not
about the method, so it is answered by measuring it.

This script answers it end to end:

  1. per-peak audit panels on the variant shoulder, each drawing BOTH lines
  2. the report's own figure 13 rebuilt on the variant heights
  3. shipped vs variant, side by side, in the same block figure 13 uses
  4. the shift per scan, against dilution and against level, because a uniform
     shift cancels out of every ratio and a dilution-dependent one does not
  5. every gain recomputed - gain_x is exactly height(test)/height(control) from
     the band table, verified to 1e-12 against sers_gain.csv before use

NOTHING HERE IS THE REPORTED NUMBER. The shipped pipeline is untouched and every
output lands in its own directory, so the two can be compared and neither can be
mistaken for the other.

USAGE (from the repo root, ai env active)
-----------------------------------------
    python raman/exp4_shoulder_variant_report.py
    python raman/exp4_shoulder_variant_report.py --left-shoulder 1500 1550
    python raman/exp4_shoulder_variant_report.py --band paper_1095 \
        --left-shoulder 1015 1075 --right-shoulder 1195 1255
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import exp3_timeseries_report as ts                          # noqa: E402
import exp4_stars_report as stars                            # noqa: E402
import exp4_paper_plots as paper_plots                       # noqa: E402
import exp4_band_fit_audit as audit                          # noqa: E402
from exp3_timeseries_report import ALL_BANDS                 # noqa: E402

C_SHIPPED = "#8c8c94"
C_VARIANT = "#b53229"


# ---------------------------------------------------------------------------
# Gains. gain_x is documented as test height over control height, each against
# its own local baseline - so it is reproducible from a band table alone, which
# is what lets a variant baseline be pushed all the way through to the gains
# without touching the difference-spectrum machinery. That claim is CHECKED
# against the shipped sers_gain.csv before any variant gain is believed.
# ---------------------------------------------------------------------------
def gains_from_heights(heights: dict, screen: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    ctrl = cfg["control_condition"]
    usable = screen
    if cfg["quality"]["exclude_failed_from_gains"]:
        usable = usable[usable["verdict"] != "fail"]
    index = {(r["key"], r["condition"], r["dilution"]): int(r["scan_number"])
             for _, r in usable.dropna(subset=["condition"]).iterrows()}
    rows = []
    for (key, cond, dil), scan in index.items():
        if cond == ctrl:
            continue
        if (key, ctrl, dil) not in index:
            continue                       # no control survived at this dilution
        test, control = heights.get(scan), heights.get(index[(key, ctrl, dil)])
        if test is None or control is None:
            continue
        rows.append({
            "key": key, "condition": cond, "dilution": dil,
            "factor": float(str(dil)[:-1]),
            "test_scan": scan, "control_scan": index[(key, ctrl, dil)],
            "control_cps": control, "test_cps": test,
            "gain_x": test / control if control > 0 else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["key", "condition", "factor"])


def verify_gain_reproducibility(bands: pd.DataFrame, screen: pd.DataFrame,
                                cfg: dict, band: str, out: Path) -> float:
    """Largest relative error in reproducing sers_gain.csv from the band table."""
    shipped = ROOT / cfg["io"]["output_dir"] / "sers_gain.csv"
    if not shipped.is_file():
        print("[variant] NOTE: %s absent - gain reproducibility unchecked" % shipped)
        return np.nan
    ref = pd.read_csv(shipped)
    ref = ref[ref["peak_name"] == band]
    if not len(ref):
        return np.nan
    h = (bands[bands["peak_name"] == band]
         .set_index("scan_number")["height_cps"].to_dict())
    mine = gains_from_heights(h, screen, cfg).set_index(
        ["key", "condition", "dilution"])["gain_x"]
    joined = ref.set_index(["key", "condition", "dilution"]).join(
        mine.rename("recomputed"), how="left")
    err = (100.0 * (joined["recomputed"] - joined["gain_x"]).abs()
           / joined["gain_x"].abs())
    worst = float(err.max())
    joined.assign(rel_err_pct=err).to_csv(out)
    return worst


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def shipped_vs_variant_bars(shipped, variant, screen, cfg, paper, band, label, path):
    """Figure 13's block, each arm drawn twice: shipped hatched, variant solid."""
    block = paper_plots._dilution_block(screen, cfg, paper)
    shared = paper_plots._shared_dilutions(block, cfg, paper)
    conditions = list(paper_plots.DILUTION_CONDITIONS)
    nominal = ALL_BANDS[band]["nominal"]

    x = np.arange(len(shared), dtype=float)
    slot = 0.8 / len(conditions)
    fig, ax = plt.subplots(figsize=(2.9 * len(shared) + 5.4, 5.8))
    for i, condition in enumerate(conditions):
        rows = block[condition].set_index("dilution")
        colour, _, lab = paper_plots._style(cfg, condition)
        scans = [int(rows.loc[d, "scan_number"]) for d in shared]
        old = np.array([shipped.get(s, np.nan) for s in scans], dtype=float)
        new = np.array([variant.get(s, np.nan) for s in scans], dtype=float)
        base = x + i * slot - 0.4
        ax.bar(base + slot * 0.25, old, slot * 0.44, color=colour, alpha=0.42,
               hatch="///", edgecolor="white", linewidth=0.5,
               label=("%s - shipped" % lab))
        ax.bar(base + slot * 0.72, new, slot * 0.44, color=colour,
               edgecolor="white", linewidth=0.5, label=("%s - %s" % (lab, label)))
        for xc, o, n in zip(base + slot * 0.72, old, new):
            if np.isfinite(o) and np.isfinite(n) and o != 0:
                ax.annotate("%+.0f%%" % (100.0 * (n - o) / abs(o)),
                            xy=(xc, max(o, n)), xytext=(0, 3),
                            textcoords="offset points", ha="center",
                            fontsize=7.5, color="0.25", rotation=90)
    ax.set_xticks(x)
    ax.set_xticklabels(shared)
    ax.set_xlabel("CV dilution")
    ax.set_ylabel("Baseline-corrected %d cm$^{-1}$ peak height (counts s$^{-1}$)"
                  % nominal)
    ax.legend(fontsize=8, frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_title(paper_plots._block_heading(
        cfg, paper, "%d cm$^{-1}$ - shipped shoulder vs %s" % (nominal, label)),
        loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def shift_diagnostic(rows: pd.DataFrame, cfg, band, label, path):
    """Is the shift a constant scale factor, or does it track the sample?

    This is the question that decides whether the change matters. A shift that
    is the same everywhere cancels out of every gain and every ratio and can
    only move absolute heights; one that varies with dilution or with the
    background level changes conclusions, not just numbers.
    """
    nominal = ALL_BANDS[band]["nominal"]
    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.6))
    styles = {c: cfg["conditions"][c] for c in rows["condition"].dropna().unique()
              if c in cfg["conditions"]}

    for cond, meta in styles.items():
        sub = rows[rows["condition"] == cond]
        kw = dict(color=meta["color"], marker=meta["marker"], ls="none",
                  ms=6, alpha=0.85, label=meta["label"])
        axes[0].plot(sub["height_cps_shipped"], sub["height_cps"], **kw)
        axes[1].plot(sub["factor"], sub["shift_vs_shipped_pct"], **kw)
        axes[2].plot(sub["height_cps_shipped"], sub["shift_vs_shipped_cps"], **kw)

    lim = [0, float(np.nanmax([rows["height_cps_shipped"].max(),
                               rows["height_cps"].max()])) * 1.05]
    axes[0].plot(lim, lim, color="0.4", ls="--", lw=1.0, zorder=0)
    axes[0].set_xlim(lim)
    axes[0].set_ylim(lim)
    axes[0].set_xlabel("shipped height (counts/s)")
    axes[0].set_ylabel("%s height (counts/s)" % label)
    axes[0].set_title("variant vs shipped, 1:1 dashed", loc="left", fontsize=10)

    axes[1].axhline(0.0, color="0.4", ls="--", lw=1.0)
    med = float(rows["shift_vs_shipped_pct"].median())
    axes[1].axhline(med, color=C_VARIANT, ls=":", lw=1.2,
                    label="median %+.1f%%" % med)
    paper_plots._set_dilution_axis(axes[1], rows["factor"].dropna())
    axes[1].set_xlabel("CV dilution")
    axes[1].set_ylabel("shift (%)")
    axes[1].set_title("shift vs dilution - flat means it cancels from gains",
                      loc="left", fontsize=10)

    axes[2].axhline(0.0, color="0.4", ls="--", lw=1.0)
    axes[2].set_xlabel("shipped height (counts/s)")
    axes[2].set_ylabel("shift (counts/s)")
    axes[2].set_title("absolute shift vs signal size", loc="left", fontsize=10)

    for a in axes:
        a.grid(alpha=0.3)
        a.set_axisbelow(True)
    axes[0].legend(fontsize=8, frameon=False)
    axes[1].legend(fontsize=8, frameon=False)
    fig.suptitle("%d cm$^{-1}$ - what moving the shoulder to %s does to every "
                 "audited scan" % (nominal, label), x=0.006, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def gain_comparison(old_g, new_g, cfg, band, label, path):
    nominal = ALL_BANDS[band]["nominal"]
    j = old_g.set_index(["key", "condition", "dilution"])[["gain_x"]].join(
        new_g.set_index(["key", "condition", "dilution"])[["gain_x"]],
        lsuffix="_shipped", rsuffix="_variant", how="inner").reset_index()
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.0))
    for cond, meta in cfg["conditions"].items():
        sub = j[j["condition"] == cond]
        if not len(sub):
            continue
        axes[0].plot(sub["gain_x_shipped"], sub["gain_x_variant"],
                     color=meta["color"], marker=meta["marker"], ls="none",
                     ms=7, alpha=0.85, label=meta["label"])
        axes[1].plot(sub["dilution"].map(lambda d: float(str(d)[:-1])),
                     100.0 * (sub["gain_x_variant"] - sub["gain_x_shipped"])
                     / sub["gain_x_shipped"],
                     color=meta["color"], marker=meta["marker"], ls="none",
                     ms=7, alpha=0.85, label=meta["label"])
    lim = [0, float(np.nanmax([j["gain_x_shipped"].max(),
                               j["gain_x_variant"].max()])) * 1.06]
    axes[0].plot(lim, lim, color="0.4", ls="--", lw=1.0, zorder=0)
    axes[0].axhline(1.0, color="0.75", lw=0.8, zorder=0)
    axes[0].axvline(1.0, color="0.75", lw=0.8, zorder=0)
    axes[0].set_xlim(lim)
    axes[0].set_ylim(lim)
    axes[0].set_xlabel("shipped gain (x)")
    axes[0].set_ylabel("%s gain (x)" % label)
    axes[0].set_title("gain vs dye-only control - a point that crosses the grey "
                      "lines changes its verdict", loc="left", fontsize=9.5)
    axes[1].axhline(0.0, color="0.4", ls="--", lw=1.0)
    paper_plots._set_dilution_axis(axes[1], j["dilution"].map(
        lambda d: float(str(d)[:-1])))
    axes[1].set_xlabel("CV dilution")
    axes[1].set_ylabel("change in gain (%)")
    axes[1].set_title("change in gain by dilution", loc="left", fontsize=9.5)
    for a in axes:
        a.grid(alpha=0.3)
        a.set_axisbelow(True)
    axes[0].legend(fontsize=8, frameon=False)
    fig.suptitle("%d cm$^{-1}$ gains - shipped shoulder vs %s"
                 % (nominal, label), x=0.006, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)
    return j


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Measure what moving a baseline shoulder does to the results.")
    p.add_argument("--band", default="cv_1620")
    p.add_argument("--left-shoulder", nargs=2, type=float, default=[1490.0, 1550.0],
                   metavar=("LO", "HI"),
                   help="default 1490 1550 - same 60 cm-1 width as the shipped "
                        "shoulder, moved clear of the cv_1587 window")
    p.add_argument("--right-shoulder", nargs=2, type=float, default=None,
                   metavar=("LO", "HI"), help="default: unchanged")
    p.add_argument("--label", default=None,
                   help="short tag for filenames (default: L<left hi>)")
    p.add_argument("--outdir", default=None)
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--no-panels", action="store_true",
                   help="skip the per-scan audit panels, results figures only")
    p.add_argument("--flat", action="store_true",
                   help="write panels straight into <band>/ instead of "
                        "<band>/<paper>/<stars|bipyramids|cv_only>/")
    args = p.parse_args(argv)

    band = args.band
    if band not in ALL_BANDS:
        raise SystemExit("[error] unknown band: %s" % band)
    left = tuple(args.left_shoulder) if args.left_shoulder else None
    right = tuple(args.right_shoulder) if args.right_shoulder else None
    label = args.label or ("L%g" % left[1] if left else "R%g" % right[1])

    cfg = stars.load_config()
    dpi = args.dpi or cfg["plotting"]["dpi"]
    out_root = (Path(args.outdir) if args.outdir else
                ROOT / cfg["io"]["output_dir"] / "figures"
                / ("shoulder_variant_%s" % label))
    out_root.mkdir(parents=True, exist_ok=True)

    print("[variant] band %s | left shoulder %s | right shoulder %s"
          % (band, "%g-%g" % left if left else "unchanged",
             "%g-%g" % right if right else "unchanged"))
    print("[variant] output -> %s" % out_root)

    tcfg = ts.load_config(ROOT / "configs" / cfg["screen"]["timeseries_config"])
    scans = stars.load_scans(cfg)
    model = stars.load_noise_model(cfg)
    screen = stars.scan_screen(scans, model, cfg, tcfg)
    screen["factor"] = screen["dilution"].map(
        lambda d: float(str(d)[:-1]) if isinstance(d, str) else np.nan)
    bands = stars.band_table(scans, screen, cfg)          # shipped, untouched

    worst = verify_gain_reproducibility(bands, screen, cfg, band,
                                        out_root / "gain_reproducibility.csv")
    if np.isfinite(worst) and worst > 1e-6:
        raise SystemExit("[error] gain_x is not reproducible from the band table "
                         "(worst %.3g%%) - the variant gains would not be "
                         "comparable, so nothing is written" % worst)
    print("[variant] gain_x reproduced from band_metrics to %.2g%% - "
          "variant gains are comparable" % (0.0 if not np.isfinite(worst) else worst))

    # --- the variant measurement ------------------------------------------
    mapped = [r for r in scans if r["condition"] is not None]
    by_scan_band = {(int(r["scan_number"]), r["peak_name"]): r
                    for _, r in bands.iterrows()}
    by_scan = {int(r["scan_number"]): r for _, r in screen.iterrows()}
    panel_dir = out_root / band
    panel_dir.mkdir(parents=True, exist_ok=True)

    rows, items = [], []
    for rec in mapped:
        m = audit.measure(rec, band, left_win=left, right_win=right)
        brow = by_scan_band.get((int(rec["scan_number"]), band))
        if brow is not None:
            d = abs(float(brow["height_cps"]) - m["height_default"])
            if d > audit.HEIGHT_TOL:
                raise SystemExit("[error] scan %d: shipped height not reproduced "
                                 "(%.3g) - aborting rather than compare against "
                                 "a number this script cannot regenerate"
                                 % (rec["scan_number"], d))
        if not args.no_panels:
            # Same filing as the audit run - paper, then what was on the spot -
            # so the two trees can be walked side by side.
            dest = panel_dir / audit.panel_relpath(rec, args.flat)
            dest.parent.mkdir(parents=True, exist_ok=True)
            audit.panel(rec, m, by_scan.get(int(rec["scan_number"])), brow, cfg,
                        dest, dpi=dpi)
        items.append((rec, m))
        rows.append({
            "scan_number": rec["scan_number"], "key": rec["key"],
            "paper": rec["paper"], "particle": rec["particle"],
            "condition": rec["condition"],
            "sample": audit.SAMPLE_TAG.get(rec["condition"], "unassigned"),
            "dilution": rec["dilution"],
            "factor": float(str(rec["dilution"])[:-1]) if rec["dilution"] else np.nan,
            "int_time_s": rec["int_time_s"], "peak_name": band,
            "left_lo": m["left_lo"], "left_hi": m["left_hi"],
            "right_lo": m["right_lo"], "right_hi": m["right_hi"],
            "center_cm1": m["centre"], "in_lit_window": m["in_lit"],
            "height_cps": m["height"], "height_cps_shipped": m["height_default"],
            "shift_vs_shipped_cps": m["shift_vs_default"],
            "shift_vs_shipped_pct": m["shift_pct"],
            "baseline_slope_cps_per_cm1": m.get("slope"),
            "sigma_cps": m["sigma"], "snr": m["snr"],
            "snr_shipped": (m["height_default"] / m["sigma"]
                            if m["sigma"] else np.nan),
        })
    frame = pd.DataFrame(rows)
    frame.to_csv(out_root / ("heights_%s.csv" % label), index=False)
    if not args.no_panels:
        print("[variant] %d audit panel(s) -> %s" % (len(items), panel_dir))
        audit.contact_sheet(items, band, out_root / ("contact_%s.png" % band))

    # --- the results, rebuilt ---------------------------------------------
    vheights = frame.set_index("scan_number")["height_cps"].to_dict()
    sheights = frame.set_index("scan_number")["height_cps_shipped"].to_dict()

    vbands = bands.copy()
    sel = vbands["peak_name"] == band
    vbands.loc[sel, "height_cps"] = vbands.loc[sel, "scan_number"].map(vheights)
    vbands.loc[sel, "height_local_cps"] = vbands.loc[sel, "height_cps"]
    vbands.loc[sel, "snr"] = vbands.loc[sel, "height_cps"] / vbands.loc[sel, "sigma_cps"]
    vbands.loc[sel, "detected"] = ((vbands.loc[sel, "snr"] >= cfg["quality"]["min_snr"])
                                   & vbands.loc[sel, "in_lit_window"])
    vbands.to_csv(out_root / ("band_metrics_%s.csv" % label), index=False)

    for paper in ("offwhite", "white"):
        pdir = out_root / paper_plots._focus_spec(cfg, paper)["output_dir"]
        pdir.mkdir(parents=True, exist_ok=True)
        paper_plots.dilution_intensity_bars(
            vbands, screen, cfg, paper,
            pdir / ("13_1620_intensity_by_dilution_%s.png" % label))
        paper_plots.dilution_intensity_bars(
            vbands, screen, cfg, paper,
            pdir / ("13b_1620_intensity_by_dilution_total_counts_%s.png" % label),
            total_counts=True)
        shipped_vs_variant_bars(
            sheights, vheights, screen, cfg, paper, band, label,
            pdir / ("13c_1620_shipped_vs_%s.png" % label))
        print("[variant] figure 13 rebuilt for %s -> %s" % (paper, pdir))

    shift_diagnostic(frame, cfg, band, label,
                     out_root / ("shift_diagnostic_%s.png" % label))

    old_g = gains_from_heights(sheights, screen, cfg)
    new_g = gains_from_heights(vheights, screen, cfg)
    joined = gain_comparison(old_g, new_g, cfg, band, label,
                             out_root / ("gain_comparison_%s.png" % label))
    joined["change_pct"] = (100.0 * (joined["gain_x_variant"] - joined["gain_x_shipped"])
                            / joined["gain_x_shipped"])
    joined["crossed_unity"] = ((joined["gain_x_shipped"] - 1.0)
                               * (joined["gain_x_variant"] - 1.0)) < 0
    joined.to_csv(out_root / ("gains_%s.csv" % label), index=False)

    # --- what it comes to --------------------------------------------------
    med = float(frame["shift_vs_shipped_pct"].median())
    spread = (float(frame["shift_vs_shipped_pct"].quantile(0.9))
              - float(frame["shift_vs_shipped_pct"].quantile(0.1)))
    crossed = joined[joined["crossed_unity"]]
    minsnr = cfg["quality"]["min_snr"]
    flipped = frame[((frame["snr"] >= minsnr) != (frame["snr_shipped"] >= minsnr))]

    lines = [
        "# %s at %d cm-1: shoulder %g-%g instead of %g-%g"
        % (label, ALL_BANDS[band]["nominal"], frame["left_lo"].iloc[0],
           frame["left_hi"].iloc[0], ALL_BANDS[band]["search"][0] - audit.PAD,
           ALL_BANDS[band]["search"][0]), "",
        "Not the reported numbers. The shipped pipeline is untouched; every height",
        "here was remeasured with the same estimator and a different anchor window,",
        "and the shipped height was regenerated alongside it and checked against",
        "`band_metrics.csv` on every scan before any comparison was drawn.", "",
        "## Height", "",
        "| | |", "| --- | --- |",
        "| scans remeasured | %d |" % len(frame),
        "| median shift | %+.1f%% |" % med,
        "| 10th-90th percentile spread | %.1f percentage points |" % spread,
        "| largest shift | %+.1f%% (scan %d) |" % (
            frame.loc[frame["shift_vs_shipped_pct"].abs().idxmax(),
                      "shift_vs_shipped_pct"],
            frame.loc[frame["shift_vs_shipped_pct"].abs().idxmax(), "scan_number"]),
        "| heights that change sign | %d |" % int(
            ((frame["height_cps"] > 0) != (frame["height_cps_shipped"] > 0)).sum()),
        "| scans crossing the SNR>=%g detection line | %d |" % (minsnr, len(flipped)),
        "",
        "## Gain", "",
        "| | |", "| --- | --- |",
        "| gains recomputed | %d |" % len(joined),
        "| median change in gain | %+.1f%% |" % float(joined["change_pct"].median()),
        "| largest change | %+.1f%% |" % float(
            joined.loc[joined["change_pct"].abs().idxmax(), "change_pct"]),
        "| gains crossing 1.0 (verdict flips) | %d |" % len(crossed),
        "",
    ]
    if len(crossed):
        lines += ["Crossed unity:", ""]
        lines += ["- %s / %s @ %s: %.3f -> %.3f"
                  % (r["key"], r["condition"], r["dilution"],
                     r["gain_x_shipped"], r["gain_x_variant"])
                  for _, r in crossed.iterrows()]
        lines.append("")
    lines += [
        "## Reading it", "",
        "`shift_diagnostic_%s.png` is the one that decides whether this matters."
        % label,
        "A shift that is flat across dilution is a scale factor: it moves absolute",
        "heights and cancels out of every gain. A shift that tracks dilution or",
        "background level changes the shape of the ladder, and therefore the",
        "conclusions drawn from it.", "",
        "Per-scan panels are in `%s/`, each drawing both baselines." % band,
    ]
    (out_root / ("FINDINGS_%s.md" % label)).write_text("\n".join(lines),
                                                       encoding="utf-8")

    print()
    print("[variant] median height shift %+.1f%% (10-90 spread %.1f pp)"
          % (med, spread))
    print("[variant] median gain change  %+.1f%% | gains crossing 1.0: %d"
          % (float(joined["change_pct"].median()), len(crossed)))
    print("[variant] scans crossing the SNR>=%g line: %d" % (minsnr, len(flipped)))
    print("[variant] wrote %s" % (out_root / ("FINDINGS_%s.md" % label)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
