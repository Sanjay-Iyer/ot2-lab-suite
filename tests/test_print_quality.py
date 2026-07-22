from __future__ import annotations

import cv2
import numpy as np

from vision_tests.print_quality import GridDefinition, analyze_print


def _synthetic_grid(*, missing: tuple[int, int] | None = None) -> tuple[np.ndarray, GridDefinition]:
    image = np.full((520, 440, 3), 242, dtype=np.uint8)
    grid = GridDefinition(
        rows=8,
        columns=3,
        origin=(0.25, 0.16),
        row_step=(0.0, 0.095),
        column_step=(0.25, 0.0),
    )
    centers = grid.centers(image.shape[1], image.shape[0])
    for row in range(grid.rows):
        for column in range(grid.columns):
            if missing == (row + 1, column + 1):
                continue
            center = tuple(int(round(v)) for v in centers[row, column])
            color = (180 + row * 5, 80 + row * 4, 25 + row * 3)
            cv2.circle(image, center, 16, color, -1, cv2.LINE_AA)
    return image, grid


def test_counts_every_expected_grid_position() -> None:
    image, grid = _synthetic_grid()
    result = analyze_print(image, grid, source="synthetic", presence_threshold=1.0)
    assert result["summary"]["expected_droplets"] == 24
    assert result["summary"]["found_droplets"] == 24
    assert all(column["found"] == 8 for column in result["summary"]["columns"])
    assert all(droplet["color_name"] == "blue/cyan" for droplet in result["droplets"])


def test_reports_missing_row_in_its_column() -> None:
    image, grid = _synthetic_grid(missing=(5, 2))
    result = analyze_print(image, grid, source="synthetic", presence_threshold=1.0)
    assert result["summary"]["found_droplets"] == 23
    assert result["summary"]["columns"][1]["missing_rows"] == [5]
    assert result["summary"]["columns"][1][
        "potentially_missing_or_below_detection_rows"
    ] == [5]


def test_marks_tiny_droplets_as_low_resolution_for_shape_and_ring() -> None:
    image = np.full((120, 120, 3), 245, dtype=np.uint8)
    grid = GridDefinition(
        rows=2,
        columns=2,
        origin=(0.35, 0.35),
        row_step=(0.0, 0.25),
        column_step=(0.25, 0.0),
    )
    for center in grid.centers(120, 120).reshape(-1, 2):
        cv2.circle(image, tuple(int(v) for v in center), 3, (180, 60, 20), -1)
    result = analyze_print(image, grid, source="tiny", presence_threshold=0.5)
    present = [d for d in result["droplets"] if d["present"]]
    assert present
    assert all(not d["shape_reliable"] for d in present)
    assert all(not d["coffee_ring_reliable"] for d in present)
    assert all(d["shape"] == "uncertain-low-resolution" for d in present)


def test_separates_unassessable_background_row_from_missing_droplets() -> None:
    image, grid = _synthetic_grid()
    result = analyze_print(
        image,
        grid,
        source="synthetic",
        presence_threshold=1.0,
        unassessable_rows=[1],
        unassessable_positions=[(2, 1)],
    )
    assert result["summary"]["expected_droplets"] == 24
    assert result["summary"]["assessable_droplets"] == 20
    assert result["summary"]["unassessable_droplets"] == 4
    assert result["summary"]["found_droplets"] == 20
    assert result["summary"]["missing_droplets"] == 0
    assert result["summary"]["columns"][0]["unassessable_rows"] == [1, 2]
    assert all(
        column["unassessable_rows"] == [1]
        for column in result["summary"]["columns"][1:]
    )
