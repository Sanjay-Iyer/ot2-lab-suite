"""Agent NanoDrop plan limits shared by the form, model, and validator.

Physical pipette and well capacities remain in each experiment's safety config.
"""

MAX_REPLICATES = 96
MAX_DROPS_PER_POSITION = 20


def max_vial_air_gap(config: dict) -> float:
    safety = config["safety"]
    return float(safety["p20_max_volume_ul"]) - float(safety["p20_min_volume_ul"])
