# OT-2 Lab Suite - Execution Guide

To ensure reliable execution, avoid running python scripts directly (e.g. `python scripts/deploy.py`). Instead, always use module-style execution from the project root. This guarantees that internal imports and paths resolve correctly.

## Standard PowerShell Commands

### 1. Start the Environment
```powershell
conda activate ai
cd C:\code\opentrons_home\ot2-lab-suite
```

### 2. Run All Tests
```powershell
python -m pytest tests
```

### 3. Run Path & Security Audit
```powershell
python -m scripts.audit_paths
```

### 4. Run Connectivity & Proxy Diagnostics
```powershell
python -m scripts.check_connectivity
```

### 5. Launch the Agent
```powershell
# Interactive mode
python -m src.agents.main

# Single prompt execution
python -m src.agents.main "Configure a standard printing run and run a simulation"
```

## Troubleshooting Stale DNS/VPN State

If you recently switched networks, activated a VPN, or changed corporate proxies, DNS resolution or API connectivity might fail with an error like `[Errno 11001] getaddrinfo failed`.

**Run these Windows commands to flush stale state:**

```powershell
ipconfig /flushdns

Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue

conda deactivate
conda activate ai
```

### Google Cloud CLI Proxy
If you use `gcloud` locally, the proxy might be cached in its configuration:
```powershell
gcloud config get-value proxy/type
gcloud config get-value proxy/address
gcloud config get-value proxy/port
```
