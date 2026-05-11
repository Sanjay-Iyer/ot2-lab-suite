# Experiment Config Schema — v2.0 (Relative Nanoparticle Dilution)

Reference for the nanoparticle workflow where concentrations are treated as
relative to a synthesized stock suspension.

---

## Top-level keys

| Key | Type | Description |
|-----|------|-------------|
| `schema_version` | `string` | Semantic version of the config schema (currently `"2.0"`). |

---

## `experiment` — Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | ✅ | Human-readable experiment identifier. |
| `project` | `string` | ✅ | Project name (used for directory organization, e.g., `"nanoparticles"`). |
| `date` | `string` | ✅ | ISO 8601 date. |
| `operator` | `string` | ✅ | Person running the experiment. |
| `notes` | `string` | ❌ | Context or special instructions. |

---

## `nanoparticle_stock` — Stock Suspension

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `material` | `string` | — | ✅ | Short identifier (e.g., `"AuNS"`). |
| `display_name` | `string` | — | ✅ | Descriptive name (e.g., `"gold nanostars"`). |
| `label` | `string` | — | ✅ | Label for the stock vial (e.g., `"AuNS_stock"`). |
| `source_slot` | `integer` | — | ✅ | Deck slot holding the stock. |
| `source_well` | `string` | — | ✅ | Well containing the stock, e.g., `"A1"`. |
| `available_volume_uL` | `number` | µL | ✅ | Total volume of stock available. |
| `concentration_basis` | `string` | — | ✅ | Typically `"relative_to_stock"`. |
| `stock_relative_concentration` | `number` | — | ✅ | Always `1.0` for undiluted stock. |
| `absolute_concentration` | `mixed` | — | ❌ | Set to `null` unless calibrated. |

### `characterization` (Optional Metadata)
- `uvvis_measured`: `boolean`
- `uvvis_lambda_max_nm`: `number`
- `absorbance_at_lambda_max`: `number`
- `notes`: `string`

---

## `diluent` — Solvent source

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | ✅ | What the diluent is (e.g. `"water"`). |
| `source_slot` | `integer` | ✅ | Deck slot holding the reservoir. |
| `source_well` | `string` | ✅ | Reservoir well to aspirate from. |
| `available_volume_uL` | `number` | ✅ | Total volume of diluent available. |

---

## `requested_dilutions` — Dilution List

A list of objects defining each target sample.

| Field | Type | Units | Required | Description |
|-------|------|-------|----------|-------------|
| `dilution_factor` | `number` | — | ✅ | Factor **N** relative to stock (N ≥ 1). |
| `final_volume_uL` | `number` | µL | ✅ | Total volume after dilution. |
| `destination_well` | `string` | — | ✅ | Target well in Slot 2. |

### Calculations
- `relative_concentration = 1 / dilution_factor`
- `stock_volume_uL = final_volume_uL / dilution_factor`
- `diluent_volume_uL = final_volume_uL - stock_volume_uL`

**DF1** (dilution_factor = 1) is undiluted stock:
- `stock_volume_uL = final_volume_uL`
- `diluent_volume_uL = 0`

---

## `labware` — Deck layout

Standard slot mapping (Slot 1: Stock, Slot 2: Dilutions, Slot 11: Tips, etc.)

---

## `outputs` — File paths

| Field | Type | Description |
|-------|------|-------------|
| `log_file` | `string` | Path for the timestamped run log. |
| `transcript` | `string` | Path for the simulation transcript. |

---

## Validation Rules

1. `dilution_factor >= 1`
2. `final_volume_uL > 0`
3. `stock_volume_uL` does not exceed `nanoparticle_stock.available_volume_uL`.
4. `diluent_volume_uL` does not exceed `diluent.available_volume_uL`.
5. `DF1` requires no diluent.

---

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-04-28 | Initial schema. |
| 1.1 | 2026-04-28 | Updated terminology for Nanoparticle/Materials context. |
| 2.0 | 2026-04-28 | Refactored for Relative Dilution model. Renamed keys to `nanoparticle_stock` and `requested_dilutions`. |
