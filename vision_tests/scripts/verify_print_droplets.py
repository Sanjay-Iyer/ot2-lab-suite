"""
vision_tests/scripts/verify_print_droplets.py
=============================================
Computer-vision QC for the vial-dilution -> paper-print demo
(src/protocols/vial_dilution_print.py).

It reads the paper-print image(s), detects the printed droplets, and logs an
easy-to-review verdict:

  * COUNT   - did we get all 8 droplets (not 4)?            -> PASS / FAIL
  * SHAPE   - circularity / area per droplet (print quality)
  * COLOUR  - mean RGB/HSV per droplet (the dilution gradient)
  * GRADIENT- does brightness trend light->dark down the column?

Outputs (under --out, default vision_tests/outputs/):
  * cv_results.csv   - one row per detected droplet
  * cv_summary.json  - machine-readable verdict + stats
  * cv_report.txt    - human-readable report (the thing to skim)
  * annotated/<stem>_droplets.jpg - droplets circled + labelled

Image source (first match wins):
  --image PATH        a single image file
  --run-dir DIR       uses DIR/images/paper/*.jpg (a real run folder)
  --mock              SYNTHESISE an 8-droplet gradient image (no camera needed -
                      lets the whole pipeline run in simulation). Combine with
                      --inject-missing K to synthesise only K droplets (FAIL path).
  (default)           vision_tests/raw/*.jpg

Run from project root, env `ai` (has cv2/numpy/pandas/rich):
  python vision_tests/scripts/verify_print_droplets.py --mock --expect 8
  python vision_tests/scripts/verify_print_droplets.py --mock --expect 8 --inject-missing 4
  python vision_tests/scripts/verify_print_droplets.py --run-dir runs/<run_id>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
import pandas as pd
import yaml

# Allow "python vision_tests/scripts/verify_print_droplets.py" from repo root.
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "vision_tests"))

import lib  # noqa: E402  (vision_tests/lib.py)

try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
except Exception:   # pragma: no cover - rich optional
    _console = None


# ── Load workflow YAML (single source of truth for well order, fold labels, CV params) ──

_WORKFLOW_CFG_PATH = PROJECT_ROOT / "configs" / "workflows" / "defaults" / "vial_dilution_print.yaml"

def _load_workflow_cfg() -> dict:
    if not _WORKFLOW_CFG_PATH.exists():
        warnings.warn(
            f"Workflow config not found at {_WORKFLOW_CFG_PATH}. "
            "WELL_ORDER, FOLD_ORDER and CV thresholds will use built-in defaults.",
            stacklevel=2,
        )
        return {}
    return yaml.safe_load(_WORKFLOW_CFG_PATH.read_text(encoding="utf-8")) or {}


def _resolve_factors(fc: dict) -> list:
    """Minimal local copy of the protocol's resolve_factors (no opentrons import)."""
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [int(x) if float(x) == int(float(x)) else float(x) for x in fc["explicit"]]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if count == 1:
        return [start]
    if mode == "linear":
        return [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    lo, hi = math.log(start), math.log(end)
    return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]


_workflow_cfg = _load_workflow_cfg()
_dil_cfg      = _workflow_cfg.get("dilution", {})
_cv_cfg       = _workflow_cfg.get("cv", {})

# ── WELL_ORDER and FOLD_ORDER: derived from YAML, not hardcoded ───────────────────
# Changing destination_column or factors in the YAML automatically updates
# droplet annotations without touching this script.
_dest_col   = str(_dil_cfg.get("destination_column", "1"))
_factors_fc = _dil_cfg.get("factors", {})
FOLD_ORDER  = _resolve_factors(_factors_fc) if _factors_fc else [1, 2, 5, 10, 20, 30, 40, 50]
WELL_ORDER  = [f"{chr(ord('A') + i)}{_dest_col}" for i in range(len(FOLD_ORDER))]

# ── MIN_CIRCULARITY_OK: read from YAML cv block, not hardcoded ───────────────────
MIN_CIRCULARITY_OK = float(_cv_cfg.get("min_circularity_ok", 0.6))


def _echo(msg: str) -> None:
    if _console:
        _console.print(msg)
    else:
        print(msg)


# ── Mock image synthesis ──────────────────────────────────────────────────────────

def synthesize_paper_image(out_path: Path, n_droplets: int) -> Path:
    """Draw n_droplets as a vertical column of filled circles on white 'paper',
    coloured as a blue food-colouring gradient (dark/top -> light/bottom).

    Droplets are evenly spaced across the full image height regardless of count,
    matching the visual layout of a real print (not clamped to the top half).
    """
    h, w   = 480, 360
    img    = np.full((h, w, 3), 255, dtype=np.uint8)   # white paper
    cx     = w // 2
    top, bottom, radius = 55, h - 55, 20
    # Divide by (n_droplets - 1) so droplets span the full height evenly.
    # For n_droplets == 1 avoid division by zero.
    n_spaces = max(n_droplets - 1, 1)
    for i in range(n_droplets):
        cy = int(top + (bottom - top) * i / n_spaces)
        t  = i / max(len(FOLD_ORDER) - 1, 1)   # colour follows the full series gradient
        b = int(150 + t * (220 - 150))
        g = int(40  + t * (150 - 40))
        r = int(10  + t * (120 - 10))
        cv2.circle(img, (cx, cy), radius, (b, g, r), thickness=-1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path


# ── Detection on one image ────────────────────────────────────────────────────────

def analyze_image(img_path: Path, det_cfg: dict):
    bgr = cv2.imread(str(img_path))
    if bgr is None:
        return []
    mask = lib.create_binary_mask(
        bgr,
        method=det_cfg.get("threshold_method", "otsu"),
        blur_k=det_cfg.get("blur_kernel", 5),
        morph_k=det_cfg.get("morph_kernel", 3),
        morph_iter=det_cfg.get("morph_iterations", 2),
        fixed_thresh=det_cfg.get("fixed_threshold", 127),
        invert=det_cfg.get("invert", True),
    )
    objects = lib.measure_objects(
        mask,
        min_area=det_cfg.get("min_area", 150),
        max_area=det_cfg.get("max_area", 100000),
        max_aspect_ratio=det_cfg.get("max_aspect_ratio", 3.0),
        min_circularity=det_cfg.get("min_circularity", 0.0),
    )
    # Sort top -> bottom so droplet order matches the dilution column.
    objects.sort(key=lambda o: o["centroid_y"])
    for o in objects:
        o.update(lib.sample_contour_color(bgr, o["contour"]))
    return objects, bgr


def annotate(bgr: np.ndarray, droplets: List[Dict[str, Any]], out_path: Path) -> None:
    vis = bgr.copy()
    for idx, o in enumerate(droplets):
        cnt = o["contour"]
        cv2.drawContours(vis, [cnt], -1, (0, 255, 0), 2)
        cv2.circle(vis, (o["centroid_x"], o["centroid_y"]), 3, (0, 0, 255), -1)
        well  = WELL_ORDER[idx] if idx < len(WELL_ORDER) else f"#{idx+1}"
        label = f"{well} c={o['circularity']:.2f} br={o['brightness']:.0f}"
        cv2.putText(vis, label, (o["bbox_x"], max(o["bbox_y"] - 6, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1, cv2.LINE_AA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), vis)


def gradient_detected(droplets: List[Dict[str, Any]]) -> bool:
    """True if brightness generally increases top->bottom (dilute toward bottom)."""
    if len(droplets) < 3:
        return False
    order  = np.arange(len(droplets))
    bright = np.array([o["brightness"] for o in droplets])
    if bright.std() < 1e-6:
        return False
    return float(np.corrcoef(order, bright)[0, 1]) > 0.5


# ── Main ──────────────────────────────────────────────────────────────────────────

def resolve_images(args, out_dir: Path) -> List[Path]:
    if args.image:
        return [Path(args.image)]
    if args.run_dir:
        # Mirror the robot_image_dir convention from the workflow YAML so that
        # --run-dir <run_root> finds images at the same relative path the robot wrote them.
        cam_cfg    = _workflow_cfg.get("camera", {})
        robot_subdir = Path(cam_cfg.get("robot_image_dir", "/data/vision/vial_dilution_print")).name
        paper_dir  = Path(args.run_dir) / robot_subdir
        if not paper_dir.exists():
            # Legacy fallback: <run_dir>/images/paper/
            paper_dir = Path(args.run_dir) / "images" / "paper"
        return lib.find_images(paper_dir)
    if args.mock:
        mock_path = out_dir / "mock" / "paper_print.jpg"
        n = args.inject_missing if args.inject_missing is not None else args.expect
        synthesize_paper_image(mock_path, n)
        return [mock_path]
    return lib.find_images(lib.PROJECT_ROOT / "vision_tests" / "raw")


def main() -> int:
    ap = argparse.ArgumentParser(description="CV verification of printed droplets.")
    ap.add_argument("--image", help="A single paper-print image to analyse.")
    ap.add_argument("--run-dir",
                    help="A run root folder; images are resolved via robot_image_dir "
                         "from the workflow YAML (fallback: <run-dir>/images/paper/).")
    ap.add_argument("--mock", action="store_true", help="Synthesize a droplet image.")
    ap.add_argument("--inject-missing", type=int, default=None,
                    help="With --mock, synthesize only this many droplets (FAIL path).")
    ap.add_argument("--expect", type=int, default=len(FOLD_ORDER),
                    help=f"Expected droplet count (default {len(FOLD_ORDER)}, "
                         "derived from workflow YAML factor count).")
    ap.add_argument("--out", default=None,
                    help="Output dir (default vision_tests/outputs).")
    args = ap.parse_args()

    # ── Build detection config: YAML cv.detection -> vision_config.yaml -> code defaults ──
    # Precedence (highest wins):
    #   1. vision_tests/configs/vision_config.yaml droplet_detection: block (per-lab tuning)
    #   2. workflow YAML cv.detection: block (project-level defaults)
    #   3. Code-level fallbacks below (last resort)
    local_cfg = lib.load_config()   # vision_config.yaml (may warn + return {})
    det_cfg = {
        "threshold_method": "otsu",
        "min_area": 250,
        "max_area": 100000,
        "blur_kernel": 5,
        "morph_kernel": 3,
        "morph_iterations": 2,
        "invert": True,
        "max_aspect_ratio": 3.0,
    }
    # Layer 2: workflow YAML cv.detection block (previously dead config — now wired)
    det_cfg.update(_cv_cfg.get("detection", {}))
    # Layer 1: vision_config.yaml droplet_detection block (per-lab overrides, highest precedence)
    det_cfg.update(local_cfg.get("droplet_detection", {}) or {})

    out_dir = (Path(args.out).resolve() if args.out
               else lib.resolve_dir(local_cfg, "output_dir", "vision_tests/outputs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir = out_dir / "annotated"

    images = resolve_images(args, out_dir)
    if not images:
        _echo("[yellow]No paper-print images found. Use --mock to synthesize one.[/]"
              if _console else "No images found. Use --mock.")
        return 0

    rows: List[Dict[str, Any]] = []
    per_image: List[Dict[str, Any]] = []
    overall_pass = True

    for img_path in images:
        result = analyze_image(img_path, det_cfg)
        if not result:
            _echo(f"Could not read {img_path}; skipping.")
            continue
        droplets, bgr = result
        annotate(bgr, droplets, annotated_dir / f"{img_path.stem}_droplets.jpg")

        found      = len(droplets)
        count_pass = (found == args.expect)
        circs      = [o["circularity"] for o in droplets] or [0.0]
        shape_pass = min(circs) >= MIN_CIRCULARITY_OK if droplets else False
        grad       = gradient_detected(droplets)
        img_pass   = count_pass
        overall_pass = overall_pass and img_pass

        for idx, o in enumerate(droplets):
            rows.append({
                "image":         img_path.name,
                "droplet_index": idx + 1,
                "well":          WELL_ORDER[idx] if idx < len(WELL_ORDER) else "",
                "fold":          FOLD_ORDER[idx] if idx < len(FOLD_ORDER) else "",
                "centroid_x": o["centroid_x"], "centroid_y": o["centroid_y"],
                "area_pixels": o["area_pixels"], "circularity": o["circularity"],
                "aspect_ratio": o["aspect_ratio"],
                "r": o["r"], "g": o["g"], "b": o["b"],
                "h": o["h"], "s": o["s"], "v": o["v"], "brightness": o["brightness"],
            })

        per_image.append({
            "image":            img_path.name,
            "expected_count":   args.expect,
            "found_count":      found,
            "count_pass":       count_pass,
            "shape_pass":       shape_pass,
            "min_circularity":  round(float(min(circs)), 3),
            "mean_circularity": round(float(np.mean(circs)), 3),
            "gradient_detected": grad,
            "status":           "PASS" if img_pass else "FAIL",
        })

    summary = {
        "generated_utc":    datetime.now(tz=timezone.utc).isoformat(),
        "workflow_yaml":    str(_WORKFLOW_CFG_PATH),
        "dest_column":      _dest_col,
        "fold_order":       FOLD_ORDER,
        "expected_count":   args.expect,
        "overall_status":   "PASS" if overall_pass else "FAIL",
        "images_analyzed":  len(per_image),
        "per_image":        per_image,
    }

    # ── Write logs ────────────────────────────────────────────────────────────────
    if rows:
        pd.DataFrame(rows).to_csv(out_dir / "cv_results.csv", index=False)
    with open(out_dir / "cv_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    report_lines = [
        "=" * 60,
        "  PRINTED-DROPLET CV VERIFICATION REPORT",
        "=" * 60,
        f"Generated      : {summary['generated_utc']}",
        f"Workflow YAML  : {_WORKFLOW_CFG_PATH.name}",
        f"Dest column    : {_dest_col}",
        f"Fold series    : {FOLD_ORDER}",
        f"Expected drops : {args.expect}",
        f"OVERALL        : {summary['overall_status']}",
        "",
    ]
    for pi in per_image:
        report_lines += [
            f"Image: {pi['image']}",
            f"  droplets found : {pi['found_count']}  (expected {pi['expected_count']})"
            f"  -> {'OK' if pi['count_pass'] else 'MISMATCH'}",
            f"  shape          : min circularity {pi['min_circularity']} "
            f"({'round' if pi['shape_pass'] else 'check - smeared/merged?'})",
            f"  colour gradient: {'detected' if pi['gradient_detected'] else 'not detected'}",
            f"  verdict        : {pi['status']}",
            "",
        ]
        for r in [x for x in rows if x["image"] == pi["image"]]:
            report_lines.append(
                f"    {r['well'] or '#'+str(r['droplet_index']):>3} "
                f"fold={str(r['fold']) + 'x':>4}  RGB=({r['r']:.0f},{r['g']:.0f},{r['b']:.0f})  "
                f"bright={r['brightness']:.0f}  circ={r['circularity']}"
            )
        report_lines.append("")
    with open(out_dir / "cv_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    # ── Console summary ───────────────────────────────────────────────────────────
    if _console:
        table = Table(title="Printed-Droplet CV Verification", show_lines=True)
        table.add_column("Image", style="cyan", max_width=28)
        table.add_column("Found/Exp", justify="right")
        table.add_column("Min Circ", justify="right")
        table.add_column("Gradient", justify="center")
        table.add_column("Verdict", justify="center")
        for pi in per_image:
            verdict = "[green]PASS[/]" if pi["status"] == "PASS" else "[red]FAIL[/]"
            table.add_row(
                pi["image"], f"{pi['found_count']}/{pi['expected_count']}",
                f"{pi['min_circularity']}",
                "yes" if pi["gradient_detected"] else "no", verdict,
            )
        _console.print(table)
        _console.print(
            f"Logs written to [green]{out_dir}[/] "
            f"(cv_results.csv, cv_summary.json, cv_report.txt)")
        banner = ("[bold green]OVERALL: PASS[/]" if overall_pass
                  else "[bold red]OVERALL: FAIL[/]")
        _console.print(banner)
    else:
        print(f"OVERALL: {summary['overall_status']}  ->  {out_dir}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
