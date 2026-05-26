"""
vision_tests/scripts/test_shape_detection.py
=============================================
Detect spots / droplets / objects and measure their geometric
properties.  Produces one CSV row per detected object with area,
perimeter, bounding box, aspect ratio, circularity, and centroid.

Outputs
-------
* ``outputs/shape_metrics.csv``  — one row per object (all images).
* ``outputs/annotated/<stem>_shapes.jpg`` — annotated image with IDs.
* Rich terminal summary.

Run from project root
---------------------
    python vision_tests/scripts/test_shape_detection.py
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
# Detection
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
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if blur_k > 0:
        blur_k = blur_k if blur_k % 2 == 1 else blur_k + 1
        gray = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)

    if method == "adaptive":
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY,
            blockSize=11, C=2,
        )
    elif method == "otsu":
        t = cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU if invert else cv2.THRESH_BINARY | cv2.THRESH_OTSU
        _, mask = cv2.threshold(gray, 0, 255, t)
    else:
        t = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        _, mask = cv2.threshold(gray, fixed_thresh, 255, t)

    if morph_k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_k, morph_k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=morph_iter)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=morph_iter)
    return mask


def measure_objects(
    mask: np.ndarray,
    *,
    min_area: int = 50,
    max_area: int = 10000,
    max_aspect_ratio: float = 10.0,
    min_circularity: float = 0.0,
) -> list[dict]:
    """Find contours and compute geometric properties for each."""
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    results: list[dict] = []
    obj_id = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue

        perimeter = cv2.arcLength(cnt, True)
        x, y, w, h = cv2.boundingRect(cnt)
        aspect_ratio = max(w, h) / max(min(w, h), 1)
        circularity = (4.0 * np.pi * area) / (perimeter ** 2) if perimeter > 0 else 0.0

        if aspect_ratio > max_aspect_ratio:
            continue
        if circularity < min_circularity:
            continue

        M = cv2.moments(cnt)
        cx = int(M["m10"] / M["m00"]) if M["m00"] != 0 else x + w // 2
        cy = int(M["m01"] / M["m00"]) if M["m00"] != 0 else y + h // 2

        # Rectangularity: how well the object fills its bounding box
        rect_area = w * h
        rectangularity = area / rect_area if rect_area > 0 else 0.0

        obj_id += 1
        results.append({
            "object_id": obj_id,
            "contour": cnt,
            "area_pixels": round(area, 1),
            "perimeter_pixels": round(perimeter, 1),
            "bbox_x": x,
            "bbox_y": y,
            "bbox_w": w,
            "bbox_h": h,
            "aspect_ratio": round(aspect_ratio, 3),
            "circularity": round(circularity, 3),
            "rectangularity": round(rectangularity, 3),
            "centroid_x": cx,
            "centroid_y": cy,
        })

    return results


# ──────────────────────────────────────────────────────────────────
# Annotation
# ──────────────────────────────────────────────────────────────────

def annotate_shapes(
    bgr: np.ndarray,
    objects: list[dict],
    ann_cfg: dict,
) -> np.ndarray:
    """Draw contours, bounding boxes, and object ID numbers."""
    vis = bgr.copy()
    contour_color = tuple(ann_cfg.get("contour_color", [0, 255, 0]))
    bbox_color = tuple(ann_cfg.get("bbox_color", [255, 0, 0]))
    centroid_color = tuple(ann_cfg.get("centroid_color", [0, 0, 255]))
    text_color = tuple(ann_cfg.get("text_color", [255, 255, 255]))
    thickness = ann_cfg.get("line_thickness", 2)
    font_scale = ann_cfg.get("font_scale", 0.5)

    for obj in objects:
        cv2.drawContours(vis, [obj["contour"]], -1, contour_color, thickness)
        x, y, w, h = obj["bbox_x"], obj["bbox_y"], obj["bbox_w"], obj["bbox_h"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), bbox_color, 1)
        cv2.circle(vis, (obj["centroid_x"], obj["centroid_y"]), 4, centroid_color, -1)

        # Object ID label with background for readability
        label = f"#{obj['object_id']}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        cv2.rectangle(vis, (x, y - th - 6), (x + tw + 4, y), (0, 0, 0), -1)
        cv2.putText(
            vis, label, (x + 2, y - 4),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale,
            text_color, 1, cv2.LINE_AA,
        )

    return vis


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold cyan]OT-2 Shape Detection[/]")

    cfg = load_config()
    det_cfg = cfg.get("detection", {})
    shape_cfg = cfg.get("shape_analysis", {})
    ann_cfg = cfg.get("annotation", {})

    raw_dir = resolve_dir(cfg, "raw_dir", "vision_tests/raw")
    annotated_dir = resolve_dir(cfg, "annotated_dir", "vision_tests/outputs/annotated")
    output_dir = resolve_dir(cfg, "output_dir", "vision_tests/outputs")

    images = find_images(raw_dir)
    if not images:
        console.print(f"[bold red]No images found in {raw_dir}[/]")
        sys.exit(0)

    console.print(f"Found [green]{len(images)}[/] image(s)\n")

    all_rows: list[dict] = []

    for img_path in images:
        console.print(f"[bold]Image:[/] {img_path.name}")

        bgr = cv2.imread(str(img_path))
        if bgr is None:
            console.print(f"  [red]Could not read -- skipping.[/]")
            continue

        mask = create_binary_mask(
            bgr,
            method=det_cfg.get("threshold_method", "adaptive"),
            blur_k=det_cfg.get("blur_kernel", 5),
            morph_k=det_cfg.get("morph_kernel", 3),
            morph_iter=det_cfg.get("morph_iterations", 2),
            fixed_thresh=det_cfg.get("fixed_threshold", 127),
            invert=det_cfg.get("invert", True),
        )

        objects = measure_objects(
            mask,
            min_area=det_cfg.get("min_area", 50),
            max_area=det_cfg.get("max_area", 10000),
            max_aspect_ratio=shape_cfg.get("max_aspect_ratio", 10.0),
            min_circularity=shape_cfg.get("min_circularity", 0.0),
        )

        console.print(f"  Objects detected: [green]{len(objects)}[/]")

        # Print summary stats for this image
        if objects:
            areas = [o["area_pixels"] for o in objects]
            circs = [o["circularity"] for o in objects]
            console.print(
                f"  Area range:  {min(areas):.0f} - {max(areas):.0f} px^2"
            )
            console.print(
                f"  Circularity: {min(circs):.3f} - {max(circs):.3f}"
            )

        # Annotate
        vis = annotate_shapes(bgr, objects, ann_cfg)
        out_name = f"{img_path.stem}_shapes.jpg"
        cv2.imwrite(str(annotated_dir / out_name), vis)

        # Collect CSV rows
        for obj in objects:
            row = {
                "image_name": img_path.name,
                "object_id": obj["object_id"],
                "area_pixels": obj["area_pixels"],
                "perimeter_pixels": obj["perimeter_pixels"],
                "bbox_x": obj["bbox_x"],
                "bbox_y": obj["bbox_y"],
                "bbox_w": obj["bbox_w"],
                "bbox_h": obj["bbox_h"],
                "aspect_ratio": obj["aspect_ratio"],
                "circularity": obj["circularity"],
                "rectangularity": obj["rectangularity"],
                "centroid_x": obj["centroid_x"],
                "centroid_y": obj["centroid_y"],
            }
            all_rows.append(row)

        console.print()

    # ── CSV ─────────────────────────────────────────────────────
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_path = output_dir / "shape_metrics.csv"
        df.to_csv(csv_path, index=False)
        console.print(f"[green]CSV saved -->[/] {csv_path}")

        # Summary table: per-image aggregate
        summary = df.groupby("image_name").agg(
            count=("object_id", "count"),
            mean_area=("area_pixels", "mean"),
            mean_circ=("circularity", "mean"),
            mean_ar=("aspect_ratio", "mean"),
        ).reset_index()

        table = Table(title="Shape Detection -- Per-Image Summary", show_lines=True)
        table.add_column("Image", style="cyan", no_wrap=True, max_width=30)
        table.add_column("Objects", justify="right", style="green")
        table.add_column("Mean Area", justify="right")
        table.add_column("Mean Circ.", justify="right")
        table.add_column("Mean AR", justify="right")

        for _, r in summary.iterrows():
            table.add_row(
                str(r["image_name"])[:30],
                str(int(r["count"])),
                f"{r['mean_area']:.1f}",
                f"{r['mean_circ']:.3f}",
                f"{r['mean_ar']:.2f}",
            )
        console.print(table)

        console.print(f"\nTotal objects across all images: [bold green]{len(df)}[/]")

    console.rule("[bold cyan]Done[/]")


if __name__ == "__main__":
    main()
