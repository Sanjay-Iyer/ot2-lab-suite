"""
vision_tests/scripts/calibrate_slots.py
========================================
Visual calibration helper — overlays the configured slot grid on an
image so you can tune ``roi_x``, ``roi_y``, ``roi_width``, and
``roi_height`` in ``vision_config.yaml`` until the grid lines align
with the physical OT-2 deck boundaries.

Usage
-----
    python vision_tests/scripts/calibrate_slots.py

The script processes all images in ``raw/`` and saves grid-overlay
images to ``outputs/annotated/<stem>_grid_calibration.jpg``.

Workflow
--------
1. Run this script.
2. Open the calibration images in an image viewer.
3. Check whether the yellow grid lines match the deck slot edges.
4. If not, edit ``configs/vision_config.yaml`` → ``slot_grid`` section:
   - ``roi_x`` / ``roi_y`` → shift the grid origin.
   - ``roi_width`` / ``roi_height`` → resize the grid area.
5. Re-run until grid lines match the physical slots.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import yaml
from rich.console import Console

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


def draw_calibration_grid(
    bgr,
    grid_cfg: dict,
    ann_cfg: dict,
) -> "np.ndarray":
    """Draw the slot grid with thick lines and large labels for calibration."""
    import numpy as np  # noqa: F811

    vis = bgr.copy()
    h, w = vis.shape[:2]

    roi_x = grid_cfg.get("roi_x", 0)
    roi_y = grid_cfg.get("roi_y", 0)
    roi_w = grid_cfg.get("roi_width") or (w - roi_x)
    roi_h = grid_cfg.get("roi_height") or (h - roi_y)
    rows = grid_cfg.get("rows", 4)
    cols = grid_cfg.get("cols", 3)
    layout = grid_cfg.get("slot_layout", [
        [10, 11, 12], [7, 8, 9], [4, 5, 6], [1, 2, 3],
    ])

    cell_w = roi_w / cols
    cell_h = roi_h / rows

    line_color = tuple(ann_cfg.get("slot_line_color", [0, 255, 255]))
    text_color = (255, 255, 255)

    # Draw ROI bounding rectangle (thick, distinct colour)
    cv2.rectangle(
        vis, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h),
        (0, 0, 255), 3,
    )

    # Vertical grid lines
    for c in range(cols + 1):
        x = int(roi_x + c * cell_w)
        cv2.line(vis, (x, roi_y), (x, roi_y + roi_h), line_color, 2)

    # Horizontal grid lines
    for r in range(rows + 1):
        y = int(roi_y + r * cell_h)
        cv2.line(vis, (roi_x, y), (roi_x + roi_w, y), line_color, 2)

    # Slot labels — large, centred
    for r_idx, row in enumerate(layout):
        for c_idx, slot_num in enumerate(row):
            cx = int(roi_x + (c_idx + 0.5) * cell_w)
            cy = int(roi_y + (r_idx + 0.5) * cell_h)
            label = f"Slot {slot_num}"
            (tw, th), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2,
            )
            # Background rectangle for readability
            cv2.rectangle(
                vis,
                (cx - tw // 2 - 4, cy - th // 2 - 4),
                (cx + tw // 2 + 4, cy + th // 2 + 4),
                (0, 0, 0), -1,
            )
            cv2.putText(
                vis, label,
                (cx - tw // 2, cy + th // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                text_color, 2, cv2.LINE_AA,
            )

    # ROI info text in top-left corner
    info = f"ROI: x={roi_x} y={roi_y} w={roi_w} h={roi_h}"
    cv2.putText(
        vis, info, (10, 20),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
        (0, 255, 0), 1, cv2.LINE_AA,
    )

    return vis


def main() -> None:
    console.rule("[bold cyan]Slot Grid Calibration[/]")

    cfg = load_config()
    grid_cfg = cfg.get("slot_grid", {})
    ann_cfg = cfg.get("annotation", {})

    raw_dir = resolve_dir(cfg, "raw_dir", "vision_tests/raw")
    annotated_dir = resolve_dir(cfg, "annotated_dir", "vision_tests/outputs/annotated")

    images = find_images(raw_dir)
    if not images:
        console.print(f"[bold red]No images in {raw_dir}[/]")
        sys.exit(0)

    console.print(f"Found [green]{len(images)}[/] image(s)\n")
    console.print(
        "[dim]Grid config:[/]  "
        f"roi_x={grid_cfg.get('roi_x', 0)}  "
        f"roi_y={grid_cfg.get('roi_y', 0)}  "
        f"roi_width={grid_cfg.get('roi_width', 'full')}  "
        f"roi_height={grid_cfg.get('roi_height', 'full')}\n"
    )

    for img_path in images:
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            console.print(f"  [red]Skipping {img_path.name}[/]")
            continue

        vis = draw_calibration_grid(bgr, grid_cfg, ann_cfg)
        out = annotated_dir / f"{img_path.stem}_grid_calibration.jpg"
        cv2.imwrite(str(out), vis)
        console.print(f"  Saved --> [cyan]{out.name}[/]")

    console.print()
    console.print(
        "[bold yellow]Next steps:[/]\n"
        "  1. Open the calibration images in an image viewer.\n"
        "  2. Check if the yellow grid lines align with slot edges.\n"
        "  3. Edit configs/vision_config.yaml --> slot_grid section.\n"
        "  4. Re-run this script until alignment is correct.\n"
    )
    console.rule("[bold cyan]Done[/]")


if __name__ == "__main__":
    main()
