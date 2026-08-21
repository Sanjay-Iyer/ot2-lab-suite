"""Simulation-laptop CLI for the deterministic printing architecture."""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from src.agents.printing_tools import (
    build_printing_protocol,
    describe_printing_workflow,
    list_printing_designs,
    list_printing_workflows,
    preview_design_coordinates,
    simulate_printing_protocol,
    validate_printing_request,
)


def _parameters(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"parameters must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("parameters JSON must be an object")
    return parsed


def _request(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family": args.family,
        "workflow_name": args.workflow,
        "parameters": args.parameters,
    }
    if args.design:
        payload["design_name"] = args.design
    return payload


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--family", required=True, choices=("standard", "design"))
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--design")
    parser.add_argument("--parameters", type=_parameters, default={})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, validate, build, and locally simulate registered printing workflows."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List discoverable printing workflows.")
    subparsers.add_parser("designs", help="List registered coordinate designs.")
    describe = subparsers.add_parser("describe", help="Describe one workflow and its parameter schema.")
    describe.add_argument("--workflow", required=True)
    for command in ("validate", "preview", "build", "simulate"):
        child = subparsers.add_parser(command)
        _add_request_arguments(child)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        print(list_printing_workflows.invoke({}))
        return 0
    if args.command == "designs":
        print(list_printing_designs.invoke({}))
        return 0
    if args.command == "describe":
        print(describe_printing_workflow.invoke({"workflow_name": args.workflow}))
        return 0
    tools: dict[str, Callable[..., Any]] = {
        "validate": validate_printing_request,
        "preview": preview_design_coordinates,
        "build": build_printing_protocol,
        "simulate": simulate_printing_protocol,
    }
    print(tools[args.command].invoke(_request(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

