---
name: design-paper-printing
description: Select a registered continuous-coordinate paper design and prepare it through validation and local simulation.
domain: printing
agent: printing
families:
  - design
designs: []
references: []
---

# Design paper printing

Use this skill when scientific intent is a registered geometric design rather than
an exact-well selection. V1 supports only `four_clover`; ring, line, rotation,
arbitrary-point, and other geometries are unsupported.

Identify the registered design, then load its specialized skill. Populate its typed
pattern with scientific geometry, placement, layer, replicate, volume, and ordering
intent. Geometry describes offsets relative to a design center. Centers describe
design instances. Layers repeat deposits at each design point. Replicates are the
number of distinct design instances.

Use a registered placement preset when it explicitly supports the requested count,
or submit user-specified reference-well centers. If neither supplies placement, ask
for centers. Never create absolute D1-D4 coordinates: deterministic resolution owns
coordinate math, paper bounds, spacing checks, volume capacity, and print order.

Submit through `create_and_compile_print_job` or modify an existing canonical job
through `modify_and_compile_print_job`. Treat structured validation errors as
authoritative and present the tool-generated preview rather than recalculating it.
