"""Focused offline cleanup checks. Uses existing pytest, CLI and simulator tools."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".test_tmp" / ("cleanup-checks-" + uuid4().hex[:10])
OUT.mkdir(parents=True)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONIOENCODING="utf-8")
RESULTS = []


def run(label, args, stdin=None):
    result = subprocess.run([sys.executable, "-B", *args], cwd=ROOT, env=ENV, input=stdin,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    (OUT / (label + ".txt")).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    RESULTS.append({"label": label, "args": args, "exit_code": result.returncode})
    print(label, result.returncode, flush=True)
    if result.returncode:
        print(result.stdout[-1500:], result.stderr[-1000:], flush=True)
    return result.returncode == 0


def main():
    run("pytest", ["-m", "pytest", "--noconftest", "-p", "no:cacheprovider", "--basetemp", str(OUT / "pytest"),
                   "tests/test_agent_entrypoints.py", "tests/test_ai_dye_demo_protocol.py", "tests/test_ai_dye_demo_skills.py", "-q"])
    run("check", ["scripts/check_ot2.py"])
    run("work-preview", ["scripts/launch_agent.py", "--config", "configs/work/agent.yaml", "--preview"])
    run("terminal", ["scripts/ai_dye_demo.py", "--simulate", "--offline", "--operator", "Cleanup Check"], "quit\n")
    for name, family in (("run_dilution", "dilution"), ("run_paper_printing", "paper_printing")):
        run(name, [f"scripts/{name}.py", "--config", f"configs/experiments/{family}/example.yaml", "--simulate",
                   "--simulator-python", str(ROOT / ".venv/ot2-api-2.15-py310/python.exe")])
    profile = yaml.safe_load((ROOT / "configs/home/agent.yaml").read_text())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    profile.update(port=port, no_browser=True, operator="Cleanup Check")
    config = OUT / "gui.yaml"; config.write_text(yaml.safe_dump(profile), encoding="utf-8")
    with (OUT / "gui.txt").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", "scripts/launch_agent.py", "--config", str(config)],
                                   cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT)
        ok = False
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                        ok = response.status == 200 and "Agent NanoDrop" in response.read().decode("utf-8", "replace")
                        if ok: break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.5)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    RESULTS.append({"label": "gui-loopback", "exit_code": 0 if ok else 1})
    print("gui-loopback", ok, flush=True)
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print("Evidence:", OUT, flush=True)
    return 0 if all(row["exit_code"] == 0 for row in RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
