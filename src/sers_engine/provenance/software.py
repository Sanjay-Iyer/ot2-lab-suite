"""Which software produced a record: git state, package versions, safe environment.

Everything here is best-effort and read-only.  A missing git binary, a detached
HEAD, or an uninstalled package must never stop an experiment from being
logged -- the field is simply left out rather than invented.

Nothing in this module reads a secret.  Environment capture is an explicit
allowlist of operational flags, never a dump of ``os.environ``.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Any

from ..schema import REPO_ROOT

# Packages whose version materially changes what the agent or the robot did.
TRACKED_PACKAGES = (
    "langchain-core",
    "langchain",
    "langgraph",
    "pydantic",
    "opentrons",
    "pyyaml",
    "numpy",
)

# Operational flags that describe the execution host. These are laboratory
# state, not credentials. Anything not on this list is never recorded, so an
# API key or token cannot reach the scientific record by accident.
ENVIRONMENT_ALLOWLIST = (
    "OT2_LAPTOP_ROLE",
    "OT2_ROBOT_READY",
    "SERS_PLATE_ASPIRATE_CONFIRMED",
    "CONDA_DEFAULT_ENV",
)

# A second line of defence: even an allowlisted name is dropped if it looks
# like it carries a secret, so widening the list above cannot leak one.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_provenance() -> dict[str, Any]:
    """Commit, branch, and whether the working tree had uncommitted changes.

    A dirty tree is reported, never cleaned: the record describes the code that
    actually ran, even when that code was not committed.
    """
    commit = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    status = _git("status", "--porcelain")
    payload: dict[str, Any] = {}
    if commit:
        payload["git_commit"] = commit
    if branch:
        payload["git_branch"] = branch
    if status is not None:
        payload["git_dirty"] = bool(status.strip())
    return payload


def package_versions() -> dict[str, str]:
    """Installed versions of the packages that decide behaviour, where present."""
    from importlib.metadata import PackageNotFoundError, version

    found: dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            continue
        except Exception:  # a broken distribution must not stop logging
            continue
    return found


def safe_environment() -> dict[str, str]:
    """The allowlisted operational flags, with any secret-looking value dropped."""
    captured: dict[str, str] = {}
    for name in ENVIRONMENT_ALLOWLIST:
        if any(marker in name.upper() for marker in _SECRET_MARKERS):
            continue
        value = os.environ.get(name)
        if value:
            captured[name] = value
    return captured


def interpreter_provenance() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "host": platform.node(),
    }


def model_provenance(llm: Any) -> dict[str, Any]:
    """Describe the language model without recording anything that authenticates it.

    Providers expose the configured model name under several different
    attributes and none of them is guaranteed, so this reads what is there and
    labels it as configured rather than resolved.
    """
    if llm is None:
        return {}
    payload: dict[str, Any] = {"model_provider": type(llm).__name__}
    for attribute in ("model_name", "model", "model_id", "deployment_name"):
        value = getattr(llm, attribute, None)
        if isinstance(value, str) and value:
            payload["model_name"] = value
            break
    configuration: dict[str, Any] = {}
    for attribute in ("temperature", "max_tokens", "max_output_tokens", "top_p", "location"):
        value = getattr(llm, attribute, None)
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            configuration[attribute] = value
    if configuration:
        payload["model_configuration"] = configuration
    # The provider does not report which weights answered a call, so what is on
    # file is the configured name. Say so rather than implying an exact version.
    payload["model_name_source"] = "configured"
    return payload
