#!/usr/bin/env python3
"""Safely test OT-2 SSH authentication with a harmless remote echo."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import Config
from src.lab.robot_connection import (
    add_robot_host_arguments,
    connection_summary,
    resolve_host,
)
from src.utils.ot2_ssh import (
    LEGACY_RSA_OPTION,
    MissingIdentityFileError,
    OT2SSHSettings,
)


def _diagnosis(stderr: str) -> str:
    lowered = stderr.lower()
    if "remote host identification has changed" in lowered or "host key verification failed" in lowered:
        return (
            "HOST KEY FAILURE: SSH refused the robot identity. Verify the OT-2 serial "
            "number independently before changing known_hosts."
        )
    if "permission denied (publickey" in lowered:
        return (
            "PUBLIC-KEY AUTHENTICATION FAILURE: verify the configured private key, "
            "confirm its matching .pub key is installed on the OT-2, and confirm "
            "legacy RSA mode matches the robot."
        )
    if "bad configuration option" in lowered and "pubkeyacceptedalgorithms" in lowered:
        return (
            "SSH CLIENT COMPATIBILITY FAILURE: this client does not support "
            f"{LEGACY_RSA_OPTION}. Upgrade OpenSSH, or manually verify whether this "
            "client requires the older PubkeyAcceptedKeyTypes=+ssh-rsa spelling."
        )
    if any(
        phrase in lowered
        for phrase in (
            "connection timed out",
            "connection refused",
            "no route to host",
            "network is unreachable",
            "could not resolve hostname",
        )
    ):
        return "NETWORK FAILURE: check the robot IP, cable, power, and local network route."
    return "SSH FAILURE: review the OpenSSH error below."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test OT-2 SSH authentication without robot motion or remote file changes."
    )
    add_robot_host_arguments(parser)
    parser.add_argument("--user", default=Config.ROBOT_SSH_USER, help="OT-2 SSH user.")
    parser.add_argument(
        "--identity-file",
        default=Config.ROBOT_SSH_KEY_PATH,
        help="Private key path. Default: ROBOT_SSH_KEY_PATH.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--legacy-rsa",
        action="store_true",
        dest="legacy_rsa",
        help="Enable OT-2 ssh-rsa user-authentication compatibility.",
    )
    mode.add_argument(
        "--no-legacy-rsa",
        action="store_false",
        dest="legacy_rsa",
        help="Do not enable the legacy ssh-rsa algorithm.",
    )
    parser.set_defaults(legacy_rsa=Config.ROBOT_SSH_LEGACY_RSA)
    parser.add_argument("--timeout", type=int, default=15, help="Connection timeout in seconds.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    robot_host = resolve_host(args.robot_host)
    print(connection_summary(robot_host))
    settings = OT2SSHSettings.from_config(
        Config,
        robot_ip=robot_host,
        user=args.user,
        identity_file=args.identity_file,
    ).with_overrides(legacy_rsa=args.legacy_rsa)

    print("=== OT-2 SSH Authentication Diagnostic ===")
    print(f"Target          : {settings.target}")
    print(f"Legacy RSA mode : {'enabled' if settings.legacy_rsa else 'disabled'}")
    print(f"Identities only : {'enabled' if settings.identities_only else 'disabled'}")
    print("Remote action   : echo OT2_SSH_OK (no robot motion; no remote file changes)")

    try:
        command = settings.ssh_command(
            "echo OT2_SSH_OK",
            batch_mode=True,
            connect_timeout=args.timeout,
        )
    except MissingIdentityFileError as exc:
        print(f"MISSING IDENTITY FILE: {exc}", file=sys.stderr)
        return 2

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.timeout + 5,
            check=False,
        )
    except FileNotFoundError:
        print("SSH CLIENT FAILURE: the 'ssh' executable was not found.", file=sys.stderr)
        return 3
    except subprocess.TimeoutExpired:
        print("NETWORK FAILURE: SSH did not finish before the timeout.", file=sys.stderr)
        return 4

    if result.returncode == 0 and result.stdout.strip() == "OT2_SSH_OK":
        print("PASS: OT-2 SSH authentication succeeded.")
        return 0

    print(_diagnosis(result.stderr), file=sys.stderr)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
