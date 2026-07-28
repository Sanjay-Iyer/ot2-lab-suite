"""Spectral preprocessing: baseline correction, smoothing, normalization, noise.

Every routine takes explicit numeric parameters (passed in from the config by
process_raman.py) - there are no magic numbers baked in here.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve
from scipy.signal import savgol_filter

# numpy 2.x renamed trapz -> trapezoid; support both.
_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")


# ---------------------------------------------------------------------------
# Baseline correction
# ---------------------------------------------------------------------------
def baseline_arpls(y: np.ndarray, lam: float = 1e5, ratio: float = 1e-6,
                   niter: int = 50) -> np.ndarray:
    """Asymmetrically Reweighted Penalized Least Squares baseline (Baek 2015).

    Robust, parameter-light baseline that handles Raman fluorescence well.
    `lam` controls smoothness (bigger = stiffer baseline).
    """
    baseline, _ = baseline_arpls_with_diagnostics(y, lam, ratio, niter)
    return baseline


def baseline_arpls_with_diagnostics(
    y: np.ndarray,
    lam: float = 1e5,
    ratio: float = 1e-6,
    niter: int = 50,
) -> tuple[np.ndarray, dict[str, float | int | bool]]:
    """Return arPLS baseline plus convergence diagnostics."""
    y = np.asarray(y, dtype=float)
    lam, ratio, niter = float(lam), float(ratio), int(niter)
    n = y.size
    d = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(n, n - 2))
    h = lam * (d @ d.transpose())
    w = np.ones(n)
    converged = False
    relative_change = float("nan")
    iterations_used = 0
    z = y.copy()
    for iteration in range(niter):
        iterations_used = iteration + 1
        wmat = sparse.diags(w, 0, shape=(n, n))
        z = spsolve((wmat + h).tocsc(), w * y)
        dneg = y - z
        dn = dneg[dneg < 0]
        if dn.size == 0:
            converged = True
            relative_change = 0.0
            break
        m, s = np.mean(dn), np.std(dn)
        exponent = 2.0 * (dneg - (2 * s - m)) / (s + 1e-12)
        wt = 1.0 / (1.0 + np.exp(np.clip(exponent, -700.0, 700.0)))
        relative_change = float(
            np.linalg.norm(w - wt) / (np.linalg.norm(w) + 1e-12)
        )
        if relative_change < ratio:
            w = wt
            converged = True
            break
        w = wt
    diagnostics: dict[str, float | int | bool] = {
        "converged": converged,
        "iterations_used": iterations_used,
        "final_relative_weight_change": relative_change,
        "baseline_finite": bool(np.all(np.isfinite(z))),
    }
    return z, diagnostics


def baseline_als(y: np.ndarray, lam: float = 1e5, p: float = 0.01,
                 niter: int = 10) -> np.ndarray:
    """Asymmetric Least Squares baseline (Eilers & Boelens 2005)."""
    y = np.asarray(y, dtype=float)
    lam, p, niter = float(lam), float(p), int(niter)
    n = y.size
    d = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(n, n - 2))
    dd = lam * (d @ d.transpose())
    w = np.ones(n)
    z = y
    for _ in range(niter):
        wmat = sparse.diags(w, 0, shape=(n, n))
        z = spsolve((wmat + dd).tocsc(), w * y)
        w = p * (y > z) + (1 - p) * (y < z)
    return z


def baseline_poly(x: np.ndarray, y: np.ndarray, order: int = 3) -> np.ndarray:
    """Simple polynomial baseline fit over the whole spectrum."""
    coeffs = np.polyfit(x, y, int(order))
    return np.polyval(coeffs, x)


def baseline_rolling_min(y: np.ndarray, window: int = 151) -> np.ndarray:
    """Rolling-minimum baseline, smoothed. Cheap and dependency-free."""
    y = np.asarray(y, dtype=float)
    window = int(window)
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(y, half, mode="edge")
    mins = np.empty_like(y)
    for i in range(y.size):
        mins[i] = padded[i:i + window].min()
    # smooth the min envelope so it isn't jagged
    sm = min(window, (y.size // 2) * 2 - 1)
    if sm >= 5:
        mins = savgol_filter(mins, sm if sm % 2 else sm - 1, 2)
    return mins


def apply_baseline(x: np.ndarray, y: np.ndarray, cfg: dict) -> np.ndarray:
    """Dispatch to the configured baseline method. Returns the baseline array."""
    method = cfg["method"]
    if method == "none":
        return np.zeros_like(y)
    if method == "arpls":
        p = cfg["arpls"]
        return baseline_arpls(y, lam=p["lam"], ratio=p["ratio"], niter=p["niter"])
    if method == "als":
        p = cfg["als"]
        return baseline_als(y, lam=p["lam"], p=p["p"], niter=p["niter"])
    if method == "poly":
        return baseline_poly(x, y, order=cfg["poly"]["order"])
    if method == "rolling_min":
        return baseline_rolling_min(y, window=cfg["rolling_min"]["window"])
    raise ValueError(f"Unknown baseline method: {method}")


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------
def apply_smoothing(y: np.ndarray, cfg: dict) -> np.ndarray:
    method = cfg["method"]
    if method == "none":
        return np.asarray(y, dtype=float).copy()
    if method == "savgol":
        p = cfg["savgol"]
        window = int(p["window"])
        window = min(window, (y.size // 2) * 2 - 1)  # keep valid for short spectra
        if window < 5:
            return np.asarray(y, dtype=float).copy()
        if window % 2 == 0:
            window -= 1
        poly = min(int(p["polyorder"]), window - 1)
        return savgol_filter(y, window, poly)
    raise ValueError(f"Unknown smoothing method: {method}")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def apply_normalize(x: np.ndarray, y: np.ndarray, cfg: dict) -> tuple[np.ndarray, float]:
    """Return (normalized_y, scale_factor). scale_factor maps normalized->original."""
    method = cfg["method"]
    if method == "none":
        return y, 1.0
    if method == "max":
        peak = np.nanmax(y)
        return (y / peak, peak) if peak else (y, 1.0)
    if method == "minmax":
        lo, hi = np.nanmin(y), np.nanmax(y)
        rng = hi - lo
        return ((y - lo) / rng, rng) if rng else (y, 1.0)
    if method == "area":
        area = _trapz(np.abs(y), x)
        return (y / area, area) if area else (y, 1.0)
    if method == "snv":  # standard normal variate
        mu, sd = np.nanmean(y), np.nanstd(y)
        return ((y - mu) / sd, sd) if sd else (y, 1.0)
    raise ValueError(f"Unknown normalize method: {method}")


# ---------------------------------------------------------------------------
# Noise estimation (for SNR)
# ---------------------------------------------------------------------------
def estimate_noise(x: np.ndarray, y_corrected: np.ndarray, cfg: dict) -> float:
    """Estimate the noise standard deviation of a baseline-corrected spectrum.

    - mad_derivative: robust, peak-insensitive estimate from first differences.
      sigma = MAD(diff(y)) * 1.4826 / sqrt(2)
    - region: plain std over a user-specified signal-free window [min, max].
    """
    method = cfg["method"]
    if method == "region":
        lo, hi = cfg.get("region", [None, None])
        if lo is None or hi is None:
            raise ValueError("noise.method='region' requires noise.region: [min, max]")
        mask = (x >= lo) & (x <= hi)
        if mask.sum() < 3:
            raise ValueError(f"noise region [{lo},{hi}] contains too few points.")
        return float(np.std(y_corrected[mask]))
    # mad_derivative (default)
    d = np.diff(y_corrected)
    mad = np.median(np.abs(d - np.median(d)))
    sigma = mad * 1.4826 / np.sqrt(2.0)
    return float(sigma) if sigma > 0 else float(np.std(y_corrected) or 1.0)


def integrate(x: np.ndarray, y: np.ndarray) -> float:
    """Trapezoidal integral (numpy-version-safe)."""
    return float(_trapz(y, x))
