# OT-2 SSH/SCP call-site audit

Audit date: 2026-07-23

All active Python call sites below construct commands through
`src/utils/ot2_ssh.py`. `ROBOT_SSH_LEGACY_RSA=true` adds
`PubkeyAcceptedAlgorithms=+ssh-rsa`; `ROBOT_SSH_IDENTITIES_ONLY=true` adds
`IdentitiesOnly=yes`. The helper applies the same SSH options to SCP and does
not disable host-key checking.

| File | Call path | Operation | Status |
|---|---|---|---|
| `scripts/check_ot2_ssh.py` | `main()` | Harmless authentication `echo` | Centralized; new diagnostic |
| `scripts/check_connectivity.py` | `main()` | Batch authentication check | Centralized |
| `scripts/check_connectivity.py` | `check_robot_calibration_paths()` | Remote calibration inspection | Centralized |
| `scripts/deploy.py` | `deploy()` | Remote mkdir and recursive SCP upload | Centralized |
| `scripts/deploy.py` | `deploy_labware()` | Remote mkdir, SCP upload, remote verify | Centralized |
| `scripts/sync_robot.py` | `sync_from_robot()` | Recursive SCP downloads | Centralized |
| `scripts/pull_vision_images.py` | `main()` | Recursive image SCP download | Centralized |
| `scripts/run_droplet_error_check.py` | `_pull_images()` | Recursive image SCP download | Centralized |
| `scripts/run_vial_print_robot.py` | `_prepare_remote_image_dir()` | Remote image-directory preparation | Centralized |
| `scripts/run_vial_print_robot.py` | `_pull_images()` | Recursive image SCP download | Centralized |
| `scripts/test_ot2_camera_capture.py` | `run_diagnostics()` | Remote capture, SCP download, cleanup | Centralized |
| `vision/capture_ot2_images.py` | `build_ssh_command()` / `capture_images()` | Remote camera capture | Centralized |
| `vision/transfer_ot2_images.py` | `build_scp_command()` / `transfer_images()` | Recursive image SCP download | Centralized |
| `vision/transfer_images.py` | `check_connectivity()` | Harmless remote echo | Centralized |
| `vision/transfer_images.py` | `discover_remote_files()` | Remote file discovery | Centralized |
| `vision/transfer_images.py` | `transfer_images()` | Per-file SCP downloads | Centralized |
| `src/agents/tools.py` | `get_robot_hardware_status()` | Remote instrument inspection | Centralized |
| `src/agents/tools.py` | `check_robot_connection()` | Remote path/executable checks | Centralized |
| `src/agents/tools.py` | `deploy_protocol_to_robot()` | Remote mkdir and recursive SCP upload | Centralized |
| `src/agents/tools.py` | `execute_protocol_on_robot()` | Remote verify and `opentrons_execute` | Centralized |
| `src/protocols/printing_demo_protocol.py` | host-side real-robot branch | SCP upload/download and SSH execution/cleanup | Centralized |
| `src/protocols/generated/printing_demo_run.py` | legacy generated host branch | SCP upload/download and SSH execution/cleanup | Centralized |

The following nearby paths were inspected and intentionally left as HTTP-only:

| File/path | Reason |
|---|---|
| `src/agents/robot_http_tools.py` | Invokes HTTP runner scripts; it does not construct SSH/SCP commands |
| `scripts/run_robot_template.py` | HTTP API template; optional SCP guidance now points to centralized examples |
| `scripts/run_smoke_test.py` | HTTP API physical dry-motion runner; no SSH/SCP command |
| `scripts/run_plate_waste_disposal.py` | HTTP API runner |
| OT-2 protocol `curl` calls | Robot-local HTTP camera calls, unrelated to laptop SSH authentication |

Tracked documentation examples were updated to use the explicit identity and
legacy RSA compatibility options. Machine-local `.env`, private keys,
`known_hosts`, robot logs, and generated run histories remain outside this
audit and must not be committed.
