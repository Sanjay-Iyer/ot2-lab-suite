# OT-2 protocols

- **Primary NanoDrop:** `printing/13_ai_agent_dilution_print_demo.py` (builder ID19).
- **Current/manual and optional methods:** other maintained files under
  `printing/`, including standard, general dilution, vial/plate printing, clover
  and layered methods. Select through the existing YAML and registry.
- **Legacy compatibility:** `printing/legacy/` contains IDs1,2,3,4,6,7,8. Their
  protocol bytes and liquid-handling operations are unchanged; registry IDs,
  output stems, defaults and pinned-v3 simulation behavior remain available.
- **Generated artifacts:** currently `generated/`; separated in cleanup C4.

Legacy protocols/config headers can contain original paths as historical comments.
Use the current registry and [commands](../../docs/COMMANDS.md) rather than copying
old header commands. Presence here does not establish current WORK qualification.
