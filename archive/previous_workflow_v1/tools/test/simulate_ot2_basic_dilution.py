"""
simulate_ot2_basic_dilution.py

Local-only OT-2-style workflow simulation.

Goal:
- Simulate homing.
- For 6 samples:
    sample source A1-A6 -> destination B1-B6
    water source C1-C6 -> same destination B1-B6
    mix destination.

This script does NOT connect to an OT-2.
It is designed as a first test backend for an AI agent tool.

Run:
    python simulate_ot2_basic_dilution.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import csv
import json
from typing import Dict, List, Optional


# ----------------------------
# Configuration
# ----------------------------

NUM_SAMPLES = 6

SAMPLE_VOLUME_UL = 10.0
WATER_VOLUME_UL = 90.0
MIX_VOLUME_UL = 60.0
MIX_REPETITIONS = 5

ASPIRATE_FLOW_RATE_UL_S = 5.0
DISPENSE_FLOW_RATE_UL_S = 5.0

PIPETTE_MAX_VOLUME_UL = 300.0
PIPETTE_MIN_VOLUME_UL = 1.0

OUTPUT_DIR = Path("simulation_output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ----------------------------
# Data models
# ----------------------------

@dataclass
class SimulatedLocation:
    name: str
    x_mm: float
    y_mm: float
    z_mm: float
    volume_ul: float = 0.0
    max_volume_ul: float = 300.0
    liquid_name: str = "unknown"

    def remove_volume(self, volume_ul: float) -> None:
        if volume_ul <= 0:
            raise ValueError(f"Volume must be positive. Got {volume_ul} µL.")
        if self.volume_ul < volume_ul:
            raise ValueError(
                f"Not enough volume in {self.name}. "
                f"Requested {volume_ul} µL, available {self.volume_ul} µL."
            )
        self.volume_ul -= volume_ul

    def add_volume(self, volume_ul: float, liquid_name: Optional[str] = None) -> None:
        if volume_ul <= 0:
            raise ValueError(f"Volume must be positive. Got {volume_ul} µL.")
        if self.volume_ul + volume_ul > self.max_volume_ul:
            raise ValueError(
                f"Destination {self.name} would overflow. "
                f"Current {self.volume_ul} µL, adding {volume_ul} µL, "
                f"max {self.max_volume_ul} µL."
            )
        self.volume_ul += volume_ul
        if liquid_name:
            if self.liquid_name in ["unknown", "empty"]:
                self.liquid_name = liquid_name
            elif self.liquid_name != liquid_name:
                self.liquid_name = f"mixture({self.liquid_name}+{liquid_name})"


@dataclass
class SimulatedPipette:
    name: str = "p300_single_gen2"
    max_volume_ul: float = PIPETTE_MAX_VOLUME_UL
    min_volume_ul: float = PIPETTE_MIN_VOLUME_UL
    current_volume_ul: float = 0.0
    current_liquid: str = "empty"
    has_tip: bool = False

    def pick_up_tip(self) -> None:
        if self.has_tip:
            raise RuntimeError("Pipette already has a tip.")
        self.has_tip = True

    def drop_tip(self) -> None:
        if not self.has_tip:
            raise RuntimeError("Cannot drop tip because no tip is attached.")
        if self.current_volume_ul > 0:
            raise RuntimeError(
                f"Cannot drop tip while holding {self.current_volume_ul} µL."
            )
        self.has_tip = False

    def aspirate(self, volume_ul: float, source: SimulatedLocation) -> None:
        self._validate_volume(volume_ul)
        if not self.has_tip:
            raise RuntimeError("Cannot aspirate without a tip.")
        if self.current_volume_ul + volume_ul > self.max_volume_ul:
            raise RuntimeError("Aspirating would exceed pipette capacity.")

        source.remove_volume(volume_ul)
        self.current_volume_ul += volume_ul
        self.current_liquid = source.liquid_name

    def dispense(self, volume_ul: float, dest: SimulatedLocation) -> None:
        self._validate_volume(volume_ul)
        if not self.has_tip:
            raise RuntimeError("Cannot dispense without a tip.")
        if self.current_volume_ul < volume_ul:
            raise RuntimeError(
                f"Cannot dispense {volume_ul} µL. "
                f"Pipette only contains {self.current_volume_ul} µL."
            )

        dest.add_volume(volume_ul, liquid_name=self.current_liquid)
        self.current_volume_ul -= volume_ul

        if self.current_volume_ul == 0:
            self.current_liquid = "empty"

    def mix(self, repetitions: int, volume_ul: float, location: SimulatedLocation) -> None:
        self._validate_volume(volume_ul)
        if not self.has_tip:
            raise RuntimeError("Cannot mix without a tip.")
        if location.volume_ul < volume_ul:
            raise RuntimeError(
                f"Cannot mix {volume_ul} µL in {location.name}. "
                f"Location only contains {location.volume_ul} µL."
            )
        if repetitions <= 0:
            raise ValueError("Mix repetitions must be positive.")

        # In simulation, mixing does not change total volume.
        # We validate that the move is physically plausible.
        return

    def _validate_volume(self, volume_ul: float) -> None:
        if volume_ul < self.min_volume_ul:
            raise ValueError(
                f"Volume {volume_ul} µL is below pipette minimum "
                f"{self.min_volume_ul} µL."
            )
        if volume_ul > self.max_volume_ul:
            raise ValueError(
                f"Volume {volume_ul} µL exceeds pipette max "
                f"{self.max_volume_ul} µL."
            )


@dataclass
class SimulatedRobot:
    pipette: SimulatedPipette
    locations: Dict[str, SimulatedLocation]
    current_position: str = "unknown"
    run_log: List[Dict] = field(default_factory=list)

    def log(self, action: str, details: Dict) -> None:
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "step": len(self.run_log) + 1,
            "action": action,
            **details,
        }
        self.run_log.append(record)
        print(f"[{record['step']:03d}] {action}: {details}")

    def home(self) -> None:
        self.current_position = "home"
        self.log("HOME", {"position": "home"})

    def move_to(self, location_name: str, z_offset_mm: float = 1.0) -> None:
        if location_name not in self.locations:
            raise KeyError(f"Unknown location: {location_name}")

        loc = self.locations[location_name]
        self.current_position = location_name

        self.log(
            "MOVE_TO",
            {
                "location": location_name,
                "x_mm": loc.x_mm,
                "y_mm": loc.y_mm,
                "z_mm": loc.z_mm + z_offset_mm,
                "z_offset_mm": z_offset_mm,
            },
        )

    def pick_up_tip(self) -> None:
        self.pipette.pick_up_tip()
        self.log("PICK_UP_TIP", {"pipette": self.pipette.name})

    def drop_tip(self) -> None:
        self.pipette.drop_tip()
        self.log("DROP_TIP", {"pipette": self.pipette.name})

    def aspirate(self, volume_ul: float, source_name: str) -> None:
        source = self.locations[source_name]
        self.pipette.aspirate(volume_ul, source)

        self.log(
            "ASPIRATE",
            {
                "volume_ul": volume_ul,
                "source": source_name,
                "source_remaining_ul": round(source.volume_ul, 3),
                "liquid": self.pipette.current_liquid,
                "flow_rate_ul_s": ASPIRATE_FLOW_RATE_UL_S,
            },
        )

    def dispense(self, volume_ul: float, dest_name: str) -> None:
        dest = self.locations[dest_name]
        liquid_before = dest.liquid_name

        self.pipette.dispense(volume_ul, dest)

        self.log(
            "DISPENSE",
            {
                "volume_ul": volume_ul,
                "destination": dest_name,
                "destination_volume_ul": round(dest.volume_ul, 3),
                "destination_liquid_before": liquid_before,
                "destination_liquid_after": dest.liquid_name,
                "flow_rate_ul_s": DISPENSE_FLOW_RATE_UL_S,
            },
        )

    def mix(self, repetitions: int, volume_ul: float, location_name: str) -> None:
        location = self.locations[location_name]
        self.pipette.mix(repetitions, volume_ul, location)

        for rep in range(1, repetitions + 1):
            self.log(
                "MIX_CYCLE",
                {
                    "location": location_name,
                    "rep": rep,
                    "total_reps": repetitions,
                    "mix_volume_ul": volume_ul,
                    "location_total_volume_ul": round(location.volume_ul, 3),
                },
            )

    def save_logs(self, basename: str = "basic_dilution_simulation") -> None:
        json_path = OUTPUT_DIR / f"{basename}.json"
        csv_path = OUTPUT_DIR / f"{basename}.csv"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.run_log, f, indent=2)

        if self.run_log:
            all_keys = sorted({k for row in self.run_log for k in row.keys()})
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=all_keys)
                writer.writeheader()
                writer.writerows(self.run_log)

        print(f"\nSaved JSON log: {json_path}")
        print(f"Saved CSV log:  {csv_path}")

    def summarize_final_volumes(self) -> None:
        print("\nFinal location volumes:")
        for name in sorted(self.locations.keys()):
            loc = self.locations[name]
            print(
                f"{name:>3} | {loc.volume_ul:>7.2f} µL | "
                f"{loc.liquid_name}"
            )


# ----------------------------
# Simulation setup
# ----------------------------

def build_locations() -> Dict[str, SimulatedLocation]:
    """
    Creates a simplified 3-row x 6-column layout.

    A1-A6 = sample sources
    B1-B6 = dilution destinations
    C1-C6 = water sources

    Coordinates are illustrative, not exact OT-2 deck coordinates.
    """
    locations: Dict[str, SimulatedLocation] = {}

    spacing_x_mm = 9.0
    spacing_y_mm = 9.0
    origin_x_mm = 10.0
    origin_y_mm = 10.0
    z_mm = 5.0

    for i in range(1, NUM_SAMPLES + 1):
        x = origin_x_mm + (i - 1) * spacing_x_mm

        locations[f"A{i}"] = SimulatedLocation(
            name=f"A{i}",
            x_mm=x,
            y_mm=origin_y_mm,
            z_mm=z_mm,
            volume_ul=50.0,
            max_volume_ul=300.0,
            liquid_name=f"sample_{i}",
        )

        locations[f"B{i}"] = SimulatedLocation(
            name=f"B{i}",
            x_mm=x,
            y_mm=origin_y_mm + spacing_y_mm,
            z_mm=z_mm,
            volume_ul=0.0,
            max_volume_ul=300.0,
            liquid_name="empty",
        )

        locations[f"C{i}"] = SimulatedLocation(
            name=f"C{i}",
            x_mm=x,
            y_mm=origin_y_mm + 2 * spacing_y_mm,
            z_mm=z_mm,
            volume_ul=200.0,
            max_volume_ul=300.0,
            liquid_name="water",
        )

    return locations


def run_basic_dilution_simulation() -> None:
    locations = build_locations()
    pipette = SimulatedPipette()
    robot = SimulatedRobot(pipette=pipette, locations=locations)

    robot.log(
        "SIMULATION_START",
        {
            "num_samples": NUM_SAMPLES,
            "sample_volume_ul": SAMPLE_VOLUME_UL,
            "water_volume_ul": WATER_VOLUME_UL,
            "mix_volume_ul": MIX_VOLUME_UL,
            "mix_repetitions": MIX_REPETITIONS,
            "mode": "local_simulation_only",
        },
    )

    robot.home()

    for i in range(1, NUM_SAMPLES + 1):
        sample_source = f"A{i}"
        destination = f"B{i}"
        water_source = f"C{i}"

        robot.log(
            "SAMPLE_START",
            {
                "sample_index": i,
                "sample_source": sample_source,
                "water_source": water_source,
                "destination": destination,
            },
        )

        # Use one clean tip per sample to avoid cross-contamination.
        robot.pick_up_tip()

        # Transfer sample from A_i to B_i.
        robot.move_to(sample_source, z_offset_mm=1.0)
        robot.aspirate(SAMPLE_VOLUME_UL, sample_source)

        robot.move_to(destination, z_offset_mm=1.0)
        robot.dispense(SAMPLE_VOLUME_UL, destination)

        # Add water from C_i to B_i.
        robot.move_to(water_source, z_offset_mm=1.0)
        robot.aspirate(WATER_VOLUME_UL, water_source)

        robot.move_to(destination, z_offset_mm=1.0)
        robot.dispense(WATER_VOLUME_UL, destination)

        # Mix diluted sample in B_i.
        robot.mix(MIX_REPETITIONS, MIX_VOLUME_UL, destination)

        robot.drop_tip()

        robot.log(
            "SAMPLE_COMPLETE",
            {
                "sample_index": i,
                "destination": destination,
                "final_destination_volume_ul": round(
                    robot.locations[destination].volume_ul, 3
                ),
                "destination_liquid": robot.locations[destination].liquid_name,
            },
        )

    robot.home()

    robot.log(
        "SIMULATION_COMPLETE",
        {
            "status": "success",
            "message": "Completed simulated 6-sample dilution workflow.",
        },
    )

    robot.summarize_final_volumes()
    robot.save_logs()


if __name__ == "__main__":
    run_basic_dilution_simulation()