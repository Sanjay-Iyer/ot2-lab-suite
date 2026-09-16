from copy import deepcopy
import pytest
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.state import ExperimentState

@pytest.fixture
def config():
    return load_config(DEFAULT_CONFIG)

def test_step_enablement_regex_hints(config):
    state = ExperimentState(deepcopy(config))

    print_only_variants = [
        "printing only",
        "just printing",
        "print only",
        "only print",
        "no dilutions, just print",
    ]
    for text in print_only_variants:
        assert state._step_stated("dilution.enabled", False, text), f"Failed for '{text}'"

    dilution_only_variants = [
        "just dilutions",
        "dilutions only",
        "only make the dilutions",
        "dilution only",
        "only dilutions",
    ]
    for text in dilution_only_variants:
        assert state._step_stated("print.enabled", False, text), f"Failed for '{text}'"

def test_printing_only_proposal(config):
    state = ExperimentState(deepcopy(config))

    proposal = state.propose([{"path": "dilution.enabled", "value": False}], request="printing only")
    dilution_change = [c for c in proposal.changes if c.path == "dilution.enabled"][0]
    assert dilution_change.after is False
    assert proposal.after["dilution"]["enabled"] is False
    assert proposal.after["print"]["enabled"] is True

def test_dilutions_only_proposal(config):
    state = ExperimentState(deepcopy(config))

    proposal = state.propose([{"path": "print.enabled", "value": False}], request="dilutions only")
    print_change = [c for c in proposal.changes if c.path == "print.enabled"][0]
    assert print_change.after is False
    assert proposal.after["print"]["enabled"] is False
    assert proposal.after["dilution"]["enabled"] is True
