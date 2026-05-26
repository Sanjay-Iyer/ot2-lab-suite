"""
vision_tests/scripts/hello_world_vision_test.py
================================================
"Hello World" machine vision sanity check for OT-2 camera images.

What it does
------------
1. Loads every .jpg / .jpeg / .png from ``vision_tests/raw/``.
2. Computes image-level colour metrics (mean RGB, HSV, LAB, brightness).
3. Runs threshold-based object detection (OpenCV contours).
4. Filters candidate objects by area, aspect ratio, and circularity.
5. Saves annotated images   → ``vision_tests/outputs/annotated/``
6. Saves binary masks        → ``vision_tests/outputs/masks/``
7. Saves a CSV summary       → ``vision_tests/outputs/results.csv``
8. Prints a rich terminal summary.

Run from project root
---------------------
    python vision_tests/scripts/hello_world_vision_test.py
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
# The script lives at  vision_tests/scripts/  →  two parents up = root.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
CONFIG_PATH = PROJECT_ROOT / "vision_tests" / "configs" / "vision_config.yaml"

console = Console()


# ──────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load and return the YAML config, creating defaults if missing."""
    if not CONFIG_PATH.exists():
        console.print(f"[yellow]WARNING:[/] Config not found at {CONFIG_PATH}")
        console.print("         Using built-in defaults.")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_dir(cfg: dict, key: str, fallback: str) -> Path:
    """Resolve a directory path from config (relative to PROJECT_ROOT)."""
    raw = cfg.get(key, fallback)
    d = (PROJECT_ROOT / raw).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ──────────────────────────────────────────────────────────────────
# Image discovery
# ──────────────────────────────────────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def find_images(raw_dir: Path) -> list[Path]:
    """Return sorted list of image paths in *raw_dir*."""
    imgs = sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return imgs


# ──────────────────────────────────────────────────────────────────
# Colour metrics
# ──────────────────────────────────────────────────────────────────

def compute_color_metrics(bgr: np.ndarray) -> dict:
    """Compute mean RGB, HSV, LAB, and brightness statistics."""
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


# ──────────────────────────────────────────────────────────────────
# Object detection
# ──────────────────────────────────────────────────────────────────

def create_binary_mask(
    bgr: np.ndarray,
    *,
    method: str = "adaptive",
    blur_k: int = 5,
    morph_k: int = 3,
    morph_iter: int = 2,
    fixed_thresh: int = 127,
    invert: bool = True,
) -> np.ndarray:
    """Return a binary mask highlighting candidate objects.

    Parameters
    ----------
    method : str
        ``"adaptive"`` (default), ``"otsu"``, or ``"fixed"``.
    invert : bool
        If True, bright objects become foreground (white).
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # 1. Gaussian blur to suppress noise
    if blur_k > 0:
        blur_k = blur_k if blur_k % 2 == 1 else blur_k + 1  # ensure odd
        gray = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)

    # 2. Thresholding
    if method == "adaptive":
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY,
            blockSize=11, C=2,
        )
    elif method == "otsu":
        thresh_type = cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU if invert else cv2.THRESH_BINARY | cv2.THRESH_OTSU
        _, mask = cv2.threshold(gray, 0, 255, thresh_type)
    else:  # fixed
        thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        _, mask = cv2.threshold(gray, fixed_thresh, 255, thresh_type)

    # 3. Morphological close to fill small gaps, then open to remove noise
    if morph_k > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (morph_k, morph_k)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=morph_iter)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=morph_iter)

    return mask


def detect_objects(
    mask: np.ndarray,
    *,
    min_area: int = 50,
    max_area: int = 10000,
    max_aspect_ratio: float = 10.0,
    min_circularity: float = 0.0,
) -> list[dict]:
    """Find contours in *mask* and return filtered object records."""
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    objects: list[dict] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue

        perimeter = cv2.arcLength(cnt, True)
        x, y, w, h = cv2.boundingRect(cnt)
        aspect_ratio = max(w, h) / max(min(w, h), 1)

        # Circularity: 1.0 = perfect circle
        circularity = (4.0 * np.pi * area) / (perimeter ** 2) if perimeter > 0 else 0.0

        if aspect_ratio > max_aspect_ratio:
            continue
        if circularity < min_circularity:
            continue

        # Centroid via moments
        M = cv2.moments(cnt)
        cx = int(M["m10"] / M["m00"]) if M["m00"] != 0 else x + w // 2
        cy = int(M["m01"] / M["m00"]) if M["m00"] != 0 else y + h // 2

        objects.append({
            "contour": cnt,
            "area_pixels": area,
            "perimeter_pixels": perimeter,
            "bbox_x": x,
            "bbox_y": y,
            "bbox_w": w,
            "bbox_h": h,
            "aspect_ratio": round(aspect_ratio, 3),
            "circularity": round(circularity, 3),
            "centroid_x": cx,
            "centroid_y": cy,
        })

    return objects


# ──────────────────────────────────────────────────────────────────
# Annotation
# ──────────────────────────────────────────────────────────────────

def annotate_image(
    bgr: np.ndarray,
    objects: list[dict],
    *,
    contour_color: tuple = (0, 255, 0),
    bbox_color: tuple = (255, 0, 0),
    centroid_color: tuple = (0, 0, 255),
    line_thickness: int = 2,
    font_scale: float = 0.5,
) -> np.ndarray:
    """Draw contours, bounding boxes, and centroids on a copy of *bgr*."""
    vis = bgr.copy()
    for i, obj in enumerate(objects):
        # Contour
        cv2.drawContours(vis, [obj["contour"]], -1, contour_color, line_thickness)
        # Bounding box
        x, y, w, h = obj["bbox_x"], obj["bbox_y"], obj["bbox_w"], obj["bbox_h"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), bbox_color, 1)
        # Centroid dot
        cv2.circle(vis, (obj["centroid_x"], obj["centroid_y"]), 4, centroid_color, -1)
        # Object ID label
        cv2.putText(
            vis, f"#{i+1}",
            (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
            font_scale, contour_color, 1, cv2.LINE_AA,
        )
    return vis


# ──────────────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold cyan]Hello World -- OT-2 Vision Test[/]")

    # Load config
    cfg = load_config()
    det_cfg = cfg.get("detection", {})
    ann_cfg = cfg.get("annotation", {})
    shape_cfg = cfg.get("shape_analysis", {})

    # Resolve directories
    raw_dir = resolve_dir(cfg, "raw_dir", "vision_tests/raw")
    annotated_dir = resolve_dir(cfg, "annotated_dir", "vision_tests/outputs/annotated")
    mask_dir = resolve_dir(cfg, "mask_dir", "vision_tests/outputs/masks")
    output_dir = resolve_dir(cfg, "output_dir", "vision_tests/outputs")
    log_dir = resolve_dir(cfg, "log_dir", "vision_tests/outputs/logs")

    # Find images
    images = find_images(raw_dir)
    if not images:
        console.print(
            f"[bold red]No images found in {raw_dir}[/]\n"
            "  Copy .jpg / .jpeg / .png files into that folder and re-run."
        )
        sys.exit(0)

    console.print(f"Found [green]{len(images)}[/] image(s) in [cyan]{raw_dir}[/]\n")

    # Collect results for CSV
    all_rows: list[dict] = []

    for img_path in images:
        console.print(f"[bold]Processing:[/] {img_path.name}")

        bgr = cv2.imread(str(img_path))
        if bgr is None:
            console.print(f"  [red]Could not read {img_path.name} -- skipping.[/]")
            continue

        h, w = bgr.shape[:2]
        console.print(f"  Dimensions: {w} x {h}")

        # ── Colour metrics ──────────────────────────────────────
        metrics = compute_color_metrics(bgr)

        # ── Object detection ────────────────────────────────────
        mask = create_binary_mask(
            bgr,
            method=det_cfg.get("threshold_method", "adaptive"),
            blur_k=det_cfg.get("blur_kernel", 5),
            morph_k=det_cfg.get("morph_kernel", 3),
            morph_iter=det_cfg.get("morph_iterations", 2),
            fixed_thresh=det_cfg.get("fixed_threshold", 127),
            invert=det_cfg.get("invert", True),
        )

        objects = detect_objects(
            mask,
            min_area=det_cfg.get("min_area", 50),
            max_area=det_cfg.get("max_area", 10000),
            max_aspect_ratio=shape_cfg.get("max_aspect_ratio", 10.0),
            min_circularity=shape_cfg.get("min_circularity", 0.0),
        )
        console.print(f"  Objects detected: [green]{len(objects)}[/]")

        # ── Annotate and save ───────────────────────────────────
        vis = annotate_image(
            bgr, objects,
            contour_color=tuple(ann_cfg.get("contour_color", [0, 255, 0])),
            bbox_color=tuple(ann_cfg.get("bbox_color", [255, 0, 0])),
            centroid_color=tuple(ann_cfg.get("centroid_color", [0, 0, 255])),
            line_thickness=ann_cfg.get("line_thickness", 2),
            font_scale=ann_cfg.get("font_scale", 0.5),
        )
        ann_path = annotated_dir / f"{img_path.stem}_annotated.jpg"
        cv2.imwrite(str(ann_path), vis)

        mask_path = mask_dir / f"{img_path.stem}_mask.jpg"
        cv2.imwrite(str(mask_path), mask)

        # ── Build CSV row ───────────────────────────────────────
        row = {
            "image_name": img_path.name,
            "width": w,
            "height": h,
            "objects_detected": len(objects),
            **metrics,
        }
        all_rows.append(row)

    # ── Save CSV ────────────────────────────────────────────────
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_path = output_dir / "results.csv"
        df.to_csv(csv_path, index=False)
        console.print(f"\n[green]CSV saved -->[/] {csv_path}")

        # ── Rich summary table ──────────────────────────────────
        table = Table(
            title="Hello World Vision Test -- Summary",
            show_lines=True,
        )
        table.add_column("Image", style="cyan", no_wrap=True)
        table.add_column("Size", justify="right")
        table.add_column("Objects", justify="right", style="green")
        table.add_column("Brightness mean", justify="right")
        table.add_column("Brightness std", justify="right")
        for row in all_rows:
            table.add_row(
                row["image_name"],
                f"{row['width']}x{row['height']}",
                str(row["objects_detected"]),
                f"{row['brightness_mean']:.1f}",
                f"{row['brightness_std']:.1f}",
            )
        console.print()
        console.print(table)

    console.rule("[bold cyan]Done[/]")


if __name__ == "__main__":
    main()
