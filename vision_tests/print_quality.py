"""Grid-aware quality control for food-dye droplets printed on paper.

The original :mod:`vision_tests.lib` contour pipeline is useful for isolated,
high-contrast blobs.  Printed dilution series need a stronger prior: the robot
prints a regular grid even when the palest droplets are barely visible.  This
module therefore measures every expected grid position instead of counting only
thresholded contours.

All functions are offline image analysis.  Nothing in this module connects to
an OT-2 or starts robot motion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class GridDefinition:
    """Regular print grid expressed in normalized image coordinates.

    ``origin`` is row 1 / column 1.  The two step vectors point toward the next
    dilution row and the next replicate/color column respectively.  Normalized
    coordinates keep the fixed OT-2 profile valid if a camera endpoint returns
    the same view at a different resolution.
    """

    rows: int
    columns: int
    origin: tuple[float, float]
    row_step: tuple[float, float]
    column_step: tuple[float, float]

    def centers(self, width: int, height: int) -> np.ndarray:
        origin = np.asarray(self.origin, dtype=np.float64)
        row_step = np.asarray(self.row_step, dtype=np.float64)
        column_step = np.asarray(self.column_step, dtype=np.float64)
        scale = np.asarray([width, height], dtype=np.float64)
        result = np.empty((self.rows, self.columns, 2), dtype=np.float64)
        for row in range(self.rows):
            for column in range(self.columns):
                result[row, column] = (
                    origin + row * row_step + column * column_step
                ) * scale
        return result


@dataclass
class DropletMeasurement:
    row: int
    column: int
    centroid_x: float
    centroid_y: float
    assessable: bool
    present: bool
    detection_status: str
    presence_confidence: float
    color_contrast: float
    background_noise: float
    color_name: str
    color_reliable: bool
    direct_color_name: str
    color_method: str
    color_strength: float
    color_delta_a: float
    color_delta_b: float
    color_rgb: tuple[int, int, int]
    hue: int
    saturation: int
    value: int
    area_pixels: float
    equivalent_diameter_pixels: float
    circularity: float | None
    aspect_ratio: float | None
    shape: str
    shape_reliable: bool
    coffee_ring_ratio: float | None
    coffee_ring_contrast: float | None
    coffee_ring: str
    coffee_ring_reliable: bool

    def serializable(self) -> dict[str, Any]:
        row = asdict(self)
        row["color_rgb"] = list(self.color_rgb)
        return row


def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 else value + 1


def _grid_pitch(centers: np.ndarray) -> float:
    distances: list[float] = []
    if centers.shape[0] > 1:
        distances.extend(
            np.linalg.norm(centers[1:] - centers[:-1], axis=2).ravel().tolist()
        )
    if centers.shape[1] > 1:
        distances.extend(
            np.linalg.norm(centers[:, 1:] - centers[:, :-1], axis=2).ravel().tolist()
        )
    if not distances:
        raise ValueError("The print grid needs at least two expected positions.")
    return float(np.median(distances))


def _color_residual(bgr: np.ndarray, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    """Return LAB data and a cheap chroma map used only for center refinement.

    Per-droplet presence uses a local paper-background estimate in
    :func:`_measure_one`.  Keeping the full-frame map simple makes 12-megapixel
    phone images practical to run on a laptop.
    """

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    residual = np.linalg.norm(lab[:, :, 1:3] - 128.0, axis=2)
    residual = cv2.GaussianBlur(residual, (0, 0), max(0.6, pitch * 0.025))
    return lab, residual


def _refine_center(
    score: np.ndarray,
    center: np.ndarray,
    search_radius: float,
) -> np.ndarray:
    """Move a nominal lattice point slightly toward its local color signal."""

    height, width = score.shape
    cx, cy = (float(center[0]), float(center[1]))
    radius = max(1, int(round(search_radius)))
    x0, x1 = max(0, int(cx) - radius), min(width, int(cx) + radius + 1)
    y0, y1 = max(0, int(cy) - radius), min(height, int(cy) + radius + 1)
    patch = score[y0:y1, x0:x1]
    if patch.size == 0:
        return center.copy()

    yy, xx = np.ogrid[y0:y1, x0:x1]
    local_disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= search_radius**2
    values = patch[local_disk]
    if values.size < 4:
        return center.copy()
    floor = float(np.percentile(values, 65))
    weights = np.where(local_disk, np.maximum(patch - floor, 0.0), 0.0)
    total = float(weights.sum())
    if total <= 1e-6:
        return center.copy()
    refined_x = float((weights * xx).sum() / total)
    refined_y = float((weights * yy).sum() / total)
    shift = np.asarray([refined_x - cx, refined_y - cy])
    limit = search_radius * 0.8
    norm = float(np.linalg.norm(shift))
    if norm > limit:
        shift *= limit / norm
    return center + shift


def _named_color(rgb: tuple[int, int, int]) -> tuple[str, int, int, int]:
    pixel = np.uint8([[[rgb[2], rgb[1], rgb[0]]]])
    hue, saturation, value = (
        int(v) for v in cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]
    )
    if saturation < 12:
        name = "neutral/too-faint"
    elif hue < 8 or hue >= 170:
        name = "pink/red"
    elif hue < 27:
        name = "orange"
    elif hue < 42:
        name = "yellow"
    elif hue < 82:
        name = "green"
    elif hue < 108:
        name = "blue/cyan"
    elif hue < 137:
        name = "blue"
    elif hue < 170:
        name = "purple"
    else:
        name = "pink/red"
    return name, hue, saturation, value


def _named_color_from_lab_delta(delta_a: float, delta_b: float) -> str:
    """Name dye color from its change relative to the local paper.

    LAB's ``a`` axis runs green-to-magenta and ``b`` runs blue-to-yellow.
    Subtracting the paper background removes warm illumination and paper cast,
    which otherwise dominate very dilute photographed droplets.
    """

    angle = float(np.degrees(np.arctan2(delta_b, delta_a)))
    if -157.5 <= angle < -67.5:
        return "blue/cyan"
    if -67.5 <= angle < -15.0:
        return "purple"
    if -15.0 <= angle < 30.0:
        return "pink/red"
    if 30.0 <= angle < 75.0:
        return "orange"
    if 75.0 <= angle < 120.0:
        return "yellow"
    if 120.0 <= angle < 165.0:
        return "green"
    return "cyan/green"


def _select_contour(
    mask: np.ndarray,
    local_center: tuple[float, float],
) -> np.ndarray | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cx, cy = local_center

    def rank(contour: np.ndarray) -> float:
        area = float(cv2.contourArea(contour))
        moments = cv2.moments(contour)
        if moments["m00"]:
            x = moments["m10"] / moments["m00"]
            y = moments["m01"] / moments["m00"]
        else:
            x, y, w, h = cv2.boundingRect(contour)
            x, y = x + w / 2.0, y + h / 2.0
        distance = float(np.hypot(x - cx, y - cy))
        return area / (1.0 + distance)

    return max(contours, key=rank)


def _measure_one(
    bgr: np.ndarray,
    lab: np.ndarray,
    score: np.ndarray,
    nominal_center: np.ndarray,
    pitch: float,
    row: int,
    column: int,
    presence_threshold: float,
) -> DropletMeasurement:
    height, width = score.shape
    center = _refine_center(score, nominal_center, pitch * 0.12)
    cx, cy = float(center[0]), float(center[1])
    half = max(3, int(round(pitch * 0.62)))
    x0, x1 = max(0, int(cx) - half), min(width, int(cx) + half + 1)
    y0, y1 = max(0, int(cy) - half), min(height, int(cy) + half + 1)
    local_lab = lab[y0:y1, x0:x1]
    local_bgr = bgr[y0:y1, x0:x1]

    yy, xx = np.ogrid[y0:y1, x0:x1]
    distance = np.hypot(xx - cx, yy - cy)
    spot_radius = pitch * 0.49
    inner = distance <= spot_radius
    # The diffused spots nearly fill a grid cell, especially in phone images.
    # Use the cell corners as local paper background; a narrow annulus directly
    # outside the nominal radius can still sit on the colored coffee-ring rim.
    dx = np.abs(xx - cx)
    dy = np.abs(yy - cy)
    background = (dx >= pitch * 0.46) & (dy >= pitch * 0.46)
    if int(background.sum()) < 8:
        background = distance >= pitch * 0.64

    if int(background.sum()):
        background_lightness = local_lab[:, :, 0][background]
        background_chroma = np.linalg.norm(
            local_lab[:, :, 1:3][background] - 128.0, axis=1
        )
        lightness_floor = float(np.percentile(background_lightness, 45))
        chroma_ceiling = float(np.percentile(background_chroma, 60))
        local_chroma = np.linalg.norm(local_lab[:, :, 1:3] - 128.0, axis=2)
        paper_background = (
            background
            & (local_lab[:, :, 0] >= lightness_floor)
            & (local_chroma <= chroma_ceiling)
        )
        if int(paper_background.sum()) < 8:
            paper_background = background
        background_lab = np.median(local_lab[paper_background], axis=0)
    else:
        paper_background = background
        background_lab = np.median(local_lab.reshape(-1, 3), axis=0)
    # Presence is a local color difference from the surrounding paper.  This is
    # essential for coffee rings: a colored rim may occupy only a small fraction
    # of the cell while the center remains almost the same color as the paper.
    local_delta = local_lab - background_lab
    # Dilute purple/blue rims can become nearly neutral gray.  Include a reduced
    # lightness term so those visible rings are measurable without letting
    # ordinary paper shading dominate the color signal.
    local_score = np.sqrt(
        np.square(local_delta[:, :, 1])
        + np.square(local_delta[:, :, 2])
        + np.square(local_delta[:, :, 0] * 0.35)
    )
    inner_values = local_score[inner]
    background_values = local_score[paper_background]
    if inner_values.size == 0:
        raise ValueError(f"Grid position R{row} C{column} lies outside the image.")
    if background_values.size == 0:
        background_values = np.asarray([0.0], dtype=np.float32)

    # A narrow coffee-ring rim (or a very small low-resolution OT-2 spot) can
    # occupy less than ten percent of the expected cell, so use an upper-tail
    # statistic instead of a mean/median that would erase it.
    signal = float(np.percentile(inner_values, 96))
    # Corner samples can contain a few fibers or the diffuse tail of an adjacent
    # spot.  A robust upper-middle percentile represents paper noise without
    # allowing those sparse colored pixels to cancel a real rim signal.
    noise = float(np.percentile(background_values, 80))
    contrast = max(0.0, signal - noise)
    present = contrast >= presence_threshold
    if present:
        detection_status = "detected"
    elif contrast >= presence_threshold * 0.65:
        detection_status = "borderline"
    else:
        detection_status = "not-detected"
    confidence = float(np.clip(contrast / max(presence_threshold * 2.0, 1e-6), 0, 1))

    color_cutoff = max(
        float(np.percentile(background_values, 95)) + 0.35,
        float(np.percentile(inner_values, 72)),
    )
    color_pixels = inner & (local_score >= color_cutoff)
    if int(color_pixels.sum()) < 3:
        color_pixels = inner & (local_score >= np.percentile(inner_values, 85))
    weights = local_score[color_pixels].astype(np.float64) + 1e-3
    colors = local_bgr[color_pixels].astype(np.float64)
    if colors.size:
        mean_bgr = np.average(colors, axis=0, weights=weights)
        mean_delta = np.average(local_delta[color_pixels], axis=0, weights=weights)
    else:
        mean_bgr = np.asarray([0.0, 0.0, 0.0])
        mean_delta = np.asarray([0.0, 0.0, 0.0])
    rgb = tuple(int(np.clip(round(v), 0, 255)) for v in mean_bgr[::-1])
    direct_color_name, hue, saturation, value = _named_color(rgb)
    delta_a = float(mean_delta[1])
    delta_b = float(mean_delta[2])
    color_strength = float(np.hypot(delta_a, delta_b))
    color_name = _named_color_from_lab_delta(delta_a, delta_b)
    color_method = "relative-to-local-paper"
    # A hue from an almost-white/gray patch is numerically defined but not
    # scientifically meaningful.  Report that limitation instead of assigning
    # a confident (and potentially wrong) color name to a dilute spot.
    color_reliable = bool(
        present
        and color_strength >= 1.5
        and contrast >= max(presence_threshold * 1.35, 1.0)
    )
    if not present:
        color_name = "not-detected"
        color_method = "not-detected"
    elif not color_reliable:
        color_name = "too-faint-for-reliable-color"

    segmentation_cutoff = max(
        float(np.percentile(background_values, 99)) + 0.5,
        float(np.percentile(inner_values, 85)),
    )
    segmentation = ((local_score >= segmentation_cutoff) & inner).astype(np.uint8) * 255
    kernel_size = _odd(max(1, int(round(pitch * 0.045))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    segmentation = cv2.morphologyEx(segmentation, cv2.MORPH_CLOSE, kernel)
    signal_points = np.argwhere(segmentation > 0)
    contour = None
    if len(signal_points) >= 3:
        xy_points = signal_points[:, ::-1].astype(np.int32).reshape(-1, 1, 2)
        contour = cv2.convexHull(xy_points)

    area = 0.0
    diameter = 0.0
    circularity: float | None = None
    aspect_ratio: float | None = None
    shape = "unresolved" if present else "not-detected"
    shape_reliable = False
    ring_ratio: float | None = None
    ring_contrast: float | None = None
    coffee_ring = "unresolved" if present else "not-detected"
    coffee_ring_reliable = False

    if present and contour is not None:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        diameter = float(2.0 * np.sqrt(max(area, 0.0) / np.pi))
        if perimeter > 0:
            circularity = float(np.clip(4.0 * np.pi * area / perimeter**2, 0, 1))
        if len(contour) >= 5:
            (_, _), (axis_a, axis_b), _ = cv2.fitEllipse(contour)
            if min(axis_a, axis_b) > 0:
                aspect_ratio = float(max(axis_a, axis_b) / min(axis_a, axis_b))
        else:
            _, _, contour_w, contour_h = cv2.boundingRect(contour)
            if min(contour_w, contour_h) > 0:
                aspect_ratio = float(max(contour_w, contour_h) / min(contour_w, contour_h))

        shape_reliable = diameter >= 15.0 and area >= 80.0
        if not shape_reliable:
            shape = "uncertain-low-resolution"
        elif (circularity or 0.0) >= 0.62 and (aspect_ratio or 99.0) <= 1.28:
            shape = "round"
        else:
            shape = "blob/irregular"

        # Evaluate the footprint center separately from the strongest annulus.
        # This works for fragmented coffee rings whose individual contours do
        # not form a single closed circle.
        central = distance <= pitch * 0.14
        edge_bands = [
            np.abs(distance - pitch * fraction) <= pitch * 0.025
            for fraction in np.linspace(0.18, 0.43, 11)
        ]
        center_signal = float(np.mean(local_score[central])) if central.any() else 0.0
        # Food-dye rings are often broken/feathered rather than uniform.  The
        # upper quartile captures a real rim without requiring every angle to
        # be colored, while remaining far less sensitive than a maximum.
        edge_values = [
            float(np.percentile(local_score[band], 75))
            for band in edge_bands
            if int(band.sum())
        ]
        edge_signal = max(edge_values, default=0.0)
        ring_ratio = float((edge_signal + 0.25) / (center_signal + 0.25))
        ring_contrast = float(edge_signal - center_signal)
        coffee_ring_reliable = diameter >= 24.0 and int(central.sum()) >= 40
        if not coffee_ring_reliable:
            coffee_ring = "uncertain-low-resolution"
        elif ring_ratio >= 1.42 and ring_contrast >= 1.5:
            coffee_ring = "strong"
        elif (
            ring_ratio >= 1.20 and ring_contrast >= 0.7
        ) or (
            ring_ratio >= 1.15 and ring_contrast >= 1.5
        ):
            coffee_ring = "possible"
        else:
            coffee_ring = "not-evident"

    return DropletMeasurement(
        row=row,
        column=column,
        centroid_x=round(cx, 2),
        centroid_y=round(cy, 2),
        assessable=True,
        present=present,
        detection_status=detection_status,
        presence_confidence=round(confidence, 4),
        color_contrast=round(contrast, 4),
        background_noise=round(noise, 4),
        color_name=color_name,
        color_reliable=color_reliable,
        direct_color_name=direct_color_name,
        color_method=color_method,
        color_strength=round(color_strength, 4),
        color_delta_a=round(delta_a, 4),
        color_delta_b=round(delta_b, 4),
        color_rgb=rgb,
        hue=hue,
        saturation=saturation,
        value=value,
        area_pixels=round(area, 2),
        equivalent_diameter_pixels=round(diameter, 2),
        circularity=round(circularity, 4) if circularity is not None else None,
        aspect_ratio=round(aspect_ratio, 4) if aspect_ratio is not None else None,
        shape=shape,
        shape_reliable=shape_reliable,
        coffee_ring_ratio=round(ring_ratio, 4) if ring_ratio is not None else None,
        coffee_ring_contrast=(
            round(ring_contrast, 4) if ring_contrast is not None else None
        ),
        coffee_ring=coffee_ring,
        coffee_ring_reliable=coffee_ring_reliable,
    )


def analyze_print(
    bgr: np.ndarray,
    grid: GridDefinition,
    *,
    source: str,
    presence_threshold: float,
    unassessable_rows: Iterable[int] = (),
    unassessable_positions: Iterable[Iterable[int]] = (),
) -> dict[str, Any]:
    """Measure every expected droplet and return a serializable analysis."""

    if bgr is None or bgr.size == 0:
        raise ValueError("The input image is empty or unreadable.")
    height, width = bgr.shape[:2]
    centers = grid.centers(width, height)
    if (
        np.any(centers[:, :, 0] < 0)
        or np.any(centers[:, :, 0] >= width)
        or np.any(centers[:, :, 1] < 0)
        or np.any(centers[:, :, 1] >= height)
    ):
        raise ValueError("One or more expected grid positions lie outside the image.")

    pitch = _grid_pitch(centers)
    lab, score = _color_residual(bgr, pitch)
    droplets = [
        _measure_one(
            bgr,
            lab,
            score,
            centers[row, column],
            pitch,
            row + 1,
            column + 1,
            presence_threshold,
        )
        for column in range(grid.columns)
        for row in range(grid.rows)
    ]

    excluded_rows = {int(row) for row in unassessable_rows}
    excluded_positions = {
        (int(position[0]), int(position[1])) for position in unassessable_positions
    }
    for droplet in droplets:
        if (
            droplet.row not in excluded_rows
            and (droplet.row, droplet.column) not in excluded_positions
        ):
            continue
        droplet.assessable = False
        droplet.present = False
        droplet.detection_status = "unassessable-background-artifact"
        droplet.color_name = "unassessable"
        droplet.color_reliable = False
        droplet.color_method = "unassessable"
        droplet.shape = "unassessable"
        droplet.shape_reliable = False
        droplet.coffee_ring = "unassessable"
        droplet.coffee_ring_reliable = False

    column_summaries = []
    for column in range(1, grid.columns + 1):
        values = [d for d in droplets if d.column == column]
        color_candidates = sorted(
            (
                d
                for d in values
                if d.present
                and d.direct_color_name != "neutral/too-faint"
                and d.saturation >= 15
            ),
            key=lambda d: d.saturation * max(d.color_contrast, 0.1),
            reverse=True,
        )[:3]
        color_scores: dict[str, float] = {}
        for candidate in color_candidates:
            label = (
                "blue/cyan"
                if candidate.direct_color_name == "blue"
                else candidate.direct_color_name
            )
            color_scores[label] = color_scores.get(label, 0.0) + (
                candidate.saturation * max(candidate.color_contrast, 0.1)
            )
        column_color = max(color_scores, key=color_scores.get) if color_scores else None
        total_color_score = sum(color_scores.values())
        column_color_reliable = bool(
            column_color
            and len(color_candidates) >= 2
            and color_scores[column_color] / max(total_color_score, 1e-6) >= 0.5
        )
        if column_color_reliable:
            for droplet in values:
                if not droplet.present:
                    continue
                droplet.color_name = column_color
                droplet.color_reliable = True
                droplet.color_method = "column-consensus-from-strongest-drops"
        unassessable = [d.row for d in values if not d.assessable]
        missing = [d.row for d in values if d.assessable and not d.present]
        borderline = [d.row for d in values if d.detection_status == "borderline"]
        column_summaries.append(
            {
                "column": column,
                "expected": grid.rows,
                "assessable": grid.rows - len(unassessable),
                "found": sum(1 for d in values if d.present),
                "missing_rows": missing,
                "potentially_missing_or_below_detection_rows": missing,
                "borderline_rows": borderline,
                "unassessable_rows": unassessable,
                "color_consensus": column_color,
                "color_consensus_reliable": column_color_reliable,
                "status": (
                    "CHECK" if missing else "LIMITED" if unassessable else "PASS"
                ),
            }
        )

    assessable = [d for d in droplets if d.assessable]
    unassessable = [d for d in droplets if not d.assessable]
    present = [d for d in assessable if d.present]
    borderline = [d for d in droplets if d.detection_status == "borderline"]
    color_reliable = [d for d in present if d.color_reliable]
    shape_reliable = [d for d in present if d.shape_reliable]
    ring_reliable = [d for d in present if d.coffee_ring_reliable]
    diameters = [d.equivalent_diameter_pixels for d in present if d.equivalent_diameter_pixels]
    summary = {
        "source": source,
        "image_width": width,
        "image_height": height,
        "expected_droplets": grid.rows * grid.columns,
        "assessable_droplets": len(assessable),
        "unassessable_droplets": len(unassessable),
        "found_droplets": len(present),
        "missing_droplets": len(assessable) - len(present),
        "potentially_missing_or_below_detection": len(assessable) - len(present),
        "borderline_droplets": len(borderline),
        "count_status": (
            "PASS"
            if len(present) == len(assessable) and not unassessable
            else "CHECK"
        ),
        "grid_rows": grid.rows,
        "grid_columns": grid.columns,
        "grid_pitch_pixels": round(pitch, 3),
        "median_measured_diameter_pixels": (
            round(float(np.median(diameters)), 3) if diameters else 0.0
        ),
        "shape_reliable_droplets": len(shape_reliable),
        "coffee_ring_reliable_droplets": len(ring_reliable),
        "color_reliable_droplets": len(color_reliable),
        "color_assessment_status": (
            "SUPPORTED" if len(color_reliable) == len(present) and present else "LIMITED"
        ),
        "shape_assessment_status": (
            "SUPPORTED" if len(shape_reliable) == len(present) and present else "LIMITED"
        ),
        "coffee_ring_assessment_status": (
            "SUPPORTED" if len(ring_reliable) == len(present) and present else "LIMITED"
        ),
        "columns": column_summaries,
    }
    return {
        "summary": summary,
        "grid": asdict(grid),
        "presence_threshold": presence_threshold,
        "droplets": [d.serializable() for d in droplets],
    }


def annotate_analysis(bgr: np.ndarray, analysis: dict[str, Any]) -> np.ndarray:
    """Create an auditable grid overlay for a completed analysis."""

    result = bgr.copy()
    height, width = result.shape[:2]
    summary = analysis["summary"]
    pitch = float(summary["grid_pitch_pixels"])
    radius = max(3, int(round(pitch * 0.43)))
    font_scale = max(0.32, min(1.0, pitch / 90.0))
    thickness = max(1, int(round(min(width, height) / 1000)))
    by_position = {
        (int(d["row"]), int(d["column"])): d for d in analysis["droplets"]
    }

    def status_color(droplet: dict[str, Any]) -> tuple[int, int, int]:
        if droplet["present"]:
            return (40, 190, 40)
        if droplet["detection_status"] == "borderline":
            return (0, 180, 255)
        if droplet["detection_status"].startswith("unassessable"):
            return (150, 150, 150)
        return (20, 20, 230)

    for droplet in analysis["droplets"]:
        center = (int(round(droplet["centroid_x"])), int(round(droplet["centroid_y"])))
        color = status_color(droplet)
        cv2.circle(result, center, radius, color, thickness)
        label = f"C{droplet['column']}R{droplet['row']}"
        cv2.putText(
            result,
            label,
            (center[0] - radius, center[1] - radius - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
            cv2.LINE_AA,
        )

    grid = analysis["grid"]
    rows, columns = int(grid["rows"]), int(grid["columns"])
    for column in range(1, columns + 1):
        first = by_position[(1, column)]
        last = by_position[(rows, column)]
        cv2.line(
            result,
            (int(first["centroid_x"]), int(first["centroid_y"])),
            (int(last["centroid_x"]), int(last["centroid_y"])),
            (255, 180, 0),
            thickness,
        )
    return result


def make_montage(
    bgr: np.ndarray,
    analysis: dict[str, Any],
    *,
    tile_size: int = 180,
) -> np.ndarray:
    """Return a column-by-row crop sheet for quick human verification."""

    pitch = float(analysis["summary"]["grid_pitch_pixels"])
    half = max(4, int(round(pitch * 0.52)))
    height, width = bgr.shape[:2]
    tiles: list[np.ndarray] = []
    droplets = sorted(
        analysis["droplets"], key=lambda d: (int(d["row"]), int(d["column"]))
    )
    for droplet in droplets:
        cx, cy = int(round(droplet["centroid_x"])), int(round(droplet["centroid_y"]))
        x0, x1 = max(0, cx - half), min(width, cx + half + 1)
        y0, y1 = max(0, cy - half), min(height, cy + half + 1)
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            crop = np.zeros((tile_size, tile_size, 3), dtype=np.uint8)
        else:
            crop = cv2.resize(crop, (tile_size, tile_size), interpolation=cv2.INTER_CUBIC)
        if droplet["present"]:
            border = (40, 190, 40)
        elif droplet["detection_status"] == "borderline":
            border = (0, 180, 255)
        elif droplet["detection_status"].startswith("unassessable"):
            border = (150, 150, 150)
        else:
            border = (20, 20, 230)
        crop = cv2.copyMakeBorder(crop, 28, 3, 3, 3, cv2.BORDER_CONSTANT, value=border)
        label = (
            f"C{droplet['column']} R{droplet['row']} "
            f"{droplet['detection_status']} {droplet['color_name']}"
        )
        cv2.putText(
            crop,
            label[:34],
            (5, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(crop)

    columns = int(analysis["summary"]["grid_columns"])
    rows = int(analysis["summary"]["grid_rows"])
    montage_rows = []
    for row in range(rows):
        montage_rows.append(cv2.hconcat(tiles[row * columns : (row + 1) * columns]))
    return cv2.vconcat(montage_rows)


def flatten_droplets(analyses: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten multiple image analyses for CSV output."""

    rows: list[dict[str, Any]] = []
    for analysis in analyses:
        image_name = analysis.get("image", "")
        benchmark = analysis.get("benchmark", "")
        source = analysis["summary"]["source"]
        for droplet in analysis["droplets"]:
            row = {"benchmark": benchmark, "image": image_name, "source": source}
            row.update(droplet)
            row["color_rgb"] = ",".join(str(v) for v in row["color_rgb"])
            rows.append(row)
    return rows


def read_image(path: Path) -> np.ndarray:
    """Read a JPEG/PNG with explicit failure instead of silently returning None."""

    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Could not read image: {path}")
    return bgr
