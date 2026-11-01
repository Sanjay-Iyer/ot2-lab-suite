# OT-2 protocols

- **Primary NanoDrop:** `printing/13_ai_agent_dilution_print_demo.py` (builder ID19).
- **Current/manual and optional methods:** other maintained files under
  `printing/`, including standard, general dilution, vial/plate printing, clover
  and layered methods. Select through the existing YAML and registry.
- **Legacy compatibility:** `printing/legacy/` contains IDs1,2,3,4,6,7,8. Their
  protocol bytes and liquid-handling operations are unchanged; registry IDs,
  output stems, defaults and pinned-v3 simulation behavior remain available.
- **Maintained hardware checks:** `checks/` (P20/P300 and API2.28 single-nozzle).
- **Generated artifacts:** `generated/protocols/current/` at the repository root;
  historical artifacts are preserved in the external cleanup archive.

Legacy protocols/config headers can contain original paths as historical comments.
Use the current registry and [commands](../../docs/COMMANDS.md) rather than copying
old header commands. Presence here does not establish current WORK qualification.
