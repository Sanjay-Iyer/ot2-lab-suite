"""Minimal NiceGUI page for the simulation-only dye demo."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import events, ui

from src.agents.dye_demo import render
from src.agents.dye_demo.columns import paper_columns_printed
from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
from src.agents.dye_demo.model import fmt_factor, format_slot, material_label, material_spec, slot_of
from src.agents.dye_demo.plan import build_plan

REPO = Path(__file__).resolve().parents[4]
DEFAULT_REFERENCE_IMAGES = {
    "96-WELL PLATE": REPO / "visualization" / "96_well_plate.jpg",
    "PAPER SUBSTRATE / HOLDER": REPO / "visualization" / "paper.jpg",
    "20 ML VIAL RACK": REPO / "visualization" / "20ml_vial_rack.jpg",
}
_PHYSICAL_LABELS = {
    "Dilution plate": "96-well plate",
    "Paper print plate": "Paper substrate / holder",
    "Vial rack": "20 mL vial rack",
}
CHAT_TITLE = "AI Agent Chatbox"
CHAT_HISTORY_HEIGHT_PX = 560


@dataclass(frozen=True)
class FlowStep:
    title: str
    source: str
    destination: str


def experiment_flow(config: dict[str, Any]) -> list[FlowStep]:
    """High-level material path derived from the same config and Plan as the detailed sections."""
    plan = build_plan(config)
    wells = " | ".join(well.well for well in plan.wells) or "none"
    factors = " | ".join(fmt_factor(well.factor) for well in plan.wells) or "none"
    steps: list[FlowStep] = []
    if plan.do_dilution:
        dye, water = material_spec(config, "sample"), material_spec(config, "solvent")
        sources = (f"{material_label(config, 'sample')} (vial {dye.get('vial')}) + "
                   f"{material_label(config, 'solvent')} (vial {water.get('vial')})")
        steps.append(FlowStep(
            "STEP 1 — PREPARE DILUTIONS",
            f"{sources} · 20 mL vial rack, {format_slot(slot_of(config, 'tuberack'))}",
            f"96-well plate, {format_slot(slot_of(config, 'plate'))} · wells {wells} · dilutions {factors}",
        ))
    if plan.do_print:
        columns = " | ".join(map(str, paper_columns_printed(config))) or "none"
        drops = int(config["print"].get("droplets_per_spot", 1))
        number = "drop" if drops == 1 else "drops"
        steps.append(FlowStep(
            f"STEP {len(steps) + 1} — PRINT",
            f"96-well plate, {format_slot(slot_of(config, 'plate'))} · dilution wells {wells}",
            f"Paper substrate / holder, {format_slot(slot_of(config, 'paper'))} · columns {columns} · "
            f"{drops} {number} per position",
        ))
    return steps


def build_page(adapter: DemoGuiAdapter) -> None:
    """Build one browser page. Business logic remains in ``DemoSession``."""
    adapter.start()
    ui.colors(primary="#315c4d", secondary="#64748b", accent="#b56a35")
    ui.add_css("""
        body { background: #f5f7f6; color: #1f2937; }
        .plan-card { min-height: 180px; }
        .plan-grid { display:grid; grid-template-columns:minmax(150px, .8fr) minmax(180px, 1.2fr); gap:4px 14px; }
        .plan-label { color:#64748b; }
        .chat-scroll { min-height: 0; }
        .section-chat { border: 3px solid #111827; border-radius: 10px; box-shadow: none; }
        .section-plan { border: 1px solid #5b8db8; box-shadow: none; }
        .section-tool { border: 1px solid #6f9b7a; box-shadow: none; }
        .flow-step { border-left: 3px solid #8ab1d2; background: #f8fbfe; }
    """)

    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("OT-2 Dye Dilution & Paper Printing").classes("text-2xl font-semibold")
            status_badge = ui.badge("READY", color="primary").classes("text-sm px-3 py-2")
        ui.label("Simulation-only prototype · changes require an explicit proposal approval").classes("text-slate-500")

        with ui.card().classes("section-chat w-full p-5"):
            ui.label(CHAT_TITLE).classes("text-xl font-semibold")
            ui.label("Describe the experiment or ask for a change in your own words.").classes(
                "text-sm text-slate-500")
            chat_box = ui.column().classes("w-full chat-scroll overflow-y-auto gap-2")
            chat_box.style(f"height: {CHAT_HISTORY_HEIGHT_PX}px; min-height: {CHAT_HISTORY_HEIGHT_PX}px")
            with ui.row().classes("w-full items-end"):
                chat_input = ui.input(placeholder="Tell the AI what you want to prepare or print…").classes("grow")
                send_button = ui.button("Send")

        current_box = ui.card().classes("section-plan plan-card w-full p-5")

        proposed_box = ui.card().classes("section-plan w-full p-5")

        with ui.card().classes("section-tool w-full p-5"):
            ui.label("GUI PARAMETER CONTROLS").classes("text-lg font-semibold")
            ui.label("Optional structured input; Submit creates a proposal and does not mutate the Current Plan.").classes(
                "text-sm text-slate-500")
            controls = _controls(adapter.snapshot().current)
            submit_controls = ui.button("Submit as proposal", icon="tune")

        with ui.card().classes("section-tool w-full p-5"):
            ui.label("LABWARE / REFERENCE IMAGES").classes("text-lg font-semibold")
            with ui.row().classes("w-full gap-4"):
                for title, image_path in DEFAULT_REFERENCE_IMAGES.items():
                    _reference_card(title, image_path)

        with ui.row().classes("w-full justify-center gap-3"):
            ui.button("Validate", icon="fact_check", on_click=adapter.validate).props("outline")
            ui.button("Simulate", icon="play_arrow", on_click=adapter.simulate)

    last = {"revision": -1, "proposal": object()}

    @ui.refreshable
    def render_current() -> None:
        snapshot = adapter.snapshot()
        current_box.clear()
        with current_box:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("CURRENT PLAN").classes("text-lg font-semibold")
                ui.badge(f"ACTIVE · revision {snapshot.revision}", color="positive")
            _experiment_flow(snapshot.current)
            _plan(snapshot.current_sections)
            if snapshot.validation != "All plan checks passed.":
                ui.label(snapshot.validation).classes("w-full whitespace-pre-wrap bg-amber-50 text-amber-900 p-3 rounded")

    @ui.refreshable
    def render_proposed() -> None:
        snapshot = adapter.snapshot()
        proposed_box.clear()
        proposed_box.set_visibility(snapshot.proposed is not None)
        with proposed_box:
            if snapshot.proposed is not None:
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(f"PROPOSED PLAN #{snapshot.proposal_id}").classes("text-lg font-semibold")
                    ui.badge("WAITING FOR APPROVAL", color="warning")
                _plan(snapshot.proposed_sections)
                if snapshot.proposal_attention:
                    with ui.card().classes("w-full bg-amber-50 text-amber-900"):
                        ui.label("ATTENTION").classes("font-semibold")
                        for item in snapshot.proposal_attention:
                            ui.label(item).classes("whitespace-pre-wrap")
                with ui.row().classes("w-full justify-center gap-3 mt-3"):
                    ui.button("Apply", icon="check", on_click=lambda: adapter.submit_text("yes"), color="positive")
                    ui.button("Discard", icon="close", on_click=lambda: adapter.submit_text("no"), color="negative").props(
                        "outline")

    def send() -> None:
        if adapter.submit_text(str(chat_input.value or "")):
            chat_input.value = ""

    send_button.on("click", send)
    chat_input.on("keydown.enter", send)
    def submit_form() -> None:
        try:
            adapter.propose_form(_control_values(controls))
        except (TypeError, ValueError):
            ui.notify("Dilution factors must be comma-separated numbers and numeric controls must be filled in.",
                      type="negative")

    submit_controls.on("click", submit_form)

    def refresh() -> None:
        for message in adapter.drain_messages():
            with chat_box:
                ui.chat_message(message.text, name="You" if message.role == "user" else "Assistant",
                                sent=message.role == "user")
        snapshot = adapter.snapshot()
        status_badge.text = snapshot.status
        if (snapshot.revision, snapshot.proposal_id) != (last["revision"], last["proposal"]):
            if snapshot.revision != last["revision"] and last["revision"] != -1:
                _sync_controls(controls, snapshot.current)
            last["revision"], last["proposal"] = snapshot.revision, snapshot.proposal_id
            render_current.refresh()
            render_proposed.refresh()

    render_current()
    render_proposed()
    ui.timer(0.25, refresh)


def _plan(sections: list[render.PlanSection]) -> None:
    for section in sections:
        expanded = section.title in {"DILUTIONS", "PRINTING", "DECK"}
        with ui.expansion(section.title, caption=section.status, value=expanded).classes("w-full"):
            _section(section)


def _section(section: render.PlanSection) -> None:
    with ui.element("div").classes("plan-grid w-full"):
        for item in section.items:
            if isinstance(item, render.Grid):
                for label, cells in item.rows:
                    ui.label(label).classes("plan-label")
                    ui.label(" | ".join(cells))
            else:
                label, value = item
                ui.label(_PHYSICAL_LABELS.get(label, label)).classes("plan-label")
                ui.label(value)


def _experiment_flow(config: dict[str, Any]) -> None:
    ui.label("EXPERIMENT FLOW · FROM → TO").classes("font-semibold text-blue-800 mt-2")
    steps = experiment_flow(config)
    if not steps:
        ui.label("No dilution or printing step is enabled.").classes("text-slate-500")
        return
    with ui.row().classes("w-full gap-3 items-stretch"):
        for step in steps:
            with ui.card().classes("flow-step grow basis-1/2 p-4 shadow-none"):
                ui.label(step.title).classes("font-semibold text-sm")
                ui.label("FROM").classes("text-xs font-semibold text-slate-500 mt-1")
                ui.label(step.source)
                ui.icon("south", size="1.15rem").classes("text-blue-600")
                ui.label("TO").classes("text-xs font-semibold text-slate-500")
                ui.label(step.destination)


def _controls(config: dict[str, Any]) -> dict[str, Any]:
    dilution, printing, deck = config["dilution"], config["print"], config["deck"]
    with ui.row().classes("w-full gap-4 items-end"):
        factors = ui.input("Dilution factors", value=", ".join(map(str, dilution["factors"])))
        dilution_enabled = ui.switch("Dilution enabled", value=dilution["enabled"])
        printing_enabled = ui.switch("Printing enabled", value=printing["enabled"])
        first_column = ui.number("First paper column", value=printing["paper_start_column"], min=1, max=12, step=1)
        replicates = ui.number("Replicate columns", value=printing["replicates"], min=1, max=12, step=1)
        drops = ui.number("Drops per position", value=printing["droplets_per_spot"], min=1, step=1)
    with ui.row().classes("w-full gap-4"):
        options = list(range(1, 12))
        plate = ui.select(options, value=deck["plate"]["slot"], label="96-well plate slot")
        paper = ui.select(options, value=deck["paper"]["slot"], label="Paper substrate / holder slot")
        tuberack = ui.select(options, value=deck["tuberack"]["slot"], label="20 mL vial rack slot")
        tiprack = ui.select(options, value=deck["tiprack"]["slot"], label="P20 tip rack slot")
    return {"factors": factors, "dilution": dilution_enabled, "printing": printing_enabled,
            "first_column": first_column, "replicates": replicates, "drops": drops,
            "plate": plate, "paper": paper, "tuberack": tuberack, "tiprack": tiprack}


def _control_values(controls: dict[str, Any]) -> dict[str, Any]:
    factors = [float(value.strip()) for value in str(controls["factors"].value).replace("×", "").replace("x", "").split(",")
               if value.strip()]
    factors = [int(value) if value.is_integer() else value for value in factors]
    return {
        "dilution.factors": factors,
        "dilution.enabled": bool(controls["dilution"].value),
        "print.enabled": bool(controls["printing"].value),
        "print.paper_start_column": int(controls["first_column"].value),
        "print.replicates": int(controls["replicates"].value),
        "print.droplets_per_spot": int(controls["drops"].value),
        "deck.plate.slot": int(controls["plate"].value),
        "deck.paper.slot": int(controls["paper"].value),
        "deck.tuberack.slot": int(controls["tuberack"].value),
        "deck.tiprack.slot": int(controls["tiprack"].value),
    }


def _sync_controls(controls: dict[str, Any], config: dict[str, Any]) -> None:
    dilution, printing, deck = config["dilution"], config["print"], config["deck"]
    values = {
        "factors": ", ".join(map(str, dilution["factors"])),
        "dilution": dilution["enabled"], "printing": printing["enabled"],
        "first_column": printing["paper_start_column"], "replicates": printing["replicates"],
        "drops": printing["droplets_per_spot"], "plate": deck["plate"]["slot"],
        "paper": deck["paper"]["slot"], "tuberack": deck["tuberack"]["slot"],
        "tiprack": deck["tiprack"]["slot"],
    }
    for name, value in values.items():
        controls[name].value = value


def _reference_card(title: str, default_image: Path) -> None:
    with ui.card().classes("grow basis-1/3 p-4 shadow-none border border-slate-200"):
        ui.label(title).classes("font-semibold")
        image_box = ui.column().classes("w-full h-52 items-center justify-center bg-slate-100 rounded")
        with image_box:
            ui.image(default_image).classes("w-full h-52 object-contain rounded")

        async def uploaded(event: events.UploadEventArguments) -> None:
            content_type = event.file.content_type.lower()
            if content_type not in {"image/png", "image/jpeg"}:
                ui.notify("Please choose a PNG or JPG image.", type="negative")
                return
            data = await event.file.read()
            image_box.clear()
            with image_box:
                ui.image(_image_data_uri(content_type, data)).classes("max-h-52 object-contain")
            ui.notify(f"{title.title()} reference updated.", type="positive")

        ui.upload(label="Upload or replace PNG/JPG", on_upload=uploaded, auto_upload=True,
                  max_file_size=5_000_000).props('accept=".png,.jpg,.jpeg,image/png,image/jpeg"').classes("w-full")


def _image_data_uri(content_type: str, data: bytes) -> str:
    """A session-only browser source used when a default reference is replaced."""
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
