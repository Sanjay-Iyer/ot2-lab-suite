"""
vision/physical_setup_image_validation.py
==========================================
Pre-flight deck verification gate.

Before an experiment runs, this module captures (or loads) an overhead
image of the OT-2 deck, zones it into the 11 usable slot regions, classifies
the footprint sitting in each slot with OpenCV, and cross-checks the result
against the labware layout declared in the workflow configuration YAML.

If the physical deck does not match the configuration the script prints a
detailed failure report and exits non-zero, so it can be wired into a
protocol launcher as a hard gate::

    python vision/physical_setup_image_validation.py \
        --workflow configs/workflows/defaults/vial_dilution_print.yaml \
        --image    vision/data/raw/deck_latest.jpg

    # or capture a fresh frame from the robot first
    python vision/physical_setup_image_validation.py \
        --workflow configs/workflows/defaults/vial_dilution_print.yaml \
        --capture

Honest scope note
-----------------
An overhead camera resolves *footprints*, not catalogue part numbers. Two
labware items that share a footprint (e.g. the dilution ``plate`` and the
``paper`` proxy are both ``corning_96_wellplate_360ul_custom``) are
indistinguishable to this gate. Validation therefore runs at *footprint*
granularity: ``load_name`` is mapped to a footprint class via
``footprint_map`` (config) and slots are compared on that class. This still
catches the failures this gate exists to catch — an empty slot that should be
loaded, or a footprint sitting in the wrong slot — without pretending the
camera can read a part number off a plastic rack.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml

# vision/physical_setup_image_validation.py sits one level below the root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# OT-2 deck: 3 columns x 4 rows. Slot 12 is the fixed trash bin and is never
# loaded with labware, so the gate verifies the 11 addressable slots.
TRASH_SLOT = 12
DEFAULT_SLOT_LAYOUT: List[List[int]] = [
    [10, 11, 12],
    [7, 8, 9],
    [4, 5, 6],
    [1, 2, 3],
]

EMPTY_CLASS = "empty"


# ─── Domain model ────────────────────────────────────────────────────────────

class FailureKind(str, Enum):
    """The category of a single deck mismatch."""

    MISSING = "missing"        # slot should be loaded but reads empty
    DISPLACED = "displaced"    # expected footprint found in a different slot
    MISMATCH = "mismatch"      # a different footprint is sitting in this slot


@dataclass(frozen=True)
class ExpectedSlot:
    """One entry of the configuration's expected deck layout."""

    slot: int
    load_name: str
    footprint: str
    role: str  # e.g. "tuberack", "plate", "paper", "tiprack"


@dataclass(frozen=True)
class DetectedSlot:
    """The CV result for one physical slot region."""

    slot: int
    footprint: str        # classified footprint class, or EMPTY_CLASS
    confidence: float     # 0..1 match score for the winning class


@dataclass
class ValidationFailure:
    """A single human-readable validation failure."""

    kind: FailureKind
    slot: int
    message: str


@dataclass
class ValidationReport:
    """Aggregate result of a deck verification pass."""

    expected: Dict[int, ExpectedSlot]
    detected: Dict[int, DetectedSlot]
    failures: List[ValidationFailure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# ─── Configuration parsing ───────────────────────────────────────────────────

def _read_yaml(path: Path) -> Dict[str, Any]:
    """Load and minimally validate a YAML mapping file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at the top level of {path}")
    return data


def _default_footprint(load_name: str) -> str:
    """Best-effort footprint class when no explicit ``footprint_map`` entry.

    The Opentrons load-name convention encodes the footprint, e.g.
    ``*_96_wellplate_*`` / ``*_24_wellplate_*`` / ``*_96_tiprack_*``. We fold a
    load name down to a coarse footprint key so the gate still works against a
    config that has not enumerated every part. Tune via ``footprint_map`` when
    two distinct footprints would otherwise collide.
    """
    name = load_name.lower()
    if "96_tiprack" in name or "tiprack" in name:
        return "tiprack_96"
    if "96_wellplate" in name or "96_well" in name:
        return "wellplate_96"
    if "24_wellplate" in name:
        return "wellplate_24"
    if "12_reservoir" in name or "reservoir" in name:
        return "reservoir"
    if "tuberack" in name or "vial" in name:
        return "tuberack"
    return name  # fall back to the literal load name as its own class


def parse_expected_layout(
    workflow_path: Path,
    footprint_map: Optional[Dict[str, str]] = None,
) -> Dict[int, ExpectedSlot]:
    """Build ``{slot_number: ExpectedSlot}`` from a workflow YAML.

    The repo's workflow configs declare a ``deck:`` block whose values each
    carry a ``slot`` and a ``load_name`` (see
    ``configs/workflows/defaults/vial_dilution_print.yaml``). This parser
    accepts that shape, and also tolerates a flat ``{slot: load_name}`` mapping
    under ``deck:`` for ad-hoc configs.

    Parameters
    ----------
    workflow_path : Path
        Path to the workflow configuration YAML.
    footprint_map : dict, optional
        Optional ``{load_name: footprint_class}`` overrides. Anything not
        listed falls back to :func:`_default_footprint`.

    Returns
    -------
    dict[int, ExpectedSlot]
    """
    footprint_map = footprint_map or {}
    cfg = _read_yaml(workflow_path)
    deck = cfg.get("deck")
    if not isinstance(deck, dict):
        raise ValueError(
            f"{workflow_path} has no 'deck:' mapping to derive expected layout."
        )

    expected: Dict[int, ExpectedSlot] = {}
    for role, entry in deck.items():
        # Shape A: {role: {slot: N, load_name: "..."}}
        if isinstance(entry, dict):
            slot = entry.get("slot")
            load_name = entry.get("load_name")
        # Shape B: {slot_number: "load_name"} flat mapping
        elif isinstance(entry, str) and str(role).isdigit():
            slot, load_name = int(role), entry
        else:
            continue

        if slot is None or load_name is None:
            continue
        slot = int(slot)
        if slot == TRASH_SLOT:
            # Never validate the fixed trash slot.
            continue

        footprint = footprint_map.get(load_name) or _default_footprint(load_name)
        if slot in expected:
            raise ValueError(
                f"Slot {slot} declared twice in {workflow_path} "
                f"('{expected[slot].role}' and '{role}')."
            )
        expected[slot] = ExpectedSlot(
            slot=slot,
            load_name=load_name,
            footprint=footprint,
            role=str(role),
        )

    if not expected:
        raise ValueError(f"No usable deck entries parsed from {workflow_path}.")
    return expected


# ─── Slot geometry ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SlotGrid:
    """Maps the deck ROI of an image to per-slot pixel rectangles."""

    roi_x: int
    roi_y: int
    roi_w: int
    roi_h: int
    rows: int
    cols: int
    layout: List[List[int]]

    @classmethod
    def from_config(cls, grid_cfg: Dict[str, Any], image_shape: Tuple[int, int]) -> "SlotGrid":
        h, w = image_shape[:2]
        roi_x = int(grid_cfg.get("roi_x", 0))
        roi_y = int(grid_cfg.get("roi_y", 0))
        roi_w = int(grid_cfg.get("roi_width") or (w - roi_x))
        roi_h = int(grid_cfg.get("roi_height") or (h - roi_y))
        return cls(
            roi_x=roi_x,
            roi_y=roi_y,
            roi_w=roi_w,
            roi_h=roi_h,
            rows=int(grid_cfg.get("rows", 4)),
            cols=int(grid_cfg.get("cols", 3)),
            layout=grid_cfg.get("slot_layout", DEFAULT_SLOT_LAYOUT),
        )

    def slot_rect(self, slot: int) -> Optional[Tuple[int, int, int, int]]:
        """Return ``(x, y, w, h)`` pixel rectangle for ``slot`` or ``None``."""
        cell_w = self.roi_w / self.cols
        cell_h = self.roi_h / self.rows
        for r_idx, row in enumerate(self.layout):
            for c_idx, s in enumerate(row):
                if s == slot:
                    x = int(self.roi_x + c_idx * cell_w)
                    y = int(self.roi_y + r_idx * cell_h)
                    return x, y, int(cell_w), int(cell_h)
        return None

    def all_slots(self) -> List[int]:
        return [s for row in self.layout for s in row if s != TRASH_SLOT]


# ─── Slot classification (computer vision) ───────────────────────────────────

class SlotClassifier:
    """Classifies the footprint inside a slot ROI.

    Strategy, in order of preference:

    1. **Template matching** — if a templates directory with one reference
       crop per footprint class is supplied, each slot crop is matched
       (``cv2.matchTemplate``, normalised cross-correlation) against every
       template and the best-scoring class above ``match_threshold`` wins.
    2. **Emptiness heuristic** — independent of templates, a slot whose
       foreground fill (edge + Otsu-mask density) sits below
       ``empty_fill_threshold`` is classified as :data:`EMPTY_CLASS`. This is
       what catches the "missing labware" failure even when no template
       library exists.

    The two combine: emptiness is decided first (so a sparse template never
    out-votes a genuinely empty slot), then template matching labels the
    remaining occupied slots. Without templates the classifier still
    distinguishes empty from occupied, and labels occupied slots ``"occupied"``.
    """

    def __init__(
        self,
        templates: Optional[Dict[str, "np.ndarray"]] = None,
        match_threshold: float = 0.45,
        empty_fill_threshold: float = 0.04,
    ) -> None:
        self.templates = templates or {}
        self.match_threshold = match_threshold
        self.empty_fill_threshold = empty_fill_threshold

    # -- public ---------------------------------------------------------------

    def classify(self, slot_bgr: "np.ndarray") -> Tuple[str, float]:
        """Return ``(footprint, confidence)`` for one slot crop."""
        fill = self._fill_ratio(slot_bgr)
        if fill < self.empty_fill_threshold:
            # Confidence that it is empty grows as fill approaches zero.
            conf = float(1.0 - fill / max(self.empty_fill_threshold, 1e-6))
            return EMPTY_CLASS, round(conf, 3)

        if self.templates:
            footprint, score = self._best_template(slot_bgr)
            if footprint is not None and score >= self.match_threshold:
                return footprint, round(float(score), 3)
            # Occupied but unrecognised: report low-confidence "occupied".
            return "occupied", round(float(score), 3)

        # No template library: we can only assert the slot is occupied.
        return "occupied", round(float(min(1.0, fill * 4)), 3)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _fill_ratio(slot_bgr: "np.ndarray") -> float:
        """Fraction of the slot occupied by foreground structure.

        Combines Canny edge density with an Otsu foreground mask so that both
        textured labware (vial racks, tip racks) and flat-but-distinct items
        (a sheet of paper) register as "occupied", while a bare aluminium slot
        reads near zero.
        """
        if slot_bgr.size == 0:
            return 0.0
        gray = cv2.cvtColor(slot_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.count_nonzero(edges)) / edges.size

        _, mask = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        # Either polarity may be foreground; take the smaller as the object.
        fg = min(np.count_nonzero(mask), mask.size - np.count_nonzero(mask))
        mask_density = float(fg) / mask.size

        return max(edge_density, mask_density)

    def _best_template(self, slot_bgr: "np.ndarray") -> Tuple[Optional[str], float]:
        """Best normalised-cross-correlation match across all templates."""
        slot_gray = cv2.cvtColor(slot_bgr, cv2.COLOR_BGR2GRAY)
        best_name: Optional[str] = None
        best_score = -1.0
        for name, template in self.templates.items():
            tmpl = template
            if tmpl.ndim == 3:
                tmpl = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
            # Resize the template to the slot crop so matchTemplate is valid.
            th, tw = tmpl.shape[:2]
            sh, sw = slot_gray.shape[:2]
            if th > sh or tw > sw:
                scale = min(sh / th, sw / tw)
                tmpl = cv2.resize(
                    tmpl, (max(1, int(tw * scale)), max(1, int(th * scale)))
                )
            res = cv2.matchTemplate(slot_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if score > best_score:
                best_score, best_name = score, name
        return best_name, best_score


def load_templates(templates_dir: Optional[Path]) -> Dict[str, "np.ndarray"]:
    """Load ``<footprint_class>.{png,jpg}`` reference crops from a directory.

    The file stem is taken as the footprint class name. Returns an empty dict
    (and the classifier degrades to empty-vs-occupied) when the directory is
    absent or holds no images.
    """
    templates: Dict[str, np.ndarray] = {}
    if templates_dir is None or not templates_dir.exists():
        return templates
    for path in sorted(templates_dir.iterdir()):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        img = cv2.imread(str(path))
        if img is not None:
            templates[path.stem] = img
    return templates


def detect_slots(
    image_bgr: "np.ndarray",
    grid: SlotGrid,
    classifier: SlotClassifier,
) -> Dict[int, DetectedSlot]:
    """Classify every addressable slot in the deck image."""
    detected: Dict[int, DetectedSlot] = {}
    for slot in grid.all_slots():
        rect = grid.slot_rect(slot)
        if rect is None:
            continue
        x, y, w, h = rect
        crop = image_bgr[y : y + h, x : x + w]
        footprint, confidence = classifier.classify(crop)
        detected[slot] = DetectedSlot(slot=slot, footprint=footprint, confidence=confidence)
    return detected


# ─── Validation ──────────────────────────────────────────────────────────────

def validate_layout(
    expected: Dict[int, ExpectedSlot],
    detected: Dict[int, DetectedSlot],
) -> ValidationReport:
    """Cross-check detected footprints against the expected layout.

    Produces three failure kinds:

    * ``MISSING``  — a slot the config requires reads empty.
    * ``MISMATCH`` — a different footprint occupies a required slot.
    * ``DISPLACED``— an expected footprint is absent from its slot but is
      found occupying some *other* (unexpected) slot.
    """
    report = ValidationReport(expected=expected, detected=detected)

    # Index detected footprints (excluding empties) for displacement lookups.
    occupied: Dict[int, str] = {
        s: d.footprint
        for s, d in detected.items()
        if d.footprint not in (EMPTY_CLASS,)
    }

    for slot, exp in sorted(expected.items()):
        det = detected.get(slot)
        det_footprint = det.footprint if det else EMPTY_CLASS

        if det_footprint == EMPTY_CLASS:
            # Required slot reads empty. Did the footprint land elsewhere?
            displaced_to = _find_displacement(exp, slot, occupied, expected)
            if displaced_to is not None:
                report.failures.append(
                    ValidationFailure(
                        kind=FailureKind.DISPLACED,
                        slot=displaced_to,
                        message=(
                            f"Displaced {exp.load_name} found in Slot "
                            f"{displaced_to} (Expected: Slot {slot})"
                        ),
                    )
                )
            else:
                report.failures.append(
                    ValidationFailure(
                        kind=FailureKind.MISSING,
                        slot=slot,
                        message=(
                            f"Missing {exp.load_name} in Slot {slot} "
                            f"(Detected: Empty)"
                        ),
                    )
                )
        elif det_footprint != exp.footprint:
            # Something is there, but the wrong footprint.
            displaced_to = _find_displacement(exp, slot, occupied, expected)
            if displaced_to is not None:
                report.failures.append(
                    ValidationFailure(
                        kind=FailureKind.DISPLACED,
                        slot=displaced_to,
                        message=(
                            f"Displaced {exp.load_name} found in Slot "
                            f"{displaced_to} (Expected: Slot {slot})"
                        ),
                    )
                )
            report.failures.append(
                ValidationFailure(
                    kind=FailureKind.MISMATCH,
                    slot=slot,
                    message=(
                        f"Wrong labware in Slot {slot}: detected "
                        f"'{det_footprint}', expected {exp.load_name} "
                        f"('{exp.footprint}')"
                    ),
                )
            )
        # else: footprint matches — slot OK.

    return report


def _find_displacement(
    exp: ExpectedSlot,
    home_slot: int,
    occupied: Dict[int, str],
    expected: Dict[int, ExpectedSlot],
) -> Optional[int]:
    """Return a slot where ``exp``'s footprint appears unexpectedly, if any.

    A candidate slot must (a) hold ``exp``'s footprint class and (b) not be a
    slot that legitimately expects that footprint (avoids reporting the plate
    as "displacing" the paper when both share a footprint).
    """
    for slot, footprint in occupied.items():
        if slot == home_slot:
            continue
        if footprint != exp.footprint:
            continue
        slot_expectation = expected.get(slot)
        if slot_expectation is not None and slot_expectation.footprint == footprint:
            # That slot is supposed to hold this footprint — not a displacement.
            continue
        return slot
    return None


# ─── Reporting ───────────────────────────────────────────────────────────────

def render_report(report: ValidationReport) -> str:
    """Render the console output in the required gate format."""
    lines: List[str] = []
    if report.passed:
        lines.append(
            "SUCCESS: Physical deck layout matches workflow configuration. "
            "Proceeding to run."
        )
        return "\n".join(lines)

    lines.append("CRITICAL ERROR: Experiment workflow cannot be completed.")
    # Stable ordering: missing/mismatch by home slot, then displacements.
    for failure in sorted(report.failures, key=lambda f: (f.kind != FailureKind.MISSING, f.slot)):
        lines.append(f"- {failure.message}")
    return "\n".join(lines)


# ─── Image acquisition ───────────────────────────────────────────────────────

def acquire_image(image_path: Optional[Path], capture: bool) -> "np.ndarray":
    """Return a deck image either from disk or a fresh OT-2 capture.

    Capture is delegated to the existing :mod:`vision.ot2_camera_workflow`
    pipeline (SSH capture + SCP transfer); we then read the newest raw frame.
    """
    if capture:
        # Imported lazily so the validation logic is testable without a robot.
        from vision.ot2_camera_workflow import run_camera_workflow  # noqa: WPS433
        from vision.config import load_vision_config  # noqa: WPS433

        if not run_camera_workflow(count=1):
            raise RuntimeError("OT-2 capture workflow failed; cannot verify deck.")
        cfg = load_vision_config()
        raw_dir = Path(cfg["local"]["raw_dir"])
        frames = sorted(
            (p for p in raw_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}),
            key=lambda p: p.stat().st_mtime,
        )
        if not frames:
            raise RuntimeError(f"No captured frame found in {raw_dir}.")
        image_path = frames[-1]

    if image_path is None:
        raise ValueError("Provide --image <path> or --capture.")
    image_path = Path(image_path)
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read deck image: {image_path}")
    return image


# ─── Orchestration ───────────────────────────────────────────────────────────

def run_validation(
    workflow_path: Path,
    image_path: Optional[Path] = None,
    *,
    capture: bool = False,
    vision_config_path: Optional[Path] = None,
    templates_dir: Optional[Path] = None,
) -> ValidationReport:
    """Full gate: parse config, classify the deck image, validate, report."""
    vision_cfg: Dict[str, Any] = {}
    if vision_config_path is not None and Path(vision_config_path).exists():
        vision_cfg = _read_yaml(Path(vision_config_path))

    grid_cfg = vision_cfg.get("slot_grid", {})
    pv_cfg = vision_cfg.get("physical_validation", {})
    footprint_map = pv_cfg.get("footprint_map", {})

    if templates_dir is None and pv_cfg.get("templates_dir"):
        templates_dir = (PROJECT_ROOT / pv_cfg["templates_dir"]).resolve()

    expected = parse_expected_layout(workflow_path, footprint_map=footprint_map)

    image = acquire_image(image_path, capture)
    grid = SlotGrid.from_config(grid_cfg, image.shape)
    classifier = SlotClassifier(
        templates=load_templates(templates_dir),
        match_threshold=float(pv_cfg.get("match_threshold", 0.45)),
        empty_fill_threshold=float(pv_cfg.get("empty_fill_threshold", 0.04)),
    )
    detected = detect_slots(image, grid, classifier)
    return validate_layout(expected, detected)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-flight deck verification: cross-check the physical OT-2 deck "
            "against a workflow configuration before running an experiment."
        ),
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        required=True,
        help="Workflow configuration YAML defining the expected deck layout.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--image",
        type=Path,
        help="Path to an overhead deck image to verify.",
    )
    src.add_argument(
        "--capture",
        action="store_true",
        help="Capture a fresh frame from the OT-2 before verifying.",
    )
    parser.add_argument(
        "--vision-config",
        type=Path,
        default=PROJECT_ROOT / "vision_tests" / "configs" / "vision_config.yaml",
        help="Vision config with slot_grid / physical_validation sections.",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=None,
        help="Directory of <footprint_class>.png reference crops (optional).",
    )
    args = parser.parse_args(argv)

    try:
        report = run_validation(
            workflow_path=args.workflow,
            image_path=args.image,
            capture=args.capture,
            vision_config_path=args.vision_config,
            templates_dir=args.templates_dir,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        # A gate that cannot evaluate must fail closed.
        print("CRITICAL ERROR: Experiment workflow cannot be completed.")
        print(f"- Deck verification could not run: {exc}")
        return 1

    print(render_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
