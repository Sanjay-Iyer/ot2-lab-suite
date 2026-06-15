"""Registry of robot protocols that can be launched from agent tools."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.utils.paths import PROJECT_ROOT, GENERATED_PROTOCOL_DIR


@dataclass(frozen=True)
class RobotProtocol:
    key: str
    display_name: str
    description: str
    runner_script: Path
    protocol_path: Path
    runner_transport: str
    exposed_to_agent: bool = False


ROBOT_PROTOCOLS: dict[str, RobotProtocol] = {
    "vial_dilution_print": RobotProtocol(
        key="vial_dilution_print",
        display_name="Vial dilution -> paper print",
        description=(
            "Builds a dilution series from water/food coloring vials, then prints "
            "an 8-channel droplet column onto paper."
        ),
        runner_script=PROJECT_ROOT / "scripts" / "run_vial_print_robot.py",
        protocol_path=GENERATED_PROTOCOL_DIR / "vial_dilution_print_latest.py",
        runner_transport="http_api",
        exposed_to_agent=True,
    ),
    "droplet_error_check": RobotProtocol(
        key="droplet_error_check",
        display_name="Droplet print error check",
        description="Runs a targeted paper droplet print and pulls before/after camera images.",
        runner_script=PROJECT_ROOT / "scripts" / "run_droplet_error_check.py",
        protocol_path=PROJECT_ROOT / "src" / "protocols" / "printing_droplet_error_check.py",
        runner_transport="http_api_plus_scp",
        exposed_to_agent=False,
    ),
    "plate_waste_disposal": RobotProtocol(
        key="plate_waste_disposal",
        display_name="Plate -> waste vial disposal",
        description="Moves liquid from a 96-well plate into a 20 mL waste vial.",
        runner_script=PROJECT_ROOT / "scripts" / "run_plate_waste_disposal.py",
        protocol_path=GENERATED_PROTOCOL_DIR / "plate_waste_disposal_latest.py",
        runner_transport="http_api",
        exposed_to_agent=False,
    ),
}


def get_robot_protocol(key: str) -> RobotProtocol:
    try:
        return ROBOT_PROTOCOLS[key]
    except KeyError as exc:
        known = ", ".join(sorted(ROBOT_PROTOCOLS))
        raise KeyError(f"Unknown robot protocol {key!r}. Known protocols: {known}") from exc


def describe_robot_protocols(*, agent_only: bool = False) -> str:
    lines = ["Registered robot protocols:"]
    for protocol in ROBOT_PROTOCOLS.values():
        if agent_only and not protocol.exposed_to_agent:
            continue
        exposed = "agent-enabled" if protocol.exposed_to_agent else "manual-only"
        lines.append(
            f"- {protocol.key}: {protocol.display_name} "
            f"({protocol.runner_transport}, {exposed})"
        )
        lines.append(f"  {protocol.description}")
    return "\n".join(lines)
