"""Peak detection, curve fitting, and per-peak metrics.

Two detection modes (chosen in config.detection.mode):

  targeted : for each peak listed in config.peaks, search a window around its
             center and characterize the strongest peak there. Best when you
             know roughly where your bands are (e.g. 1080 +/- 20).

  global   : run scipy.find_peaks across the whole spectrum with the configured
             thresholds and report everything above them. Best when you don't
             know how many peaks there are.

Reported metrics per peak: raw center (argmax), fitted center, height above
baseline, prominence, FWHM (from fit or half-max interpolation), integrated
area, and SNR (height / noise sigma).
"""
from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit
from scipy.signal import find_peaks, peak_prominences, peak_widths
from scipy.special import wofz

from .config import peak_search_bounds
from .preprocessing import integrate


# ---------------------------------------------------------------------------
# Peak shape models (for optional fitting)
# ---------------------------------------------------------------------------
def gaussian(x, amp, cen, sigma, offset):
    return amp * np.exp(-((x - cen) ** 2) / (2 * sigma ** 2)) + offset


def lorentzian(x, amp, cen, gamma, offset):
    return amp * (gamma ** 2 / ((x - cen) ** 2 + gamma ** 2)) + offset


def voigt(x, amp, cen, sigma, gamma, offset):
    z = ((x - cen) + 1j * gamma) / (sigma * np.sqrt(2))
    return amp * np.real(wofz(z)) / (sigma * np.sqrt(2 * np.pi)) + offset


def _fwhm_from_fit(model: str, popt: np.ndarray) -> float:
    if model == "gaussian":
        return float(2.0 * np.sqrt(2.0 * np.log(2.0)) * abs(popt[2]))
    if model == "lorentzian":
        return float(2.0 * abs(popt[2]))
    if model == "voigt":
        fg = 2.0 * np.sqrt(2.0 * np.log(2.0)) * abs(popt[2])
        fl = 2.0 * abs(popt[3])
        return float(0.5346 * fl + np.sqrt(0.2166 * fl ** 2 + fg ** 2))
    return float("nan")


def fit_peak(x: np.ndarray, y: np.ndarray, cen_guess: float, model: str) -> dict[str, Any] | None:
    """Fit a single peak on a local window. Returns fit params or None on failure."""
    if model == "none" or x.size < 5:
        return None
    amp0 = float(np.max(y) - np.min(y))
    off0 = float(np.min(y))
    span = float(x.max() - x.min())
    width0 = max(span / 6.0, 1e-3)
    try:
      with warnings.catch_warnings():
        # Fitting near-noise windows can emit a benign covariance warning.
        warnings.simplefilter("ignore", OptimizeWarning)
        if model == "gaussian":
            p0 = [amp0, cen_guess, width0, off0]
            popt, _ = curve_fit(gaussian, x, y, p0=p0, maxfev=10000)
            fitted = gaussian(x, *popt)
        elif model == "lorentzian":
            p0 = [amp0, cen_guess, width0, off0]
            popt, _ = curve_fit(lorentzian, x, y, p0=p0, maxfev=10000)
            fitted = lorentzian(x, *popt)
        elif model == "voigt":
            p0 = [amp0, cen_guess, width0, width0, off0]
            popt, _ = curve_fit(voigt, x, y, p0=p0, maxfev=10000)
            fitted = voigt(x, *popt)
        else:
            return None
    except (RuntimeError, ValueError):
        return None

    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1.0
    return {
        "fit_center_cm1": float(popt[1]),
        "fit_amplitude": float(popt[0]),
        "fit_fwhm_cm1": _fwhm_from_fit(model, popt),
        "fit_r2": 1.0 - ss_res / ss_tot,
        "fit_model": model,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _interp_fwhm(x: np.ndarray, y: np.ndarray, peak_idx: int) -> float:
    """Half-max width by linear interpolation around a local peak index."""
    peak_h = y[peak_idx]
    half = peak_h / 2.0
    # walk left
    li = peak_idx
    while li > 0 and y[li] > half:
        li -= 1
    ri = peak_idx
    while ri < y.size - 1 and y[ri] > half:
        ri += 1
    if li == ri:
        return float("nan")
    xl = np.interp(half, [y[li], y[li + 1]], [x[li], x[li + 1]]) if li + 1 <= peak_idx else x[peak_idx]
    xr = np.interp(half, [y[ri], y[ri - 1]], [x[ri], x[ri - 1]]) if ri - 1 >= peak_idx else x[peak_idx]
    return float(abs(xr - xl))


def _characterize(x, y_corr, idx, noise, fit_model, area_multiplier=2.0):
    """Build a metrics dict for a peak located at index idx in the corrected signal."""
    height = float(y_corr[idx])
    prom = float(peak_prominences(y_corr, [idx])[0][0])
    try:
        w_res = peak_widths(y_corr, [idx], rel_height=0.5)
        fwhm_pts = float(w_res[0][0])
        dx = float(np.median(np.diff(x)))
        fwhm_interp = fwhm_pts * dx
    except Exception:
        fwhm_interp = _interp_fwhm(x, y_corr, idx)

    # local area over center +/- area_multiplier * FWHM (config-controlled)
    area_halfwidth_pts = max(
        int(round((fwhm_interp / max(np.median(np.diff(x)), 1e-9)) * float(area_multiplier))), 3)
    lo = max(0, idx - area_halfwidth_pts)
    hi = min(x.size, idx + area_halfwidth_pts + 1)
    area = integrate(x[lo:hi], np.clip(y_corr[lo:hi], 0, None))

    metrics = {
        "center_cm1": float(x[idx]),
        "index": int(idx),
        "height": height,
        "prominence": prom,
        "fwhm_cm1": float(fwhm_interp),
        "area": float(area),
        "snr": float(height / noise) if noise else float("nan"),
    }
    # optional fit on local window
    lo_f = max(0, idx - area_halfwidth_pts)
    hi_f = min(x.size, idx + area_halfwidth_pts + 1)
    fit = fit_peak(x[lo_f:hi_f], y_corr[lo_f:hi_f], float(x[idx]), fit_model)
    if fit:
        metrics.update(fit)
    return metrics


def _passes_thresholds(m: dict[str, Any], det: dict[str, Any]) -> bool:
    if det.get("min_height") is not None and m["height"] < det["min_height"]:
        return False
    if det.get("min_prominence") is not None and m["prominence"] < det["min_prominence"]:
        return False
    if det.get("min_snr") is not None and (np.isnan(m["snr"]) or m["snr"] < det["min_snr"]):
        return False
    if det.get("min_width") is not None and m["fwhm_cm1"] < det["min_width"]:
        return False
    if det.get("max_width") is not None and m["fwhm_cm1"] > det["max_width"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Public detection entry points
# ---------------------------------------------------------------------------
def detect_targeted(x, y_corr, noise, cfg) -> list[dict[str, Any]]:
    """For each configured peak, find & characterize the strongest peak in its window."""
    det = cfg["detection"]
    fit_model = cfg["fitting"]["model"] if cfg["fitting"]["enabled"] else "none"
    area_mult = det.get("area_fwhm_multiplier", 2.0)
    default_window = det.get("default_window", 20.0)
    results: list[dict[str, Any]] = []

    for i, pk in enumerate(cfg["peaks"]):
        name = pk.get("name", f"peak_{i}")
        lo, hi = peak_search_bounds(pk, default_window=default_window)
        mask = (x >= lo) & (x <= hi)
        rec: dict[str, Any] = {
            "name": name,
            "target_center_cm1": pk.get("center"),
            "search_min_cm1": lo,
            "search_max_cm1": hi,
            "primary": bool(pk.get("primary", False)),
        }
        if mask.sum() < 3:
            rec.update({"found": False, "reason": "search window has < 3 points"})
            results.append(rec)
            continue

        idxs = np.where(mask)[0]
        local = y_corr[idxs]
        # Prefer a genuine interior local maximum. Falling back to a bare argmax
        # would happily report a window edge on a monotonic slope (e.g. when the
        # band has drifted out of the window, or isn't present) as a "peak".
        loc_peaks, _ = find_peaks(local)
        if loc_peaks.size:
            best_local = int(loc_peaks[int(np.argmax(local[loc_peaks]))])
            at_edge = False
        else:
            best_local = int(np.argmax(local))
            at_edge = best_local in (0, local.size - 1)
        idx = int(idxs[best_local])

        # per-peak threshold overrides fall back to global detection thresholds
        peak_det = {**det, **{k: pk[k] for k in
                    ("min_height", "min_prominence", "min_snr", "min_width", "max_width")
                    if k in pk}}
        m = _characterize(x, y_corr, idx, noise, fit_model, area_multiplier=area_mult)
        m.update(rec)
        m["at_window_edge"] = bool(at_edge)
        m["found"] = _passes_thresholds(m, peak_det)
        if not m["found"]:
            m["reason"] = "below thresholds"
        # shift from nominal center (useful for the 1080 that drifts 1070-1090).
        # Prefer the sub-pixel fitted center when a fit succeeded.
        if pk.get("center") is not None:
            observed = m.get("fit_center_cm1", m["center_cm1"])
            m["shift_from_target_cm1"] = float(observed - pk["center"])
        results.append(m)
    return results


def detect_global(x, y_corr, noise, cfg) -> list[dict[str, Any]]:
    """Find every peak above the configured thresholds across the whole spectrum."""
    det = cfg["detection"]
    fit_model = cfg["fitting"]["model"] if cfg["fitting"]["enabled"] else "none"
    area_mult = det.get("area_fwhm_multiplier", 2.0)
    dx = float(np.median(np.diff(x)))

    kwargs: dict[str, Any] = {}
    if det.get("min_height") is not None:
        kwargs["height"] = det["min_height"]
    if det.get("min_prominence") is not None:
        kwargs["prominence"] = det["min_prominence"]
    if det.get("distance") is not None:
        kwargs["distance"] = max(int(round(det["distance"] / dx)), 1) if det["distance"] else 1
    if det.get("min_width") is not None:
        kwargs["width"] = det["min_width"] / dx

    peak_idxs, _ = find_peaks(y_corr, **kwargs)
    results = []
    for n, idx in enumerate(peak_idxs):
        m = _characterize(x, y_corr, int(idx), noise, fit_model, area_multiplier=area_mult)
        m["name"] = f"peak_{n:03d}_{int(round(x[idx]))}"
        m["found"] = _passes_thresholds(m, det)
        results.append(m)

    results = [m for m in results if m["found"]]
    results.sort(key=lambda m: m["prominence"], reverse=True)
    cap = det.get("global_max_peaks")
    if cap:
        results = results[: int(cap)]
    results.sort(key=lambda m: m["center_cm1"])
    return results


def detect(x, y_corr, noise, cfg) -> list[dict[str, Any]]:
    if cfg["detection"]["mode"] == "targeted":
        return detect_targeted(x, y_corr, noise, cfg)
    return detect_global(x, y_corr, noise, cfg)
