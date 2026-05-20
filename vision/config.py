"""
vision/config.py
================
Loads ``configs/vision.yaml`` and ``.env``, resolves environment
variables, converts relative paths to absolute paths, and ensures
that all required local directories exist.

Usage
-----
>>> from vision.config import load_vision_config
>>> cfg = load_vision_config()
>>> print(cfg["robot"]["host"])       # resolved IP
>>> print(cfg["local"]["raw_dir"])    # absolute Path
"""

import os
import sys
import yaml
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv


# ─── Project-root discovery ──────────────────────────────────────
# vision/config.py sits one level below the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load the ``.env`` file from the project root."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print(
            f"[vision.config] WARNING: .env not found at {env_path}. "
            "Robot connection variables may be missing.",
            file=sys.stderr,
        )
    load_dotenv(env_path)


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Return the parsed contents of a YAML file."""
    if not path.exists():
        raise FileNotFoundError(
            f"[vision.config] Config file not found: {path}\n"
            f"Please ensure configs/vision.yaml exists under {PROJECT_ROOT}"
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"[vision.config] Expected a YAML mapping in {path}")
    return data


# ─── Public API ──────────────────────────────────────────────────

def load_vision_config(
    config_path: Path | None = None,
) -> Dict[str, Any]:
    """Load, resolve, and validate the full vision configuration.

    Parameters
    ----------
    config_path : Path, optional
        Override path to ``vision.yaml``.  Defaults to
        ``<PROJECT_ROOT>/configs/vision.yaml``.

    Returns
    -------
    dict
        Fully-resolved configuration dictionary.

    Raises
    ------
    FileNotFoundError
        If ``vision.yaml`` or ``.env`` is missing critical values.
    ValueError
        If required environment variables are unset or invalid.
    """
    # 1. Load .env first so env-vars are available
    _load_dotenv()

    # 2. Load YAML
    if config_path is None:
        config_path = PROJECT_ROOT / "configs" / "vision.yaml"
    cfg = _load_yaml(config_path)

    # 3. Resolve robot section
    _resolve_robot(cfg)

    # 4. Resolve local paths (relative → absolute) and create dirs
    _resolve_local_paths(cfg)

    return cfg


# ─── Internal helpers ────────────────────────────────────────────

def _resolve_robot(cfg: Dict[str, Any]) -> None:
    """Replace env-var *names* in the robot section with their values."""
    robot = cfg.get("robot", {})

    # Host IP
    host_var = robot.get("host_env_var", "ROBOT_IP")
    host = os.getenv(host_var, "").strip()
    if not host or host in ("127.0.0.1", "localhost"):
        raise ValueError(
            f"[vision.config] {host_var} is not set or is a loopback address "
            f"('{host}'). Set a real OT-2 IP in .env."
        )
    robot["host"] = host

    # SSH key path
    key_var = robot.get("ssh_key_env_var", "ROBOT_SSH_KEY_PATH")
    key_path_str = os.getenv(key_var, "").strip()
    if not key_path_str:
        raise ValueError(
            f"[vision.config] {key_var} is not set in .env. "
            "Provide the path to the OT-2 SSH private key."
        )
    key_path = Path(key_path_str).resolve()
    if not key_path.exists():
        raise FileNotFoundError(
            f"[vision.config] SSH key not found at {key_path} "
            f"(from env var {key_var})."
        )
    robot["ssh_key_path"] = key_path

    # Username (default: root)
    robot.setdefault("username", "root")


def _resolve_local_paths(cfg: Dict[str, Any]) -> None:
    """Convert relative directory strings to absolute ``Path`` objects
    and create missing directories.
    """
    local = cfg.get("local", {})
    for key in ("base_dir", "raw_dir", "processed_dir", "logs_dir"):
        raw_value = local.get(key, "")
        if raw_value:
            abs_path = (PROJECT_ROOT / raw_value).resolve()
            local[key] = abs_path
            abs_path.mkdir(parents=True, exist_ok=True)
