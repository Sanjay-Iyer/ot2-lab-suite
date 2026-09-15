#!/usr/bin/env python3
"""Launch the simulation-only NiceGUI proof of concept for the OT-2 dye demo."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", help="Required: run locally without contacting a robot.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(REPO)))
    parser.add_argument("--operator", default="GUI Operator")
    parser.add_argument("--session-label", default="GUI Prototype")
    parser.add_argument("--offline", action="store_true", help="Launch without an LLM; plan controls still work.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not args.simulate:
        parser.error("this home-laptop GUI is simulation-only; pass --simulate")
    source = Path(args.config)
    source = source if source.is_absolute() else REPO / source
    if not source.is_file():
        parser.error(f"starting config not found: {source}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    settings = SessionSettings(
        simulate=True,
        config_source=source,
        working_config=REPO / "configs" / "workflows" / "user" / f"ai_dye_demo_gui_{stamp}.yaml",
        run_dir=REPO / "runs" / "ai_dye_demo_gui" / stamp,
        session_label=f"{datetime.now():%Y-%m-%d} {args.session_label}",
        operator=args.operator,
        llm_description="" if args.offline else Config.describe_llm_auth(),
    )
    llm = None if args.offline else LLMClient(lambda: Config.get_llm(temperature=0))
    session = DemoSession(settings, load_config(source), llm=llm, executor=SubprocessExecutor())
    adapter = DemoGuiAdapter(session)
    build_page(adapter)
    app.on_shutdown(adapter.stop)
    ui.run(host=args.host, port=args.port, show=not args.no_browser, title="OT-2 Dye Demo", reload=False)
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    # NiceGUI re-executes the script to build its auto-index page. Raising
    # SystemExit here converts that successful page build into an HTTP 404.
    main()
