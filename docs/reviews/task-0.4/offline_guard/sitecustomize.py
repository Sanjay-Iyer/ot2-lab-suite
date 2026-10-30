"""Reviewed-code offline baseline guard, not an OS egress sandbox.

Loaded before pytest and inherited by approved Python children. This directory
is put on PYTHONPATH only by run_baseline.py; normal application use is untouched.
"""
import atexit
import ipaddress
import json
import os
from pathlib import Path
import sys
import subprocess

ROOT = Path(os.environ["OT2_BASELINE_ROOT"]).resolve()
GUARD = str(Path(__file__).resolve().parent)
COUNTS = {}
RECORDS = []
MAX_RECORDS = 1000


def blocked(kind, detail=""):
    COUNTS[kind] = COUNTS.get(kind, 0) + 1
    if len(RECORDS) < MAX_RECORDS:
        RECORDS.append({"kind": kind, "detail": str(detail)[:200], "test": os.environ.get("PYTEST_CURRENT_TEST", "")})
    raise OSError("Offline baseline blocked " + kind)


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def audit(event, args):
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr"}:
        if not loopback(args[0]):
            blocked(event, args[0])
    elif event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
        address = args[1] if event == "socket.connect" else args[-1]
        if not isinstance(address, tuple) or not loopback(address[0]):
            blocked(event, address)
    elif event == "socket.getnameinfo" and not loopback(args[0][0]):
        blocked(event, args[0])


def check_child(command, environment=None, executable=None):
    if not isinstance(command, (list, tuple)):
        blocked("shell-command", command)
    words = [str(word) for word in command]
    name = Path(str(executable or words[0])).name.lower()
    if name in {"git", "git.exe"} and words[1:] == ["ls-files"]:
        return
    if name not in {"python", "python.exe"}:
        blocked("external-process", words)
    child = environment if environment is not None else os.environ
    if (child.get("OT2_BASELINE_ROOT") != str(ROOT) or
            GUARD not in child.get("PYTHONPATH", "").split(os.pathsep) or
            any(flag in words for flag in ("-I", "-E", "-S", "--live", "--connect"))):
        blocked("unguarded-python-child", words)
    allowed = False
    if words[1:5] == ["-B", "-m", "opentrons.cli", "analyze"]:
        allowed = True      # pinned simulator analysis of a local file; it has no robot or network role
    elif len(words) > 2 and words[1] == "-c":
        code = words[2]
        allowed = code in {
            "import importlib.metadata; print(importlib.metadata.version('opentrons'))",
            "import numpy as np; np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); from opentrons.simulate import main; main()",
            "import sys; from src.agents.printing_tools import StandardExperimentApprovalSealV1, _verify_standard_approval; a=StandardExperimentApprovalSealV1.model_validate_json(sys.argv[1]); _verify_standard_approval(sys.argv[2], a)",
        }
    elif len(words) > 1:
        script = Path(words[1]).resolve()
        allowed = script in {ROOT / "scripts/build_vial_dilution_print.py",
                             ROOT / "scripts/validate_vial_print.py"}
        allowed = allowed or (script == ROOT / "vision_tests/scripts/verify_print_droplets.py" and "--mock" in words)
    if not allowed:
        blocked("unreviewed-python-child", words)

sys.addaudithook(audit)
GUARD_ACTIVE = True
# Inspect list arguments before Windows converts them to a command-line string.
_popen_init = subprocess.Popen.__init__
def guarded_init(self, args, *positional, **kwargs):
    if positional or kwargs.get("shell"):
        blocked("shell-or-positional-process", args)
    check_child(args, kwargs.get("env"), kwargs.get("executable"))
    return _popen_init(self, args, **kwargs)
subprocess.Popen.__init__ = guarded_init


@atexit.register
def write_counts():
    target = ROOT.parent / ("guard-" + str(os.getpid()) + ".json")
    target.write_text(json.dumps({"pid": os.getpid(), "blocked": COUNTS, "records": RECORDS}) + "\n", encoding="utf-8")
