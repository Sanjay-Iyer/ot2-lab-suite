#!/usr/bin/env python3
"""NiceGUI page for the OT-2 dye demo: build the experiment in the browser, then run it on the OT-2.

    python scripts/ai_dye_demo_gui.py              # work laptop: Run on OT-2 runs the real robot
    python scripts/ai_dye_demo_gui.py --simulate   # any laptop: the run button only builds and simulates

Launching never contacts the robot. The OT-2 is found and checked only when Run on OT-2 is pressed. The run then takes
the terminal demo's robot path (scripts/run_vial_print_robot.py: build + simulate, upload over the HTTP API, start,
monitor), and the page's Stop button stops it the way Ctrl-C does in the terminal.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nicegui import app, ui  # noqa: E402

from src.agents.dye_demo.gui.adapter import DemoGuiAdapter  # noqa: E402
from src.agents.dye_demo.gui.app import build_page  # noqa: E402
from src.agents.dye_demo.llm import LLMClient  # noqa: E402
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config  # noqa: E402
from src.agents.dye_demo.session import DemoSession, SessionSettings, SubprocessExecutor  # noqa: E402
from src.core.config import Config  # noqa: E402

RUN_ON_OT2 = "Run on OT-2"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearsal without a robot: the run button builds and simulates only.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(REPO)),
                        help="Starting YAML; a timestamped working copy is created.")
    parser.add_argument("--operator", default=None,
                        help="Who is running the experiment (otherwise asked in the chat).")
    parser.add_argument("--session-label", default="NiceGUI",
                        help='Session name recorded after the date, e.g. "Demo 2".')
    parser.add_argument("--robot-host", default=None,
                        help="Override the OT-2 host resolved from configs/robot.yaml (used only when the run starts).")
    parser.add_argument("--offline", action="store_true",
                        help="Simulation rehearsal without an LLM: the controls work, plain-language requests do not.")
    parser.add_argument("--host", default="127.0.0.1", help="Address the page is served on.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    if args.offline and not args.simulate:
        print("--offline is only for --simulate rehearsals; a live session needs the LLM.", file=sys.stderr)
        return 2
    if not args.simulate and args.host not in LOCAL_HOSTS:
        print("A live session serves its page to this laptop only; leave out --host.", file=sys.stderr)
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
        working_config=REPO / "configs" / "workflows" / "user" / f"ai_dye_demo_gui_{stamp}.yaml",
        run_dir=REPO / "runs" / "ai_dye_demo_gui" / stamp,
        session_label=f"{datetime.now():%Y-%m-%d} {args.session_label}",
        operator=args.operator,
        llm_description="" if args.offline else Config.describe_llm_auth(),
        run_button="Simulate" if args.simulate else RUN_ON_OT2,
    )
    llm = None if args.offline else LLMClient(lambda: Config.get_llm(temperature=0))
    executor = SubprocessExecutor(robot_host=args.robot_host)
    session = DemoSession(settings, load_config(source), llm=llm, executor=executor)
    adapter = DemoGuiAdapter(session)

    def emit(line: str) -> None:
        print(line, flush=True)             # this window keeps a copy of the build and robot runner output
        adapter.add_output(line)

    executor.emit = emit
    app.on_shutdown(adapter.stop)
    # One session for the whole server: every browser page shows it (the page function runs per page load).
    ui.run(lambda: build_page(adapter), host=args.host, port=args.port, show=not args.no_browser,
           title="OT-2 Dye Demo", reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
