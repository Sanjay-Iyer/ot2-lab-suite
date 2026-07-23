# OT-2 SSH compatibility

The OT-2's older SSH server requires the `ssh-rsa` user-authentication
signature algorithm. Modern OpenSSH clients disable it by default.

This is separate from SSH host-key verification. The repository enables
`ssh-rsa` only for commands it constructs for the configured OT-2, and it does
not change the user's global OpenSSH configuration.

## Repository configuration

Set these machine-local values in `.env` on the real robot laptop:

```env
OT2_ROBOT_HOST=OT2CEP20220929R02.local
ROBOT_SSH_USER=root
ROBOT_SSH_KEY_PATH=C:\Users\<username>\.ssh\id_rsa_opentrons
ROBOT_SSH_IDENTITIES_ONLY=true
ROBOT_SSH_LEGACY_RSA=true
```

Set `ROBOT_SSH_LEGACY_RSA=false` for a modern SSH server that does not require
the compatibility algorithm. `IdentitiesOnly=yes` may remain enabled whenever
an explicit private key is configured.

All Python SSH/SCP call sites use `src/utils/ot2_ssh.py`. The helper adds the
configured identity as one subprocess argument, uses the same compatibility
options for SSH and SCP, and never adds `StrictHostKeyChecking=no` or a null
`UserKnownHostsFile`.

## Safe authentication diagnostic

This command performs only `echo OT2_SSH_OK`; it causes no robot motion and
does not modify robot files:

```powershell
python scripts\check_ot2_ssh.py `
  --robot-host OT2CEP20220929R02.local `
  --identity-file "$env:USERPROFILE\.ssh\id_rsa_opentrons" `
  --legacy-rsa
```

The diagnostic reports missing identity files, network failures, host-key
failures, public-key authentication failures, and unsupported OpenSSH options
with different messages.

## Working direct command

```powershell
ssh `
  -o IdentitiesOnly=yes `
  -o PubkeyAcceptedAlgorithms=+ssh-rsa `
  -i "$env:USERPROFILE\.ssh\id_rsa_opentrons" `
  root@OT2CEP20220929R02.local
```

For non-interactive commands, also add `-o BatchMode=yes`. For SCP, use the
same two `-o` options and add capital `-O` because the OT-2 lacks an SFTP
server.

## Optional per-host user SSH config

The repository does not require this when `.env` is configured correctly, but
an operator may choose this narrowly scoped user configuration:

```text
Host ot2
    HostName OT2CEP20220929R02.local
    User root
    IdentityFile ~/.ssh/id_rsa_opentrons
    IdentitiesOnly yes
    PubkeyAcceptedAlgorithms +ssh-rsa
```

Then connect with:

```powershell
ssh ot2
```

Do not put the legacy RSA option under `Host *`.

## Host-key changes

The OT-2 host key identifies the robot to the laptop. The user's RSA key
identifies the user to the robot. Replacing one does not validate the other.

If SSH reports that the remote host identification changed, do not assume it
is harmless. First verify the connected robot identity and serial number:

```powershell
curl.exe `
  -H "opentrons-version: *" `
  http://OT2CEP20220929R02.local:31950/health
```

After independently confirming the expected robot, remove only its stale host
entry:

```powershell
ssh-keygen -R OT2CEP20220929R02.local `
  -f "$env:USERPROFILE\.ssh\known_hosts"
```

Reconnect and verify the new fingerprint before accepting it. Repository code
never removes `known_hosts` entries automatically.

## Verify a public/private key pair

Compare the two SHA-256 fingerprints without printing private-key material:

```powershell
ssh-keygen -lf "$env:USERPROFILE\.ssh\id_rsa_opentrons.pub"
ssh-keygen -y -f "$env:USERPROFILE\.ssh\id_rsa_opentrons" |
  ssh-keygen -lf -
```

The fingerprints must match.

## Protocol API compatibility is separate

SSH compatibility does not change the Python Protocol API supported by the
robot. A robot reporting `maximum_protocol_api_version: 2.15` cannot run an
API `2.28` protocol merely because SSH authentication now works. Use an
appropriate robot software/API path; do not lower a protocol's API level as an
SSH workaround.
