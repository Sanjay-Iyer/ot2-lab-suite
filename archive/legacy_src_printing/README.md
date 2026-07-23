# Nanoparticle Printing Optimization Suite (v2.0)

Automate the preparation and optimization of gold-silver nanostar printing on the Opentrons OT-2.

## New Structure: Dynamic & Modular

This version follows a structured layout similar to the main Opentrons workflow:

- **`configs/`**: JSON/YAML files containing parameters for specific runs.
- **`protocols/`**: Static OT-2 Python scripts that read from the `configs/` directory.
- **`tools/`**: Planning and post-processing scripts.
- **`outputs/`**: Generated manifests and aggregated records.
- **`logs/`**: Script and robot execution logs.

## Workflow Overview

1.  **Configure**: Run `tools/config.py` to define your targets.
2.  **Plan**: Run `tools/dilution_planner.py` and `tools/size_variant_planner.py`.
3.  **Execute**: Run `protocols/dilution_protocol.py` on your robot.
4.  **Analyze**: Record results in `outputs/analysis_template.csv`.
5.  **Optimize**: Run `tools/optimizer.py` to calculate the next best parameters.

## Usage

```bash
# 1. Initialize configuration
python tools/config.py --stock-conc 150 --dilutions 1,10,100,1000

# 2. Generate detailed plans
python tools/deck_setup.py
python tools/dilution_planner.py
python tools/size_variant_planner.py

# 3. Simulate (or Deploy)
opentrons_simulate protocols/dilution_protocol.py
```

## Tutorial Run

Run the end-to-end "Mock Tutorial" to see the full data flow:
```bash
python test_workflow.py
```

## Generated Protocols

Protocols are written to the `generated/` directory:
- `run_dilution.py`: Stage 1 (Dilutions)
- `run_size_mixing.py`: Stage 2 (Size pools)

## Outputs

All structured data (CSV/JSON) is saved in the `outputs/` directory.
Logs are saved in the `logs/` directory.
