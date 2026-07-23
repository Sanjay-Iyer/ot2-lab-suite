"""Central SSH/SCP command construction for OT-2 connections.

The OT-2's older Dropbear SSH server may require RSA/SHA-1 user
authentication signatures.  Modern OpenSSH clients call that algorithm
``ssh-rsa`` and disable it by default.  This module scopes the compatibility
flag to commands created for a configured OT-2; it never changes the user's
global SSH configuration or disables host-key verification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence


LEGACY_RSA_OPTION = "PubkeyAcceptedAlgorithms=+ssh-rsa"
IDENTITIES_ONLY_OPTION = "IdentitiesOnly=yes"
_FORBIDDEN_OPTIONS = (
    "stricthostkeychecking=no",
    "userknownhostsfile=/dev/null",
    "userknownhostsfile=nul",
)


class OT2SSHConfigurationError(ValueError):
    """Raised when an OT-2 SSH command cannot be constructed safely."""


class MissingIdentityFileError(FileNotFoundError):
    """Raised before SSH/SCP starts when the configured key does not exist."""


def parse_bool(value: Any, *, default: bool = False) -> bool:
    """Parse common environment/YAML boolean values."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise OT2SSHConfigurationError(
        f"Expected a boolean value, got {value!r}. "
        "Use true/false, yes/no, on/off, or 1/0."
    )


def require_identity_file(identity_file: str | Path | None) -> str:
    """Return an identity path or fail before any network connection."""
    raw = str(identity_file or "").strip()
    if not raw:
        raise MissingIdentityFileError(
            "OT-2 SSH identity file is not configured. "
            "Set ROBOT_SSH_KEY_PATH or pass an explicit identity file."
        )
    path = Path(raw).expanduser()
    if path.suffix.lower() == ".pub":
        raise OT2SSHConfigurationError(
            f"OT-2 SSH identity must be a private key, not a .pub file: {path}"
        )
    if not path.is_file():
        raise MissingIdentityFileError(f"OT-2 SSH identity file not found: {path}")
    return str(path)


def _validate_extra_options(options: Sequence[str]) -> list[str]:
    rendered = [str(option) for option in options]
    combined = " ".join(rendered).replace(" ", "").lower()
    for forbidden in _FORBIDDEN_OPTIONS:
        if forbidden in combined:
            raise OT2SSHConfigurationError(
                f"Refusing insecure SSH option: {forbidden}. "
                "OT-2 host-key verification must remain enabled."
            )
    return rendered


@dataclass(frozen=True)
class OT2SSHSettings:
    """Connection settings shared by all repository-managed SSH/SCP calls."""

    robot_ip: str
    user: str = "root"
    identity_file: str | Path | None = None
    identities_only: bool = True
    legacy_rsa: bool = False

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        robot_ip: str | None = None,
        user: str | None = None,
        identity_file: str | Path | None = None,
    ) -> "OT2SSHSettings":
        """Build settings from ``src.core.config.Config``-style attributes."""
        resolved_host = str(robot_ip or "").strip()
        if not resolved_host:
            from src.lab.robot_connection import resolve_host

            resolved_host = resolve_host()
        return cls(
            robot_ip=resolved_host,
            user=str(user or getattr(config, "ROBOT_SSH_USER", "root") or "root").strip(),
            identity_file=(
                identity_file
                if identity_file is not None
                else getattr(config, "ROBOT_SSH_KEY_PATH", "")
            ),
            identities_only=parse_bool(
                getattr(config, "ROBOT_SSH_IDENTITIES_ONLY", True),
                default=True,
            ),
            legacy_rsa=parse_bool(
                getattr(config, "ROBOT_SSH_LEGACY_RSA", False),
                default=False,
            ),
        )

    @classmethod
    def from_mapping(cls, robot: Mapping[str, Any]) -> "OT2SSHSettings":
        """Build settings from the resolved ``vision`` robot configuration."""
        return cls(
            robot_ip=str(robot.get("host") or robot.get("ip") or "").strip(),
            user=str(robot.get("username") or robot.get("user") or "root").strip(),
            identity_file=robot.get("ssh_key_path"),
            identities_only=parse_bool(
                robot.get("ssh_identities_only", robot.get("identities_only")),
                default=True,
            ),
            legacy_rsa=parse_bool(
                robot.get("ssh_legacy_rsa", robot.get("legacy_rsa")),
                default=False,
            ),
        )

    def with_overrides(
        self,
        *,
        robot_ip: str | None = None,
        user: str | None = None,
        identity_file: str | Path | None = None,
        identities_only: bool | None = None,
        legacy_rsa: bool | None = None,
    ) -> "OT2SSHSettings":
        """Return a copy with CLI/runtime overrides applied."""
        updates: dict[str, Any] = {}
        if robot_ip is not None:
            updates["robot_ip"] = robot_ip
        if user is not None:
            updates["user"] = user
        if identity_file is not None:
            updates["identity_file"] = identity_file
        if identities_only is not None:
            updates["identities_only"] = identities_only
        if legacy_rsa is not None:
            updates["legacy_rsa"] = legacy_rsa
        return replace(self, **updates)

    @property
    def target(self) -> str:
        if not self.robot_ip:
            raise OT2SSHConfigurationError("OT-2 robot IP is not configured.")
        if not self.user:
            raise OT2SSHConfigurationError("OT-2 SSH user is not configured.")
        return f"{self.user}@{self.robot_ip}"

    def remote_path(self, path: str) -> str:
        """Return an SCP remote path using the configured target."""
        return f"{self.target}:{path}"

    def options(
        self,
        *,
        batch_mode: bool = True,
        connect_timeout: int | None = 30,
        validate_identity: bool = True,
        extra_options: Sequence[str] = (),
    ) -> list[str]:
        """Return common OpenSSH arguments for both ``ssh`` and ``scp``."""
        identity = (
            require_identity_file(self.identity_file)
            if validate_identity
            else str(self.identity_file or "").strip()
        )
        if not identity:
            raise MissingIdentityFileError("OT-2 SSH identity file is not configured.")

        options: list[str] = []
        if self.identities_only:
            options.extend(["-o", IDENTITIES_ONLY_OPTION])
        if self.legacy_rsa:
            options.extend(["-o", LEGACY_RSA_OPTION])
        options.extend(["-i", identity])
        if batch_mode:
            options.extend(["-o", "BatchMode=yes"])
        if connect_timeout is not None:
            options.extend(["-o", f"ConnectTimeout={int(connect_timeout)}"])
        options.extend(_validate_extra_options(extra_options))
        return options

    def ssh_command(
        self,
        remote_command: str | None = None,
        *,
        batch_mode: bool = True,
        connect_timeout: int | None = 30,
        validate_identity: bool = True,
        extra_options: Sequence[str] = (),
    ) -> list[str]:
        """Build an ``ssh`` subprocess argument list."""
        command = [
            "ssh",
            *self.options(
                batch_mode=batch_mode,
                connect_timeout=connect_timeout,
                validate_identity=validate_identity,
                extra_options=extra_options,
            ),
            self.target,
        ]
        if remote_command is not None:
            command.append(remote_command)
        return command

    def scp_command(
        self,
        sources: str | Path | Sequence[str | Path],
        destination: str | Path,
        *,
        recursive: bool = False,
        legacy_protocol: bool = True,
        batch_mode: bool = True,
        connect_timeout: int | None = 30,
        validate_identity: bool = True,
        extra_options: Sequence[str] = (),
    ) -> list[str]:
        """Build an ``scp`` subprocess argument list with shared SSH options."""
        if isinstance(sources, (str, Path)):
            source_args = [str(sources)]
        else:
            source_args = [str(source) for source in sources]
        if not source_args:
            raise OT2SSHConfigurationError("At least one SCP source is required.")

        command = ["scp"]
        if legacy_protocol:
            command.append("-O")
        if recursive:
            command.append("-r")
        command.extend(
            self.options(
                batch_mode=batch_mode,
                connect_timeout=connect_timeout,
                validate_identity=validate_identity,
                extra_options=extra_options,
            )
        )
        command.extend([*source_args, str(destination)])
        return command
