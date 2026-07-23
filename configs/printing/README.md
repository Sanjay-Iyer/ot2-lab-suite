# configs/printing — Workflow 01 example configurations

Complete, commented configs for **Workflow 01** (vial → dilution → paper print), in the
unified schema (sections: `deck / labware / pipettes / materials / dilution_plan /
mixing_plan / print_groups / imaging / tip_policy`). They share the `01_vial_dilution_paper_print`
stem; the suffix names the pipette variant. Understandable without reading the Python.
Build any of them with:

```bash
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.<variant>.yaml
```

## BP v3: robot API 7.0.2 / Protocol API 2.15

`bp_20260723_v3.yaml` is the robot-compatible BP workflow. It does not use
partial-nozzle pickup: the P20 performs all vial transfers and all four paper
spot volumes, while the P300 multi-channel pipette only mixes plate column 11
with a complete column of eight tips.

Opentrons 7.0.2 has dependencies that conflict with the main `ai` environment,
so its simulator is intentionally isolated:

```powershell
conda create --prefix .venv\ot2-api-2.15-py310 python=3.10 pip -y
.\.venv\ot2-api-2.15-py310\python.exe -m pip install -r requirements-ot2-api-2.15.txt
```

The build and validation scripts automatically find that interpreter. On a
different path, set `OT2_API_2_15_PYTHON` to its `python.exe`.

```powershell
conda activate ai
python scripts\build_vial_dilution_print.py --config configs\printing\bp_20260723_v3.yaml
python scripts\validate_vial_print.py --config configs\printing\bp_20260723_v3.yaml --robot-ip 169.254.46.57
```

The second command fetches `/health` and requires the generated API level to
equal the robot's reported maximum before running all five simulation modes.

| File | Pipettes | Droplets | Layout | Status (simulation) |
|------|----------|----------|--------|---------------------|
| `01_vial_dilution_paper_print.p20_only.yaml` | P300 (dilution prep) + P20 (printing) | 5 µL, 10 µL | `single_spot` | Simulated OK |
| `01_vial_dilution_paper_print.p300_only.yaml` | P300 only | 30 µL, 35 µL | `column_8up` | Simulated OK |
| `01_vial_dilution_paper_print.mixed.yaml` | P300 + P20 | 5/10 µL (P20) + 30/35 µL (P300) | mixed | Simulated OK |

## Notes
- **01 is "P20-only printing", not "P20-only hardware."** A single-channel P20 cannot
  make the 200 µL dilution transfers, so the P300 prepares/mixes the plate and the P20
  prints. To run with only a P20 mounted, set `dilution_plan.enabled: false` and pre-fill
  the plate.
- **Pipette selection** is `auto` by default (small → P20, large → P300) or an explicit
  pipette name; resolved at build time by `src/core/pipette_selection.py`.
- **No paper-destination overlap**: 02 uses paper columns 1–4; 03 puts P300 on 1–4 and
  P20 on 5–8.
- **Tips**: each group reuses its assigned tip/block and returns it to its own rack
  (P300 → 300 µL rack, P20 → 20 µL rack); tip state is independent per pipette.
- **Deck**: the 20 µL rack sits in slot 2 (verified clear of the P300 partial-tip
  collision envelope). See [skills/printing_workflow/SAFETY_AND_HARDWARE_CONSTRAINTS.md](../../skills/printing_workflow/SAFETY_AND_HARDWARE_CONSTRAINTS.md).
