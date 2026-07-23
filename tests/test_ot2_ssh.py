from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from scripts import check_ot2_ssh
from src.utils.ot2_ssh import (
    IDENTITIES_ONLY_OPTION,
    LEGACY_RSA_OPTION,
    MissingIdentityFileError,
    OT2SSHConfigurationError,
    OT2SSHSettings,
)


def _settings(identity_file: Path, *, legacy_rsa: bool) -> OT2SSHSettings:
    return OT2SSHSettings(
        robot_ip="169.254.46.57",
        user="root",
        identity_file=identity_file,
        identities_only=True,
        legacy_rsa=legacy_rsa,
    )


def test_legacy_mode_adds_required_authentication_options(tmp_path):
    identity = tmp_path / "id_rsa_opentrons"
    identity.write_text("test fixture", encoding="utf-8")

    command = _settings(identity, legacy_rsa=True).ssh_command("echo ok")

    assert ["-o", IDENTITIES_ONLY_OPTION] == command[1:3]
    assert LEGACY_RSA_OPTION in command
    assert command[-2:] == ["root@169.254.46.57", "echo ok"]


def test_modern_mode_omits_legacy_rsa_option(tmp_path):
    identity = tmp_path / "id_rsa_opentrons"
    identity.write_text("test fixture", encoding="utf-8")

    command = _settings(identity, legacy_rsa=False).ssh_command("echo ok")

    assert IDENTITIES_ONLY_OPTION in command
    assert LEGACY_RSA_OPTION not in command


def test_ssh_command_includes_identity_as_one_windows_argument():
    windows_identity = r"C:\Work Folder\OT-2 Keys\id_rsa_opentrons"
    command = OT2SSHSettings(
        robot_ip="169.254.46.57",
        identity_file=windows_identity,
        legacy_rsa=True,
    ).ssh_command("echo ok", validate_identity=False)

    identity_index = command.index("-i") + 1
    assert command[identity_index] == windows_identity
    assert command.count(windows_identity) == 1


def test_scp_receives_the_same_compatibility_options(tmp_path):
    identity = tmp_path / "id_rsa_opentrons"
    identity.write_text("test fixture", encoding="utf-8")
    settings = _settings(identity, legacy_rsa=True)

    command = settings.scp_command(
        "local file.txt",
        settings.remote_path("/data/test file.txt"),
        legacy_protocol=True,
    )

    assert command[0:2] == ["scp", "-O"]
    assert IDENTITIES_ONLY_OPTION in command
    assert LEGACY_RSA_OPTION in command
    assert "local file.txt" in command
    assert "root@169.254.46.57:/data/test file.txt" in command


def test_missing_identity_fails_before_connection(monkeypatch, tmp_path):
    missing = tmp_path / "does-not-exist"
    called = False

    def fail_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run when the identity is missing")

    monkeypatch.setattr(check_ot2_ssh.subprocess, "run", fail_run)
    exit_code = check_ot2_ssh.main(
        [
            "--robot-ip",
            "169.254.46.57",
            "--identity-file",
            str(missing),
            "--legacy-rsa",
        ]
    )

    assert exit_code != 0
    assert called is False


def test_public_key_path_is_rejected_before_connection(tmp_path):
    public_key = tmp_path / "id_rsa_opentrons.pub"
    public_key.write_text("ssh-rsa test", encoding="utf-8")

    with pytest.raises(OT2SSHConfigurationError, match=r"\.pub"):
        _settings(public_key, legacy_rsa=True).ssh_command("echo ok")


def test_host_key_checking_cannot_be_disabled(tmp_path):
    identity = tmp_path / "id_rsa_opentrons"
    identity.write_text("test fixture", encoding="utf-8")
    settings = _settings(identity, legacy_rsa=True)

    command = settings.ssh_command("echo ok")
    rendered = " ".join(command)
    assert "StrictHostKeyChecking=no" not in rendered
    assert "UserKnownHostsFile" not in rendered

    with pytest.raises(OT2SSHConfigurationError, match="host-key verification"):
        settings.ssh_command(
            "echo ok",
            extra_options=["-o", "StrictHostKeyChecking=no"],
        )


def test_diagnostic_only_runs_harmless_echo_and_does_not_log_key_contents(
    monkeypatch,
    tmp_path,
    capsys,
):
    private_marker = "PRIVATE-CONTENT-MUST-NOT-BE-LOGGED"
    identity = tmp_path / "id_rsa_opentrons"
    identity.write_text(private_marker, encoding="utf-8")
    captured_command = []

    def fake_run(command, **kwargs):
        captured_command.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="OT2_SSH_OK\n",
            stderr="",
        )

    monkeypatch.setattr(check_ot2_ssh.subprocess, "run", fake_run)
    exit_code = check_ot2_ssh.main(
        [
            "--robot-ip",
            "169.254.46.57",
            "--identity-file",
            str(identity),
            "--legacy-rsa",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert captured_command[-1] == "echo OT2_SSH_OK"
    assert "rm " not in " ".join(captured_command)
    assert private_marker not in output.out
    assert private_marker not in output.err


def test_runtime_python_call_sites_do_not_bypass_shared_builder():
    root = Path(__file__).resolve().parent.parent
    search_roots = [root / "scripts", root / "src", root / "vision"]
    helper = (root / "src" / "utils" / "ot2_ssh.py").resolve()
    bypasses: list[str] = []

    for search_root in search_roots:
        for path in search_root.rglob("*.py"):
            if path.resolve() == helper or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                    continue
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value in {"ssh", "scp"}:
                    bypasses.append(f"{path.relative_to(root)}:{node.lineno}")

    assert bypasses == [], f"Direct SSH/SCP command construction bypasses helper: {bypasses}"
