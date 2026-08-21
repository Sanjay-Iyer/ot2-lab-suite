#!/usr/bin/env python3
"""
Terminal runner for the vial dilution -> paper print demo.

This uses the robot HTTP API instead of opentrons_execute. By default it creates
a dry run. Pass --live to run the real liquid-handling print. Protocol v3 targets
API 2.15 and bakes run modes into the generated file because runtime parameters
are unavailable at that API level.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

if __name__ == "__main__" and not __package__:
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))

from src.core.config import Config
from src.lab.robot_connection import (
    add_robot_host_arguments,
    base_url,
    connection_summary,
    resolve_host,
)
from src.utils.robot_run_log import RobotRunLog, repo_relative
from src.utils.ot2_ssh import OT2SSHSettings
from src.printing.workflows.registry import (
    api_215_versions,
    builder_protocol_versions,
    imageless_versions,
    no_matrix_versions,
)


REPO = Path(__file__).resolve().parent.parent
GENERATED_PROTOCOL_DIR = REPO / "src" / "protocols" / "generated"

# Markers the builder writes around the embedded CONFIG dict (keep in sync with
# scripts/build_vial_dilution_print.py).
CONFIG_START_SENTINEL = "# >>> CONFIG START >>>"
CONFIG_END_SENTINEL   = "# <<< CONFIG END <<<"
# Exact generated artifact per version, derived from the same registry as the builder.
_PROTOCOL_BY_VERSION = {
    version: GENERATED_PROTOCOL_DIR / f"{generated_stem}_latest.py"
    for version, (_, generated_stem) in builder_protocol_versions().items()
}
DEFAULT_PROTOCOL = _PROTOCOL_BY_VERSION[1]

# Versions that target API 2.15 on the robot: run modes are baked into the generated
# file at build time and no runtime parameters are sent.
API_215_VERSIONS = api_215_versions()

# Versions with no camera step — skip all before/after image handling (and the SSH
# it needs) for these.
IMAGELESS_VERSIONS = imageless_versions()

# Versions with no run-mode matrix; the build's own simulation is the whole check.
NO_MATRIX_VERSIONS = no_matrix_versions()


def _config_version(config_path: str | None) -> int:
    if not config_path:
        return 1
    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read printing config {config_path!r}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"printing config {config_path!r} must contain a YAML mapping")
    try:
        version = int(raw["protocol_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"printing config {config_path!r} needs a valid protocol_version"
        ) from exc
    if version not in _PROTOCOL_BY_VERSION:
        raise ValueError(f"unknown protocol_version {version}; refusing artifact fallback")
    return version


def _protocol_for_config(config_path: str | None) -> Path:
    """The generated protocol a config builds into, from its `protocol_version` key.

    Uploading the wrong artifact is the failure mode this prevents: a v2 config builds
    ``vial_dilution_print_v2_latest.py``, while the v1 default path would silently
    upload whatever v1 build happened to be lying around.
    """
    if not config_path:
        return DEFAULT_PROTOCOL
    version = _config_version(config_path)
    try:
        return _PROTOCOL_BY_VERSION[version]
    except KeyError as exc:
        raise ValueError(
            f"unknown protocol_version {version}; refusing artifact fallback"
        ) from exc
REMOTE_VISION_DIR = "/data/vision/vial_dilution_print"
LOCAL_VISION_BASE = REPO / "vision_runs" / "vial_dilution_print"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_rsa_opentrons"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}


def _api_url(robot_ip: str, path: str) -> str:
    return f"{base_url(robot_ip)}{path}"


def _request(method: str, robot_ip: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, _api_url(robot_ip, path), headers=HEADERS, timeout=30, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    return payload


def _run_local_step(command: list[str], label: str) -> None:
    print(f"\n[{label}] {' '.join(command)}")
    subprocess.run(command, cwd=REPO, check=True)


def _custom_labware_files(protocol_path: Path) -> list[Path]:
    """Custom labware JSONs the protocol references that live in the repo's labware/.

    The robot only knows labware that was previously imported to it (via the App) or
    that is uploaded WITH the protocol. Standard Opentrons labware (e.g. the tipracks)
    is built in and has no file here, so it is skipped. Uploading the referenced
    custom defs makes a run self-contained: a brand-new labware like the flat paper
    surface works without a manual App import first."""
    cfg = _embedded_config(protocol_path) or {}
    files: list[Path] = []
    seen: set[str] = set()
    for spec in cfg.get("deck", {}).values():
        if not isinstance(spec, dict):
            continue
        name = spec.get("load_name")
        if not name or name in seen:
            continue
        seen.add(name)
        path = REPO / "labware" / f"{name}.json"
        if path.exists():
            files.append(path)
    return files


def _report_run_error(robot_ip: str, run_id: str, run_log) -> None:
    """Print the robot's own error(s) for a non-succeeded run so failures are not blind.

    Reads the run object's `errors`, then the last few failed commands, which carry
    the specific detail (e.g. a labware-not-found, or a hard collision)."""
    try:
        run = _request("GET", robot_ip, f"/runs/{run_id}").get("data", {})
    except Exception as exc:  # noqa: BLE001 - reporting must never mask the failure
        print(f"(could not fetch run error detail: {exc})")
        return
    errors = run.get("errors") or []
    print("\n--- robot run error ---")
    for err in errors:
        print(f"  {err.get('errorType', 'Error')}: {err.get('detail', err)}")
    try:
        cmds = _request(
            "GET", robot_ip, f"/runs/{run_id}/commands",
        ).get("data", [])
        failed = [c for c in cmds if c.get("status") == "failed"]
        for c in failed[-3:]:
            ce = c.get("error", {})
            print(f"  command {c.get('commandType')}: "
                  f"{ce.get('errorType', '')} {ce.get('detail', '')}".rstrip())
    except Exception:  # noqa: BLE001
        pass
    print("--- end ---")
    run_log.event("run_error", errors=errors)


def _upload_protocol(robot_ip: str, protocol_path: Path) -> str:
    lw_files = _custom_labware_files(protocol_path)
    extra = f" + {len(lw_files)} custom labware def(s)" if lw_files else ""
    print(f"\n[upload] {protocol_path.name}{extra}")
    for p in lw_files:
        print(f"         labware: {p.name}")
    handles = []
    try:
        multipart = [("files", (protocol_path.name, protocol_path.open("rb"), "text/x-python"))]
        handles.append(multipart[0][1][1])
        for p in lw_files:
            fh = p.open("rb")
            handles.append(fh)
            multipart.append(("files", (p.name, fh, "application/json")))
        response = requests.post(
            _api_url(robot_ip, "/protocols"),
            headers=HEADERS,
            files=multipart,
            timeout=120,
        )
    finally:
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"Protocol upload failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    protocol_id = payload.get("data", {}).get("id")
    if not protocol_id:
        raise RuntimeError(f"Protocol upload response did not include data.id:\n{json.dumps(payload, indent=2)}")
    # Surface analysis errors (e.g. a labware definition the robot still cannot load)
    # instead of letting them silently fail the run later.
    for analysis in payload.get("data", {}).get("analysisSummaries", []):
        if analysis.get("status") == "completed" and analysis.get("result") == "not-ok":
            print("WARNING: protocol analysis reported problems; the run may fail.")
    print(f"Protocol ID: {protocol_id}")
    return protocol_id


def _embedded_config(protocol_path: Path) -> dict | None:
    """Extract the CONFIG dict embedded in a generated protocol.

    The generated file is the artifact actually uploaded to the robot, so reading the
    deck/tip plan back out of it is the only description that cannot drift from the
    run. The builder writes a plain `pprint` repr between the sentinels, which is
    literal-eval-able. Returns None if the file is missing or not a generated build.
    """
    try:
        text = protocol_path.read_text(encoding="utf-8")
        start = text.index(CONFIG_START_SENTINEL)
        start = text.index("CONFIG = {", start) + len("CONFIG = ")
        end = text.index(CONFIG_END_SENTINEL)
        return ast.literal_eval(text[start:end].rstrip().rstrip("\n"))
    except (OSError, ValueError, SyntaxError):
        return None


def _describe_deck(config: dict) -> None:
    """Print the deck layout, vial assignments and tip plan from the embedded CONFIG."""
    deck = config.get("deck", {})
    role_labels = {"tuberack": "vial rack", "plate": "mixing plate", "paper": "paper",
                   "tiprack": "P300 tips", "tiprack_p20": "P20 tips"}
    print("\nDeck (from the built protocol):")
    for role, spec in deck.items():
        if not isinstance(spec, dict) or spec.get("slot") is None:
            continue
        print(f"  slot {str(spec['slot']):>2}  {role_labels.get(role, role):<13} "
              f"{spec.get('load_name', '?')}")

    pv = int(config.get("protocol_version", 0) or 0)

    # v8 direct vial -> paper: no dilution, no plate, layered passes with drying rests.
    if pv == 8:
        src = config.get("source", {})
        pr = config.get("print", {})
        rows = [str(r) for r in pr.get("rows", [])]
        slot = deck.get("tuberack", {}).get("slot", "?")
        print(f"\nSource (rack in slot {slot}):")
        print(f"  {src.get('vial')}  {src.get('material')} "
              f"(aspirate {src.get('aspirate_height_mm')} mm above bottom) "
              f"— NEAT STOCK, nothing is diluted")
        mix = src.get("mix_before_pass") or {}
        if int(mix.get("reps", 0) or 0):
            print(f"  re-suspend {mix.get('reps')}x @ {mix.get('volume_ul')} uL "
                  f"before each pass")
        print(f"\nDroplet: {pr.get('volume_ul')} uL + {pr.get('air_gap_ul', 0)} uL air gap, "
              f"z={pr.get('z_mm')} mm above paper")
        print(f"  clearing: push_out {pr.get('push_out_ul', 0)} uL"
              + (" then blow_out" if pr.get("blow_out", True) else ", blow_out OFF")
              + f", {pr.get('post_dispense_delay_s', 0)} s hold per spot")
        # Layer counts must match the protocol's own expansion of `passes`.
        layers: dict = {}
        rest_total = 0.0
        passes = pr.get("passes", [])
        print(f"\nPrint plan ({len(passes)} pass(es), rows {', '.join(rows)}, "
              f"one reused tip {config.get('tips', {}).get('p20', {}).get('print_tip')}):")
        for i, spec in enumerate(passes, start=1):
            cols = [int(c) for c in spec.get("columns", [])]
            for c in cols:
                layers[c] = layers.get(c, 0) + 1
            rest = float(spec.get("rest_minutes", 0) or 0)
            rest_total += rest
            tail = f", rest {rest:g} min" if rest > 0 else ""
            print(f"  pass {i}: column(s) "
                  + ", ".join(f"{c} (layer {layers[c]})" for c in cols) + tail)
        drops = sum(len(s.get("columns", [])) for s in passes) * len(rows)
        print(f"\nTotals: " + ", ".join(f"col {c} = {n} layer(s)"
                                        for c, n in sorted(layers.items())))
        print(f"  {drops} droplet(s) = {drops * float(pr.get('volume_ul', 0)):g} uL "
              f"drawn from vial {src.get('vial')}; {rest_total:g} min of drying rests.")
        return

    # v7 print-only: no dilution, no vials — just an already-prepared plate column.
    if pv == 7:
        src = config.get("source", {})
        mix = config.get("mixing", {})
        pr = config.get("print", {})
        rows = src.get("rows", [])
        print(f"\nSource: plate column {src.get('plate_column')} "
              f"(ALREADY PREPARED — nothing is diluted)")
        print(f"  rows: {', '.join(rows)}")
        print(f"\nPrint plan (P20, z={pr.get('z_mm')} mm, mix {mix.get('reps')}x "
              f"@ {mix.get('volume_ul')} uL before each):")
        for g in pr.get("groups", []):
            pin = g.get("paper_column")
            where = f"column {pin}" if pin is not None else "auto column"
            reps = int(g.get("replicates", 1))
            drops = int(g.get("droplets_per_spot", 1))
            if reps > 1:
                where += f" x{reps}"
            drop_txt = f", {drops} drops/spot" if drops > 1 else ""
            print(f"  {g.get('volume_ul')} uL -> {where}{drop_txt}")
        return

    # v6 P20-only workflow: role-tagged materials, dilution, mix-before-print.
    if pv == 6:
        slot = deck.get("tuberack", {}).get("slot", "?")
        print(f"\nMaterials (rack in slot {slot}):")
        for name, m in config.get("materials", {}).items():
            print(f"  {m.get('vial')}  {name} [{m.get('role')}] "
                  f"(aspirate {m.get('aspirate_height_mm')} mm above bottom)")
        dil = config.get("dilution", {})
        factors = ", ".join(f"{f:g}x" for f in dil.get("factors", []))
        print(f"\nDilution: {factors} in plate column {dil.get('plate_column')}, "
              f"{dil.get('total_volume_ul')} uL each.")
        mix = config.get("mixing", {})
        pr = config.get("print", {})
        print(f"\nPrint plan (P20, z={pr.get('z_mm')} mm, mix {mix.get('reps')}x "
              f"@ {mix.get('volume_ul')} uL before each; auto columns, "
              f"paper width {pr.get('paper_columns')}):")
        # The protocol owns column assignment (pinned + auto-fill); report each group
        # as declared rather than re-deriving the layout here and risking drift.
        needed = 0
        for g in pr.get("groups", []):
            reps = int(g.get("replicates", 1))
            drops = int(g.get("droplets_per_spot", 1))
            needed += reps
            pin = g.get("paper_column")
            where = f"column {pin}" if pin is not None else "auto column"
            if reps > 1:
                where += f" x{reps}"
            drop_txt = f", {drops} drops/spot" if drops > 1 else ""
            print(f"  {g.get('volume_ul')} uL -> {where}{drop_txt}")
        budget = int(pr.get("paper_columns", 12))
        if needed > budget:
            print(f"  NOTE: plan needs {needed} columns but paper has {budget}; "
                  f"the overflow is skipped at run time.")
        return

    # v4 quick test has its own tiny shape (one vial, one P20 tip, a few paper spots).
    if pv == 4:
        src = config.get("source", {})
        pr = config.get("print", {})
        slot = deck.get("tuberack", {}).get("slot", "?")
        print(f"\nSource (rack in slot {slot}):")
        print(f"  {src.get('vial')}  {src.get('material')} "
              f"(aspirate {src.get('aspirate_height_mm')} mm above bottom)")
        rows = pr.get("rows", [])
        col = pr.get("paper_column")
        span = f"{rows[0]}{col}..{rows[-1]}{col}" if rows else "?"
        print("\nPrint plan:")
        print(f"  p20_single_gen2  {pr.get('volume_ul')} uL x {len(rows)} spot(s) "
              f"-> paper column {col} ({span}), one reused tip {pr.get('tip')}")
        return

    # Vials: prefer the declared materials, else the legacy sources block.
    materials = config.get("materials") or {}
    rack = deck.get("tuberack", {}).get("load_name")
    vials = [(m.get("vial"), f"{name} ({m.get('role', 'material')})")
             for name, m in materials.items()
             if isinstance(m, dict) and (not rack or m.get("labware") == rack)]
    if not vials:
        sources = config.get("sources", {})
        vials = [(sources.get("water_vial"), "water")]
        vials += [(s.get("dye_vial"), s.get("name", "dye"))
                  for s in config.get("color_series", [])]
    vials = [(v, label) for v, label in vials if v]
    if vials:
        slot = deck.get("tuberack", {}).get("slot", "?")
        print(f"\nVials (rack in slot {slot}):")
        for vial, label in sorted(vials):
            print(f"  {vial}  {label}")
    height = config.get("sources", {}).get("vial_aspirate_height_mm")
    if height is not None:
        print(f"  aspirate height: {height} mm above vial bottom")

    # Tip plan: dilution setup tips, then each print group's own tips.
    dil = config.get("dilution", {})
    print("\nTip plan:")
    if int(config.get("protocol_version", 0) or 0) == 3:
        p20_tips = config.get("tips", {}).get("p20", {})
        by_row = p20_tips.get("print_by_row", {})
        print(f"  P20 rack   water setup tip {p20_tips.get('water')}")
        if dil.get("stock_tip_policy", "single") == "single":
            print(f"  P20 rack   bp stock setup tip {p20_tips.get('stock')}")
        else:
            print("  P20 rack   bp stock tips by row: "
                  + ", ".join(f"{row}={tip}" for row, tip in
                              p20_tips.get("stock_by_row", {}).items()))
        print("  P20 rack   print tips by dilution row: "
              + ", ".join(f"{row}={by_row.get(row)}" for row in "ABCDEFGH"))
        print("  P300 rack  full 8-tip mixing block column "
              f"{config.get('tips', {}).get('p300', {}).get('mix_block_column')}")
        for g in config.get("print_groups", []):
            start_col = g.get("destination", {}).get("paper_start_column")
            print(f"  p20_single_gen2  {g.get('name')}: {g.get('volume_ul')} uL, "
                  f"plate column {g.get('source', {}).get('plate_column')} "
                  f"-> paper column {start_col}, one row-mapped tip")
        return
    if dil.get("enabled", True):
        print(f"  P300 rack  water setup tip {dil.get('water_setup_tip', '?')}")
        for s in config.get("color_series", []):
            print(f"  P300 rack  {s.get('name')} stock setup tip {s.get('setup_tip')}")
        sv = dil.get("small_volume") or {}
        if sv.get("enabled"):
            per = sv.get("series_setup_tips") or {}
            print(f"  P20 rack   small-volume dilution (<= "
                  f"{sv.get('threshold_ul', 20)} uL) on {sv.get('pipette')}: "
                  f"water tip {sv.get('water_setup_tip')}"
                  + (", " + ", ".join(f"{k} tip {v}" for k, v in per.items()) if per else ""))
    for g in config.get("print_groups", []):
        tips = g.get("tips", {})
        where = (f"8-tip block column {tips.get('block_column')}"
                 if g.get("layout") == "column_8up" else f"tip {tips.get('well')}")
        start_col = g.get("destination", {}).get("paper_start_column")
        reps = int(g.get("replicates", 1))
        cols = str(start_col) if reps <= 1 else f"{start_col}-{start_col + reps - 1}"
        drops = int(g.get("droplets_per_spot", 1) or 1)
        print(f"  {g.get('pipette', '?'):<16} {g.get('name')}: {g.get('volume_ul')} uL"
              + (f" x{drops} droplets/spot" if drops > 1 else "")
              + f", plate column {g.get('source', {}).get('plate_column')}"
              f" -> paper column {cols}, {where}")


def _create_run(
    robot_ip: str,
    protocol_id: str,
    *,
    dry_run: bool,
    do_dilution: bool,
    do_print: bool,
    paper_start_column: int | None = None,
    pipette_check: bool = False,
    send_runtime_parameters: bool = True,
) -> str:
    run_time_parameter_values: dict[str, Any] = {
        "dry_run": dry_run,
        "do_dilution": do_dilution,
        "do_print": do_print,
    }
    # Only send pipette_check to protocols that declare it (v2); v1 would reject an
    # unknown runtime parameter.
    if pipette_check:
        run_time_parameter_values["pipette_check"] = True
    # Only forward the paper start column when explicitly overridden on the CLI;
    # otherwise the protocol uses the value baked in from the workflow YAML at build.
    if paper_start_column is not None:
        run_time_parameter_values["print_start_column"] = paper_start_column
    data: dict[str, Any] = {"protocolId": protocol_id}
    if send_runtime_parameters:
        data["runTimeParameterValues"] = run_time_parameter_values
    body = {"data": data}
    print("\n[create run]")
    if send_runtime_parameters:
        print(json.dumps(run_time_parameter_values, indent=2))
    else:
        print("run modes are baked into the API-2.15 protocol; no runtime parameters sent")
    payload = _request("POST", robot_ip, "/runs", json=body)
    run_id = payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"Create-run response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Run ID: {run_id}")
    return run_id


def _run_status(robot_ip: str, run_id: str) -> dict[str, Any]:
    payload = _request("GET", robot_ip, f"/runs/{run_id}")
    return payload.get("data", {})


def _play_run(robot_ip: str, run_id: str) -> None:
    print("\n[play]")
    _request("POST", robot_ip, f"/runs/{run_id}/actions", json={"data": {"actionType": "play"}})


def _monitor(robot_ip: str, run_id: str, poll_s: float) -> str:
    print("\n[monitor]")
    last = None
    while True:
        data = _run_status(robot_ip, run_id)
        status = str(data.get("status", "unknown"))
        current = f"status={status}"
        if current != last:
            print(current)
            last = current
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_s)


def _resolve_ssh_key(cli_key: str | None) -> str:
    """--ssh-key > .env ROBOT_SSH_KEY_PATH > ~/.ssh/id_rsa_opentrons."""
    for candidate in (cli_key, getattr(Config, "ROBOT_SSH_KEY_PATH", ""), str(DEFAULT_SSH_KEY)):
        key = str(candidate or "").strip()
        if key:
            return key
    return str(DEFAULT_SSH_KEY)


def _remote_image_dir() -> str:
    """Return the robot-side image directory used by the generated protocol."""
    return REMOTE_VISION_DIR


def _prepare_remote_image_dir(
    robot_ip: str,
    cli_key: str | None,
    run_log: RobotRunLog | None = None,
) -> bool:
    """Clear old camera images so the post-run pull contains only fresh files."""
    key = _resolve_ssh_key(cli_key)
    remote_dir = _remote_image_dir()
    if not Path(key).exists():
        print(f"\nWARNING: SSH key {key} not found; cannot clear robot image folder.")
        print(f"Images may still be captured on the robot at {remote_dir}, but automatic pullback will be skipped.")
        if run_log:
            run_log.event("image_prepare_skipped", reason="ssh_key_missing", remote_dir=remote_dir, ssh_key=key)
        return False

    quoted = shlex.quote(remote_dir)
    settings = OT2SSHSettings.from_config(
        Config,
        robot_ip=robot_ip,
        identity_file=key,
    )
    cmd = settings.ssh_command(
        f"mkdir -p {quoted} && rm -f {quoted}/*.jpg {quoted}/*.jpeg {quoted}/*.png",
        connect_timeout=10,
    )
    print(f"\n[prepare images] clearing {remote_dir}")
    if run_log:
        run_log.event("image_prepare_started", remote_dir=remote_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not clear robot image folder before run:\n{result.stderr.strip()}")
        if run_log:
            run_log.event("image_prepare_failed", remote_dir=remote_dir, stderr=result.stderr)
        return False
    if run_log:
        run_log.event("image_prepare_finished", remote_dir=remote_dir)
    return True


def _pull_images(
    robot_ip: str,
    cli_key: str | None,
    local_name: str,
    run_log: RobotRunLog | None = None,
) -> None:
    key = _resolve_ssh_key(cli_key)
    remote_dir = _remote_image_dir()
    if not Path(key).exists():
        print(f"\nWARNING: SSH key {key} not found; skipping image pull.")
        print(f"Images remain on the robot at {remote_dir}.")
        if run_log:
            run_log.event("image_pull_skipped", reason="ssh_key_missing", remote_dir=remote_dir, ssh_key=key)
        return

    LOCAL_VISION_BASE.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_VISION_BASE / local_name
    settings = OT2SSHSettings.from_config(
        Config,
        robot_ip=robot_ip,
        identity_file=key,
    )
    cmd = settings.scp_command(
        settings.remote_path(remote_dir),
        str(dest),
        recursive=True,
        legacy_protocol=True,
        connect_timeout=10,
    )
    print(f"\n[pull images] {' '.join(cmd)}")
    if run_log:
        run_log.event("image_pull_started", remote_dir=remote_dir, local_dest=repo_relative(dest))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not pull images from {remote_dir}:\n{result.stderr.strip()}")
        if run_log:
            run_log.event("image_pull_failed", remote_dir=remote_dir, local_dest=repo_relative(dest), stderr=result.stderr)
        return

    imgs = sorted(p.name for p in dest.glob("*.jpg")) if dest.exists() else []
    if run_log:
        run_log.event("image_pull_finished", local_dest=repo_relative(dest), image_count=len(imgs), images=imgs)
    print(f"Images saved to: {dest}")
    print(f"  {', '.join(imgs) if imgs else '(no .jpg found; check camera capture warnings in the run output)'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload and run the vial dilution print protocol through the OT-2 HTTP API."
    )
    add_robot_host_arguments(parser)
    parser.add_argument("--protocol", default=None,
                        help="Generated protocol file to upload. Default: derived from "
                             "--config's protocol_version (v1 -> "
                             "vial_dilution_print_latest.py, v2/v3 -> their "
                             "versioned latest artifact).")
    parser.add_argument("--live", action="store_true", help="Run liquid motion. Default is dry run.")
    parser.add_argument(
        "--pipette-check", action="store_true",
        help="Bring-up mode (protocol v2): pick up and return every tip this config "
             "uses on BOTH mounts, visiting each labware at its working height. Real "
             "motion, NO liquid. Run this before the first --live run.")
    parser.add_argument("--no-dilution", action="store_true", help="Skip dilution phase.")
    parser.add_argument("--no-print", action="store_true", help="Skip print phase.")
    parser.add_argument(
        "--paper-start-column", type=int, default=None, metavar="N",
        help="Override the leftmost paper column to print on (1-12). Default: the "
             "value baked into the generated protocol from the workflow YAML.")
    parser.add_argument(
        "--config", default=None, metavar="YAML",
        help="Workflow YAML to rebuild from (passed to build_vial_dilution_print.py). "
             "Default: that script's own default config. Without this, a rebuild would "
             "silently regenerate the protocol from the DEFAULT config.")
    parser.add_argument("--skip-build", action="store_true", help="Do not rebuild the generated protocol first.")
    parser.add_argument("--skip-validate", action="store_true", help="Do not run scripts/validate_vial_print.py first.")
    parser.add_argument(
        "--simulator-python", default=None,
        help="v3 isolated simulator Python containing opentrons==7.0.2. Default: "
             "OT2_API_2_15_PYTHON.",
    )
    parser.add_argument("--no-start", action="store_true", help="Upload and create the run, but do not press play.")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    parser.add_argument("--ssh-key", default=None, help="SSH key for image pullback. Default: ROBOT_SSH_KEY_PATH or ~/.ssh/id_rsa_opentrons.")
    parser.add_argument("--no-pull-images", action="store_true", help="Do not pull camera images from the robot after a live run.")
    parser.add_argument("--no-clean-remote-images", action="store_true", help="Do not clear the robot image folder before starting a live run.")
    args = parser.parse_args()
    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log   : {run_log.path}")

    try:
        robot_ip = resolve_host(args.robot_host)
        os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"
        print(connection_summary(robot_ip))

        version = _config_version(args.config)
        # --pipette-check needs real motion, so it must not also be a dry run.
        dry_run = not (args.live or args.pipette_check)
        do_dilution = not args.no_dilution
        do_print = not args.no_print
        if version == 3:
            if args.pipette_check:
                raise ValueError(
                    "--pipette-check is not available for API-2.15 v3; its P300 "
                    "always uses a complete column of eight tips."
                )
            if args.paper_start_column is not None:
                raise ValueError(
                    "--paper-start-column is not available for v3; columns 1-4 "
                    "are fixed in the validated config."
                )
            if args.skip_build or args.skip_validate:
                raise ValueError(
                    "v3 requires build, pinned simulation, and live /health "
                    "validation immediately before upload; do not use "
                    "--skip-build or --skip-validate."
                )
        if version in (4, 6, 7, 8) and args.pipette_check:
            raise ValueError(
                f"--pipette-check is not available for v{version} (API-2.15, no "
                f"runtime parameters). Just add --live."
            )

        protocol_path = Path(
            args.protocol or _protocol_for_config(args.config)).resolve()
        if not args.skip_build:
            run_log.event("local_step", label="build + simulate")
            build_cmd = [sys.executable, "scripts/build_vial_dilution_print.py"]
            if args.config:
                build_cmd += ["--config", args.config]
            if args.simulator_python:
                build_cmd += ["--simulator-python", args.simulator_python]
            if version in API_215_VERSIONS:
                # API-2.15 protocols have no runtime parameters, so run modes are
                # baked into the generated file at build time.
                build_cmd += [
                    "--set-dry-run", str(dry_run).lower(),
                    "--set-do-dilution", str(do_dilution).lower(),
                    "--set-do-print", str(do_print).lower(),
                ]
            # The builder reads protocol_version from the config itself, so no version
            # flag is needed here — but the artifact it writes must be the one we
            # upload, which _protocol_for_config() resolved from the same key.
            _run_local_step(build_cmd, "build + simulate")
        if version in NO_MATRIX_VERSIONS:
            # These self-contained protocols have no run-mode matrix — the build
            # already simulated them, which is the whole check.
            print(f"[validate matrix] skipped for v{version} (build+simulate is the check)")
        elif not args.skip_validate:
            run_log.event("local_step", label="validate matrix")
            validate_cmd = [sys.executable, "scripts/validate_vial_print.py"]
            if args.config:
                validate_cmd += ["--config", args.config]
            if args.simulator_python:
                validate_cmd += ["--simulator-python", args.simulator_python]
            if version == 3:
                validate_cmd += ["--robot-host", robot_ip]
            _run_local_step(validate_cmd, "validate matrix")
        if not protocol_path.exists():
            raise FileNotFoundError(f"Generated protocol not found: {protocol_path}")

        image_run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        rtp: dict[str, Any] = {
            "dry_run": dry_run,
            "do_dilution": do_dilution,
            "do_print": do_print,
        }
        if args.pipette_check:
            rtp["pipette_check"] = True
        if args.paper_start_column is not None:
            rtp["print_start_column"] = args.paper_start_column
        run_log.update(
            robot_ip=robot_ip,
            protocol_path=repo_relative(protocol_path),
            live=args.live,
            no_start=args.no_start,
            pull_images=not args.no_pull_images,
            clean_remote_images=not args.no_clean_remote_images,
            robot_image_dir=_remote_image_dir(),
            local_image_run_id=image_run_id,
            local_image_dir=repo_relative(LOCAL_VISION_BASE / image_run_id),
            local_image_base=repo_relative(LOCAL_VISION_BASE),
            config=args.config,
            skip_build=args.skip_build,
            skip_validate=args.skip_validate,
            run_time_parameter_values=rtp,
        )

        print("\n=== Vial Dilution Print Robot Runner ===")
        print(f"Robot     : {robot_ip}")
        print(f"Protocol  : {protocol_path}")
        mode = ("PIPETTE CHECK (motion, no liquid)" if args.pipette_check
                else "LIVE LIQUID RUN" if args.live else "DRY RUN (no motion)")
        print(f"Mode      : {mode}")
        print(f"Dilution  : {do_dilution}")
        print(f"Print     : {do_print}")
        if args.paper_start_column is not None:
            print(f"Paper col : {args.paper_start_column} (CLI override)")
        embedded = _embedded_config(protocol_path)
        if embedded:
            _describe_deck(embedded)
        else:
            print("\nWARNING: could not read the CONFIG out of "
                  f"{protocol_path.name}; check the deck against your YAML by hand.")
        # v4/v6 capture no images (no camera), so skip all image handling for them.
        images_enabled = (args.live and not args.no_pull_images
                          and version not in IMAGELESS_VERSIONS)
        if images_enabled:
            print(f"Images    : will pull {_remote_image_dir()} to {LOCAL_VISION_BASE / image_run_id} after the run.")

        protocol_id = _upload_protocol(robot_ip, protocol_path)
        run_log.event("protocol_uploaded", protocol_id=protocol_id)
        run_id = _create_run(
            robot_ip,
            protocol_id,
            dry_run=dry_run,
            do_dilution=do_dilution,
            do_print=do_print,
            paper_start_column=args.paper_start_column,
            pipette_check=args.pipette_check,
            send_runtime_parameters=version not in API_215_VERSIONS,
        )
        run_log.event("run_created", protocol_id=protocol_id, run_id=run_id)

        if args.no_start:
            print(f"\nCreated run but did not start it. Run ID: {run_id}")
            run_log.finish("created_not_started", exit_code=0)
            return 0

        if images_enabled and not args.no_clean_remote_images:
            _prepare_remote_image_dir(robot_ip, args.ssh_key, run_log)

        _play_run(robot_ip, run_id)
        run_log.event("run_started", run_id=run_id)
        status = _monitor(robot_ip, run_id, args.poll_seconds)
        print(f"\nRun finished with status: {status}")
        if status != "succeeded":
            _report_run_error(robot_ip, run_id, run_log)
        if images_enabled:
            _pull_images(robot_ip, args.ssh_key, image_run_id, run_log)
        exit_code = 0 if status == "succeeded" else 1
        run_log.finish(status, exit_code=exit_code)
        return exit_code
    except Exception as exc:
        run_log.finish("error", exit_code=1, error=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
