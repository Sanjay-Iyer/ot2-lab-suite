"""
vision_tests/scripts/test_tip_count_by_slot.py
===============================================
Count detected objects (pipette tips) in each OT-2 deck slot.

Algorithm
---------
1. Load YAML config  (slot grid, detection params).
2. Load each image from ``raw/``.
3. Overlay a configurable 3×4 grid on the image (ROI-cropped).
4. Run threshold + contour detection.
5. Assign each detected object to a slot based on its centroid.
6. Count objects per slot.
7. Draw slot boundaries, slot numbers, bounding boxes, centroids.
8. Save annotated images  → ``outputs/annotated/<name>_slot_counts.jpg``
9. Save CSV               → ``outputs/slot_counts.csv``
10. Print a readable terminal summary.

Run from project root
---------------------
    python vision_tests/scripts/test_tip_count_by_slot.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

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


# ──────────────────────────────────────────────────────────────────
# Helpers (shared logic – thin wrappers)
# ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        console.print(f"[yellow]WARNING:[/] Config not found at {CONFIG_PATH}")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_dir(cfg: dict, key: str, fallback: str) -> Path:
    raw = cfg.get(key, fallback)
    d = (PROJECT_ROOT / raw).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_images(raw_dir: Path) -> list[Path]:
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


# ──────────────────────────────────────────────────────────────────
# Binary mask creation (same as hello_world but kept self-contained)
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


# ──────────────────────────────────────────────────────────────────
# Object detection
# ──────────────────────────────────────────────────────────────────

def detect_objects(
    mask: np.ndarray,
    *,
    min_area: int = 50,
    max_area: int = 10000,
    max_aspect_ratio: float = 10.0,
    min_circularity: float = 0.0,
) -> list[dict]:
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
        circularity = (4.0 * np.pi * area) / (perimeter ** 2) if perimeter > 0 else 0.0
        if aspect_ratio > max_aspect_ratio:
            continue
        if circularity < min_circularity:
            continue
        M = cv2.moments(cnt)
        cx = int(M["m10"] / M["m00"]) if M["m00"] != 0 else x + w // 2
        cy = int(M["m01"] / M["m00"]) if M["m00"] != 0 else y + h // 2
        objects.append({
            "contour": cnt,
            "area_pixels": area,
            "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h,
            "aspect_ratio": round(aspect_ratio, 3),
            "circularity": round(circularity, 3),
            "centroid_x": cx, "centroid_y": cy,
        })
    return objects


# ──────────────────────────────────────────────────────────────────
# Slot grid
# ──────────────────────────────────────────────────────────────────

class SlotGrid:
    """Represents the 3×4 OT-2 deck grid over an image ROI."""

    def __init__(
        self,
        img_w: int,
        img_h: int,
        roi_x: int = 0,
        roi_y: int = 0,
        roi_w: Optional[int] = None,
        roi_h: Optional[int] = None,
        rows: int = 4,
        cols: int = 3,
        slot_layout: Optional[list[list[int]]] = None,
    ):
        self.roi_x = roi_x
        self.roi_y = roi_y
        self.roi_w = roi_w if roi_w else img_w - roi_x
        self.roi_h = roi_h if roi_h else img_h - roi_y
        self.rows = rows
        self.cols = cols

        # Default OT-2 slot layout (camera top-down, top-to-bottom)
        self.slot_layout = slot_layout or [
            [10, 11, 12],
            [7, 8, 9],
            [4, 5, 6],
            [1, 2, 3],
        ]

        # Precompute cell dimensions
        self.cell_w = self.roi_w / self.cols
        self.cell_h = self.roi_h / self.rows

    def slot_for_point(self, x: int, y: int) -> Optional[int]:
        """Return the slot number for a point (x, y), or None if outside ROI."""
        rx = x - self.roi_x
        ry = y - self.roi_y
        if rx < 0 or ry < 0 or rx >= self.roi_w or ry >= self.roi_h:
            return None
        col_idx = int(rx / self.cell_w)
        row_idx = int(ry / self.cell_h)
        col_idx = min(col_idx, self.cols - 1)
        row_idx = min(row_idx, self.rows - 1)
        return self.slot_layout[row_idx][col_idx]

    def slot_bbox(self, slot_num: int) -> Optional[tuple[int, int, int, int]]:
        """Return (x, y, w, h) for a given slot number."""
        for r, row in enumerate(self.slot_layout):
            for c, sn in enumerate(row):
                if sn == slot_num:
                    x = int(self.roi_x + c * self.cell_w)
                    y = int(self.roi_y + r * self.cell_h)
                    w = int(self.cell_w)
                    h = int(self.cell_h)
                    return (x, y, w, h)
        return None

    def draw_grid(
        self,
        vis: np.ndarray,
        line_color: tuple = (0, 255, 255),
        text_color: tuple = (255, 255, 255),
        thickness: int = 2,
        font_scale: float = 0.6,
    ) -> np.ndarray:
        """Draw slot grid lines and slot numbers on *vis* (in-place)."""
        # Vertical lines
        for c in range(self.cols + 1):
            x = int(self.roi_x + c * self.cell_w)
            cv2.line(
                vis,
                (x, self.roi_y),
                (x, self.roi_y + self.roi_h),
                line_color, thickness,
            )
        # Horizontal lines
        for r in range(self.rows + 1):
            y = int(self.roi_y + r * self.cell_h)
            cv2.line(
                vis,
                (self.roi_x, y),
                (self.roi_x + self.roi_w, y),
                line_color, thickness,
            )
        # Slot labels
        for r, row in enumerate(self.slot_layout):
            for c, slot_num in enumerate(row):
                cx = int(self.roi_x + (c + 0.5) * self.cell_w)
                cy = int(self.roi_y + (r + 0.1) * self.cell_h) + 15
                label = f"S{slot_num}"
                cv2.putText(
                    vis, label, (cx - 15, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                    text_color, 2, cv2.LINE_AA,
                )
        return vis


# ──────────────────────────────────────────────────────────────────
# Annotation
# ──────────────────────────────────────────────────────────────────

def annotate_slot_image(
    bgr: np.ndarray,
    objects: list[dict],
    grid: SlotGrid,
    slot_counts: dict[int, int],
    ann_cfg: dict,
) -> np.ndarray:
    """Draw slot grid, bounding boxes, centroids, and per-slot counts."""
    vis = bgr.copy()

    # Draw grid
    grid.draw_grid(
        vis,
        line_color=tuple(ann_cfg.get("slot_line_color", [0, 255, 255])),
        text_color=tuple(ann_cfg.get("text_color", [255, 255, 255])),
        thickness=ann_cfg.get("line_thickness", 2),
        font_scale=ann_cfg.get("font_scale", 0.5),
    )

    # Draw each detected object
    bbox_color = tuple(ann_cfg.get("bbox_color", [255, 0, 0]))
    centroid_color = tuple(ann_cfg.get("centroid_color", [0, 0, 255]))
    contour_color = tuple(ann_cfg.get("contour_color", [0, 255, 0]))

    for obj in objects:
        x, y, w, h = obj["bbox_x"], obj["bbox_y"], obj["bbox_w"], obj["bbox_h"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), bbox_color, 1)
        cv2.circle(vis, (obj["centroid_x"], obj["centroid_y"]), 4, centroid_color, -1)
        cv2.drawContours(vis, [obj["contour"]], -1, contour_color, 1)

    # Draw per-slot count text inside each cell
    for slot_num, count in slot_counts.items():
        bbox = grid.slot_bbox(slot_num)
        if bbox is None:
            continue
        sx, sy, sw, sh = bbox
        # Place count text at centre-bottom of cell
        tx = sx + sw // 2 - 10
        ty = sy + sh - 10
        cv2.putText(
            vis, f"{count} obj",
            (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
            0.45, (0, 255, 0), 1, cv2.LINE_AA,
        )

    return vis


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold cyan]OT-2 Tip Count by Slot[/]")

    cfg = load_config()
    det_cfg = cfg.get("detection", {})
    shape_cfg = cfg.get("shape_analysis", {})
    ann_cfg = cfg.get("annotation", {})
    grid_cfg = cfg.get("slot_grid", {})

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

        h, w = bgr.shape[:2]

        # Build slot grid
        grid = SlotGrid(
            img_w=w, img_h=h,
            roi_x=grid_cfg.get("roi_x", 0),
            roi_y=grid_cfg.get("roi_y", 0),
            roi_w=grid_cfg.get("roi_width"),
            roi_h=grid_cfg.get("roi_height"),
            rows=grid_cfg.get("rows", 4),
            cols=grid_cfg.get("cols", 3),
            slot_layout=grid_cfg.get("slot_layout"),
        )

        # Detect objects
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

        # Assign objects to slots
        slot_counts: dict[int, int] = {s: 0 for row in grid.slot_layout for s in row}
        for obj in objects:
            slot = grid.slot_for_point(obj["centroid_x"], obj["centroid_y"])
            if slot is not None:
                slot_counts[slot] += 1

        total = len(objects)
        console.print(f"  Total objects detected: [green]{total}[/]")

        # Print per-slot counts
        for slot_num in sorted(slot_counts.keys()):
            cnt = slot_counts[slot_num]
            style = "green" if cnt > 0 else "dim"
            console.print(f"    Slot {slot_num:>2}: [{style}]{cnt}[/]")

        # Annotate and save
        vis = annotate_slot_image(bgr, objects, grid, slot_counts, ann_cfg)
        out_name = f"{img_path.stem}_slot_counts.jpg"
        cv2.imwrite(str(annotated_dir / out_name), vis)

        # CSV rows -- one row per image x slot
        for slot_num in sorted(slot_counts.keys()):
            all_rows.append({
                "image_name": img_path.name,
                "slot_number": slot_num,
                "object_count": slot_counts[slot_num],
                "total_objects_detected": total,
                "detection_method": det_cfg.get("threshold_method", "adaptive"),
            })

        console.print()

    # ── Save CSV ────────────────────────────────────────────────
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_path = output_dir / "slot_counts.csv"
        df.to_csv(csv_path, index=False)
        console.print(f"[green]CSV saved -->[/] {csv_path}")

        # Summary table
        # Pivot: rows = slot, columns = images → show count
        pivot = df.pivot_table(
            index="slot_number", columns="image_name",
            values="object_count", aggfunc="sum",
        )
        table = Table(title="Slot Counts -- Summary", show_lines=True)
        table.add_column("Slot", style="cyan", justify="right")
        for col in pivot.columns:
            table.add_column(col[:20], justify="right")
        for slot_num in sorted(pivot.index):
            vals = [str(int(pivot.loc[slot_num, c])) for c in pivot.columns]
            table.add_row(str(slot_num), *vals)
        console.print(table)

    console.rule("[bold cyan]Done[/]")


if __name__ == "__main__":
    main()
