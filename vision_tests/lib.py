"""
vision_tests/lib.py
===================
Shared classical-CV pipeline for the analysis scripts in ``vision_tests/``.

This consolidates the threshold -> morphology -> contour -> measure pipeline that
was previously copy-pasted across ``scripts/*.py`` (see docs/computer_vision.md
gotcha #4) plus a per-contour colour sampler. New code (``verify_print_droplets``)
imports from here; the older scripts are left untouched.

No machine learning - purely geometric measurement (area, circularity, aspect
ratio) and colour means (RGB/HSV) inside each detected blob.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "vision_tests" / "configs" / "vision_config.yaml"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


# ── Config / IO helpers ─────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_dir(cfg: dict, key: str, fallback: str) -> Path:
    d = (PROJECT_ROOT / cfg.get(key, fallback)).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_images(raw_dir: Path) -> List[Path]:
    if not raw_dir.exists():
        return []
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


# ── Detection pipeline ──────────────────────────────────────────────────────────

def create_binary_mask(
    bgr: np.ndarray,
    *,
    method: str = "otsu",
    blur_k: int = 5,
    morph_k: int = 3,
    morph_iter: int = 2,
    fixed_thresh: int = 127,
    invert: bool = True,
) -> np.ndarray:
    """Grayscale -> blur -> threshold -> morphology. ``invert`` makes objects
    darker-than-background become the foreground (droplets on white paper)."""
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
    min_area: int = 150,
    max_area: int = 100000,
    max_aspect_ratio: float = 3.0,
    min_circularity: float = 0.0,
) -> List[Dict[str, Any]]:
    """Find external contours and compute geometric properties for each blob."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results: List[Dict[str, Any]] = []
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

        obj_id += 1
        results.append({
            "object_id": obj_id,
            "contour": cnt,
            "area_pixels": round(float(area), 1),
            "perimeter_pixels": round(float(perimeter), 1),
            "bbox_x": int(x), "bbox_y": int(y), "bbox_w": int(w), "bbox_h": int(h),
            "aspect_ratio": round(float(aspect_ratio), 3),
            "circularity": round(float(circularity), 3),
            "centroid_x": cx, "centroid_y": cy,
        })
    return results


def sample_contour_color(bgr: np.ndarray, contour: np.ndarray) -> Dict[str, Any]:
    """Mean colour inside a contour, in RGB and HSV, plus brightness (0-255)."""
    mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, thickness=-1)
    mean_bgr = cv2.mean(bgr, mask=mask)[:3]
    b, g, r = (round(float(c), 1) for c in mean_bgr)
    hsv = cv2.cvtColor(np.uint8([[[mean_bgr[0], mean_bgr[1], mean_bgr[2]]]]), cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = (int(hsv[0]), int(hsv[1]), int(hsv[2]))
    brightness = round((r + g + b) / 3.0, 1)
    return {
        "r": r, "g": g, "b": b,
        "h": h, "s": s, "v": v,
        "brightness": brightness,
    }
