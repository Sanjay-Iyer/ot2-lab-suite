"""Stage 6 generalized printing skill isolation and content tests."""

import re

from src.printing.skills import (
    load_printing_skill_content,
    select_standard_experiment_skills,
)


def test_generalized_skill_is_separately_routed_from_legacy_v9_skills():
    assert select_standard_experiment_skills() == ("standard-printing-experiment",)


def test_generalized_skill_contains_reusable_procedure_not_study_answers():
    content = load_printing_skill_content("standard-printing-experiment")
    lowered = content.lower()

    for concept in (
        "transfer",
        "mix",
        "direct_dilution",
        "serial_dilution",
        "repeats",
        "delay_after_pass_s",
        "control",
        "replicates",
        "machine_profile",
        "explicit user approval",
        "ready_for_execution",
        "20 ml",
        "96-well",
        "product_liquid_ids",
        "factor-1",
        "globally unique",
    ):
        assert concept in lowered

    forbidden = (
        "nanoparticle",
        "crystal violet",
        "1/128",
        "eight twofold",
        "three np drops",
        "four columns",
        "300-second",
        "3ce809a8",
    )
    assert [token for token in forbidden if token in lowered] == []

    semantic_leaks = (
        r"(?:eight|8)\s+(?:two[- ]?fold|2[- ]?fold)",
        r"1\s*/\s*128",
        r"(?:three|3)\s+(?:np|nanoparticle)\s+(?:drops?|deposits?)",
        r"(?:four|4)\s+(?:printing\s+)?columns?",
        r"(?:300\s*(?:seconds?|s)\b|five[- ]minute|5\s*min)",
        r"\b[0-9a-f]{64}\b",
    )
    assert [pattern for pattern in semantic_leaks if re.search(pattern, lowered)] == []


def test_skill_forbids_machine_and_motion_invention():
    content = load_printing_skill_content("standard-printing-experiment").lower()

    assert "never invent" in content
    assert "never write ot-2 python" in content
    assert "never treat model text as approval" in content
