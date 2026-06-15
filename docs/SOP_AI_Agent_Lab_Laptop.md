# SOP — AI Agent on Lab Laptop (Live OT-2)

> **This SOP is for the lab laptop only.** The lab laptop has:
> - The real OT-2 connected via USB/Ethernet (IP `169.254.46.57`)
> - A live `.env` with Vertex AI / gcloud ADC auth, plus `ROBOT_IP` and `ROBOT_SSH_KEY_PATH`
> - The SSH private key that authenticates to the robot
>
> The dev/code laptop cannot connect to the robot. Do not attempt to run `deploy_protocol_to_robot` or `execute_protocol_on_robot` from the dev laptop.

---

## Prerequisites (one-time setup — already done on lab laptop)

- [ ] Conda environment `llm` exists with all dependencies installed
- [ ] `.env` file is present in project root with real credentials
- [ ] SSH key path in `.env` matches actual key location on this machine
- [ ] OT-2 is powered on and network-connected to this laptop
- [ ] You can ping `169.254.46.57` from terminal

Verify with:
```bash
ping 169.254.46.57
ssh -i <key_path> root@169.254.46.57 "echo connected"
```

---

## Step 1 — Navigate to Project Root

```bash
cd ~/ot2-lab-suite
# or wherever the repo is cloned on the lab laptop
```

All commands in this SOP must be run from the project root.

---

## Step 2 — Activate the Conda Environment

```bash
conda activate llm
```

Verify:
```bash
python -c "import langgraph; import langchain_google_genai; import langchain_google_vertexai; print('OK')"
```

---

## Step 3 — Start the AI Agent

**Recommended for the current vial dilution -> paper print workflow:**
```bash
python -m src.agents.vial_print_agent
```

This dedicated agent builds and validates the vial-print protocol, then launches
the real run through `scripts/run_vial_print_robot.py` and the OT-2 HTTP API after
you reply exactly `RUN ROBOT`.

**Standard mode (Gemini LLM, no rate limiting):**
```bash
python -m src.agents.main
```

**If you hit Gemini 429 rate-limit errors (Free Tier):**
```bash
python -m src.agents.main --rate-limit
```

**Mock mode (no API calls, for testing agent logic only):**
```bash
python -m src.agents.main --mock
```

**Pass an initial prompt directly:**
```bash
python -m src.agents.main "Set up a dilution workflow with 8 steps"
```

You will see:
```
--- AI Agent Initialized (Gemini) ---
Logging to: src/agents/logs/agent_session_YYYYMMDD_HHMMSS.log

[USER]:
```

---

## Step 4 — Example Session: Run a Dilution Protocol

Type natural language. The agent handles all tool calls internally.

```
[USER]: I want to run a dilution workflow
[AGENT]: Loading dilution defaults... [shows config summary]

[USER]: Looks good, use default settings
[AGENT]: Validating... PASSED. Generating protocol... Simulating... PASSED.

[AGENT]: Checking robot connection...
[AGENT]: Getting hardware status... LEFT mount: p300_single_gen2

[AGENT]: PRE-RUN SUMMARY
  Protocol: generated_dilution.py
  SHA256: abc12345...
  Robot IP: 169.254.46.57
  Deck: Slot 1 = opentrons_96_tiprack_300ul, Slot 2 = corning_96_wellplate_360ul_flat
  Pipette: p300_single_gen2 on LEFT (matches config)
  Estimated transfers: 16
  
  Reply with exactly: RUN ROBOT

[USER]: RUN ROBOT
[AGENT]: Deploying to robot... Executing... COMPLETE.
```

---

## Step 5 — Example Session: Run a Printing Protocol

```
[USER]: Set up a printing workflow using the 96-well plate config
[AGENT]: Loading printing_96 defaults... [shows config]

[USER]: Change the source volume to 50ul
[AGENT]: Updated config. Validating... PASSED. Generating... Simulating...

[USER]: Run on the robot
[AGENT]: [presents PRE-RUN SUMMARY]

[USER]: RUN ROBOT
[AGENT]: Deploying... Executing... COMPLETE.
```

---

## Available Workflows to Request

| What to say | Workflow loaded |
|-------------|----------------|
| "dilution workflow" | `dilution` |
| "printing workflow" | `printing` |
| "demo print" or "demo workflow" | `printing_demo` |
| "12-well printing" | `printing_12` |
| "96-well printing" | `printing_96` |

---

## Key Commands Reference

| Command | Purpose |
|---------|---------|
| `python -m src.agents.main` | Start agent (normal mode) |
| `python -m src.agents.main --rate-limit` | Start with Gemini rate limiting |
| `python -m src.agents.main --mock` | Start in mock mode (no LLM calls) |
| `python -m src.agents.run_stress_tests` | Run 10-test validation benchmark |
| `python -m src.agents.test_simulation_agent` | Run simulation integration test |
| `python -m src.agents.check_models` | List available Gemini models |

---

## What to Type in the Agent

| Intent | What to type |
|--------|-------------|
| Start a workflow | "I want to run a [dilution/printing/96-well] workflow" |
| Accept defaults | "Use default settings" or "looks good" |
| Change a parameter | "Change the number of dilution steps to 6" |
| See current config | "Show me the full config" |
| Check the robot | "Check robot connection" |
| Confirm execution | `RUN ROBOT` (exactly this — case-insensitive) |
| Quit | `exit` or `quit` or Ctrl+C |

---

## Safety Rules (Enforced by the Agent)

1. **Simulation must pass** before any robot execution. The agent will refuse to run if `simulations.json` has no PASS record for the current protocol SHA256.
2. **PRE-RUN SUMMARY required.** The agent always presents a full summary (protocol, hash, IP, deck layout, pipettes, transfers) before asking for confirmation.
3. **`RUN ROBOT` is the only accepted confirmation.** The agent will not proceed if you say "yes", "ok", or anything else.
4. **Hardware verification.** The agent checks actual pipettes on the robot via SSH and compares to config. Mismatches are reported before you confirm.

---

## Troubleshooting

### "SSH unreachable at 169.254.46.57"
- Verify the OT-2 is on and the USB/Ethernet cable is connected
- Run: `ping 169.254.46.57`
- Check `ROBOT_SSH_KEY_PATH` in `.env` points to the actual key file

### "ROBOT_SSH_KEY_PATH is missing"
- Open `.env` and confirm `ROBOT_SSH_KEY_PATH=` has a real path (not blank)
- On the lab laptop the key is typically at `~/.ssh/ot2_ssh_key` or `keys/ot2_ssh_key`

### Gemini / Vertex AI credential errors
- On the real robot laptop, use Vertex AI / gcloud ADC only: set `LLM_PROVIDER=vertexai`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_API_KEY` is only for simulation-laptop agent testing and will be refused by live robot tools
- Confirm ADC is available with: `gcloud auth application-default login`
- If getting 429 errors, restart with `--rate-limit` flag

### "opentrons.simulate not found"
- You're not in the `llm` conda environment: `conda activate llm`

### "SIMULATION FAILED"
- The generated protocol has an error. Read the error output carefully.
- Tell the agent: "The simulation failed, here's the error: [paste error]"
- The agent will adjust the config and regenerate

### Agent gives wrong pipette in summary
- The agent re-reads pipette config from YAML at generate time to prevent this
- If it still happens, tell the agent: "Use the config's pipette value, not your memory"

---

## Log Files

All sessions are logged to:
```
src/agents/logs/agent_session_YYYYMMDD_HHMMSS.log
```

Each log contains full conversation history + LangGraph debug traces. Useful for:
- Auditing what the agent did
- Debugging tool call sequences
- Reproducing a past run

---

## File Outputs from a Typical Run

| File | What it is |
|------|-----------|
| `runs/generated/generated_{workflow}.py` | Generated OT-2 protocol |
| `runs/simulations.json` | SHA256-keyed simulation pass/fail records |
| `runs/deploy/run_YYYYMMDD_HHhMMmSSs/` | Local staging folder (protocol + manifest) |
| `src/agents/logs/agent_session_*.log` | Full session log |
| On robot: `/var/lib/jupyter/notebooks/ot2_runs/run_*/` | Deployed run folder |

---

## Reference

- [AI_Agent_Overview.md](AI_Agent_Overview.md) — Full file map, tool specs, architecture
- [LangChain_ot2_agent_guide.md](LangChain_ot2_agent_guide.md) — Deep-dive architecture doc
- [SOP_Robot_Deployment.md](SOP_Robot_Deployment.md) — Manual deployment (no agent)
