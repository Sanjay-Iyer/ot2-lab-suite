#!/usr/bin/env python
"""exp3_snr_original_method.py - the per-paper SNR plots as they were ORIGINALLY.

figures_plain/white.png and offwhite.png are drawn from `snr` in
band_metrics.csv, which since the baseline patch means the LOCAL-baseline
height over sigma. This regenerates the same two plots from the height the
analysis used before any of the four baseline variants existed: the arPLS
lambda = 1e5 corrected, Savitzky-Golay smoothed trace.

    original SNR = height_arpls_1e5_cps / sigma_cps

sigma is the same number in both - it never changed across any method, because
the second-difference MAD is blind to a smooth background. So these plots differ
from white.png / offwhite.png only in the numerator.

Writes NEW files; nothing existing is touched.

Run (from raman/, after exp3_timeseries_report.py):
    python exp3_snr_original_method.py
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
from exp3_timeseries_report import ALL_BANDS                 # noqa: E402

PARTICLE_COLOR = {"bipyramids": "#2f6ea8", "stars": "#a8322d"}


def main() -> int:
    cfg = ts.load_config()
    band = cfg["reference_band"]
    nominal = ALL_BANDS[band]["nominal"]
    res = ROOT / cfg["io"]["output_dir"]
    out = res / "figures_plain"

    bands = pd.read_csv(res / "band_metrics.csv")
    verdict = pd.read_csv(res / "verdict.csv")
    g = bands[bands["peak_name"] == band].copy()
    g["snr_original"] = g["height_arpls_1e5_cps"] / g["sigma_cps"]

    for paper in dict.fromkeys(s["paper"] for s in cfg["sweeps"]):
        specs = [s for s in cfg["sweeps"] if s["paper"] == paper]
        fig, ax = plt.subplots(figsize=(6.4, 4.6))
        ticks = set()
        for spec in specs:
            h = g[g["key"] == spec["key"]].sort_values("int_time_s")
            v = (verdict[verdict["key"] == spec["key"]]
                 .set_index("int_time_s")["overall"].to_dict())
            t = h["int_time_s"].to_numpy(float)
            y = h["snr_original"].to_numpy(float)
            ticks.update(t.tolist())
            c = PARTICLE_COLOR[spec["particle"]]
            # Every point drawn the same way and joined, as the plot was before
            # the QC markers were added. Which scans failed the raw-data vetoes
            # is in verdict.csv; it is deliberately not encoded here.
            ax.plot(t, y, color=c, lw=1.6, marker="o", ms=8, zorder=2,
                    label=spec["particle"])
        tt = sorted(ticks)
        ax.set_xticks(tt)
        ax.set_xticklabels(["%g" % q for q in tt])
        ax.set_xlabel("Integration time (s)")
        ax.set_ylabel("SNR at %d cm$^{-1}$" % nominal)
        ax.set_ylim(bottom=0)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False)
        ax.set_title(paper.capitalize())
        fig.tight_layout()
        path = out / ("%s_original_arpls.png" % paper)
        fig.savefig(path, dpi=cfg["plotting"]["dpi"])
        plt.close(fig)
        print("wrote", path)

    pd.set_option("display.width", 200)
    cmp = g.pivot_table(index="int_time_s", columns="key",
                        values="snr_original").round(1)
    cur = (bands[bands["peak_name"] == band]
           .pivot_table(index="int_time_s", columns="key", values="snr").round(1))
    print("\noriginal method (arPLS 1e5):")
    print(cmp.to_string())
    print("\ncurrent method (local baseline), for reference:")
    print(cur.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
