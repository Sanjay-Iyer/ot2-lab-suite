"""The scientific record must survive the process that produced it.

These tests are about reconstructability, not about logging mechanics: given
only a session directory, can someone tell what was asked, what the agent
proposed, how the workflow changed, what exactly was simulated, what was
approved, and what the robot was sent?

No test here touches a physical OT-2. The robot transport is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.sers_engine.agent.graph import SERSExperimentAgent
from src.sers_engine.provenance import (
    Event,
    close_session,
    create_session,
    export_session,
    set_active_session,
)
from src.sers_engine.state import REGISTRY, ExperimentSession
from tests.fake_llm import ScriptedChatModel

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
]
STEPS = [
    {"step_type": "dilution", "step_id": "np_a", "source": "nanoparticles",
     "diluent": "water", "destination": "working_plate:A1",
     "dilution_factor": 30, "final_volume_ul": 150},
    {"step_type": "print", "step_id": "print_a", "source": "np_a",
     "targets": ["A1:C1"], "drop_volume_ul": 5, "drops_per_target": 1},
]

CREATE_CALL = {
    "name": "create_experiment",
    "args": {"experiment_name": "np_titration", "deck": DECK, "liquids": LIQUIDS, "steps": STEPS},
}


@pytest.fixture
def record(tmp_path):
    """A provenance session rooted in a temporary directory."""
    REGISTRY.clear()
    session = create_session(label="test", mode="agent", root=tmp_path, thread_id="t-1")
    yield session
    close_session(session, status="test")
    set_active_session(None)
    REGISTRY.clear()


def _lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events(record) -> list[dict]:
    return _lines(record.events_path)


def _event_names(record) -> list[str]:
    return [item["event"] for item in _events(record)]


def _new_experiment() -> ExperimentSession:
    session = ExperimentSession.create(
        {"experiment_name": "np_titration", "deck": DECK, "liquids": LIQUIDS, "steps": STEPS}
    )
    REGISTRY.add(session)
    session.resolve_and_validate()
    return session


def _agent(script) -> SERSExperimentAgent:
    return SERSExperimentAgent(ScriptedChatModel(script), thread_id="t-1")


# ---------------------------------------------------------------------------
# A. Conversation persistence
# ---------------------------------------------------------------------------


def test_every_conversational_turn_survives_the_process_that_produced_it(record):
    """The checkpointer dies with the CLI; conversation.jsonl must not."""
    agent = _agent(["Which labware is approved?", [CREATE_CALL], "Here is the plan."])
    agent.send("What can this robot do?")
    agent.send("Make a 30x nanoparticle dilution and print it on column 1.")

    turns = _lines(record.conversation_path)
    user_text = [item["text"] for item in turns if item["role"] == "user"]
    assert user_text == [
        "What can this robot do?",
        "Make a 30x nanoparticle dilution and print it on column 1.",
    ]
    assistant = [item for item in turns if item["role"] == "assistant"]
    assert "Which labware is approved?" in [item["text"] for item in assistant]
    assert "Here is the plan." in [item["text"] for item in assistant]

    # Sequence numbers are strictly increasing and every turn is timestamped
    # with an offset, so the record is orderable and unambiguous later.
    assert [item["sequence"] for item in turns] == sorted(item["sequence"] for item in turns)
    for item in turns:
        assert item["timestamp"][10] == "T"
        assert item["timestamp"][-6] in "+-" or item["timestamp"].endswith("Z")
        assert item["thread_id"] == "t-1"


def test_a_second_agent_over_the_same_record_appends_rather_than_restarting(record):
    """Recreating the agent must not truncate what the first one wrote."""
    first = _agent(["one"])
    first.send("first question")
    before = len(_lines(record.conversation_path))

    second = SERSExperimentAgent(ScriptedChatModel(["two"]), thread_id="t-2", provenance=record)
    second.send("second question")

    turns = _lines(record.conversation_path)
    assert len(turns) > before
    assert [item["text"] for item in turns if item["role"] == "user"] == [
        "first question",
        "second question",
    ]


def test_the_full_original_text_is_kept_not_a_summary(record):
    long_request = (
        "Make 30x and 50x nanoparticle dilutions at 150 uL each, print three "
        "drops of each onto columns 1 and 2 of the paper, wait thirty minutes "
        "for them to dry, then overprint one drop of crystal violet on every "
        "printed spot."
    )
    agent = _agent(["understood"])
    agent.send(long_request)
    turns = _lines(record.conversation_path)
    assert turns[0]["text"] == long_request


# ---------------------------------------------------------------------------
# B. Tool-call persistence
# ---------------------------------------------------------------------------


def test_tool_arguments_and_results_are_both_persisted(record):
    agent = _agent(
        [
            [CREATE_CALL],
            [{"name": "validate_experiment", "args": {}}],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            "simulated",
        ]
    )
    agent.send("build and simulate it")

    calls = _lines(record.tool_calls_path)
    by_name = {item["tool_name"]: item for item in calls}
    assert {"create_experiment", "validate_experiment", "approve_plan", "simulate_experiment"} <= set(by_name)

    created = by_name["create_experiment"]
    assert created["arguments"]["experiment_name"] == "np_titration"
    assert created["arguments"]["steps"][0]["dilution_factor"] == 30
    assert created["ok"] is True
    assert created["result"]["state"]["experiment_id"].startswith("exp_")
    assert created["tool_call_id"]
    assert created["revision_after"] == 0

    simulated = by_name["simulate_experiment"]
    assert simulated["result"]["simulation"]["status"] == "passed"


def test_a_failed_tool_call_is_recorded_as_a_failure_not_dropped(record):
    agent = _agent([[{"name": "update_experiment", "args": {"update_steps": [{"step_id": "nope"}]}}], "sorry"])
    agent.send("change a step that does not exist")

    calls = _lines(record.tool_calls_path)
    assert calls[-1]["tool_name"] == "update_experiment"
    assert calls[-1]["ok"] is False
    assert "no current experiment" in calls[-1]["result"]["error"]


def test_revision_movement_is_recorded_against_each_tool_call(record):
    agent = _agent(
        [
            [CREATE_CALL],
            [{"name": "update_experiment", "args": {"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]}}],
            "changed",
        ]
    )
    agent.send("make it, then change it to 50x")

    calls = {item["tool_name"]: item for item in _lines(record.tool_calls_path)}
    assert calls["create_experiment"]["revision_after"] == 0
    assert calls["update_experiment"]["revision_before"] == 0
    assert calls["update_experiment"]["revision_after"] == 1


# ---------------------------------------------------------------------------
# C. Revision history
# ---------------------------------------------------------------------------


def test_three_revisions_produce_three_immutable_snapshots(record):
    session = _new_experiment()
    session.apply_patch({"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]})
    session.resolve_and_validate()

    first_bytes = (record.directory / "revisions" / "revision_001.yaml").read_bytes()
    second_bytes = (record.directory / "revisions" / "revision_002.yaml").read_bytes()

    session.apply_patch({"update_steps": [{"step_id": "print_a", "drops_per_target": 3}]})
    session.resolve_and_validate()

    snapshots = sorted((record.directory / "revisions").glob("revision_*.yaml"))
    assert [item.name for item in snapshots] == [
        "revision_001.yaml",
        "revision_002.yaml",
        "revision_003.yaml",
    ]
    # Earlier revisions are untouched by anything that happened afterwards.
    assert (record.directory / "revisions" / "revision_001.yaml").read_bytes() == first_bytes
    assert (record.directory / "revisions" / "revision_002.yaml").read_bytes() == second_bytes

    factors = [
        yaml.safe_load(item.read_text(encoding="utf-8"))["steps"][0]["dilution_factor"]
        for item in snapshots
    ]
    assert factors == [30, 50, 50]
    drops = [
        yaml.safe_load(item.read_text(encoding="utf-8"))["steps"][1]["drops_per_target"]
        for item in snapshots
    ]
    assert drops == [1, 1, 3]


def test_each_snapshot_is_a_complete_experiment_not_a_diff(record):
    session = _new_experiment()
    session.apply_patch({"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]})
    session.resolve_and_validate()

    payload = yaml.safe_load(
        (record.directory / "revisions" / "revision_002.yaml").read_text(encoding="utf-8")
    )
    assert {"experiment_id", "experiment_name", "deck", "liquids", "steps"} <= set(payload)
    assert len(payload["steps"]) == 2
    assert len(payload["liquids"]) == 2
    # And it round-trips back into a valid experiment.
    ExperimentSession.create(payload)


def test_a_revision_carries_its_reason_hashes_and_a_structured_diff(record):
    session = _new_experiment()
    session.apply_patch({"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]})
    session.resolve_and_validate()

    sidecar = json.loads(
        (record.directory / "revisions" / "revision_002.json").read_text(encoding="utf-8")
    )
    assert sidecar["revision"] == 1
    assert sidecar["revision_index"] == 2
    assert "dilution_factor=50" in sidecar["reason"]
    assert sidecar["config_hash"] and sidecar["resolved_hash"]
    assert sidecar["experiment_sha256"]

    diff = json.loads(
        (record.directory / "revisions" / "revision_002.diff.json").read_text(encoding="utf-8")
    )
    assert diff["changed_fields"] == ["steps"]
    assert diff["changes"]["steps"]["before"][0]["dilution_factor"] == 30
    assert diff["changes"]["steps"]["after"][0]["dilution_factor"] == 50


def test_revalidating_an_unchanged_experiment_invents_no_revision_history(record):
    session = _new_experiment()
    session.resolve_and_validate()
    session.resolve_and_validate()
    assert len(list((record.directory / "revisions").glob("revision_*.yaml"))) == 1


def test_the_resolved_plan_is_saved_per_revision_in_machine_readable_form(record):
    session = _new_experiment()
    plan = json.loads(
        (record.directory / "resolved" / "revision_001.json").read_text(encoding="utf-8")
    )
    assert plan["resolved_hash"] == session.resolved.resolved_hash
    assert plan["config_hash"] == session.resolved.config_hash
    # The deterministic numbers a paper needs, not a prose summary.
    assert plan["steps"][0]["kind"] == "dilution"
    assert plan["steps"][0]["stock_volume_ul"] == 5
    assert plan["steps"][0]["diluent_volume_ul"] == 145
    assert plan["operations"], "the exact operation list must be persisted"
    assert plan["totals"]["tips_required"] >= 1
    assert plan["execution_config"]["deck_layout"]["labware"]["working_plate"]["slot"] == 1
    assert any("slot" in key for key in plan["deck"])


def test_validation_detail_is_persisted_per_revision(record):
    _new_experiment()
    report = json.loads(
        (record.directory / "validation" / "revision_001.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "valid"
    assert "aspiration liquid depth" in report["checks_run"]
    assert "tip availability" in report["checks_run"]
    assert report["requirements"]["tips_required"] >= 1
    assert report["requirements"]["liquid_requirements"]
    assert report["config_hash"] and report["resolved_hash"]


# ---------------------------------------------------------------------------
# D and E. Simulation provenance and edit-after-simulation
# ---------------------------------------------------------------------------


def test_the_simulation_report_records_the_hashes_it_was_bound_to(record):
    session = _new_experiment()
    session.approve_plan()
    report = session.simulate()

    stored = json.loads(
        (record.directory / "simulation" / "revision_001.json").read_text(encoding="utf-8")
    )
    assert stored["status"] == "passed"
    assert stored["resolved_hash"] == session.resolved.resolved_hash
    assert stored["config_hash"] == session.resolved.config_hash
    assert stored["resolved_hash"] == report.resolved_hash
    assert stored["command_count"] > 0
    assert stored["opentrons_api_level"] == "2.15"
    assert stored["protocol_file"] == "protocols/revision_001.py"
    assert stored["protocol_sha256"]


def test_the_simulated_protocol_is_persisted_and_carries_the_resolved_hash(record):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()

    protocol = (record.directory / "protocols" / "revision_001.py").read_text(encoding="utf-8")
    assert session.resolved.resolved_hash in protocol
    assert session.resolved.config_hash in protocol
    assert (record.directory / "generated_protocol.py").read_text(encoding="utf-8") == protocol


def test_editing_after_simulation_is_logged_and_preserves_the_old_simulation(record):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    first_report = (record.directory / "simulation" / "revision_001.json").read_bytes()
    first_protocol = (record.directory / "protocols" / "revision_001.py").read_bytes()

    session.apply_patch({"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]})
    session.resolve_and_validate()
    session.approve_plan()
    session.simulate()

    assert Event.SIMULATION_INVALIDATED in _event_names(record)
    # The superseded evidence is still exactly where it was written.
    assert (record.directory / "simulation" / "revision_001.json").read_bytes() == first_report
    assert (record.directory / "protocols" / "revision_001.py").read_bytes() == first_protocol
    assert (record.directory / "simulation" / "revision_002.json").is_file()

    old = json.loads((record.directory / "simulation" / "revision_001.json").read_text("utf-8"))
    new = json.loads((record.directory / "simulation" / "revision_002.json").read_text("utf-8"))
    assert old["resolved_hash"] != new["resolved_hash"]
    # And the convenience pointer now names the current one.
    final = json.loads((record.directory / "simulation_report.json").read_text("utf-8"))
    assert final["resolved_hash"] == new["resolved_hash"]


def test_a_failed_simulation_is_recorded_rather_than_hidden(record, monkeypatch):
    session = _new_experiment()
    session.approve_plan()

    def explode(plan):
        from src.sers_engine.simulation import SimulationReport

        return SimulationReport(
            status="failed",
            experiment_id=plan.experiment_id,
            experiment_name=plan.experiment_name,
            config_hash=plan.config_hash,
            resolved_hash=plan.resolved_hash,
            machine_profile_id=plan.machine_profile_id,
            errors=["RuntimeError: tip collision"],
        )

    monkeypatch.setattr("src.sers_engine.state.simulate_resolved", explode)
    session.simulate()

    assert Event.SIMULATION_FAILED in _event_names(record)
    stored = json.loads((record.directory / "simulation" / "revision_001.json").read_text("utf-8"))
    assert stored["status"] == "failed"
    assert stored["errors"] == ["RuntimeError: tip collision"]


# ---------------------------------------------------------------------------
# F. Approval logging
# ---------------------------------------------------------------------------


def test_the_operators_own_approval_words_are_persisted_with_the_hash(record):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    session.approve_live_execution("Yes, run this exact workflow.")

    approvals = [item for item in _events(record) if item["event"] == Event.LIVE_APPROVED]
    assert len(approvals) == 1
    assert approvals[0]["approval_text"] == "Yes, run this exact workflow."
    assert approvals[0]["resolved_hash"] == session.resolved.resolved_hash
    assert approvals[0]["details"]["simulated_hash"] == session.simulated_hash

    plan_approvals = [item for item in _events(record) if item["event"] == Event.PLAN_APPROVED]
    assert plan_approvals[0]["config_hash"] == session.resolved.config_hash
    assert plan_approvals[0]["details"]["authorizes"] == "simulation only"


def test_plan_approval_quotes_the_researchers_last_message(record):
    agent = _agent(
        [[CREATE_CALL], "Here is the plan.", [{"name": "approve_plan", "args": {}}], "approved"]
    )
    agent.send("Build it.")
    agent.send("Yes, that plan is right - approve it.")

    approvals = [item for item in _events(record) if item["event"] == Event.PLAN_APPROVED]
    assert approvals[-1]["approval_text"] == "Yes, that plan is right - approve it."


def test_refusing_a_robot_call_is_recorded_with_what_was_refused(record):
    agent = _agent(
        [
            [CREATE_CALL],
            [{"name": "approve_live_execution", "args": {"confirmation": "go"}}],
            "understood",
        ]
    )
    agent.send("build it and run it")
    result = agent.refuse_pending_tool("the operator declined at the terminal")
    assert not result["interrupted"]

    names = _event_names(record)
    assert Event.LIVE_APPROVAL_REQUESTED in names
    assert Event.LIVE_REFUSED in names
    refusal = [item for item in _events(record) if item["event"] == Event.LIVE_REFUSED][-1]
    assert refusal["details"]["reason"] == "the operator declined at the terminal"
    assert refusal["details"]["pending_tools"][0]["name"] == "approve_live_execution"
    assert Event.LIVE_APPROVED not in names


# ---------------------------------------------------------------------------
# Metadata, manifest, and the shape of the record
# ---------------------------------------------------------------------------


def test_metadata_records_software_and_model_provenance_without_inventing_it(record):
    agent = _agent(["hello"])
    agent.send("hi")
    _new_experiment()
    record.write_manifest()

    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["session_id"] == record.session_id
    assert metadata["provenance_schema_version"] == "sers-provenance/v1"
    assert metadata["python_version"]
    assert metadata["intent_schema_version"] == "sers-experiment-intent/v1"
    assert metadata["opentrons_api_level"] == "2.15"
    assert metadata["machine_profile"].endswith("ot2_sers_p20_v1.yaml")
    assert metadata["machine_profile_sha256"]
    assert metadata["system_prompt_sha256"]
    assert metadata["tool_schema_sha256"]
    assert "create_experiment" in metadata["tool_names"]
    assert metadata["model_provider"] == "ScriptedChatModel"
    # An unknown value is left out rather than guessed.
    assert "langgraph" in metadata["packages"]
    assert metadata["degraded"] is False


def test_the_manifest_maps_every_artifact_to_a_hash_that_matches(record):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    record.write_transcript()
    record.write_manifest()

    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    assert manifest["final_revision"] == 0
    assert manifest["final_resolved_hash"] == session.resolved.resolved_hash
    assert manifest["revision_count"] == 1
    for name in ("events", "final_experiment", "resolved_workflow", "protocol", "metadata"):
        assert name in manifest["artifacts"], name

    from src.sers_engine.provenance import sha256_path

    for name, entry in manifest["artifacts"].items():
        target = record.directory / entry["path"]
        if not target.is_file():
            target = Path(entry["path"])  # the machine profile lives in the repo
        assert target.is_file(), name
        assert sha256_path(target) == entry["sha256"], name


def test_the_final_experiment_is_the_exact_structured_state_that_was_resolved(record):
    session = _new_experiment()
    session.apply_patch({"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]})
    session.resolve_and_validate()

    from src.sers_engine.intent import intent_as_dict

    final = yaml.safe_load((record.directory / "final_experiment.yaml").read_text("utf-8"))
    assert final == intent_as_dict(session.experiment)
    latest = yaml.safe_load(
        (record.directory / "revisions" / "revision_002.yaml").read_text("utf-8")
    )
    assert final == latest


def test_the_human_readable_transcript_is_generated_beside_the_canonical_jsonl(record):
    agent = _agent([[CREATE_CALL], "Here is the plan."])
    agent.send("Make a 30x dilution and print column 1.")

    transcript = (record.directory / "conversation.md").read_text(encoding="utf-8")
    assert "# SERS Agent Conversation" in transcript
    assert "Make a 30x dilution and print column 1." in transcript
    assert "### Tool call: create_experiment" in transcript
    # The JSONL is still the canonical record and says so.
    assert "conversation.jsonl" in transcript
    assert record.conversation_path.is_file()


# ---------------------------------------------------------------------------
# G. Manual runner
# ---------------------------------------------------------------------------


def test_the_manual_runner_records_a_full_session_without_any_agent(tmp_path, monkeypatch):
    """No LLM, no conversation - but the same reconstructable record."""
    REGISTRY.clear()
    set_active_session(None)
    monkeypatch.setattr("src.sers_engine.provenance.logger.SESSIONS_ROOT", tmp_path)

    import scripts.run_sers_experiment as runner

    config = Path("configs/experiments/sers_exp1_np_cv.yaml")
    assert config.is_file(), "the reference intent config is missing"
    code = runner.main(["--config", str(config)])
    assert code == 0

    sessions = [item for item in tmp_path.iterdir() if item.is_dir()]
    assert len(sessions) == 1
    directory = sessions[0]

    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["mode"] == "manual"
    assert metadata["model_name"] is None
    assert metadata["input_config"].endswith("sers_exp1_np_cv.yaml")
    assert metadata["input_config_sha256"]

    names = [json.loads(line)["event"] for line in (directory / "events.jsonl").read_text("utf-8").splitlines()]
    assert Event.MANUAL_CONFIG_EXECUTION in names
    assert Event.SIMULATION_PASSED in names
    assert Event.PLAN_APPROVED in names

    for expected in (
        "input_config.yaml",
        "final_experiment.yaml",
        "resolved_workflow.json",
        "validation_report.json",
        "simulation_report.json",
        "generated_protocol.py",
        "manifest.json",
    ):
        assert (directory / expected).is_file(), expected
    # There was no conversation, so there is no conversation file to fake.
    assert not (directory / "conversation.jsonl").is_file()
    set_active_session(None)
    REGISTRY.clear()


# ---------------------------------------------------------------------------
# H. Export
# ---------------------------------------------------------------------------


def test_the_si_export_copies_the_record_and_its_manifest_still_resolves(record, tmp_path):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    record.write_manifest()

    result = export_session(record.session_id, destination=tmp_path / "si", root=record.directory.parent)
    export_dir = Path(result["export_dir"])

    assert result["verification"] == []
    assert (export_dir / "README.md").is_file()
    readme = (export_dir / "README.md").read_text(encoding="utf-8")
    assert session.resolved.resolved_hash in readme
    assert "conversation.jsonl" in readme or "manifest.json" in readme

    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["artifacts"].values():
        target = export_dir / entry["path"]
        if not target.is_file():
            target = Path(entry["path"])
        assert target.is_file(), entry["path"]

    assert (export_dir / "revisions" / "revision_001.yaml").is_file()
    assert (export_dir / "protocols" / "revision_001.py").is_file()
    # Exporting must not disturb the original record.
    assert record.manifest_path.is_file()
    assert (record.directory / "revisions" / "revision_001.yaml").is_file()


def test_the_exporter_reports_a_tampered_artifact(record, tmp_path):
    _new_experiment()
    record.write_manifest()
    (record.directory / "final_experiment.yaml").write_text("tampered: true\n", encoding="utf-8")

    result = export_session(record.session_id, destination=tmp_path / "si", root=record.directory.parent)
    assert any("final_experiment" in problem for problem in result["verification"])


# ---------------------------------------------------------------------------
# I. Secret protection
# ---------------------------------------------------------------------------


def test_no_credential_reaches_the_record(record, monkeypatch):
    secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-SECRETVALUE0001",
        "GOOGLE_APPLICATION_CREDENTIALS": "C:/keys/vertex-SECRETVALUE0002.json",
        "OT2_SSH_PASSWORD": "SECRETVALUE0003",
        "OPENAI_API_KEY": "sk-SECRETVALUE0004",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OT2_LAPTOP_ROLE", "real-robot")

    agent = _agent([[CREATE_CALL], "done"])
    agent.send("build it")
    session = REGISTRY.get()
    session.approve_plan()
    session.simulate()
    record.describe(**{"environment": __import__("src.sers_engine.provenance.software", fromlist=["x"]).safe_environment()})
    record.write_manifest()
    record.write_transcript()

    for path in record.directory.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, value in secrets.items():
            assert value not in text, f"{name} leaked into {path.name}"
            assert name not in text, f"{name} was named in {path.name}"

    # The operational flag that is on the allowlist is recorded, deliberately.
    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["environment"]["OT2_LAPTOP_ROLE"] == "real-robot"


def test_the_environment_allowlist_never_contains_a_secret_shaped_name():
    from src.sers_engine.provenance.software import ENVIRONMENT_ALLOWLIST

    for name in ENVIRONMENT_ALLOWLIST:
        upper = name.upper()
        assert not any(
            marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        ), name


# ---------------------------------------------------------------------------
# J. Multiple physical runs, and the live-execution provenance gate
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"

    def json(self):
        return self._payload


def _simulated_session(record) -> ExperimentSession:
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    session.approve_live_execution("Run it on the robot.")
    return session


def _mock_robot(monkeypatch, run_ids):
    """Stand in for the OT-2 HTTP API. Nothing physical is touched."""
    from src.sers_engine import execution

    issued = iter(run_ids)
    monkeypatch.setattr(execution, "preflight", lambda session, host=None: _ready_report(session))
    monkeypatch.setattr(
        "src.lab.robot_connection.resolve_host", lambda host: host or "10.0.0.5", raising=False
    )
    monkeypatch.setattr(
        "src.lab.robot_connection.base_url", lambda host: f"http://{host}:31950", raising=False
    )
    import requests

    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResponse({"data": {"id": "protocol-1"}})
    )

    def fake_request(method, host, path, **kwargs):
        if method == "POST" and path == "/runs":
            return {"data": {"id": next(issued)}}
        return {"data": {"status": "queued"}}

    monkeypatch.setattr(execution, "_request", fake_request)


def _ready_report(session):
    from src.sers_engine.execution import PreflightReport

    return PreflightReport(
        status="ready",
        experiment_id=session.experiment.experiment_id,
        resolved_hash=session.resolved.resolved_hash,
        simulated_hash=session.simulated_hash,
    )


def test_two_physical_runs_create_two_records_rather_than_overwriting_one(record, monkeypatch):
    from src.sers_engine.execution import execute_live

    session = _simulated_session(record)
    _mock_robot(monkeypatch, ["robot-run-A", "robot-run-B"])

    first = execute_live(session, robot_host="10.0.0.5", wait_for_completion=False)
    # A replicate needs its own approval and simulation, which the engine still
    # enforces; the record simply must not lose the first run when it happens.
    session.status = type(session.status).SIMULATED
    session.approve_live_execution("Run the replicate.")
    second = execute_live(session, robot_host="10.0.0.5", wait_for_completion=False)

    assert first["robot_run_id"] == "robot-run-A"
    assert second["robot_run_id"] == "robot-run-B"

    runs = sorted((record.directory / "robot_runs").glob("run_*.json"))
    assert [item.name for item in runs] == ["run_001.json", "run_002.json"]
    payloads = [json.loads(item.read_text(encoding="utf-8")) for item in runs]
    assert [item["robot_run_id"] for item in payloads] == ["robot-run-A", "robot-run-B"]
    assert payloads[0]["resolved_hash"] == session.resolved.resolved_hash
    assert payloads[0]["operator_approval_text"] == "Run it on the robot."
    assert payloads[1]["operator_approval_text"] == "Run the replicate."
    assert payloads[0]["protocol_sha256"]
    assert payloads[0]["machine_profile_sha256"]
    assert payloads[0]["opentrons_api_level"] == "2.15"
    assert payloads[0]["session_id"] == record.session_id

    record.write_manifest()
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    assert manifest["robot_run_count"] == 2
    assert [item["robot_run_id"] for item in manifest["robot_runs"]] == [
        "robot-run-A",
        "robot-run-B",
    ]


def test_a_completed_run_records_its_outcome(record, monkeypatch):
    from src.sers_engine import execution

    session = _simulated_session(record)
    _mock_robot(monkeypatch, ["robot-run-A"])
    monkeypatch.setattr(execution, "_monitor", lambda host, run_id, poll: "succeeded")
    monkeypatch.setattr(
        execution,
        "get_robot_run_status",
        lambda run_id, host=None: {"robot_run_id": run_id, "status": "succeeded", "errors": []},
    )
    execution.execute_live(session, robot_host="10.0.0.5", wait_for_completion=True)

    payload = json.loads((record.directory / "robot_runs" / "run_001.json").read_text("utf-8"))
    assert payload["status"] == "succeeded"
    assert payload["finished_at"]
    assert Event.EXECUTION_STARTED in _event_names(record)
    assert Event.EXECUTION_COMPLETE in _event_names(record)


def test_live_execution_is_blocked_when_nothing_is_recording_it(tmp_path, monkeypatch):
    """An unlogged run cannot be reconstructed, so it is refused."""
    REGISTRY.clear()
    set_active_session(None)
    from src.sers_engine.execution import preflight

    session = _new_experiment()
    assert session.provenance is None
    report = preflight(session)
    assert "provenance record complete" in report.blocking
    detail = [item for item in report.gates if item.name == "provenance record complete"][0]
    assert "no provenance session" in detail.detail
    REGISTRY.clear()


def test_live_execution_is_blocked_when_the_record_is_incomplete(record):
    from src.sers_engine.execution import preflight

    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    session.approve_live_execution("go")
    # The record is complete at this point.
    ready, missing = record.live_readiness(session)
    assert ready, missing

    # Losing one required artifact must block the robot, not be shrugged off.
    (record.directory / "protocols" / "revision_001.py").unlink()
    ready, missing = record.live_readiness(session)
    assert not ready
    assert any("protocols/revision_001.py" in item for item in missing)
    assert "provenance record complete" in preflight(session).blocking


def test_a_write_failure_degrades_the_session_loudly_and_blocks_the_robot(record, capsys):
    session = _new_experiment()
    session.approve_plan()
    session.simulate()
    session.approve_live_execution("go")

    record._degrade("disk full while writing revision_002.yaml")
    captured = capsys.readouterr()
    assert "PROVENANCE ERROR" in captured.err

    ready, missing = record.live_readiness(session)
    assert not ready
    assert any("disk full" in item for item in missing)

    record.write_manifest()
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    assert manifest["degraded"] is True
    assert manifest["degraded_reasons"]


# ---------------------------------------------------------------------------
# The whole point: reconstruct an experiment from its directory alone
# ---------------------------------------------------------------------------


def test_a_finished_session_reconstructs_the_whole_story(record):
    """Request, revision, simulation, modification, re-simulation, approval."""
    agent = _agent(
        [
            [CREATE_CALL],
            "Here is the 30x plan.",
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            "Simulation passed.",
            [{"name": "update_experiment", "args": {"update_steps": [{"step_id": "np_a", "dilution_factor": 50}]}}],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            "Re-simulated at 50x.",
        ]
    )
    agent.send("Make a 30x nanoparticle dilution and print column 1.")
    agent.send("Approve and simulate it.")
    agent.send("Actually make it 50x, then approve and re-simulate.")
    record.write_manifest()

    # 1. What was asked.
    asked = [item["text"] for item in _lines(record.conversation_path) if item["role"] == "user"]
    assert asked[0].startswith("Make a 30x")
    assert "50x" in asked[-1]

    # 2. What the agent decided to do about it.
    tools = [item["tool_name"] for item in _lines(record.tool_calls_path)]
    assert tools.count("simulate_experiment") == 2
    assert "update_experiment" in tools

    # 3. How the workflow changed, with both versions kept.
    factors = [
        yaml.safe_load(item.read_text(encoding="utf-8"))["steps"][0]["dilution_factor"]
        for item in sorted((record.directory / "revisions").glob("revision_*.yaml"))
    ]
    assert factors == [30, 50]

    # 4. What exactly was simulated, each time, and with which protocol.
    simulations = sorted((record.directory / "simulation").glob("revision_*.json"))
    assert len(simulations) == 2
    hashes = [json.loads(item.read_text("utf-8"))["resolved_hash"] for item in simulations]
    assert hashes[0] != hashes[1]
    for index, digest in enumerate(hashes, start=1):
        protocol = (record.directory / "protocols" / f"revision_{index:03d}.py").read_text("utf-8")
        assert digest in protocol

    # 5. The order of events, including the invalidation in the middle.
    names = _event_names(record)
    assert names.index(Event.SIMULATION_PASSED) < names.index(Event.SIMULATION_INVALIDATED)
    assert names.index(Event.SIMULATION_INVALIDATED) < names.index(Event.EXPERIMENT_UPDATED)

    # 6. The final state, and the manifest that indexes all of it.
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    session = REGISTRY.get()
    assert manifest["final_resolved_hash"] == session.resolved.resolved_hash
    assert manifest["revision_count"] == 2
    assert manifest["conversation_turns"] >= 3
    assert manifest["tool_calls"] == len(tools)
