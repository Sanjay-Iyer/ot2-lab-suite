#!/usr/bin/env python3
"""Conversational OT-2 demo: dilute a dye series, then print it onto paper.

    python scripts/ai_dye_demo.py --simulate  # local only, no robot contact
    python scripts/ai_dye_demo.py             # the real robot

At start the program builds the LLM client and gets READY back from a tiny
startup request before anything else, then asks who is running the experiment.
After that it takes plain-language requests. Every change is proposed, checked by
deterministic Python, shown together with what does NOT change (and the current
vs proposed deck when labware moves), and applied only after the operator types
yes. `/ask <question>` answers without changing anything.

Robot Python stays deterministic: the LLM never writes code and never touches the
pipette, the calibrated liquid handling or the safety limits. The workflow is
protocol v19 (src/protocols/printing/13_ai_agent_dilution_print_demo.py); the
conversation lives in src/agents/dye_demo/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.agents.dye_demo.llm import LLMClient  # noqa: E402
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config  # noqa: E402
from src.agents.dye_demo.session import DemoSession, SessionSettings, SubprocessExecutor  # noqa: E402
from src.core.config import Config  # noqa: E402

USER_CONFIG_DIR = REPO / "configs" / "workflows" / "user"
RUN_ROOT = REPO / "runs" / "ai_dye_demo"


def session_label(label: str | None, *, today: datetime | None = None, run_root: Path = RUN_ROOT) -> str:
    """'2026-09-03 Demo 1' when a label is given, else '2026-09-03 Session N'."""
    today = today or datetime.now()
    date = today.strftime("%Y-%m-%d")
    if label and label.strip():
        return f"{date} {label.strip()}"
    stamp = today.strftime("%Y%m%d")
    count = sum(1 for path in run_root.glob(f"{stamp}_*") if path.is_dir()) if run_root.exists() else 0
    return f"{date} Session {count + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simulate", action="store_true",
                        help="Local only; never discovers or contacts a robot.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(REPO)),
                        help="Starting YAML; a timestamped working copy is created.")
    parser.add_argument("--operator", default=None,
                        help="Who is running the experiment (skips the startup question).")
    parser.add_argument("--session-label", default=None,
                        help='Session name recorded after the date, e.g. "Demo 1".')
    parser.add_argument("--request", default=None,
                        help="Optional first natural-language request.")
    parser.add_argument("--robot-host", default=None,
                        help="Override the OT-2 host resolved from configs/robot.yaml.")
    parser.add_argument("--offline", action="store_true",
                        help="Simulation rehearsal without an LLM: commands work, changes and /ask do not.")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    if args.offline and not args.simulate:
        print("--offline is only for --simulate rehearsals; a live session needs the LLM.", file=sys.stderr)
        return 2
    source = Path(args.config)
    source = source if source.is_absolute() else REPO / source
    if not source.is_file():
        print(f"starting config not found: {source}", file=sys.stderr)
        return 2
    if not args.simulate:
        auth_error = Config.live_robot_llm_auth_error()
        if auth_error:
            print(auth_error, file=sys.stderr)
            return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    settings = SessionSettings(
        simulate=args.simulate,
        config_source=source,
        working_config=USER_CONFIG_DIR / f"ai_dye_demo_{stamp}.yaml",
        run_dir=RUN_ROOT / stamp,
        session_label=session_label(args.session_label),
        operator=args.operator,
        first_request=args.request,
        llm_description="" if args.offline else Config.describe_llm_auth(),
    )
    llm = None if args.offline else LLMClient(lambda: Config.get_llm(temperature=0))
    session = DemoSession(settings, load_config(source), llm=llm,
                          executor=SubprocessExecutor(robot_host=args.robot_host))
    return session.run()


if __name__ == "__main__":
    raise SystemExit(main())
