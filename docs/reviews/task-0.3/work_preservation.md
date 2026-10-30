# WORK preservation — WORK VALIDATION PENDING

Do this on the WORK laptop before updating it. No WORK access, live execution,
deployment promotion, cloud calls or credential changes occurred on HOME.

1. Stop application writers and record current commit, branch, status and local
   refs. Make a **private full-state backup outside the checkout**, including Git
   history and readable tracked, untracked and ignored project files. Follow
   [Task 0.2 recovery](../task-0.2/recovery.md); verify full archive hashes, an
   extracted restore and Git bundle recovery. A Git tag alone is insufficient.
   Investigate read failures instead of treating them as empty directories. The
   HOME scratch allowlist is not an allowlist for unknown WORK files.
2. Back up WORK-owned `.env`, SSH configuration/keys, gcloud ADC/configuration,
   proxy/network settings and any external `OT_API_CONFIG_DIR` privately with
   appropriate access controls. Do not commit them or publish their hashes,
   contents, URLs, tokens, raw environment exports, pip direct URLs or conda
   explicit package URLs. This helper intentionally does not inspect external
   credentials. Presence/stat evidence cannot establish credential integrity.
3. Preserve `configs/robot.yaml`, all machine/config YAML, user workflows,
   custom labware JSON and geometry, all generated/manual protocols, experiment
   records/history and scientific/Raman data. Preserve external App/robot
   labware-offset, deck, pipette and tip-length calibration exports and dated
   physical validation notes. Record which exact protocol/config/hashes were
   actually run and the physical setup. HOME mirrors and old comments are
   historical evidence, not observations of current WORK calibration.
4. Before collection, privately confirm `configs`, `labware` and `src/labware`
   contain only approved nonsecret settings. Name exclusions are conservative
   heuristics, not a secret detector; do not collect unknown secret-bearing YAML
   or JSON. Activate `llm` and collect local metadata with the helper copied to a trusted
   temporary location if it is not yet present on WORK. The role is an operator
   declaration; package metadata describes **only that Python interpreter**.
   Separately record conda name/Python, pinned simulator interpreter and robot
   software/API versions from local evidence. Avoid imports and connectivity
   probes during preservation.

```powershell
conda activate llm
python -B docs\reviews\task-0.3\settings_evidence.py collect --role WORK --root C:\code\opentrons_home\ot2-lab-suite --output C:\private-backup\work-before.json
```

Use the actual WORK checkout/private-backup paths. Create the private folder
with restricted access first. The report refuses an existing output file.

5. Privately confirm WORK uses `LLM_PROVIDER=vertexai`, the correct project and
   location, and gcloud ADC; do not replace them with HOME API-key settings.
   Record proxy/network and SSH configuration ownership privately. Presence
   booleans cannot demonstrate provider values or successful authentication.
6. Review the candidate update against the private WORK baseline before applying
   it. Preserve/reconcile every changed WORK-owned file explicitly. Do not copy
   HOME `.env`, robot discovery, offsets or calibration onto WORK. After an
   authorized candidate update, collect `work-after.json` with the **same root and
   declared role**, then compare:

```powershell
python -B docs\reviews\task-0.3\settings_evidence.py compare C:\private-backup\work-before.json C:\private-backup\work-after.json
```

Exit 1 means drift or inspection issues require review; it does not perform
restoration or prevent writes. Root/role/schema mismatch refuses comparison.
Use the full private backup to check credential bytes and files outside the
nonsecret `configs`, `labware`, `src/labware` scope. New secret-bearing files must
never be placed in these maintained public settings roots.

7. Obtain dated operator evidence of robot serial, pipettes, tips, deck, labware,
   offsets/calibrations and existing method qualification. Later WORK checks may
   use `python scripts\find_robot.py --check` for read-only discovery diagnostics;
   no discovery or robot request was made here. Resolve identity from
   `configs/robot.yaml`, not an old IP in a guide/skill.

Task 0.3 WORK acceptance requires a verified WORK private backup, real metadata,
and before/after reconciliation proving settings were not silently replaced.
All three are currently pending. Physical method qualification remains separate
and cannot be established by this helper or by simulation.
