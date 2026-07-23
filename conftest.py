"""Repo-root pytest configuration.

Applies the numpy.trapz compatibility shim BEFORE any test imports opentrons.
opentrons 9.0.0 references the removed numpy.trapz on numpy>=2.0; this restores it
(mirrors the shim used by scripts/build_vial_dilution_print.py and the runners), so
the opentrons-dependent tests can import and simulate under the `ai` env.

Being at the repo root, this file also anchors pytest's rootdir so `from src....`
imports resolve when tests are run from the project root.
"""
import numpy as _np

if not hasattr(_np, "trapz"):  # numpy>=2.0 removed trapz in favor of trapezoid
    _np.trapz = _np.trapezoid
