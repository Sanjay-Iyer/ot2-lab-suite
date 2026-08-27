"""Focused tests for the conversational SERS architecture.

These cover the contracts that make the design safe: deterministic dilution
maths, machine-profile authority, incremental patching, and the rule that any
edit invalidates every downstream approval.
"""

from __future__ import annotations

import copy

import pytest

from src.sers_engine.emitter import emit_protocol
from src.sers_engine.execution import preflight
from src.sers_engine.intent import validate_intent
from src.sers_engine.machine import load_machine_profile
from src.sers_engine.resolver import resolve_experiment
from src.sers_engine.schema import SERSConfigError
from src.sers_engine.state import REGISTRY, ExperimentSession, ExperimentStatus
from src.sers_engine.targets import TargetSpecError, resolve_targets
from src.sers_engine.validator import validate_experiment

DECK = [
    {"role": "working_plate", "kind": "plate", "slot": 1},
    {"role": "paper", "kind": "paper", "slot": 5},
    {"role": "vial_rack", "kind": "vial_rack", "slot": 7},
    {"role": "tips", "kind": "tiprack", "slot": 9},
]
LIQUIDS = [
    {"name": "nanoparticles", "labware": "vial_rack", "well": "A1",
     "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500},
    {"name": "water", "labware": "vial_rack", "well": "A2",
     "loaded_volume_ul": 15000, "minimum_remaining_volume_ul": 2500},
    {"name": "crystal_violet", "labware": "vial_rack", "well": "B1",
     "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500},
]


def experiment(steps, deck=None, liquids=None, name="test_exp"):
    return {
        "experiment_id": name,
        "experiment_name": name,
        "deck": copy.deepcopy(deck or DECK),
        "liquids": copy.deepcopy(liquids or LIQUIDS),
        "steps": copy.deepcopy(steps),
    }


def dilution(step_id, factor, destination, source="nanoparticles", volume=150):
    return {
        "step_type": "dilution", "step_id": step_id, "source": source,
        "diluent": "water", "destination": destination,
        "dilution_factor": factor, "final_volume_ul": volume,
    }


def printing(step_id, source, targets, drops=1, volume=5.0, **extra):
    return {
        "step_type": "print", "step_id": step_id, "source": source,
        "targets": targets, "drop_volume_ul": volume, "drops_per_target": drops,
        **extra,
    }


def resolved(steps, **kwargs):
    report, plan = validate_experiment(experiment(steps, **kwargs))
    assert report.ok, report.errors
    return plan


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


# ---------------------------------------------------------------------------
# Target expansion
# ---------------------------------------------------------------------------


def test_paper_targets_accept_the_language_a_scientist_uses():
    assert resolve_targets("A1:C1") == ["A1", "B1", "C1"]
    assert resolve_targets("column 1") == [f"{row}1" for row in "ABCDEFGH"]
    assert len(resolve_targets("columns 1 and 2")) == 16
    assert resolve_targets("A1, B4, F7") == ["A1", "B4", "F7"]
    assert resolve_targets("A1:B2") == ["A1", "B1", "A2", "B2"]
    assert len(resolve_targets("rows A-C")) == 36


def test_repeated_paper_targets_are_refused_rather_than_collapsed():
    with pytest.raises(TargetSpecError, match="drops_per_target"):
        resolve_targets(["A1:C1", "A1"])


# ---------------------------------------------------------------------------
# Deterministic dilution arithmetic
# ---------------------------------------------------------------------------


def test_resolver_computes_transfer_volumes_from_the_scientific_target():
    plan = resolved([dilution("d30", 30, "working_plate:A1"),
                     dilution("d50", 50, "working_plate:A2")])
    first, second = plan.steps
    assert (first.stock_volume_ul, first.diluent_volume_ul) == (5.0, 145.0)
    assert (second.stock_volume_ul, second.diluent_volume_ul) == (3.0, 147.0)
    assert first.dilution_factor_achieved == 30
    assert second.dilution_factor_achieved == 50


def test_a_dilution_below_the_p20_minimum_is_refused_with_a_usable_fix():
    with pytest.raises(SERSConfigError) as exc:
        resolve_experiment(
            validate_intent(experiment([dilution("d200", 200, "working_plate:A1", volume=100)]))
        )
    message = str(exc.value)
    assert "below the P20 minimum" in message
    assert "at least 200" in message  # 200x of 1 uL minimum needs 200 uL final


def test_large_transfers_are_chunked_for_the_p20():
    plan = resolved([dilution("d30", 30, "working_plate:A1")])
    step = plan.steps[0]
    assert sum(step.diluent_chunks_ul) == pytest.approx(145.0)
    assert max(step.diluent_chunks_ul) <= 18.0
    assert min(step.diluent_chunks_ul) >= 1.0


def test_an_undiluted_condition_needs_no_diluent_leg():
    plan = resolved([dilution("neat", 1, "working_plate:A1", volume=100)])
    step = plan.steps[0]
    assert step.diluent_volume_ul == 0
    assert step.diluent_chunks_ul == []
    assert step.tips == 2  # stock transfer + mix, no diluent tip


# ---------------------------------------------------------------------------
# Machine profile authority
# ---------------------------------------------------------------------------


def test_calibrated_geometry_comes_from_the_profile_not_the_experiment():
    profile = load_machine_profile()
    plan = resolved([dilution("d30", 30, "working_plate:A1")])
    config = plan.as_experiment_config()
    step = config.dilutions[0]
    assert step.source.bottom_offset_mm == profile.labware_for("vial_rack").aspirate_height_mm
    assert step.destination.dispense_reference == "top"
    assert step.destination.dispense_offset_mm == -2.0
    assert config.pipette.mount == profile.machine.pipette.mount == "left"


def test_an_experiment_cannot_name_its_own_labware():
    payload = experiment([dilution("d30", 30, "working_plate:A1")])
    payload["deck"][0]["load_name"] = "some_unapproved_plate"
    with pytest.raises(SERSConfigError):
        validate_intent(payload)


def test_paper_release_height_is_tunable_only_inside_the_validated_envelope():
    plan = resolved([printing("p", "crystal_violet", ["A1"], dispense_height_mm=1.5)])
    assert plan.steps[0].dispense_height_mm == 1.5
    clamped = resolved([printing("p", "crystal_violet", ["A1"], dispense_height_mm=9.0)])
    assert clamped.steps[0].dispense_height_mm == 2.0
    assert any("envelope" in warning for warning in clamped.warnings)


# ---------------------------------------------------------------------------
# Representative workflows (A-G)
# ---------------------------------------------------------------------------


def test_a_printing_stock_only():
    plan = resolved([printing("p", "nanoparticles", ["A1:C1"])])
    assert plan.totals.dilution_count == 0
    assert plan.totals.deposits == 3


def test_b_dilution_only():
    plan = resolved([dilution("d10", 10, "working_plate:A1"),
                     dilution("d20", 20, "working_plate:A2")])
    assert plan.totals.print_count == 0
    assert plan.totals.deposits == 0
    assert plan.totals.final_well_volumes["working_plate:A1"] == 150.0


def test_c_dilution_series_printed_one_well_per_paper_column():
    steps = [dilution(f"d{factor}", factor, f"working_plate:A{index}")
             for index, factor in enumerate([5, 10, 20, 30], start=1)]
    steps += [printing(f"p{index}", f"d{factor}", [f"A{index}:C{index}"])
              for index, factor in enumerate([5, 10, 20, 30], start=1)]
    plan = resolved(steps)
    assert plan.totals.dilution_count == 4
    assert plan.totals.deposits == 12


def test_d_np_print_then_wait_then_cv_overprint():
    plan = resolved([
        dilution("d30", 30, "working_plate:A1"),
        printing("np", "d30", ["A1:C1"], drops=3),
        {"step_type": "wait", "step_id": "dry", "duration_s": 3600, "reason": "dry"},
        printing("cv", "crystal_violet", ["A1:C1"]),
    ])
    kinds = [step.kind for step in plan.steps]
    assert kinds == ["dilution", "print", "wait", "print"]
    assert plan.totals.hold_time_s == 3600


def test_e_both_materials_diluted():
    plan = resolved([
        dilution("np10", 10, "working_plate:A1"),
        dilution("cv10", 10, "working_plate:B1", source="crystal_violet"),
        printing("pnp", "np10", ["A1:C1"]),
        printing("pcv", "cv10", ["A1:C1"]),
    ])
    sources = {step.source_liquid for step in plan.steps if step.kind == "print"}
    assert len(sources) == 2
    assert plan.totals.dilution_count == 2


def test_f_arbitrary_drop_counts_scale_volume_and_deposits():
    plan = resolved([
        printing("p1", "crystal_violet", ["A1"], drops=1),
        printing("p3", "crystal_violet", ["A2"], drops=3),
        printing("p5", "crystal_violet", ["A3"], drops=5),
        printing("p10", "crystal_violet", ["A4"], drops=10),
    ])
    assert [step.total_deposits for step in plan.steps] == [1, 3, 5, 10]
    assert plan.totals.deposits == 19
    assert plan.totals.printed_volume_ul == pytest.approx(95.0)


def test_g_two_paper_fixtures_on_separate_slots():
    deck = DECK + [{"role": "paper_2", "kind": "paper", "slot": 11}]
    plan = resolved(
        [printing("p1", "crystal_violet", ["A1:C1"], paper="paper"),
         printing("p2", "crystal_violet", ["A1:C1"], paper="paper_2")],
        deck=deck,
    )
    assert {step.paper_slot for step in plan.steps} == {5, 11}


# ---------------------------------------------------------------------------
# H. Conversational revision and invalidation
# ---------------------------------------------------------------------------


def _session_for_revision() -> ExperimentSession:
    session = ExperimentSession.create(experiment([
        dilution("np_a", 10, "working_plate:A1"),
        dilution("np_b", 20, "working_plate:A2"),
        printing("print_a", "np_a", ["A1:C1"], drops=3),
        printing("print_b", "np_b", ["A2:C2"], drops=3),
        {"step_type": "wait", "step_id": "dry", "duration_s": 1800},
        printing("print_cv", "crystal_violet", ["A1:C1", "A2:C2"]),
    ]))
    REGISTRY.add(session)
    session.resolve_and_validate()
    return session


def test_h_revision_updates_only_what_was_asked_for():
    session = _session_for_revision()
    before = session.resolved.config_hash

    session.apply_patch({"update_steps": [
        {"step_id": "np_a", "dilution_factor": 30},
        {"step_id": "np_b", "dilution_factor": 50},
    ]})
    session.resolve_and_validate()

    factors = [step.dilution_factor for step in session.experiment.steps
               if step.step_type == "dilution"]
    assert factors == [30, 50]
    # Everything unrelated survives the edit.
    assert [step.step_id for step in session.experiment.steps] == [
        "np_a", "np_b", "print_a", "print_b", "dry", "print_cv"
    ]
    prints = [step for step in session.experiment.steps if step.step_type == "print"]
    assert [step.drops_per_target for step in prints] == [3, 3, 1]
    assert session.resolved.config_hash != before


def test_revision_can_move_a_condition_to_another_column():
    session = _session_for_revision()
    session.apply_patch({"update_steps": [{"step_id": "print_a", "targets": ["A3:C3"]}]})
    session.resolve_and_validate()
    step = next(item for item in session.resolved.steps if item.step_id == "print_a")
    assert step.targets == ["A3", "B3", "C3"]


def test_revision_can_change_only_the_drop_count():
    session = _session_for_revision()
    session.apply_patch({"update_steps": [{"step_id": "print_b", "drops_per_target": 5}]})
    session.resolve_and_validate()
    step = next(item for item in session.resolved.steps if item.step_id == "print_b")
    assert step.drops_per_target == 5
    assert step.total_deposits == 15


def test_patching_an_unknown_step_is_refused_and_changes_nothing():
    session = _session_for_revision()
    before = session.resolved.config_hash
    with pytest.raises(SERSConfigError, match="unknown step"):
        session.apply_patch({"update_steps": [{"step_id": "nope", "drops_per_target": 2}]})
    assert session.resolved.config_hash == before
    assert session.revision == 0


def test_a_rejected_patch_leaves_the_experiment_untouched():
    session = _session_for_revision()
    with pytest.raises(SERSConfigError):
        session.apply_patch({"update_steps": [{"step_id": "print_a", "targets": ["Z9"]}]})
    assert [step.step_id for step in session.experiment.steps][0] == "np_a"
    assert session.revision == 0


# ---------------------------------------------------------------------------
# Approval, hashing, and gating
# ---------------------------------------------------------------------------


def test_simulation_requires_plan_approval_first():
    session = _session_for_revision()
    with pytest.raises(SERSConfigError, match="plan approval"):
        session.simulate()


def test_editing_after_simulation_invalidates_everything_downstream():
    session = _session_for_revision()
    session.approve_plan()
    report = session.simulate()
    assert report.passed
    session.approve_live_execution()
    assert session.status is ExperimentStatus.APPROVED_FOR_LIVE
    simulated = session.simulated_hash

    session.apply_patch({"update_steps": [{"step_id": "print_b", "drops_per_target": 5}]})

    assert session.status is ExperimentStatus.DRAFT
    assert session.simulation is None
    assert session.simulated_hash is None
    assert session.plan_approved is False
    assert session.live_execution_approved is False
    session.resolve_and_validate()
    assert session.resolved.resolved_hash != simulated
    assert session.hash_is_current() is False


def test_live_approval_is_refused_without_a_current_simulation():
    session = _session_for_revision()
    with pytest.raises(SERSConfigError, match="passing simulation"):
        session.approve_live_execution()


def test_preflight_blocks_live_execution_on_this_machine():
    session = _session_for_revision()
    report = preflight(session)
    assert not report.ready
    assert "simulation passed" in report.blocking
    assert "human live approval" in report.blocking
    # The development laptop is never a real-robot execution host.
    assert "real-robot laptop" in report.blocking


def test_status_moves_through_the_expected_lifecycle():
    session = _session_for_revision()
    assert session.status is ExperimentStatus.VALIDATED
    session.approve_plan()
    assert session.status is ExperimentStatus.APPROVED_FOR_SIMULATION
    assert session.simulate().passed
    assert session.status is ExperimentStatus.SIMULATED
    session.approve_live_execution()
    assert session.status is ExperimentStatus.APPROVED_FOR_LIVE


# ---------------------------------------------------------------------------
# Physical safety carried over from the deterministic engine
# ---------------------------------------------------------------------------


def test_a_vial_too_shallow_for_the_calibrated_height_is_refused():
    liquids = copy.deepcopy(LIQUIDS)
    liquids[0]["loaded_volume_ul"] = 500  # 0.8 mm in a 28 mm bore
    liquids[0]["minimum_remaining_volume_ul"] = 0
    report, _ = validate_experiment(
        experiment([dilution("d30", 30, "working_plate:A1")], liquids=liquids)
    )
    assert not report.ok
    assert any("would draw air" in error for error in report.errors)


def test_the_volume_ledger_refuses_a_run_that_would_exhaust_a_source():
    liquids = copy.deepcopy(LIQUIDS)
    liquids[2]["loaded_volume_ul"] = 2520  # only 20 uL above its reserve
    report, _ = validate_experiment(
        experiment([printing("p", "crystal_violet", ["A1:H1"], drops=2)], liquids=liquids)
    )
    assert not report.ok
    assert any("reserve" in error or "available" in error for error in report.errors)


def test_dilution_still_purges_once_per_transfer_not_once_per_chunk():
    plan = resolved([dilution("d30", 30, "working_plate:A1")])
    blowouts = [op for op in plan.operations if op["op"] == "blow_out"]
    # One purge for the stock leg, one for the nine-chunk diluent leg.
    assert len(blowouts) == 2


def test_tip_availability_is_checked_before_anything_moves():
    # 96 per-target tips exactly fills a fresh rack, so starting one tip in
    # leaves the run one short.
    steps = [printing("p", "crystal_violet", ["rows A-H"], tip_strategy="per_target")]
    payload = experiment(steps)
    payload["tips"] = {"start_tip": "B1"}
    report, _ = validate_experiment(payload)
    assert not report.ok
    assert any("tips" in error for error in report.errors)


# ---------------------------------------------------------------------------
# The emitted protocol is the simulated workflow
# ---------------------------------------------------------------------------


def test_emitted_protocol_carries_the_resolved_hash_and_no_arithmetic():
    plan = resolved([
        dilution("d30", 30, "working_plate:A1"),
        printing("p", "d30", ["A1:C1"], drops=2),
    ])
    source = emit_protocol(plan)
    assert plan.resolved_hash in source
    assert "RESOLVED_HASH" in source
    # The protocol replays recorded operations; it must not recompute volumes.
    assert "dilution_factor" not in source
    assert source.count("OPERATIONS = json.loads") == 1


def test_recorded_operations_match_the_resolved_plan():
    plan = resolved([printing("p", "crystal_violet", ["A1:C1"], drops=2)])
    aspirations = [op for op in plan.operations if op["op"] == "aspirate"]
    assert len(aspirations) == 6  # 3 locations x 2 drops
    assert all(op["volume"] == 5.0 for op in aspirations)
    assert all(op["labware"] == "vial_rack" and op["well"] == "B1" for op in aspirations)
