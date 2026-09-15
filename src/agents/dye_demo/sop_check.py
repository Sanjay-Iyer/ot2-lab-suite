"""Check an AI dye demo configuration against a user-test SOP's expected final state.

The five user-test SOPs in docs/ai_dye_demo/user_testing/ each have a machine-readable expected state in
docs/ai_dye_demo/user_testing/expected/. The configuration used by each run the SOP requires passes when it

  * passes every deterministic check (validation.validate);
  * has the required value of every conversation-editable setting, or one of the allowed values where the SOP
    leaves a choice to the tester;
  * keeps every lab-owned setting of the SOP's starting configuration; and
  * produces the expected plan: dilution wells and factors, FROM/TO print steps with drops, transfers and tips.

scripts/check_ai_dye_demo_sop.py applies this to a finished session folder. The tests replay scripted user paths
through the real DemoSession and apply it to every run. Nothing here contacts a robot.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.agents.dye_demo.model import (
    REPO,
    TIP_ORDER,
    FieldError,
    get_path,
    load_config,
    normalize_loaded_config,
    resolve_path,
)
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.state import fingerprint, lab_owned_view
from src.agents.dye_demo.validation import validate

EXPECTED_DIR = REPO / "docs" / "ai_dye_demo" / "user_testing" / "expected"


@dataclass(frozen=True)
class Finding:
    run: str
    requirement: str
    ok: bool
    found: str = ""


def expected_paths() -> list[Path]:
    return sorted(EXPECTED_DIR.glob("sop_*.yaml"))


def load_expected(sop: int) -> dict[str, Any]:
    matches = [path for path in expected_paths() if path.name.startswith(f"sop_{int(sop):02d}_")]
    if len(matches) != 1:
        raise FileNotFoundError(f"no expected state for SOP {sop} in {EXPECTED_DIR}")
    return yaml.safe_load(matches[0].read_text(encoding="utf-8"))


def start_config(expected: dict[str, Any]) -> dict[str, Any]:
    config = dict(load_config(REPO / expected["start_config"]))
    config.pop("session", None)
    return config


def setting(config: dict[str, Any], dotted: str) -> Any:
    try:
        return get_path(config, resolve_path(config, dotted))
    except (FieldError, KeyError, TypeError):
        return None


def same(actual: Any, wanted: Any) -> bool:
    """Equal as the scientist would read it: 5 == 5.0, '11' == 11, well names case-insensitive."""
    if isinstance(wanted, dict):
        if not isinstance(actual, dict):
            return False
        actual = {str(key).upper(): value for key, value in actual.items()}
        wanted = {str(key).upper(): value for key, value in wanted.items()}
        return set(actual) == set(wanted) and all(same(actual[key], wanted[key]) for key in wanted)
    if isinstance(wanted, (list, tuple)):
        return isinstance(actual, (list, tuple)) and len(actual) == len(wanted) and all(
            same(item, target) for item, target in zip(actual, wanted))
    if isinstance(wanted, bool) or isinstance(actual, bool):
        return actual is wanted
    if wanted is None or actual is None:
        return actual is None and wanted is None
    if isinstance(wanted, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(wanted)) < 1e-6
    return str(actual).strip().upper() == str(wanted).strip().upper()


def allowed(actual: Any, rule: dict[str, Any]) -> bool:
    if "one_of" in rule:
        return any(same(actual, option) for option in rule["one_of"])
    if "between" in rule:
        low, high = rule["between"]
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and low - 1e-6 <= actual <= high + 1e-6
    if "at_or_after" in rule:
        tip = str(actual).strip().upper()
        return tip in TIP_ORDER and TIP_ORDER.index(tip) >= TIP_ORDER.index(str(rule["at_or_after"]).upper())
    raise ValueError(f"unknown choice rule {rule!r}")


def describe(rule: dict[str, Any]) -> str:
    if "one_of" in rule:
        return "one of " + ", ".join(str(option) for option in rule["one_of"])
    if "between" in rule:
        return f"between {rule['between'][0]} and {rule['between'][1]}"
    return f"{rule['at_or_after']} or a later tip in rack order"


def _show(value: Any) -> str:
    return json.dumps(value, default=str)


def plan_facts(config: dict[str, Any]) -> dict[str, Any]:
    plan = build_plan(config)
    return {
        "makes_dilutions": plan.do_dilution,
        "prints": plan.do_print,
        "source_wells": {well.well: well.factor for well in plan.wells},
        "print_steps": [[op.source, op.destination, op.droplets, op.volume_ul]
                        for op in plan.operations if op.kind == "print"],
        "transfers": sum(op.kind == "transfer" for op in plan.operations),
        "total_drops": plan.total_drops,
        "tips": [assignment.tip for assignment in plan.tips],
        "next_tip": plan.next_tip,
    }


def check_run(config: dict[str, Any], run: dict[str, Any], start: dict[str, Any]) -> list[Finding]:
    name = run.get("name", "run")
    config = {key: value for key, value in config.items() if key != "session"}
    report = validate(config)
    findings = [
        Finding(name, "every deterministic check passes", not report.errors, "; ".join(report.error_messages())),
        Finding(name, "lab-owned settings are those of the starting configuration",
                fingerprint(lab_owned_view(config)) == fingerprint(lab_owned_view(start))),
    ]
    for dotted, wanted in (run.get("settings") or {}).items():
        actual = setting(config, dotted)
        findings.append(Finding(name, f"{dotted} is {_show(wanted)}", same(actual, wanted), _show(actual)))
    for dotted, rule in (run.get("choices") or {}).items():
        actual = setting(config, dotted)
        findings.append(Finding(name, f"{dotted} is {describe(rule)}", allowed(actual, rule), _show(actual)))
    facts = None if report.errors else plan_facts(config)
    for key, wanted in (run.get("plan") or {}).items():
        if facts is None:
            findings.append(Finding(name, f"plan {key}", False, "the plan does not validate"))
            continue
        if key == "tips_count":
            ok, found = len(facts["tips"]) == wanted, len(facts["tips"])
        elif key == "tips_exclude":
            reused = [tip for tip in facts["tips"] if tip in set(wanted)]
            ok, found = not reused, reused or "no excluded tip"
        else:
            ok, found = same(facts[key], wanted), facts[key]
        findings.append(Finding(name, f"plan {key} is {_show(wanted)}", ok, _show(found)))
    return findings


def check_runs(configs: list[dict[str, Any]], expected: dict[str, Any]) -> list[Finding]:
    """The last len(runs) run configurations, in order, against the SOP's required runs."""
    runs = expected["runs"]
    start = start_config(expected)
    findings = [Finding("session", f"{len(runs)} successful run(s)", len(configs) >= len(runs),
                        f"{len(configs)} successful run(s)")]
    used = configs[-len(runs):] if len(configs) >= len(runs) else configs
    for config, run in zip(used, runs):
        findings += check_run(config, run, start)
    return findings


def session_run_configs(session: Any) -> list[dict[str, Any]]:
    """Configurations of a DemoSession's successful runs, from the snapshot of the revision each run used."""
    return [session.state.snapshots[run["revision"]]["config"] for run in session.state.runs if run["exit_code"] == 0]


def folder_run_configs(folder: Path) -> list[dict[str, Any]]:
    """Configurations of the successful runs recorded in a runs/ai_dye_demo/<session>/ folder."""
    summary = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    configs = []
    for run in summary.get("runs") or []:
        path = folder / f"executed_config_run{run.get('run')}.yaml"
        if run.get("exit_code") == 0 and path.exists():
            configs.append(normalize_loaded_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))
    return configs
