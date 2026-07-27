"""Config loading, defaulting and validation.

A config is a plain dict loaded from YAML. This module fills in defaults so
the rest of the pipeline can rely on every key existing, and validates the
few things that would otherwise fail deep inside numpy/scipy with a cryptic
error.

Design rule: the *only* place analysis parameters live is the YAML file.
This module never invents peak positions or thresholds - it only supplies
structural defaults (e.g. "smoothing is on unless you say otherwise").
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Structural defaults. These are deliberately conservative and generic; the
# scientifically meaningful values (peak centers, thresholds) must come from
# the user's config so they are never silently wrong.
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "name": "unnamed",
    "description": "",
    "plate": None,                   # optional default plate name/path (CLI --plate overrides)
    "io": {
        "input_dir": "raw",
        "output_dir": "results",
        "file_glob": "*.csv",
        "csv": {
            "has_header": None,      # None => auto-detect
            "wavelength_col": 0,
            "intensity_col": 1,
            "delimiter": ",",
            "comment": None,
        },
    },
    "analysis_range": {
        "enabled": False,
        "min": None,
        "max": None,
    },
    "preprocessing": {
        "baseline": {
            "method": "arpls",       # arpls | als | poly | rolling_min | none
            "arpls": {"lam": 1.0e5, "ratio": 1.0e-6, "niter": 50},
            "als": {"lam": 1.0e5, "p": 0.01, "niter": 10},
            "poly": {"order": 3},
            "rolling_min": {"window": 151},
        },
        "smoothing": {
            "method": "savgol",      # savgol | none
            "savgol": {"window": 11, "polyorder": 3},
        },
        "normalize": {
            "method": "none",        # none | max | area | minmax | snv
        },
    },
    "noise": {
        "method": "mad_derivative",  # mad_derivative | region
        "region": [None, None],
    },
    "detection": {
        "mode": "targeted",          # targeted | global
        "min_height": None,
        "min_prominence": None,
        "min_snr": None,
        "min_width": None,
        "max_width": None,
        "distance": 1,
        "global_max_peaks": None,    # cap number of peaks reported in global mode
        "default_window": 20.0,      # +/- cm^-1 used when a peak gives center but no window
        "area_fwhm_multiplier": 2.0, # integrate area over center +/- this * FWHM
    },
    "fitting": {
        "enabled": True,
        "model": "gaussian",         # gaussian | lorentzian | voigt | none
    },
    "peaks": [],                     # list of {name, center, window|search_min/max, ...}
    "plotting": {
        "overview": True,
        "zoom": True,
        "dpi": 150,
        "figsize": [12, 6],
        "show_baseline": True,
        "show_raw": True,
        "annotate": True,
    },
}

_BASELINE_METHODS = {"arpls", "als", "poly", "rolling_min", "none"}
_SMOOTH_METHODS = {"savgol", "none"}
_NORMALIZE_METHODS = {"none", "max", "area", "minmax", "snv"}
_NOISE_METHODS = {"mad_derivative", "region"}
_DETECTION_MODES = {"targeted", "global"}
_FIT_MODELS = {"gaussian", "lorentzian", "voigt", "none"}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base."""
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


class ConfigError(ValueError):
    """Raised when a config is structurally invalid."""


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config, merge over defaults, and validate it."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")

    cfg = _deep_merge(DEFAULTS, raw)
    cfg["_source_path"] = str(path.resolve())
    _validate(cfg)
    return cfg


def _validate(cfg: dict[str, Any]) -> None:
    base = cfg["preprocessing"]["baseline"]["method"]
    if base not in _BASELINE_METHODS:
        raise ConfigError(f"preprocessing.baseline.method={base!r} not in {sorted(_BASELINE_METHODS)}")

    smooth = cfg["preprocessing"]["smoothing"]["method"]
    if smooth not in _SMOOTH_METHODS:
        raise ConfigError(f"preprocessing.smoothing.method={smooth!r} not in {sorted(_SMOOTH_METHODS)}")
    if smooth == "savgol":
        sg = cfg["preprocessing"]["smoothing"]["savgol"]
        if sg["window"] % 2 == 0 or sg["window"] < 3:
            raise ConfigError("savgol.window must be an odd integer >= 3")
        if sg["polyorder"] >= sg["window"]:
            raise ConfigError("savgol.polyorder must be < savgol.window")

    norm = cfg["preprocessing"]["normalize"]["method"]
    if norm not in _NORMALIZE_METHODS:
        raise ConfigError(f"normalize.method={norm!r} not in {sorted(_NORMALIZE_METHODS)}")

    if cfg["noise"]["method"] not in _NOISE_METHODS:
        raise ConfigError(f"noise.method not in {sorted(_NOISE_METHODS)}")

    mode = cfg["detection"]["mode"]
    if mode not in _DETECTION_MODES:
        raise ConfigError(f"detection.mode={mode!r} not in {sorted(_DETECTION_MODES)}")

    model = cfg["fitting"]["model"]
    if model not in _FIT_MODELS:
        raise ConfigError(f"fitting.model={model!r} not in {sorted(_FIT_MODELS)}")

    if mode == "targeted":
        peaks = cfg.get("peaks") or []
        if not peaks:
            raise ConfigError(
                "detection.mode='targeted' requires a non-empty 'peaks:' list. "
                "Add at least one peak with a 'center' (and 'window' or search_min/search_max)."
            )
        seen = set()
        for i, pk in enumerate(peaks):
            if "center" not in pk and not ("search_min" in pk and "search_max" in pk):
                raise ConfigError(
                    f"peaks[{i}] needs either 'center' (+optional 'window') "
                    f"or both 'search_min' and 'search_max'."
                )
            name = pk.get("name", f"peak_{i}")
            if name in seen:
                raise ConfigError(f"Duplicate peak name {name!r} in peaks list.")
            seen.add(name)


def peak_search_bounds(peak: dict[str, Any], default_window: float = 20.0) -> tuple[float, float]:
    """Return (search_min, search_max) in cm^-1 for a targeted peak entry."""
    if "search_min" in peak and "search_max" in peak:
        return float(peak["search_min"]), float(peak["search_max"])
    center = float(peak["center"])
    window = float(peak.get("window", default_window))
    return center - window, center + window
