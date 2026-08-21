# Architecture Audit 5 — Generalized experiment tools

An independent reviewer inventoried the registered printing tools, tested the new
generalized surface directly, and re-audited each remediation. All execution was
local simulation only.

## Agent-facing tools

| Tool | Purpose | Strict input | Typed output | Deterministic implementation | Why agent-facing |
|---|---|---|---|---|---|
| `list_standard_printing_experiment_capabilities` | Discover the supported schema, registered profile, scientific steps, approval boundary, and terminal state | none | JSON capability description | static registered capability declaration | Lets the model discover vocabulary without seeing motions. |
| `create_standard_printing_experiment_config` | Validate and persist immutable proposed YAML | `StandardExperimentProposalV1` plus safe `output_name` | `StandardExperimentConfigArtifactV1` | Pydantic schema, registered-profile loader, canonical YAML serialization, SHA-256 store | The AI may create scientific configuration, not protocol code. |
| `validate_standard_printing_experiment` | Validate a proposal without writing it | `StandardExperimentProposalV1` | `StandardExperimentValidationResultV1` | `PrintExperimentJobV1` loader/schema | Turns probabilistic output into an explicit fail-closed job. |
| `resolve_standard_printing_experiment` | Resolve and summarize an exact experiment | `StandardExperimentProposalV1` | `StandardExperimentResolutionResultV1` | deterministic resolver and physical/setup/topology fingerprints | Gives the agent decisions and totals, not actions or motions. |
| `inspect_standard_printing_layout` | Produce the scientist-readable preapproval review | `StandardExperimentProposalV1` | `StandardExperimentPreviewResultV1` | resolver plus `render_plan_review` | Lets the model present what the scientist must verify. |
| `simulate_approved_standard_printing_experiment` | Build and locally simulate one externally approved exact job | proposal plus `StandardExperimentApprovalSealV1` | `StandardExperimentSimulationResultV1` | persistent protected-key HMAC verification, resolver, trusted builder, local Opentrons simulator | Exposes a safe terminal operation while preserving the human boundary. |
| `report_printing_request_issue` | Return clarification/unsupported/error information | status, code, message, details | structured interpretation result | deterministic result schema | Lets the model stop rather than invent missing science. |

Low-level capabilities such as transfer splitting, liquid replay, aspiration,
dispensing, motion, print release, and protocol construction remain internal.

## Independent findings and resolutions

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Existing agent tools only compiled the legacy `PrintJobV1` family | blocker | valid | Added a distinct strict `PrintExperimentJobV1` tool surface. |
| Legacy compatibility compile tools were production defaults | blocker | valid | Removed them from the default surface; legacy use is explicit opt-in only. |
| Approved simulation was advertised but absent | major | valid | Added exact-job externally sealed local simulation terminating at `READY_FOR_EXECUTION`. |
| Tool schema exposed an opaque `dict[str, Any]` | major | valid | Replaced it with recursive `StandardExperimentProposalV1` embedding `ExperimentSpecV1` and an allowlisted profile literal. |
| Proposed YAML identity depended on mapping insertion order | major | valid | Normalize through the typed model before serialization and hashing; order-invariance is tested. |
| Proposal artifacts were stored in a test directory | major | valid | Production writes immutable content-addressed YAML under `configs/experiments/proposals`; tests inject isolated stores. |
| Approval receipt could be replayed against a changed job | blocker check | invalid after remediation | HMAC verification binds the exact `job_sha256` and statement using a protected persistent key that is absent from agent tools and outputs; modified-job replay is rejected. |

Focused generalized tool suite: **9 passed**. The independent reviewer also
verified approval replay rejection and the exact seven-tool generalized surface.

Verdict: **AUDIT CLEAN**.
