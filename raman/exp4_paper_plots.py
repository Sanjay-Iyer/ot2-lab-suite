"""Clear, Experiment-1-style nanostar figures rendered one paper at a time."""
from __future__ import annotations

from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from exp3_timeseries_report import ALL_BANDS


CONDITIONS = ("dyeonly", "stars5x", "stars")
TEST_CONDITIONS = ("stars5x", "stars")
COMBINED_FIGURES = (
    "01_saturation_screen.png",
    "02_map_check_dilution_ladder.png",
    "03_dilution_response.png",
    "04_paired_difference_spectra.png",
    "05_sers_gain.png",
    "06_detection_limits.png",
    "07_scans_in_order.png",
)
OBSOLETE_PAPER_FIGURES = (
    "03_band_windows_zoom.png",
    "04_band_intensity_by_condition.png",
    "05_difference_vs_cv_reference.png",
    "06_background_and_integration_time.png",
    "07_substrate_corrected_band_gain.png",
)


def _active_dilutions(cfg):
    """The dilution set every figure is held to, from plotting.active_dilutions.

    Every arm measured 2x, 5x and 10x; only the offwhite nanostar ladders went
    further. Holding the figures to the shared three keeps paper, particle and
    dilution readable against each other. The CSVs keep the full ladder.
    """
    active = cfg["plotting"].get("active_dilutions")
    return None if not active else list(active)


def _keep_active(rows, cfg):
    active = _active_dilutions(cfg)
    return rows if active is None else rows[rows["dilution"].isin(active)]


def _style(cfg, condition):
    meta = cfg["conditions"][condition]
    return meta["color"], meta["marker"], meta["label"]


def _focus_spec(cfg, paper):
    try:
        return cfg["plotting"]["paper_focus"][paper]
    except KeyError as exc:
        raise SystemExit(
            "[error] plotting.paper_focus.%s is required" % paper
        ) from exc


def _focus_rows(screen, cfg, paper):
    spec = _focus_spec(cfg, paper)
    rows = screen[
        (screen["key"] == spec["key"])
        & (screen["dilution"] == spec["dilution"])
        & (screen["condition"].isin(CONDITIONS))
    ].copy()
    missing = [c for c in CONDITIONS if not (rows["condition"] == c).any()]
    if missing:
        raise SystemExit(
            "[error] %s focus %s/%s is missing %s"
            % (paper, spec["key"], spec["dilution"], missing)
        )
    failed = rows.loc[rows["verdict"] == "fail", "condition"].tolist()
    if failed:
        raise SystemExit(
            "[error] %s focus comparison includes failed scans: %s"
            % (paper, failed)
        )
    return rows.set_index("condition").loc[list(CONDITIONS)].reset_index()


def _paper_keys(screen, cfg, paper):
    """Return only the one exposure-matched star panel active for this paper."""
    key = _focus_spec(cfg, paper)["key"]
    present = screen[
        (screen["paper"] == paper)
        & (screen["particle"] == "stars")
        & (screen["key"] == key)
    ]
    if not len(present):
        raise SystemExit("[error] active plot panel %s is absent for %s" % (key, paper))
    return [key]


def _view_label(cfg, paper):
    key = _focus_spec(cfg, paper)["key"]
    return cfg["panel_label"][key]


def _matched_bipyramid_row(screen, cfg, paper, dilution):
    """Return the usable bipyramid acquired at the active paper/time/dilution."""
    spec = cfg["plotting"]["particle_comparison"][paper]
    rows = screen[
        (screen["key"] == spec["bipyramids_key"])
        & (screen["condition"] == "bp")
        & (screen["dilution"] == dilution)
    ]
    if len(rows) != 1:
        raise SystemExit(
            "[error] expected one matched bipyramid for %s/%s; found %d"
            % (paper, dilution, len(rows))
        )
    row = rows.iloc[0]
    if row["verdict"] == "fail":
        raise SystemExit(
            "[error] matched bipyramid scan %d failed the saturation screen"
            % int(row["scan_number"])
        )
    if float(row["int_time_s"]) != float(spec["exposure_s"]):
        raise SystemExit(
            "[error] matched bipyramid exposure mismatch for %s: expected %s, found %s"
            % (paper, spec["exposure_s"], row["int_time_s"])
        )
    return row


def _mark_bands(ax, cfg, spans=False):
    """Mark only the primary 1620 cm-1 dye band on active figures."""
    meta = ALL_BANDS[cfg["sers_band"]]
    if spans:
        ax.axvspan(
            meta["lit"][0],
            meta["lit"][1],
            color="tab:green",
            alpha=0.11,
            lw=0,
            zorder=0,
        )
    else:
        ax.axvline(meta["nominal"], color="0.65", lw=0.7, ls=":", zorder=0)


def _save(fig, path, cfg, rect=None):
    fig.tight_layout(rect=rect)
    fig.savefig(path, dpi=cfg["plotting"]["dpi"])
    plt.close(fig)


def _set_dilution_axis(ax, values):
    """Keep log spacing while showing bench-style labels: 2x, 5x, 10x."""
    ticks = sorted({float(value) for value in values if np.isfinite(float(value))})
    ax.set_xscale("log")
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [
            ("%d" % value if float(value).is_integer() else "%g" % value) + "x"
            for value in ticks
        ]
    )
    ax.minorticks_off()


def raw_vs_corrected(scans, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper)
    row = focus[focus["condition"] == "stars"].iloc[0]
    rec = {r["scan_number"]: r for r in scans}[int(row["scan_number"])]
    x = rec["x_fit"]
    raw = rec["y_counts"]
    baseline = rec["baseline_cps"] * rec["int_time_s"]
    corrected = rec["corr_cps"] * rec["int_time_s"]
    nominal = ALL_BANDS[cfg["sers_band"]]["nominal"]
    peak_i = int(np.argmin(np.abs(x - nominal)))

    fig, axes = plt.subplots(2, 1, figsize=(13, 8.5), sharex=True)
    axes[0].plot(x, raw, color="#3b3b45", lw=1.15, label="raw detector signal")
    axes[0].plot(
        x,
        baseline,
        color="#c02f28",
        lw=1.35,
        ls=(0, (5, 2)),
        label="fitted fluorescence baseline (arPLS)",
    )
    axes[0].set_ylabel("Intensity (counts)")
    axes[0].set_title(
        "Raw signal - fluorescence dominates the detector scale",
        fontsize=11,
        loc="left",
    )
    axes[0].legend(fontsize=9, frameon=False)
    axes[0].grid(alpha=0.25)
    axes[0].annotate(
        "%d cm$^{-1}$ band" % nominal,
        xy=(x[peak_i], raw[peak_i]),
        xytext=(x[peak_i] - 390, raw.max() * 0.82),
        color="#1e8b57",
        fontsize=9,
        arrowprops=dict(arrowstyle="->", color="#1e8b57", lw=1.2),
    )

    axes[1].plot(
        x, corrected, color="#1e8b57", lw=1.15, label="baseline-corrected signal"
    )
    axes[1].axhline(0, color="0.25", lw=0.8)
    _mark_bands(axes[1], cfg, spans=True)
    axes[1].set_xlim(400, 1800)
    axes[1].set_xlabel("Raman shift (cm$^{-1}$)")
    axes[1].set_ylabel("Intensity (counts)")
    axes[1].set_title(
        "Same scan after fluorescence-background removal", fontsize=11, loc="left"
    )
    axes[1].legend(fontsize=9, frameon=False)
    axes[1].grid(alpha=0.25)
    fig.suptitle(
        "%s paper - raw vs baseline-corrected scale\n"
        "stock stars, %s CV, scan %d, %.0f s"
        % (
            paper.capitalize(),
            row["dilution"],
            row["scan_number"],
            row["int_time_s"],
        ),
        fontsize=12,
    )
    _save(fig, path, cfg, rect=(0, 0, 1, 0.94))


def focus_spectra(scans, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper)
    by_scan = {r["scan_number"]: r for r in scans}
    spec = _focus_spec(cfg, paper)
    fig, ax = plt.subplots(figsize=(14, 6.2))
    for _, row in focus.iterrows():
        rec = by_scan[int(row["scan_number"])]
        col, _, label = _style(cfg, row["condition"])
        ax.plot(rec["x_fit"], rec["smooth_cps"], color=col, lw=1.3, label=label)
    _mark_bands(ax, cfg, spans=True)
    ax.axhline(0, color="0.25", lw=0.7)
    ax.set_xlim(400, 1800)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Baseline-corrected counts s$^{-1}$")
    ax.set_title(
        "%s paper - matched %s spectra (%s)"
        % (paper.capitalize(), spec["dilution"], cfg["panel_label"][spec["key"]]),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(alpha=0.25)
    _save(fig, path, cfg)


def focus_spectra_with_bipyramids(scans, screen, cfg, paper, path):
    """Add the exposure- and dilution-matched bipyramid to the focus spectra."""
    focus = _focus_rows(screen, cfg, paper)
    focus_spec = _focus_spec(cfg, paper)
    bp_row = _matched_bipyramid_row(
        screen, cfg, paper, focus_spec["dilution"]
    )

    by_scan = {r["scan_number"]: r for r in scans}
    fig, ax = plt.subplots(figsize=(14, 6.2))
    for _, row in focus.iterrows():
        rec = by_scan[int(row["scan_number"])]
        colour, _, label = _style(cfg, row["condition"])
        ax.plot(rec["x_fit"], rec["smooth_cps"], color=colour, lw=1.3, label=label)
    bp_rec = by_scan[int(bp_row["scan_number"])]
    bp_colour, _, bp_label = _style(cfg, "bp")
    ax.plot(
        bp_rec["x_fit"],
        bp_rec["smooth_cps"],
        color=bp_colour,
        lw=1.45,
        label=bp_label,
    )
    _mark_bands(ax, cfg, spans=True)
    ax.axhline(0, color="0.25", lw=0.7)
    ax.set_xlim(400, 1800)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Baseline-corrected counts s$^{-1}$")
    ax.set_title(
        "%s - matched %s spectra with bipyramids"
        % (_view_label(cfg, paper), focus_spec["dilution"]),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(alpha=0.25)
    fig.text(
        0.5,
        0.01,
        "All nanoparticle spectra use the same paper, CV dilution, and exposure.",
        ha="center",
        fontsize=8,
        color="0.35",
    )
    _save(fig, path, cfg, rect=(0, 0.035, 1, 1))


def band_windows(scans, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper)
    by_scan = {r["scan_number"]: r for r in scans}
    meta = ALL_BANDS[cfg["sers_band"]]
    lo, hi = meta["nominal"] - 70, meta["nominal"] + 45
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    for _, row in focus.iterrows():
        rec = by_scan[int(row["scan_number"])]
        selected = (rec["x_fit"] >= lo) & (rec["x_fit"] <= hi)
        col, _, label = _style(cfg, row["condition"])
        ax.plot(
            rec["x_fit"][selected],
            rec["smooth_cps"][selected],
            color=col,
            lw=1.45,
            label=label,
        )
    ax.axvspan(
        meta["lit"][0], meta["lit"][1], color="tab:green", alpha=0.14, lw=0
    )
    ax.axhline(0, color="0.3", lw=0.7)
    ax.set_xlim(lo, hi)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Baseline-corrected counts s$^{-1}$")
    ax.set_title("%d cm$^{-1}$ CV band window" % meta["nominal"])
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, frameon=False)
    spec = _focus_spec(cfg, paper)
    fig.suptitle(
        "%s paper - matched %s comparison focused on 1620 cm$^{-1}$ (%s)"
        % (paper.capitalize(), spec["dilution"], cfg["panel_label"][spec["key"]]),
        fontsize=12,
    )
    _save(fig, path, cfg, rect=(0, 0, 1, 0.93))


def band_intensity(bands, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper)
    scan_of = focus.set_index("condition")["scan_number"].astype(int).to_dict()
    focus_spec = _focus_spec(cfg, paper)
    bp_row = _matched_bipyramid_row(screen, cfg, paper, focus_spec["dilution"])
    scan_of["bp"] = int(bp_row["scan_number"])
    plot_conditions = CONDITIONS + ("bp",)
    order = [cfg["sers_band"]]
    x = np.arange(len(order))
    width = 0.8 / len(plot_conditions)
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, condition in enumerate(plot_conditions):
        rows = (
            bands[bands["scan_number"] == scan_of[condition]]
            .set_index("peak_name")
            .reindex(order)
        )
        colour, _, label = _style(cfg, condition)
        ax.bar(
            x + i * width - 0.4 + width / 2,
            rows["height_cps"],
            width,
            color=colour,
            edgecolor="white",
            linewidth=0.5,
            label=label,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([str(ALL_BANDS[b]["nominal"]) for b in order])
    ax.set_xlabel("CV band (cm$^{-1}$)")
    ax.set_ylabel("Baseline-corrected peak height (counts s$^{-1}$)")
    ax.set_title(
        "%s - matched %s intensity of the 1620 cm$^{-1}$ CV band"
        % (_view_label(cfg, paper), focus_spec["dilution"]),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    _save(fig, path, cfg)


def difference_spectra(scans, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper).set_index("condition")
    by_scan = {r["scan_number"]: r for r in scans}
    control = by_scan[int(focus.loc["dyeonly", "scan_number"])]
    fig, ax = plt.subplots(figsize=(14, 6))
    for condition in TEST_CONDITIONS:
        test = by_scan[int(focus.loc[condition, "scan_number"])]
        reference = np.interp(
            test["x_fit"], control["x_fit"], control["smooth_cps"]
        )
        colour, _, label = _style(cfg, condition)
        ax.plot(
            test["x_fit"],
            test["smooth_cps"] - reference,
            color=colour,
            lw=1.25,
            label=label + " minus CV only",
        )
    _mark_bands(ax, cfg, spans=True)
    ax.axhline(0, color="0.25", lw=0.8)
    nominal = ALL_BANDS[cfg["sers_band"]]["nominal"]
    ax.set_xlim(nominal - 70, nominal + 45)
    ax.set_xlabel("Raman shift (cm$^{-1}$)")
    ax.set_ylabel("Difference (counts s$^{-1}$)")
    ax.set_title(
        "%s - matched %s difference from CV only at 1620 cm$^{-1}$"
        % (_view_label(cfg, paper), _focus_spec(cfg, paper)["dilution"]),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(alpha=0.25)
    _save(fig, path, cfg)


def background_and_time(scans, screen, cfg, paper, path):
    focus = _focus_rows(screen, cfg, paper)
    by_scan = {r["scan_number"]: r for r in scans}
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    for _, row in focus.iterrows():
        rec = by_scan[int(row["scan_number"])]
        colour, _, label = _style(cfg, row["condition"])
        axes[0].plot(
            rec["x_fit"], rec["y_cps"], color=colour, lw=1.15, label=label
        )
    _mark_bands(axes[0], cfg, spans=True)
    nominal = ALL_BANDS[cfg["sers_band"]]["nominal"]
    axes[0].set_xlim(nominal - 70, nominal + 45)
    axes[0].set_xlabel("Raman shift (cm$^{-1}$)")
    axes[0].set_ylabel("Raw counts s$^{-1}$")
    axes[0].set_title("Raw signal around the 1620 cm$^{-1}$ CV band", loc="left")
    axes[0].legend(fontsize=8, frameon=False)
    axes[0].grid(alpha=0.25)

    keys = _paper_keys(screen, cfg, paper)
    rows = screen[
        (screen["paper"] == paper)
        & (screen["particle"] == "stars")
        & (screen["key"].isin(keys))
    ]
    measured = [
        set(rows.loc[rows["condition"] == condition, "dilution"].dropna())
        for condition in CONDITIONS
    ]
    shared = set.intersection(*measured) if measured else set()
    if not shared:
        raise SystemExit("[error] %s has no dilution shared by all conditions" % paper)
    rows = rows[rows["dilution"].isin(shared)]
    for key in keys:
        for condition in CONDITIONS:
            subset = rows[
                (rows["key"] == key) & (rows["condition"] == condition)
            ].sort_values("factor")
            if not len(subset):
                continue
            colour, marker, label = _style(cfg, condition)
            axes[1].plot(
                subset["factor"],
                subset["int_time_s"],
                color=colour,
                marker=marker,
                lw=1.0,
                label="%s - %s" % (label, cfg["panel_label"][key]),
            )
    _set_dilution_axis(axes[1], rows["factor"].dropna())
    axes[1].set_xlabel("CV dilution factor (×)")
    axes[1].set_ylabel("Integration time (s)")
    axes[1].set_title("Exposure used across the dilution ladder", loc="left")
    axes[1].legend(fontsize=6.5, frameon=False)
    axes[1].grid(alpha=0.25)
    fig.suptitle(
        "%s - 1620 cm$^{-1}$ background and integration time"
        % _view_label(cfg, paper),
        fontsize=12,
    )
    _save(fig, path, cfg, rect=(0, 0, 1, 0.94))


def corrected_band_gain(gain, cfg, paper, path):
    focus = _focus_spec(cfg, paper)
    subset = gain[
        (gain["key"] == focus["key"])
        & (gain["dilution"] == focus["dilution"])
        & (gain["condition"].isin(TEST_CONDITIONS))
    ]
    order = [cfg["sers_band"]]
    x = np.arange(len(order))
    width = 0.36
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, condition in enumerate(TEST_CONDITIONS):
        rows = (
            subset[subset["condition"] == condition]
            .set_index("peak_name")
            .reindex(order)
        )
        colour, _, label = _style(cfg, condition)
        # Match the exp-1 figure's meaning: paired band height on the test spot
        # minus paired band height on the same-paper dye-only spot.  Do not use
        # delta_cps here; that is the largest positive excursion anywhere in the
        # difference window and exists specifically for the SNR test.
        paired_difference = rows["test_cps"] - rows["control_cps"]
        ax.bar(
            x + (i - 0.5) * width,
            paired_difference,
            width,
            color=colour,
            edgecolor="white",
            linewidth=0.5,
            label=label,
        )
    ax.axhline(0, color="0.25", lw=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([str(ALL_BANDS[b]["nominal"]) for b in order])
    ax.set_xlabel("CV band (cm$^{-1}$)")
    ax.set_ylabel("Difference from CV-only reference (counts s$^{-1}$)")
    ax.set_title(
        "%s - paired 1620 cm$^{-1}$ signal gain at %s"
        % (_view_label(cfg, paper), focus["dilution"]),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    _save(fig, path, cfg)


def gain_ladder(gain, screen, cfg, paper, path):
    band = cfg["sers_band"]
    keys = _paper_keys(screen, cfg, paper)
    subset = _keep_active(
        gain[
            (gain["peak_name"] == band)
            & (gain["condition"].isin(TEST_CONDITIONS))
        ],
        cfg,
    )
    fig, axes = plt.subplots(
        1, len(keys), figsize=(6.5 * len(keys), 5.3), squeeze=False, sharey=True
    )
    for ax, key in zip(axes.ravel(), keys):
        plotted_factors = []
        for condition in TEST_CONDITIONS:
            rows = subset[
                (subset["key"] == key) & (subset["condition"] == condition)
            ].sort_values("factor")
            if not len(rows):
                continue
            plotted_factors.extend(rows["factor"].tolist())
            colour, marker, label = _style(cfg, condition)
            ax.plot(
                rows["factor"],
                rows["gain_x"],
                color=colour,
                marker=marker,
                lw=1.25,
                ms=6,
                label=label,
            )
            uncertain = rows[~rows["gain_trustworthy"]]
            ax.scatter(
                uncertain["factor"],
                uncertain["gain_x"],
                s=88,
                facecolor="none",
                edgecolor="0.2",
                linewidth=1.15,
                zorder=4,
            )
        ax.axhline(1, color="0.3", lw=1.0)
        _set_dilution_axis(ax, plotted_factors)
        ax.set_xlabel("CV dilution factor (×)")
        ax.set_title(cfg["panel_label"][key], fontsize=10, loc="left")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, frameon=False)
    axes.ravel()[0].set_ylabel(
        "Gain over CV only at %d cm$^{-1}$ (×)"
        % ALL_BANDS[band]["nominal"]
    )
    fig.suptitle(
        "%s - nanostar gain across the dilution ladder\n"
        "hollow rings = gain not established above noise"
        % _view_label(cfg, paper),
        fontsize=12,
    )
    _save(fig, path, cfg, rect=(0, 0, 1, 0.91))


def particle_intensity_comparison(bands, cfg, paper, path):
    """Compare particles only where both have a measured dilution."""
    try:
        spec = cfg["plotting"]["particle_comparison"][paper]
    except KeyError as exc:
        raise SystemExit(
            "[error] plotting.particle_comparison.%s is required" % paper
        ) from exc

    selections = (
        ("stars", spec["nanostars_key"], "Stock nanostars"),
        ("bp", spec["bipyramids_key"], "Bipyramids"),
    )
    selected_rows = {}
    for condition, key, label in selections:
        rows = _keep_active(
            bands[
                (bands["key"] == key)
                & (bands["condition"] == condition)
                & (bands["peak_name"] == cfg["sers_band"])
            ],
            cfg,
        ).sort_values("factor")
        if not len(rows):
            raise SystemExit(
                "[error] no 1620 data for %s/%s/%s" % (paper, key, condition)
            )
        time_column = next(
            (
                name
                for name in ("int_time_s", "int_time_s_x", "int_time_s_y")
                if name in rows.columns
            ),
            None,
        )
        if time_column is None:
            raise SystemExit("[error] band table has no integration-time column")
        exposures = set(rows[time_column].astype(float))
        if exposures != {float(spec["exposure_s"])}:
            raise SystemExit(
                "[error] %s/%s exposure mismatch: expected %s, found %s"
                % (paper, key, spec["exposure_s"], sorted(exposures))
            )
        selected_rows[condition] = rows

    shared = sorted(
        set(selected_rows["stars"]["dilution"])
        & set(selected_rows["bp"]["dilution"]),
        key=lambda value: float(str(value).rstrip("x")),
    )
    if not shared:
        raise SystemExit(
            "[error] %s has no shared bipyramid/nanostar dilutions" % paper
        )

    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    plotted_factors = []
    for condition, _, label in selections:
        rows = selected_rows[condition]
        rows = rows[rows["dilution"].isin(shared)].sort_values("factor")
        plotted_factors.extend(rows["factor"].tolist())
        colour, marker, _ = _style(cfg, condition)
        usable = rows[rows["verdict"] != "fail"]
        failed = rows[rows["verdict"] == "fail"]
        ax.plot(
            usable["factor"],
            usable["height_cps"],
            color=colour,
            marker=marker,
            lw=1.5,
            ms=7,
            label=label,
        )
        for _, row in usable.iterrows():
            ax.annotate(
                "%.1f" % row["height_cps"],
                (row["factor"], row["height_cps"]),
                xytext=(0, 7),
                textcoords="offset points",
                ha="center",
                fontsize=7,
                color=colour,
            )
        if len(failed):
            ax.scatter(
                failed["factor"],
                failed["height_cps"],
                marker=marker,
                s=72,
                facecolor="none",
                edgecolor=colour,
                linewidth=1.3,
                label=label + " (failed saturation screen)",
            )

    ax.axhline(0, color="0.3", lw=0.8)
    _set_dilution_axis(ax, plotted_factors)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("CV dilution factor (×)")
    ax.set_ylabel(
        "Baseline-corrected %d cm$^{-1}$ peak height (counts s$^{-1}$)"
        % ALL_BANDS[cfg["sers_band"]]["nominal"]
    )
    ax.set_title(
        "%s paper - bipyramids vs stock nanostars at %d cm$^{-1}$\n"
        "matched %.0f s exposure; shared dilutions only; labels give counts s$^{-1}$"
        % (
            paper.capitalize(),
            ALL_BANDS[cfg["sers_band"]]["nominal"],
            float(spec["exposure_s"]),
        ),
        loc="left",
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(alpha=0.28)
    fig.text(
        0.5,
        0.01,
        "Only dilutions measured for both particles are shown. Stock nanostars "
        "are used so particle shape is not confounded with NP dilution.",
        ha="center",
        fontsize=8,
        color="0.35",
    )
    _save(fig, path, cfg, rect=(0, 0.035, 1, 1))


def particle_spectrum_comparison(scans, bands, cfg, paper, path):
    """Full spectra plus a 1620 zoom at every shared, usable dilution."""
    spec = cfg["plotting"]["particle_comparison"][paper]
    selections = (
        ("stars", spec["nanostars_key"], "Stock nanostars"),
        ("bp", spec["bipyramids_key"], "Bipyramids"),
    )
    band = cfg["sers_band"]
    metrics = {}
    for condition, key, _ in selections:
        rows = _keep_active(
            bands[
                (bands["key"] == key)
                & (bands["condition"] == condition)
                & (bands["peak_name"] == band)
                & (bands["verdict"] != "fail")
            ],
            cfg,
        ).copy()
        time_column = next(
            (
                name
                for name in ("int_time_s", "int_time_s_x", "int_time_s_y")
                if name in rows.columns
            ),
            None,
        )
        if time_column is None:
            raise SystemExit("[error] band table has no integration-time column")
        exposures = set(rows[time_column].astype(float))
        if exposures != {float(spec["exposure_s"])}:
            raise SystemExit(
                "[error] %s/%s exposure mismatch: expected %s, found %s"
                % (paper, key, spec["exposure_s"], sorted(exposures))
            )
        metrics[condition] = rows.set_index("dilution")
    shared = sorted(
        set(metrics["stars"].index) & set(metrics["bp"].index),
        key=lambda value: float(str(value).rstrip("x")),
    )
    if not shared:
        raise SystemExit(
            "[error] %s has no shared usable bipyramid/nanostar dilutions" % paper
        )

    by_scan = {r["scan_number"]: r for r in scans}
    fig, axes = plt.subplots(
        2,
        len(shared),
        figsize=(5.2 * len(shared), 8.0),
        squeeze=False,
        sharey="row",
    )
    window = ALL_BANDS[band]["lit"]
    nominal = ALL_BANDS[band]["nominal"]
    for column, dilution in enumerate(shared):
        title_parts = []
        for condition, _, label in selections:
            row = metrics[condition].loc[dilution]
            rec = by_scan[int(row["scan_number"])]
            colour, _, _ = _style(cfg, condition)
            axes[0, column].plot(
                rec["x_fit"], rec["smooth_cps"], color=colour, lw=1.15, label=label
            )
            selected = (rec["x_fit"] >= nominal - 70) & (
                rec["x_fit"] <= nominal + 45
            )
            axes[1, column].plot(
                rec["x_fit"][selected],
                rec["smooth_cps"][selected],
                color=colour,
                lw=1.45,
                label=label,
            )
            short = "Stars" if condition == "stars" else "BP"
            title_parts.append("%s SNR %.1f" % (short, row["snr"]))

        _mark_bands(axes[0, column], cfg)
        axes[0, column].axhline(0, color="0.35", lw=0.55)
        axes[0, column].set_xlim(400, 1800)
        axes[0, column].set_title(
            "%s CV\n%s" % (dilution, " | ".join(title_parts)), fontsize=10
        )
        axes[0, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[0, column].grid(alpha=0.23)

        axes[1, column].axvspan(
            window[0], window[1], color="tab:green", alpha=0.14, lw=0
        )
        axes[1, column].axhline(0, color="0.35", lw=0.55)
        axes[1, column].set_xlim(nominal - 70, nominal + 45)
        axes[1, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[1, column].set_title(
            "%d cm$^{-1}$ window (shared y-scale)" % nominal, fontsize=9
        )
        axes[1, column].grid(alpha=0.25)

    axes[0, 0].set_ylabel("Full spectrum\ncorrected counts s$^{-1}$")
    axes[1, 0].set_ylabel(
        "%d cm$^{-1}$ zoom\ncorrected counts s$^{-1}$" % nominal
    )
    axes[0, 0].legend(fontsize=8, frameon=False)
    fig.suptitle(
        "%s paper - bipyramid versus stock-nanostar spectra\n"
        "matched %.0f s exposure; only dilutions measured for both particles"
        % (paper.capitalize(), float(spec["exposure_s"])),
        fontsize=12,
    )
    _save(fig, path, cfg, rect=(0, 0, 1, 0.93))


# ---------------------------------------------------------------------------
# Dilution comparisons
#
# Three readings of one matched block - one paper, one exposure - kept apart
# because they answer different questions:
#   11  one panel per nanoparticle, that arm's own dye ladder inside it:
#       how the signal falls as the dye is diluted, particle by particle
#   12  one panel per dilution, the nanoparticles inside it: which particle
#       leads at that dilution
#   13  the same block as 1620 cm-1 peak heights, so 11 and 12 can be checked
#       against a number rather than an eyeball
# 12 and 13 use only the dilutions every arm actually measured, because the
# bipyramid ladder (2x, 10x, 20x) and the nanostar ladder (2x, 5x, 10x, ...)
# are not the same ladder. Nothing is compared across papers or exposures.
# ---------------------------------------------------------------------------
DILUTION_CONDITIONS = ("stars", "stars5x", "bp", "dyeonly")


def _factor(dilution):
    return float(str(dilution).rstrip("x"))


def _dilution_label(factor):
    return ("%d" % factor if float(factor).is_integer() else "%g" % factor) + "x"


def _comparison_spec(cfg, paper):
    try:
        return cfg["plotting"]["particle_comparison"][paper]
    except KeyError as exc:
        raise SystemExit(
            "[error] plotting.particle_comparison.%s is required" % paper
        ) from exc


def _dilution_block(screen, cfg, paper):
    """Screened rows per condition, all from this paper's matched exposure.

    The dye-only arm is taken from the nanostar panel rather than the bipyramid
    one: both papers carry two dye-only ladders, and mixing them would put a
    different print behind the control at different dilutions.
    """
    spec = _comparison_spec(cfg, paper)
    key_of = {
        "stars": spec["nanostars_key"],
        "stars5x": spec["nanostars_key"],
        "dyeonly": spec["nanostars_key"],
        "bp": spec["bipyramids_key"],
    }
    block = {}
    for condition in DILUTION_CONDITIONS:
        rows = _keep_active(
            screen[
                (screen["key"] == key_of[condition])
                & (screen["condition"] == condition)
            ],
            cfg,
        ).copy()
        if not len(rows):
            raise SystemExit(
                "[error] %s: panel %s carries no %s scans"
                % (paper, key_of[condition], condition)
            )
        exposures = set(rows["int_time_s"].astype(float))
        if exposures != {float(spec["exposure_s"])}:
            raise SystemExit(
                "[error] %s/%s exposure mismatch: expected %s, found %s"
                % (paper, key_of[condition], spec["exposure_s"], sorted(exposures))
            )
        block[condition] = rows.sort_values("factor").reset_index(drop=True)
    return block


def _shared_dilutions(block, cfg, paper):
    """Dilutions measured on every arm - the only ones the particles share."""
    override = _comparison_spec(cfg, paper).get("dilutions")
    if override:
        shared = list(override)
        for condition, rows in block.items():
            missing = [d for d in shared if d not in set(rows["dilution"])]
            if missing:
                raise SystemExit(
                    "[error] %s/%s does not cover configured dilutions %s"
                    % (paper, condition, missing)
                )
    else:
        common = None
        for rows in block.values():
            values = set(rows["dilution"])
            common = values if common is None else common & values
        shared = list(common or [])
    if not shared:
        raise SystemExit("[error] %s: the arms share no dilution" % paper)
    return sorted(shared, key=_factor)


def _dilution_colours(values):
    """One colour per dilution, dark = concentrated, identical in every panel."""
    ordered = sorted({_factor(v) for v in values})
    cmap = plt.get_cmap("viridis")
    stops = np.linspace(0.08, 0.78, len(ordered)) if len(ordered) > 1 else [0.35]
    return {f: cmap(p) for f, p in zip(ordered, stops)}


def _block_heading(cfg, paper, what):
    spec = _comparison_spec(cfg, paper)
    return "%s paper, %.0f s exposure - %s" % (
        paper.capitalize(), float(spec["exposure_s"]), what
    )


def _usable(rows):
    """Drop scans that failed the saturation screen.

    They are dropped from the spectra rather than drawn dashed: a saturated
    trace on this instrument is a large ringing artefact, and on a shared y
    axis it flattens every real spectrum next to it. Figure 13 keeps them,
    hollow, so the omission is still visible somewhere.
    """
    return rows[rows["verdict"] != "fail"]


def _dropped_note(fig, dropped):
    if not dropped:
        return
    fig.text(
        0.5, 0.005,
        "%d saturated scan(s) omitted: %s"
        % (len(dropped), ", ".join(sorted(dropped))),
        ha="center", fontsize=8, color="0.4",
    )


def dilution_series_spectra(scans, screen, cfg, paper, path):
    """Figure 11 - each nanoparticle's own dye ladder, one panel per particle."""
    block = _dilution_block(screen, cfg, paper)
    by_scan = {r["scan_number"]: r for r in scans}
    colours = _dilution_colours(
        [d for rows in block.values() for d in rows["dilution"]]
    )

    band = ALL_BANDS[cfg["sers_band"]]
    nominal = band["nominal"]
    fig, axes = plt.subplots(
        2,
        len(DILUTION_CONDITIONS),
        figsize=(4.5 * len(DILUTION_CONDITIONS), 7.6),
        squeeze=False,
        sharey="row",
    )
    dropped = set()
    for column, condition in enumerate(DILUTION_CONDITIONS):
        _, _, label = _style(cfg, condition)
        rows = block[condition]
        dropped |= {"%s %s" % (label, d)
                    for d in rows.loc[rows["verdict"] == "fail", "dilution"]}
        for _, row in _usable(rows).iterrows():
            rec = by_scan[int(row["scan_number"])]
            colour = colours[_factor(row["dilution"])]
            axes[0, column].plot(rec["x_fit"], rec["smooth_cps"], color=colour, lw=1.1)
            window = (rec["x_fit"] >= nominal - 70) & (rec["x_fit"] <= nominal + 45)
            axes[1, column].plot(
                rec["x_fit"][window], rec["smooth_cps"][window], color=colour, lw=1.4
            )
        _mark_bands(axes[0, column], cfg)
        axes[0, column].axhline(0, color="0.35", lw=0.6)
        axes[0, column].set_xlim(400, 1800)
        axes[0, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[0, column].set_title(label, fontsize=11)
        axes[0, column].grid(alpha=0.22)

        axes[1, column].axvspan(
            band["lit"][0], band["lit"][1], color="tab:green", alpha=0.12, lw=0
        )
        axes[1, column].axhline(0, color="0.35", lw=0.6)
        axes[1, column].set_xlim(nominal - 70, nominal + 45)
        axes[1, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[1, column].grid(alpha=0.24)
    axes[0, 0].set_ylabel("Full spectrum, corrected counts s$^{-1}$")
    axes[1, 0].set_ylabel("%d cm$^{-1}$ band, corrected counts s$^{-1}$" % nominal)

    handles = [
        Line2D([], [], color=colour, lw=1.7, label=_dilution_label(factor))
        for factor, colour in colours.items()
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.022),
        ncol=len(handles),
        frameon=False,
        fontsize=9,
        title="CV dilution",
        title_fontsize=9,
    )
    fig.suptitle(
        _block_heading(cfg, paper, "CV dilution series for each nanoparticle"),
        fontsize=12,
    )
    _dropped_note(fig, dropped)
    _save(fig, path, cfg, rect=(0, 0.09, 1, 0.94))


def particles_at_each_dilution(scans, screen, cfg, paper, path):
    """Figure 12 - the nanoparticles side by side at each shared dilution."""
    block = _dilution_block(screen, cfg, paper)
    shared = _shared_dilutions(block, cfg, paper)
    by_scan = {r["scan_number"]: r for r in scans}
    band = ALL_BANDS[cfg["sers_band"]]
    nominal = band["nominal"]

    fig, axes = plt.subplots(
        2,
        len(shared),
        figsize=(4.9 * len(shared), 7.8),
        squeeze=False,
        sharey="row",
    )
    dropped, drawn = set(), []
    for column, dilution in enumerate(shared):
        for condition in DILUTION_CONDITIONS:
            rows = block[condition]
            row = rows[rows["dilution"] == dilution].iloc[0]
            colour, _, label = _style(cfg, condition)
            if row["verdict"] == "fail":
                dropped.add("%s %s" % (label, dilution))
                continue
            if condition not in drawn:
                drawn.append(condition)
            rec = by_scan[int(row["scan_number"])]
            axes[0, column].plot(
                rec["x_fit"], rec["smooth_cps"], color=colour, lw=1.15
            )
            window = (rec["x_fit"] >= nominal - 70) & (rec["x_fit"] <= nominal + 45)
            axes[1, column].plot(
                rec["x_fit"][window],
                rec["smooth_cps"][window],
                color=colour,
                lw=1.45,
            )
        _mark_bands(axes[0, column], cfg)
        axes[0, column].axhline(0, color="0.35", lw=0.6)
        axes[0, column].set_xlim(400, 1800)
        axes[0, column].set_title("%s CV" % dilution, fontsize=11)
        axes[0, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[0, column].grid(alpha=0.22)

        axes[1, column].axvspan(
            band["lit"][0], band["lit"][1], color="tab:green", alpha=0.12, lw=0
        )
        axes[1, column].axhline(0, color="0.35", lw=0.6)
        axes[1, column].set_xlim(nominal - 70, nominal + 45)
        axes[1, column].set_xlabel("Raman shift (cm$^{-1}$)")
        axes[1, column].grid(alpha=0.24)

    axes[0, 0].set_ylabel("Full spectrum, corrected counts s$^{-1}$")
    axes[1, 0].set_ylabel("%d cm$^{-1}$ band, corrected counts s$^{-1}$" % nominal)
    handles = [
        Line2D([], [], color=_style(cfg, c)[0], lw=1.7, label=_style(cfg, c)[2])
        for c in DILUTION_CONDITIONS if c in drawn
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.022),
        ncol=len(handles),
        frameon=False,
        fontsize=9,
    )
    fig.suptitle(
        _block_heading(cfg, paper, "nanoparticles compared at each shared dilution"),
        fontsize=12,
    )
    _dropped_note(fig, dropped)
    _save(fig, path, cfg, rect=(0, 0.07, 1, 0.94))


def dilution_intensity_bars(bands, screen, cfg, paper, path):
    """Figure 13 - 1620 cm-1 height, grouped by dilution and nanoparticle."""
    block = _dilution_block(screen, cfg, paper)
    shared = _shared_dilutions(block, cfg, paper)
    heights = (
        bands[bands["peak_name"] == cfg["sers_band"]]
        .set_index("scan_number")["height_cps"]
        .to_dict()
    )
    nominal = ALL_BANDS[cfg["sers_band"]]["nominal"]

    x = np.arange(len(shared), dtype=float)
    width = 0.8 / len(DILUTION_CONDITIONS)
    fig, ax = plt.subplots(figsize=(2.6 * len(shared) + 5.0, 5.8))
    # Plain bars, one per arm per dilution. The linearity caveat on the two
    # white 2x spots is carried in scan_screen.csv and in the v2 workbook
    # (`band_above_fit_ceiling`) rather than drawn here: on a chart this
    # compact, a second visual code costs more clarity than it buys.
    for i, condition in enumerate(DILUTION_CONDITIONS):
        rows = block[condition].set_index("dilution")
        colour, _, label = _style(cfg, condition)
        offsets = x + i * width - 0.4 + width / 2
        values = np.asarray(
            [heights.get(int(rows.loc[d, "scan_number"]), np.nan) for d in shared],
            dtype=float,
        )
        ax.bar(
            offsets,
            values,
            width,
            color=colour,
            edgecolor="white",
            linewidth=0.5,
            label=label,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(shared)
    ax.set_xlabel("CV dilution")
    ax.set_ylabel(
        "Baseline-corrected %d cm$^{-1}$ peak height (counts s$^{-1}$)" % nominal
    )
    ax.legend(fontsize=9, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_title(
        _block_heading(
            cfg, paper, "%d cm$^{-1}$ CV band by dilution and nanoparticle" % nominal
        ),
        loc="left",
    )
    _save(fig, path, cfg)


def generate(scans, screen, bands, gain, cfg, figure_dir):
    for paper in ("offwhite", "white"):
        out = Path(figure_dir) / _focus_spec(cfg, paper)["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        raw_vs_corrected(
            scans, screen, cfg, paper, out / "01_raw_vs_baseline_corrected_scale.png"
        )
        focus_spectra(
            scans, screen, cfg, paper, out / "02_baseline_corrected_spectra.png"
        )
        focus_spectra_with_bipyramids(
            scans,
            screen,
            cfg,
            paper,
            out / "02b_baseline_corrected_spectra_with_bipyramids.png",
        )
        band_windows(
            scans, screen, cfg, paper, out / "03_1620_band_zoom.png"
        )
        band_intensity(
            bands, screen, cfg, paper, out / "04_1620_intensity_by_condition.png"
        )
        difference_spectra(
            scans, screen, cfg, paper, out / "05_1620_difference_vs_cv_reference.png"
        )
        background_and_time(
            scans,
            screen,
            cfg,
            paper,
            out / "06_1620_background_and_integration_time.png",
        )
        corrected_band_gain(
            gain, cfg, paper, out / "07_1620_gain_at_focus_dilution.png"
        )
        gain_ladder(
            gain, screen, cfg, paper, out / "08_1620_gain_vs_dilution.png"
        )
        particle_intensity_comparison(
            bands,
            cfg,
            paper,
            out / "09_1620_bipyramids_vs_nanostars.png",
        )
        particle_spectrum_comparison(
            scans,
            bands,
            cfg,
            paper,
            out / "10_bipyramids_vs_nanostars_spectra.png",
        )
        dilution_series_spectra(
            scans, screen, cfg, paper, out / "11_dilution_series_by_nanoparticle.png"
        )
        particles_at_each_dilution(
            scans, screen, cfg, paper, out / "12_nanoparticles_at_each_dilution.png"
        )
        dilution_intensity_bars(
            bands, screen, cfg, paper, out / "13_1620_intensity_by_dilution.png"
        )


def remove_obsolete_non1620(figure_dir):
    """Remove generated dashboards superseded by the 1620-only figure set."""
    figure_dir = Path(figure_dir)
    legacy = figure_dir / "combined_legacy"
    for name in COMBINED_FIGURES:
        source = figure_dir / name
        if source.is_file():
            source.unlink()
        archived = legacy / name
        if archived.is_file():
            archived.unlink()
    for paper in ("offwhite", "white"):
        for name in OBSOLETE_PAPER_FIGURES:
            source = figure_dir / paper / name
            if source.is_file():
                source.unlink()
        old_dir = figure_dir / paper
        if old_dir.is_dir():
            shutil.rmtree(old_dir)
    if legacy.is_dir() and not any(legacy.iterdir()):
        legacy.rmdir()
