"""
vision/transfer_images.py
=========================
SSH into the OT-2, discover image files, and SCP them individually
to a timestamped local run folder.

Key design decisions
--------------------
* ``ssh … find`` is used to discover files — avoids unreliable SCP
  wildcard expansion on Windows.
* Each file is transferred one-by-one with ``scp -O`` (legacy protocol
  mode) for maximum compatibility with the OT-2 SSH server.
* Filenames are sanitised for Windows before writing.
"""

import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vision.transfer")


# ─── Filename sanitisation ───────────────────────────────────────

_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitise_filename(name: str) -> str:
    """Replace characters that are illegal on Windows with underscores."""
    return _WINDOWS_ILLEGAL.sub("_", name)


# ─── SSH / SCP helpers ──────────────────────────────────────────

def _ssh_opts(cfg: Dict[str, Any]) -> List[str]:
    """Return the common SSH option flags used by both ssh and scp."""
    key_path = str(cfg["robot"]["ssh_key_path"])
    return [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30",
        "-o", "StrictHostKeyChecking=no",
        "-i", key_path,
    ]


def _ssh_target(cfg: Dict[str, Any]) -> str:
    """Return ``user@host`` for ssh/scp commands."""
    return f"{cfg['robot']['username']}@{cfg['robot']['host']}"


def check_connectivity(cfg: Dict[str, Any]) -> bool:
    """Return *True* if the OT-2 responds to a simple SSH command."""
    cmd = ["ssh"] + _ssh_opts(cfg) + [_ssh_target(cfg), "echo ok"]
    logger.info("Checking OT-2 connectivity: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        logger.info("OT-2 connectivity check PASSED.")
        return True
    logger.error(
        "OT-2 connectivity check FAILED (rc=%d): %s",
        result.returncode,
        result.stderr.strip(),
    )
    return False


# ─── Remote file discovery ──────────────────────────────────────

def _build_find_command(
    remote_dirs: List[str],
    extensions: List[str],
) -> str:
    """Build a POSIX ``find`` command string that locates matching files
    across one or more remote directories.
    """
    # Build the -name predicates: -name '*.jpg' -o -name '*.png' …
    name_clauses: List[str] = []
    for ext in extensions:
        # Ensure the extension starts with a dot
        ext = ext if ext.startswith(".") else f".{ext}"
        name_clauses.append(f"-name '*{ext}'")

    name_expr = " -o ".join(name_clauses)

    # Search each directory (some may not exist on a given robot)
    parts: List[str] = []
    for d in remote_dirs:
        parts.append(
            f"find {d} -type f \\( {name_expr} \\) 2>/dev/null"
        )
    return " ; ".join(parts)


def discover_remote_files(
    cfg: Dict[str, Any],
    remote_dirs_override: Optional[List[str]] = None,
) -> List[str]:
    """SSH into the robot and return a list of absolute remote file paths.

    Parameters
    ----------
    cfg : dict
        Resolved vision config (from ``load_vision_config``).
    remote_dirs_override : list[str], optional
        Override the directories to search (ignores YAML config).

    Returns
    -------
    list[str]
        Absolute POSIX paths on the robot.
    """
    remote_dirs = remote_dirs_override or cfg["robot"].get("remote_image_dirs", [])
    extensions = cfg.get("transfer", {}).get("file_extensions", [".jpg", ".jpeg", ".png", ".zip"])

    find_cmd = _build_find_command(remote_dirs, extensions)
    cmd = ["ssh"] + _ssh_opts(cfg) + [_ssh_target(cfg), find_cmd]

    logger.info("Discovering remote files: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.warning(
            "Remote find returned rc=%d — stderr: %s",
            result.returncode,
            result.stderr.strip(),
        )

    # Parse non-empty lines from stdout
    files = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]
    logger.info("Discovered %d remote file(s).", len(files))
    return files


# ─── Transfer ───────────────────────────────────────────────────

def _make_run_dir(cfg: Dict[str, Any], output_dir_override: Optional[Path] = None) -> Path:
    """Create and return the local run directory."""
    transfer_cfg = cfg.get("transfer", {})
    raw_dir: Path = output_dir_override or cfg["local"]["raw_dir"]

    if transfer_cfg.get("create_timestamped_run_folder", True):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = raw_dir / f"run_{timestamp}"
    else:
        run_dir = raw_dir

    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def transfer_images(
    cfg: Dict[str, Any],
    remote_dirs_override: Optional[List[str]] = None,
    output_dir_override: Optional[Path] = None,
    dry_run_override: Optional[bool] = None,
    overwrite_override: Optional[bool] = None,
) -> Dict[str, Any]:
    """Run the full discovery → transfer pipeline.

    Parameters
    ----------
    cfg : dict
        Resolved vision config.
    remote_dirs_override : list[str], optional
        Limit the search to specific remote directories.
    output_dir_override : Path, optional
        Write files to this directory instead of the config default.
    dry_run_override : bool, optional
        If *True*, log what *would* happen without transferring.
    overwrite_override : bool, optional
        If *True*, overwrite existing local files.

    Returns
    -------
    dict
        ``{"success": bool, "local_run_dir": str,
        "files_transferred": int, "failed_paths": list,
        "warnings": list}``
    """
    transfer_cfg = cfg.get("transfer", {})
    dry_run = dry_run_override if dry_run_override is not None else transfer_cfg.get("dry_run", False)
    overwrite = overwrite_override if overwrite_override is not None else transfer_cfg.get("overwrite_existing", False)

    summary: Dict[str, Any] = {
        "success": True,
        "local_run_dir": "",
        "files_transferred": 0,
        "failed_paths": [],
        "warnings": [],
    }

    # 1. Connectivity check
    if not check_connectivity(cfg):
        summary["success"] = False
        summary["warnings"].append("OT-2 is unreachable — aborting transfer.")
        return summary

    # 2. Discover remote files
    remote_files = discover_remote_files(cfg, remote_dirs_override)
    if not remote_files:
        summary["warnings"].append("No image files found on the robot.")
        return summary

    # 3. Prepare local run directory
    run_dir = _make_run_dir(cfg, output_dir_override)
    summary["local_run_dir"] = str(run_dir)

    # 4. Transfer each file
    for remote_path in remote_files:
        remote_posix = PurePosixPath(remote_path)
        local_name = _sanitise_filename(remote_posix.name)
        local_dest = run_dir / local_name

        # Skip existing files unless overwrite is on
        if local_dest.exists() and not overwrite:
            msg = f"Skipped (exists): {local_name}"
            logger.info(msg)
            summary["warnings"].append(msg)
            continue

        if dry_run:
            logger.info("[DRY-RUN] Would transfer: %s → %s", remote_path, local_dest)
            summary["files_transferred"] += 1
            continue

        # SCP the file (legacy mode with -O for OT-2 compatibility)
        scp_cmd = (
            ["scp", "-O"]
            + _ssh_opts(cfg)
            + [f"{_ssh_target(cfg)}:{remote_path}", str(local_dest)]
        )
        logger.info("SCP: %s", " ".join(scp_cmd))

        result = subprocess.run(scp_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Transferred: %s → %s", remote_path, local_dest)
            summary["files_transferred"] += 1
        else:
            logger.error(
                "FAILED to transfer %s (rc=%d): %s",
                remote_path,
                result.returncode,
                result.stderr.strip(),
            )
            summary["failed_paths"].append(remote_path)

    if summary["failed_paths"]:
        summary["success"] = False

    return summary
