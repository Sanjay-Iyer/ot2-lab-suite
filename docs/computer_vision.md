# Computer Vision — Workflow, Packages, Paths & Future Guidance

> **Scope** — How machine vision works in `ot2-lab-suite` today: image acquisition from
> the OT-2 robot, classical-CV image analysis on the laptop, the exact commands, paths,
> file types, and config knobs — plus a forward-looking guide for upgrading the CV stack
> or swapping in an external camera.

---

## 1. Summary Overview

Computer vision in this repo is split into **two independent halves** that communicate
only through JPEG files on disk:

| Half | Directory | Job | Stack |
| ---- | --------- | --- | ----- |
| **Acquisition** | `vision/` | SSH into the OT-2, trigger its built-in camera over HTTP, pull images back to the laptop | `pyyaml` + stdlib (`subprocess`, `pathlib`, `argparse`); `ssh` / `scp -O` / `curl` |
| **Analysis** | `vision_tests/` | Read JPEGs from disk, run classical OpenCV detection/measurement, emit annotated images + CSVs | `opencv-python` (`cv2`), `numpy`, `pandas`, `pyyaml`, `rich` |

**Key facts:**

- **Camera** = the OT-2's stock built-in deck camera. There is **no dedicated camera SDK
  and no external camera**. It is reached through the robot server's REST endpoint
  `http://localhost:31950/camera/picture` (called *on the robot*, via SSH).
- **Analysis is 100% classical image processing** — threshold + contour detection +
  geometric/colour measurement. **No machine learning, no neural nets, no object
  classifier.** "Identification" means *measuring blobs* (area, circularity, colour),
  not recognising what they are.
- The two halves are decoupled: acquisition writes to `vision/raw/`, analysis reads from
  `vision_tests/raw/`. **Note the two `raw/` dirs are different** — you currently move
  files between them manually (see [Gotchas](#7-gotchas--inconsistencies)).

```
 OT-2 robot                          Laptop
┌───────────────────────┐           ┌──────────────────────────────────────────┐
│ built-in camera        │  ssh+curl │ vision/  (acquisition)                     │
│ REST: :31950/camera    │◀──────────│  capture_ot2_images.py                     │
│ saves → /data/vision/  │           │  transfer_ot2_images.py  ── scp -O ──▶ raw/│
└───────────────────────┘           │  ot2_camera_workflow.py  (capture+transfer)│
                                     ├──────────────────────────────────────────┤
                                     │ vision_tests/ (analysis, OpenCV)           │
   (manual copy raw → raw)  ───────▶ │  raw/ → scripts/*.py → outputs/{annotated, │
                                     │         masks, *.csv}                      │
                                     └──────────────────────────────────────────┘
```

---

## 2. Acquisition — the `vision/` module

### Mechanism

The OT-2 has no project code on it for vision. Acquisition is pure remote shell:

1. **Capture** ([vision/capture_ot2_images.py](../vision/capture_ot2_images.py)) — builds a
   single remote shell string and runs it over SSH:
   ```sh
   mkdir -p /data/vision ;
   curl -s -X POST -H 'opentrons-version: *' \
        http://localhost:31950/camera/picture \
        --output /data/vision/ot2_image_$(date +%Y%m%d_%H%M%S).jpg ;
   echo 'Captured image 1' ;
   sleep 3 ;   # repeated per requested image
   ...
   ```
   SSH options come from `src/utils/ot2_ssh.py`: `IdentitiesOnly=yes`, the
   configured identity, finite timeout, and (when enabled)
   `PubkeyAcceptedAlgorithms=+ssh-rsa`. Host-key checking remains enabled.

2. **Transfer** ([vision/transfer_ot2_images.py](../vision/transfer_ot2_images.py)) — pulls
   images back with **legacy SCP**:
   ```sh
   scp -O -o IdentitiesOnly=yes \
       -o PubkeyAcceptedAlgorithms=+ssh-rsa \
       -i <key> -o BatchMode=yes -o ConnectTimeout=30 \
       -r root@<ip>:/data/vision/* vision/raw
   ```
   **`-O` (capital O) is mandatory** — the OT-2 lacks `/usr/libexec/sftp-server`, so default
   (SFTP-based) SCP fails with `sh: /usr/libexec/sftp-server: not found`. Do **not** confuse
   `-O` (legacy protocol) with `-o` (SSH option).

3. **Combined workflow** ([vision/ot2_camera_workflow.py](../vision/ot2_camera_workflow.py)) —
   capture then transfer in one call; aborts transfer if capture fails.

All three scripts support `--dry-run` (print the command, execute nothing).

### Other files in `vision/`

| File | Purpose | Status |
| ---- | ------- | ------ |
| `capture_ot2_images.py` | SSH + curl capture | Active |
| `transfer_ot2_images.py` | `scp -O` transfer | Active |
| `ot2_camera_workflow.py` | capture → transfer orchestration | Active |
| `config.py` | Alternative **env-var-based** config loader | Parallel/legacy — not used by the three scripts above (they read `configs/vision.yaml` directly) |
| `transfer_images.py` | Alternative transfer using SSH `find` | Parallel/legacy |
| `image_inventory.py` | CSV inventory generator | Helper |
| `validate_images.py` | Image-integrity checker | Helper |
| `__init__.py` | Package marker | — |
| `raw/` | Local landing dir for transferred images (git-ignored) | Output |

> There are effectively **two config conventions** in `vision/` — the active scripts use
> `configs/vision.yaml`; `config.py`/`transfer_images.py` use env vars. Treat the YAML path
> as canonical.

### Commands (acquisition)

```powershell
# Capture 2 images (config default) on the robot
python vision/capture_ot2_images.py
python vision/capture_ot2_images.py --count 5 --delay 2
python vision/capture_ot2_images.py --dry-run

# Pull images to the laptop
python vision/transfer_ot2_images.py
python vision/transfer_ot2_images.py --remote-dir /data/vision --local-dir vision/raw
python vision/transfer_ot2_images.py --dry-run

# Capture + transfer in one step
python vision/ot2_camera_workflow.py --count 2 --delay 3
python vision/ot2_camera_workflow.py --dry-run
```

> **Two-machine note:** acquisition only works on the lab laptop (real OT-2 + SSH key).
> On the dev/testing laptop, use `--dry-run` or work directly on pre-captured images in
> the analysis half.

---

## 3. Analysis — the `vision_tests/` module

All analysis scripts share the same classical-CV pipeline and read their knobs from
[vision_tests/configs/vision_config.yaml](../vision_tests/configs/vision_config.yaml).

### The shared detection pipeline

Implemented (and largely duplicated) in each script:

1. `cv2.cvtColor` → grayscale
2. `cv2.GaussianBlur` → denoise (kernel forced odd)
3. **Threshold** → binary mask. Three methods: `adaptive` (default, `ADAPTIVE_THRESH_GAUSSIAN_C`),
   `otsu`, or `fixed`. `invert=True` makes bright objects foreground.
4. **Morphology** → `MORPH_CLOSE` then `MORPH_OPEN` with an elliptical kernel to fill gaps / drop noise.
5. `cv2.findContours` (`RETR_EXTERNAL`, `CHAIN_APPROX_SIMPLE`) → blobs.
6. **Per-blob measurement**: area, perimeter, bounding box, **aspect ratio**,
   **circularity** = `4π·area / perimeter²` (1.0 = perfect circle), rectangularity, centroid (via moments).
7. **Filter** by `min_area`/`max_area`, `max_aspect_ratio`, `min_circularity`.

This is *geometric* identification: a near-1.0 circularity small blob ≈ a printed droplet;
elongated high-aspect-ratio blobs are filtered out.

### The scripts

| Script | What it adds on top of the pipeline | Outputs |
| ------ | ----------------------------------- | ------- |
| [hello_world_vision_test.py](../vision_tests/scripts/hello_world_vision_test.py) | Sanity check: detection + colour metrics together | `outputs/results.csv`, `outputs/annotated/*_annotated.jpg`, `outputs/masks/*_mask.jpg` |
| [test_shape_detection.py](../vision_tests/scripts/test_shape_detection.py) | One CSV row **per detected object** with full geometry | `outputs/shape_metrics.csv`, `outputs/annotated/*_shapes.jpg` |
| [test_tip_count_by_slot.py](../vision_tests/scripts/test_tip_count_by_slot.py) | Overlays a **3×4 deck-slot grid** (`SlotGrid` class), assigns each blob to a slot by centroid, counts per slot | `outputs/slot_counts.csv`, `outputs/annotated/*_slot_counts.jpg` |
| [test_color_change.py](../vision_tests/scripts/test_color_change.py) | Per-image colour means in **RGB/HSV/LAB** + brightness; optional **ΔE (LAB Euclidean)** vs a baseline image | `outputs/color_metrics.csv`, `outputs/masks/*_color_mask.jpg` |
| [calibrate_slots.py](../vision_tests/scripts/calibrate_slots.py) | Visual calibration: draws the slot grid + ROI box so you can tune `roi_*` until grid lines match the physical deck | `outputs/annotated/*_grid_calibration.jpg` |

### Commands (analysis)

```powershell
# Run from project root; all read vision_tests/raw/ and write vision_tests/outputs/
python vision_tests/scripts/hello_world_vision_test.py
python vision_tests/scripts/test_shape_detection.py
python vision_tests/scripts/test_tip_count_by_slot.py
python vision_tests/scripts/test_color_change.py
python vision_tests/scripts/calibrate_slots.py
```

None take CLI flags — **all behaviour is driven by the YAML config**.

### Slot grid model (how it "knows" deck positions)

`SlotGrid` ([test_tip_count_by_slot.py](../vision_tests/scripts/test_tip_count_by_slot.py))
splits an ROI into an even `rows × cols` grid and maps cells to OT-2 slot numbers:

```
[10, 11, 12]
[ 7,  8,  9]
[ 4,  5,  6]
[ 1,  2,  3]   # row-major, top-to-bottom as the camera sees the deck
```

A blob's centroid → `(row, col)` → slot number. **It is a naive even split with no
perspective correction** — if the camera is angled or the deck isn't centred in the frame,
slot assignment drifts. Use `calibrate_slots.py` to tune the ROI first.

---

## 4. Packages

| Package | Used by | Why | Declared in `requirements.txt`? |
| ------- | ------- | --- | ------------------------------- |
| `pyyaml` | both halves | parse YAML config | ✅ yes |
| `subprocess`/`pathlib`/`argparse` | acquisition | run ssh/scp, paths, CLI | stdlib |
| **`opencv-python` (`cv2`)** | analysis | all image processing | ❌ **MISSING** |
| **`rich`** | analysis | terminal tables/output | ❌ **MISSING** |
| `numpy` | analysis | array math | ✅ yes |
| `pandas` | analysis | CSV/dataframes | ✅ yes |

> **Footgun:** `opencv-python` and `rich` are imported by every analysis script but appear
> in **no** requirements file. A fresh `pip install -r requirements.txt` then running any
> `vision_tests` script → `ModuleNotFoundError`. **Fix:** add `opencv-python` and `rich`
> (and they belong under a `# --- Computer Vision ---` group).

External binaries required on PATH for acquisition: **`ssh`** and **`scp`** (OpenSSH, with
`-O` support — i.e. a reasonably modern OpenSSH client). `curl` is needed **on the robot**, not the laptop.

---

## 5. Paths, Directories & File Types

### Config files

| Path | Used by | Contents |
| ---- | ------- | -------- |
| [configs/vision.yaml](../configs/vision.yaml) | `vision/` acquisition | robot ip/user/`ssh_key_path`, remote `vision_dir`, `camera_endpoint`, capture count/delay/prefix, transfer flags |
| [vision_tests/configs/vision_config.yaml](../vision_tests/configs/vision_config.yaml) | `vision_tests/` analysis | dir layout, `slot_grid`, `detection`, `color_analysis`, `shape_analysis`, `annotation` |

### Directory map & file types

```
vision/
├── *.py                         # acquisition scripts
└── raw/                         # ← transferred JPEGs land here (git-ignored)

vision_tests/
├── configs/vision_config.yaml   # analysis knobs
├── scripts/*.py                 # analysis scripts
├── raw/                         # ← input JPEGs for analysis (.jpg/.jpeg/.png)
└── outputs/
    ├── annotated/  *.jpg        # images with contours/bbox/grid/labels drawn
    ├── masks/      *.jpg        # binary + colour masks (debug)
    ├── logs/                    # (declared in config; reserved)
    ├── results.csv              # hello_world summary (1 row/image)
    ├── shape_metrics.csv        # 1 row/object
    ├── slot_counts.csv          # 1 row/(image × slot)
    └── color_metrics.csv        # 1 row/image, colour means + ΔE

On the robot:
/data/vision/ot2_image_YYYYMMDD_HHMMSS.jpg     # capture target
http://localhost:31950/camera/picture          # camera REST endpoint
```

**File types in play:** input/output images are **JPEG** (`.jpg`; analysis also accepts
`.jpeg`/`.png`); config is **YAML**; results are **CSV**; annotated/mask images are JPEG.

### Config knobs (analysis) — quick reference

- `slot_grid`: `enabled`, `roi_x/y/width/height` (null = full image), `rows`, `cols`, `slot_layout`
- `detection`: `min_area`, `max_area`, `threshold_method` (`adaptive|otsu|fixed`),
  `fixed_threshold`, `blur_kernel`, `morph_kernel`, `morph_iterations`, `invert`
- `color_analysis`: `enabled`, `compare_to_baseline`, `baseline_image`
- `shape_analysis`: `calculate_circularity`, `calculate_aspect_ratio`, `min_circularity`, `max_aspect_ratio`
- `annotation`: BGR colours for contour/bbox/centroid/slot-line/text, `line_thickness`, `font_scale`

---

## 6. End-to-End: a typical run

```powershell
# 1. (lab laptop) capture + pull from the robot
python vision/ot2_camera_workflow.py --count 2 --delay 3
#    → JPEGs in vision/raw/

# 2. stage images for analysis (currently manual — see gotcha #1)
#    copy vision/raw/*.jpg  →  vision_tests/raw/

# 3. (optional, first time / new camera position) calibrate the deck grid
python vision_tests/scripts/calibrate_slots.py
#    → inspect outputs/annotated/*_grid_calibration.jpg, edit slot_grid in YAML, repeat

# 4. analyse
python vision_tests/scripts/hello_world_vision_test.py   # sanity
python vision_tests/scripts/test_shape_detection.py      # droplet geometry
python vision_tests/scripts/test_color_change.py         # colour vs baseline
python vision_tests/scripts/test_tip_count_by_slot.py    # per-slot counts
#    → CSVs + annotated JPEGs in vision_tests/outputs/
```

---

## 7. Gotchas & Inconsistencies

1. **Two separate `raw/` dirs.** Acquisition writes `vision/raw/`; analysis reads
   `vision_tests/raw/`. Nothing bridges them automatically — you copy files by hand. A
   one-line fix is to point `transfer_ot2_images.py --local-dir vision_tests/raw`, or unify
   the path in config.
2. **`opencv-python` and `rich` are undeclared** dependencies (see §4).
3. **Machine-specific SSH values** are resolved from `.env` through the
   `*_env_var` fields in `configs/vision.yaml`; do not put a personal key path in
   the tracked YAML.
4. **Pipeline code is duplicated** across all four analysis scripts (`create_binary_mask`,
   `detect_objects` are copy-pasted). Refactor into a shared `vision_tests/lib.py` before
   the logic diverges.
5. **Slot grid has no perspective correction** — even split only. Misaligned camera → wrong
   slot assignments. Calibrate, and consider a homography (see §8).
6. **ΔE is simplified** (LAB Euclidean, not CIEDE2000) — fine for coarse "did colour change"
   but not perceptually accurate.
7. **No tests / CI** for the vision code; `outputs/` is overwritten in place each run (no
   timestamped run dirs like the protocol side has).

---

## 8. Future Guidance — CV Packages & External Cameras

### A. Hardening the current classical stack (low effort, high value)

- **Add the missing deps** and pin them: `opencv-python`, `rich`.
- **De-duplicate** the pipeline into one module; have every script import it.
- **Unify the `raw/` dirs** and add timestamped output run folders (mirror the
  `runs/<id>/` convention already used by the protocol side).
- **Perspective correction**: detect the deck's four corners (ArUco/AprilTag markers placed
  on the deck, or `cv2.findChessboardCorners`) and apply `cv2.getPerspectiveTransform` +
  `cv2.warpPerspective` so the slot grid maps correctly regardless of camera angle. This is
  the single biggest accuracy win for `test_tip_count_by_slot`.
- **Colour calibration**: include a colour reference card (e.g. a small ColorChecker) in
  frame and normalise per-image; upgrade ΔE to **CIEDE2000** via `colormath` or `colour-science`.
- **Circle-specific detection** for droplets: `cv2.HoughCircles` can be more robust than
  contour circularity for round dots on paper.

### B. Better measurement / detection libraries (medium effort)

- **`scikit-image`** — richer region properties (`regionprops`: eccentricity, solidity,
  intensity stats) than raw OpenCV contours; cleaner API for scientific imaging.
- **`scipy.ndimage`** — labelling, watershed for **splitting touching droplets** (a real
  limitation of `RETR_EXTERNAL` contours today).
- **`Pillow`/`imageio`** — robust I/O if you move beyond JPEG (e.g. PNG/TIFF for lossless
  capture — JPEG compression artefacts hurt small-droplet measurement).

### C. Learned vision (higher effort — only if classical hits a wall)

- **Ultralytics YOLO (`ultralytics`)** — fast object detection/segmentation if you need to
  *classify* (tip vs well vs droplet) rather than just measure blobs. Requires labelled data.
- **`segment-anything` (SAM)** — zero-shot segmentation of droplets/wells without training;
  good for bootstrapping labels.
- **Cloud multimodal LLM** — the repo already wires **Gemini** (`langchain-google-genai`,
  `google-generativeai`) for the agent side. The OT-2 JPEGs could be sent to a vision-capable
  Gemini model for qualitative QC ("is the print uniform? any missing spots?") without any
  CV code. Cheapest path to semantic analysis; cost/latency per image is the trade-off.

### D. External / better cameras (when the OT-2 camera is the bottleneck)

The OT-2's built-in webcam is low-resolution and fixed-position. To upgrade:

- **USB UVC camera / machine-vision cam** (e.g. a higher-res industrial USB3 camera mounted
  over the deck). Capture with **OpenCV `cv2.VideoCapture(index)`** or vendor SDK; this fully
  bypasses the `:31950/camera/picture` endpoint. You'd add a new acquisition backend in
  `vision/` and select it via `configs/vision.yaml` (e.g. `camera.backend: ot2 | usb | rtsp`).
- **Raspberry Pi HQ camera** alongside the robot — capture via `picamera2`, serve over the
  network, pull like today.
- **Industrial GigE/USB3 cameras** (FLIR/Basler) — use their SDKs (`PySpin`, `pypylon`) for
  controlled exposure/gain, which matters for repeatable colour measurement.
- **Phone/DSLR tethered** for high-res one-off QC — `gphoto2` or just manual capture into
  `vision_tests/raw/`.

**Design recommendation:** abstract acquisition behind a small `Camera` interface
(`capture() -> list[Path]`) with pluggable backends (`OT2RestCamera`, `UsbCamera`,
`RtspCamera`). The analysis half already only cares about JPEGs on disk, so **any new camera
slots in without touching `vision_tests/` at all** — which is the main strength of the
current decoupled design.

### E. Lighting & rig (often beats any software change)

Repeatable CV starts with repeatable optics: fixed diffuse lighting, a matte non-reflective
deck surface, a fixed camera mount, and a colour/scale reference in every frame. No package
upgrade compensates for inconsistent lighting.
```
