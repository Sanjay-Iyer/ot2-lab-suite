#!/usr/bin/env python3
"""Version 11 agent test harness - no GCloud, no robot.

    python scripts/11_test_agent_configs.py
    python scripts/11_test_agent_configs.py --only standard_print
    python scripts/11_test_agent_configs.py --case std_18_state_persists_across_turns
    python scripts/11_test_agent_configs.py --rules      (also exercise the offline
                                                          English parser)

Drives src/printing/v11/agent_core.ExperimentState with the structured intents in
configs/tests/11_agent_test_cases.yaml, then checks the resolved config that the
real Version 11 loaders produce. This is the GCloud-free path: the LLM adapter is
replaced by scripted intents, and everything below it is the same code the work
laptop runs.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml

from src.printing.v11.agent_core import AgentError, ExperimentState
from src.printing.v11.labware import V11ConfigError
from src.printing.v11.llm_adapter import RuleBasedAdapter

CASES = REPO / "configs" / "tests" / "11_agent_test_cases.yaml"
VALID_SECTIONS = ("standard_print", "clover_print", "dilution")


def dotted(data: Any, path: str) -> Any:
    """Walk a dotted path; numeric parts index into lists (e.g. groups.0.droplets)."""
    node = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(f"{path} (stopped at {part!r})")
    return node


def close(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def _resolved_targets(resolved: dict) -> list[str]:
    """Explicit targets out of a resolved standard-print config."""
    for key in ("resolved_targets", "targets"):
        if key in resolved:
            return list(resolved[key])
    groups = resolved.get("groups") or resolved.get("print_groups") or []
    out: list[str] = []
    for group in groups:
        out.extend(group.get("targets", []))
    return out


def _resolved_series(resolved: dict) -> list[str]:
    for path in ("series.wells", "dilution.series.wells", "destination.wells",
                 "resolved_series_wells"):
        try:
            value = dotted(resolved, path)
            if value:
                return list(value)
        except KeyError:
            continue
    return []


def _resolved_volumes(resolved: dict) -> tuple[float | None, float | None]:
    for stock_path, diluent_path in (
        ("single.stock_volume_ul", "single.diluent_volume_ul"),
        ("dilution.stock_volume_ul", "dilution.diluent_volume_ul"),
        ("stock_volume_ul", "diluent_volume_ul"),
    ):
        try:
            return float(dotted(resolved, stock_path)), float(dotted(resolved, diluent_path))
        except (KeyError, TypeError, ValueError):
            continue
    return None, None


def _separation(resolved: dict) -> tuple[float | None, float | None]:
    """Actual droplet separation in mm from a resolved clover config."""
    for path in ("geometry", "clover_geometry"):
        try:
            geometry = dotted(resolved, path)
        except KeyError:
            continue
        if not isinstance(geometry, dict):
            continue
        if geometry.get("separation_x_mm") is not None:
            return (float(geometry["separation_x_mm"]),
                    float(geometry.get("separation_y_mm", geometry["separation_x_mm"])))
        if geometry.get("half_width_mm") is not None:
            return (2 * float(geometry["half_width_mm"]),
                    2 * float(geometry.get("half_height_mm", geometry["half_width_mm"])))
    clovers = resolved.get("clovers") or []
    if clovers and isinstance(clovers[0], dict):
        geometry = clovers[0].get("geometry") or {}
        if geometry.get("half_width_mm") is not None:
            return (2 * float(geometry["half_width_mm"]),
                    2 * float(geometry.get("half_height_mm", geometry["half_width_mm"])))
    return None, None


def run_case(case: dict, section: str) -> tuple[bool, list[str]]:
    name = case.get("name", "<unnamed>")
    problems: list[str] = []
    state = ExperimentState()

    expect_error = case.get("expect_error")
    expect_error_on_validate = case.get("expect_error_on_validate")

    # -- apply the intents -------------------------------------------------
    try:
        for intent in case.get("intents", []):
            state.apply(intent)
    except (AgentError, V11ConfigError, ValueError, KeyError) as exc:
        if expect_error:
            if expect_error.lower() in str(exc).lower():
                return True, []
            return False, [f"expected error containing {expect_error!r}, got: {exc}"]
        return False, [f"apply() raised: {exc}"]
    if expect_error:
        return False, [f"expected an error containing {expect_error!r}, but apply() succeeded"]

    # -- validate through the real loader ----------------------------------
    ok, message, resolved = state.validate()
    if expect_error_on_validate:
        if ok:
            return False, [
                f"expected validation to reject with {expect_error_on_validate!r}, "
                "but it passed"
            ]
        if expect_error_on_validate.lower() in message.lower():
            return True, []
        return False, [
            f"expected validation error containing {expect_error_on_validate!r}, "
            f"got: {message}"
        ]
    if not ok:
        return False, [f"validation failed: {message}"]

    # -- assertions --------------------------------------------------------
    for path, want in (case.get("expect") or {}).items():
        try:
            got = dotted(resolved, path)
        except KeyError as exc:
            problems.append(f"{path}: missing from resolved config ({exc})")
            continue
        if not close(got, want):
            problems.append(f"{path}: expected {want!r}, got {got!r}")

    want_targets = case.get("expect_resolved_targets")
    if want_targets is not None:
        got = _resolved_targets(resolved)
        if got != list(want_targets):
            problems.append(f"resolved targets: expected {want_targets}, got {got}")

    want_series = case.get("expect_resolved_series")
    if want_series is not None:
        got = _resolved_series(resolved)
        if got != list(want_series):
            problems.append(f"resolved series: expected {want_series}, got {got}")

    want_volumes = case.get("expect_resolved_volumes")
    if want_volumes is not None:
        stock, diluent = _resolved_volumes(resolved)
        if stock is None or diluent is None:
            problems.append("resolved volumes: could not find stock/diluent in config")
        else:
            if not close(stock, want_volumes["stock"]):
                problems.append(
                    f"stock volume: expected {want_volumes['stock']}, got {stock}")
            if not close(diluent, want_volumes["diluent"]):
                problems.append(
                    f"diluent volume: expected {want_volumes['diluent']}, got {diluent}")

    want_separation = case.get("expect_separation")
    if want_separation is not None:
        sep_x, sep_y = _separation(resolved)
        if sep_x is None:
            problems.append("separation: could not determine from resolved config")
        else:
            if not close(sep_x, want_separation["x"]):
                problems.append(
                    f"separation x: expected {want_separation['x']} mm, got {sep_x} mm")
            if not close(sep_y, want_separation["y"]):
                problems.append(
                    f"separation y: expected {want_separation['y']} mm, got {sep_y} mm")

    want_count = case.get("expect_clover_count")
    if want_count is not None:
        got = len(resolved.get("clovers") or [])
        if got != want_count:
            problems.append(f"clover count: expected {want_count}, got {got}")

    want_refs = case.get("expect_clover_references")
    if want_refs is not None:
        got = [c.get("reference") for c in (resolved.get("clovers") or [])]
        if got != list(want_refs):
            problems.append(f"clover references: expected {want_refs}, got {got}")

    return not problems, problems


def run_rule_based(cases: dict) -> tuple[int, int, list[str]]:
    """Feed the human phrasings through the offline English parser."""
    adapter = RuleBasedAdapter()
    passed = failed = 0
    notes: list[str] = []
    for section in VALID_SECTIONS:
        for case in cases.get(section) or []:
            conversation = case.get("conversation")
            if not conversation:
                continue
            state = ExperimentState()
            try:
                for line in conversation:
                    intent = adapter.interpret(line)
                    if intent:
                        state.apply(intent)
                if state.workflow is None:
                    raise AgentError("no workflow inferred from the conversation")
                ok, message, _ = state.validate()
                if not ok:
                    raise AgentError(message)
                passed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                notes.append(f"  {case.get('name')}: {exc}")
    return passed, failed, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=[*VALID_SECTIONS, "invalid"])
    parser.add_argument("--case")
    parser.add_argument("--rules", action="store_true",
                        help="also run the offline English parser over the phrasings")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    (REPO / ".test_tmp").mkdir(exist_ok=True)
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    sections = [args.only] if args.only else [*VALID_SECTIONS, "invalid"]

    total = passed = 0
    failures: list[tuple[str, str, list[str]]] = []

    for section in sections:
        entries = cases.get(section) or []
        if args.case:
            entries = [c for c in entries if c.get("name") == args.case]
        if not entries:
            continue
        print(f"\n=== {section} ({len(entries)} cases) ===")
        for case in entries:
            total += 1
            name = case.get("name", "<unnamed>")
            try:
                ok, problems = run_case(case, section)
            except Exception as exc:  # noqa: BLE001
                ok, problems = False, [f"harness error: {exc}"]
                if args.verbose:
                    traceback.print_exc()
            if ok:
                passed += 1
                print(f"  PASS  {name}")
            else:
                failures.append((section, name, problems))
                print(f"  FAIL  {name}")
                for problem in problems:
                    print(f"          {problem}")

    print(f"\n{'=' * 60}")
    print(f"AGENT CONFIG CASES: {passed}/{total} passed")

    if args.rules:
        rp, rf, notes = run_rule_based(cases)
        print(f"OFFLINE ENGLISH PARSER: {rp}/{rp + rf} conversations resolved "
              f"({rf} unresolved)")
        print("  NOTE: the offline parser is a regex testing convenience, NOT the")
        print("  production path (the work laptop uses Vertex). Its miss rate is")
        print("  reported for information and does NOT affect the exit code -- an")
        print("  unresolved conversation is the parser safely declining to guess.")
        for note in notes:
            print(note)

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for section, name, problems in failures:
            print(f"  [{section}] {name}")
            for problem in problems:
                print(f"      {problem}")
        return 1
    print("ALL AGENT CONFIG CASES PASSED"
          + (" (offline-parser figures above are informational only)"
             if args.rules else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
