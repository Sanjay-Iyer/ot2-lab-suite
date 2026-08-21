# Experiment 01 static ground truth

`src/protocols/printing/01_printing_standard_ground_truth.py` is the independent,
hardcoded OT-2 reference for Experiment 01. It is intentionally not reusable by
the later configuration executor.

Stage 1 artifacts:

- `static_protocol_reference.json`: protocol path and byte hash;
- `static_canonical_trace.json`: ordered resolved physical actions;
- `static_canonical_sha256.txt`: SHA-256 of canonical structured data, independent
  of JSON whitespace.

The trace uses a mathematically back-calculated twofold serial cascade. It allocates
59.765625 uL of original stock per series and retains exactly 30 uL in each of the
eight prepared wells. Every physical transfer is split to at most 20 uL for the P20.

Architecture Audit 1 approved this protocol on 2026-08-20. The protocol and its
canonical trace are now frozen. Later stages may compare against these artifacts
but must not import the static protocol as the generic implementation.
