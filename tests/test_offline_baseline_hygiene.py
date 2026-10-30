"""Collection regression and non-networking probes of the optional baseline guard."""
import builtins
import os
from pathlib import Path
import runpy
import socket
import subprocess
from unittest.mock import patch

import pytest


def test_gemini_module_import_does_not_import_model_or_application():
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        if name.startswith(("src", "langchain", "google")):
            raise AssertionError("Connectivity probe imported a provider/application during collection")
        return original(name, *args, **kwargs)
    with patch.object(builtins, "__import__", checked):
        runpy.run_path(str(Path(__file__).with_name("test_gemini.py")), run_name="offline_import_probe")


def test_gemini_probe_refuses_to_connect_without_the_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["test_gemini.py"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).with_name("test_gemini.py")), run_name="__main__")
    assert stopped.value.code == 2


@pytest.mark.skipif(not os.environ.get("OT2_BASELINE_ROOT"), reason="optional controlled-baseline guard")
def test_baseline_guard_hook_is_live_for_real_sockets_and_processes():
    with pytest.raises(OSError, match="Offline baseline blocked"):
        socket.create_connection(("192.0.2.1", 31950), timeout=0.01)
    with pytest.raises(OSError, match="Offline baseline blocked"):
        subprocess.run(["ping", "192.0.2.1"], capture_output=True)


@pytest.mark.skipif(not os.environ.get("OT2_BASELINE_ROOT"), reason="optional controlled-baseline guard")
def test_baseline_guard_denies_external_network_and_unguarded_processes():
    import sitecustomize as guard
    for event, args in (
        ("socket.connect", (None, ("192.0.2.1", 31950))),
        ("socket.sendto", (None, b"probe", ("224.0.0.251", 5353))),
        ("socket.sendmsg", (None, ("192.0.2.1", 5353))),
        ("socket.getaddrinfo", ("robot.invalid", 31950)),
    ):
        with pytest.raises(OSError, match="Offline baseline blocked"):
            guard.audit(event, args)
    guard.audit("socket.connect", (None, ("127.0.0.1", 12345)))
    for command, env in (
        (["ssh", "robot.invalid"], None),
        (["gcloud", "auth", "print-access-token"], None),
        (["python.exe", str(guard.ROOT / "scripts/run_vial_print_robot.py")], None),
        (["python.exe", "-I", "-c", "pass"], None),
        (["python.exe", "-c", "pass"], {}),
    ):
        with pytest.raises(OSError, match="Offline baseline blocked"):
            guard.check_child(command, env)
    guard.check_child(["git", "ls-files"])
