"""
vision_tests/scripts/test_color_change.py
==========================================
Compute image-level and (optionally) region-level colour metrics
for OT-2 camera images, and compare against a baseline when configured.

Outputs
-------
* ``outputs/color_metrics.csv``  — one row per image.
* ``outputs/masks/``             — optional colour mask images.
* Rich terminal summary.

Run from project root
---------------------
    python vision_tests/scripts/test_color_change.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import yaml
from rich.console import Console
from rich.table import Table

# ─── Project-root discovery ──────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
CONFIG_PATH = PROJECT_ROOT / "vision_tests" / "configs" / "vision_config.yaml"

console = Console()
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        console.print(f"[yellow]WARNING:[/] Config not found -- using defaults.")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_dir(cfg: dict, key: str, fallback: str) -> Path:
    d = (PROJECT_ROOT / cfg.get(key, fallback)).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_images(raw_dir: Path) -> list[Path]:
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


# ──────────────────────────────────────────────────────────────────
# Colour analysis
# ──────────────────────────────────────────────────────────────────

def compute_color_metrics(bgr: np.ndarray) -> dict:
    """Compute per-channel mean for RGB, HSV, LAB and brightness stats."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    return {
        "mean_R": float(np.mean(rgb[:, :, 0])),
        "mean_G": float(np.mean(rgb[:, :, 1])),
        "mean_B": float(np.mean(rgb[:, :, 2])),
        "mean_H": float(np.mean(hsv[:, :, 0])),
        "mean_S": float(np.mean(hsv[:, :, 1])),
        "mean_V": float(np.mean(hsv[:, :, 2])),
        "mean_L": float(np.mean(lab[:, :, 0])),
        "mean_a": float(np.mean(lab[:, :, 1])),
        "mean_b": float(np.mean(lab[:, :, 2])),
        "brightness_mean": float(np.mean(gray)),
        "brightness_std": float(np.std(gray)),
    }


def compute_color_difference(metrics_a: dict, metrics_b: dict) -> dict:
    """Approximate colour difference between two metric dicts.

    Uses simple Euclidean distance in LAB space (ΔE*ab) and absolute
    differences for brightness.  A proper CIEDE2000 formula could
    replace this when ``colormath`` is wired in.
    """
    delta_L = metrics_a["mean_L"] - metrics_b["mean_L"]
    delta_a = metrics_a["mean_a"] - metrics_b["mean_a"]
    delta_b = metrics_a["mean_b"] - metrics_b["mean_b"]
    delta_E = float(np.sqrt(delta_L ** 2 + delta_a ** 2 + delta_b ** 2))

    return {
        "delta_L": round(delta_L, 2),
        "delta_a": round(delta_a, 2),
        "delta_b": round(delta_b, 2),
        "delta_E_lab": round(delta_E, 2),
        "delta_brightness": round(
            metrics_a["brightness_mean"] - metrics_b["brightness_mean"], 2
        ),
    }


def save_color_mask(
    bgr: np.ndarray,
    mask_dir: Path,
    stem: str,
) -> None:
    """Save a simple HSV-based colour-range mask for visual debugging.

    This keeps anything that is NOT grey/white — i.e. coloured objects.
    Tune the saturation threshold to match your images.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Keep pixels with saturation > 30 (i.e. not grey/white)
    lower = np.array([0, 30, 50], dtype=np.uint8)
    upper = np.array([180, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    out_path = mask_dir / f"{stem}_color_mask.jpg"
    cv2.imwrite(str(out_path), mask)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold cyan]OT-2 Colour Change Analysis[/]")

    cfg = load_config()
    color_cfg = cfg.get("color_analysis", {})

    raw_dir = resolve_dir(cfg, "raw_dir", "vision_tests/raw")
    mask_dir = resolve_dir(cfg, "mask_dir", "vision_tests/outputs/masks")
    output_dir = resolve_dir(cfg, "output_dir", "vision_tests/outputs")

    images = find_images(raw_dir)
    if not images:
        console.print(f"[bold red]No images found in {raw_dir}[/]")
        sys.exit(0)

    console.print(f"Found [green]{len(images)}[/] image(s)\n")

    # ── Load baseline if configured ─────────────────────────────
    baseline_metrics: dict | None = None
    compare = color_cfg.get("compare_to_baseline", False)
    if compare:
        bl_path_str = color_cfg.get("baseline_image")
        if bl_path_str:
            bl_path = (PROJECT_ROOT / bl_path_str).resolve()
            if bl_path.exists():
                bl_bgr = cv2.imread(str(bl_path))
                if bl_bgr is not None:
                    baseline_metrics = compute_color_metrics(bl_bgr)
                    console.print(
                        f"Baseline loaded: [cyan]{bl_path.name}[/]\n"
                    )
                else:
                    console.print(f"[yellow]Could not read baseline image.[/]")
            else:
                console.print(f"[yellow]Baseline file not found: {bl_path}[/]")

    all_rows: list[dict] = []

    for img_path in images:
        console.print(f"[bold]Image:[/] {img_path.name}")

        bgr = cv2.imread(str(img_path))
        if bgr is None:
            console.print(f"  [red]Could not read -- skipping.[/]")
            continue

        metrics = compute_color_metrics(bgr)

        row: dict = {"image_name": img_path.name, **metrics}

        # Baseline comparison
        if baseline_metrics:
            diff = compute_color_difference(metrics, baseline_metrics)
            row.update(diff)
            console.print(
                f"  dE(LAB) vs baseline: [yellow]{diff['delta_E_lab']:.2f}[/]"
            )
        else:
            console.print(
                f"  Brightness: mean={metrics['brightness_mean']:.1f}  "
                f"std={metrics['brightness_std']:.1f}"
            )

        all_rows.append(row)

        # Save colour mask
        if color_cfg.get("enabled", True):
            save_color_mask(bgr, mask_dir, img_path.stem)

    # ── CSV ─────────────────────────────────────────────────────
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_path = output_dir / "color_metrics.csv"
        df.to_csv(csv_path, index=False)
        console.print(f"\n[green]CSV saved -->[/] {csv_path}")

        # ── Rich table ──────────────────────────────────────────
        table = Table(title="Colour Metrics Summary", show_lines=True)
        table.add_column("Image", style="cyan", no_wrap=True, max_width=30)
        table.add_column("R", justify="right")
        table.add_column("G", justify="right")
        table.add_column("B", justify="right")
        table.add_column("H", justify="right")
        table.add_column("S", justify="right")
        table.add_column("V", justify="right")
        table.add_column("L", justify="right")
        table.add_column("a*", justify="right")
        table.add_column("b*", justify="right")
        table.add_column("Br mean", justify="right")
        table.add_column("Br std", justify="right")

        for r in all_rows:
            table.add_row(
                r["image_name"][:30],
                f"{r['mean_R']:.1f}", f"{r['mean_G']:.1f}", f"{r['mean_B']:.1f}",
                f"{r['mean_H']:.1f}", f"{r['mean_S']:.1f}", f"{r['mean_V']:.1f}",
                f"{r['mean_L']:.1f}", f"{r['mean_a']:.1f}", f"{r['mean_b']:.1f}",
                f"{r['brightness_mean']:.1f}", f"{r['brightness_std']:.1f}",
            )
        console.print(table)

    console.rule("[bold cyan]Done[/]")


if __name__ == "__main__":
    main()
