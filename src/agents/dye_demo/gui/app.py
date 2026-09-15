"""Minimal NiceGUI page for the simulation-only dye demo."""
from __future__ import annotations

import base64
from typing import Any

from nicegui import events, ui

from src.agents.dye_demo import render
from src.agents.dye_demo.gui.adapter import DemoGuiAdapter


def build_page(adapter: DemoGuiAdapter) -> None:
    """Build one browser page. Business logic remains in ``DemoSession``."""
    adapter.start()
    ui.colors(primary="#315c4d", secondary="#64748b", accent="#b56a35")
    ui.add_css("""
        body { background: #f5f7f6; color: #1f2937; }
        .plan-card { min-height: 180px; }
        .plan-grid { display:grid; grid-template-columns:minmax(150px, .8fr) minmax(180px, 1.2fr); gap:4px 14px; }
        .plan-label { color:#64748b; }
        .chat-scroll { height: 520px; }
    """)

    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("OT-2 Dye Dilution & Paper Printing").classes("text-2xl font-semibold")
            status_badge = ui.badge("READY", color="primary").classes("text-sm px-3 py-2")
        ui.label("Simulation-only prototype · changes require an explicit proposal approval").classes("text-slate-500")

        with ui.row().classes("w-full items-stretch gap-4"):
            current_box = ui.card().classes("plan-card grow basis-1/2 p-5")
            with ui.card().classes("grow basis-1/2 p-5"):
                ui.label("AI CHAT").classes("text-lg font-semibold")
                chat_box = ui.column().classes("w-full chat-scroll overflow-auto gap-2")
                with ui.row().classes("w-full items-end"):
                    chat_input = ui.input(placeholder="Type a natural-language instruction…").classes("grow")
                    send_button = ui.button("Send")

        proposed_box = ui.card().classes("w-full p-5")

        with ui.card().classes("w-full p-5"):
            ui.label("GUI PARAMETER CONTROLS").classes("text-lg font-semibold")
            ui.label("Optional structured input; Submit creates a proposal and does not mutate the Current Plan.").classes(
                "text-sm text-slate-500")
            controls = _controls(adapter.snapshot().current)
            submit_controls = ui.button("Submit as proposal", icon="tune")

        ui.label("LABWARE / REFERENCE IMAGES").classes("text-lg font-semibold")
        with ui.row().classes("w-full gap-4"):
            for title in ("96-WELL PLATE", "PAPER SUBSTRATE", "8-VIAL RACK"):
                _reference_card(title)

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
            _plan(snapshot.current_sections)
            if snapshot.validation != "All plan checks passed.":
                ui.label(snapshot.validation).classes("w-full whitespace-pre-wrap bg-amber-50 text-amber-900 p-3 rounded")

    @ui.refreshable
    def render_proposed() -> None:
        snapshot = adapter.snapshot()
        proposed_box.clear()
        with proposed_box:
            if snapshot.proposed is None:
                ui.label("PROPOSED PLAN").classes("text-lg font-semibold")
                ui.label("No proposal is waiting.").classes("text-slate-500")
            else:
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
                ui.label(label).classes("plan-label")
                ui.label(value)


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
        plate = ui.select(options, value=deck["plate"]["slot"], label="Dilution plate slot")
        paper = ui.select(options, value=deck["paper"]["slot"], label="Paper plate slot")
        tuberack = ui.select(options, value=deck["tuberack"]["slot"], label="Vial rack slot")
        tiprack = ui.select(options, value=deck["tiprack"]["slot"], label="Tip rack slot")
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


def _reference_card(title: str) -> None:
    with ui.card().classes("grow basis-1/3 p-4"):
        ui.label(title).classes("font-semibold")
        image_box = ui.column().classes("w-full h-52 items-center justify-center bg-slate-100 rounded")
        with image_box:
            ui.icon("image", size="4rem").classes("text-slate-300")
            ui.label("No image uploaded").classes("text-slate-400")

        async def uploaded(event: events.UploadEventArguments) -> None:
            content_type = event.file.content_type.lower()
            if content_type not in {"image/png", "image/jpeg"}:
                ui.notify("Please choose a PNG or JPG image.", type="negative")
                return
            data = await event.file.read()
            encoded = base64.b64encode(data).decode("ascii")
            image_box.clear()
            with image_box:
                ui.image(f"data:{content_type};base64,{encoded}").classes("max-h-52 object-contain")
            ui.notify(f"{title.title()} reference updated.", type="positive")

        ui.upload(label="Upload or replace PNG/JPG", on_upload=uploaded, auto_upload=True,
                  max_file_size=5_000_000).props('accept=".png,.jpg,.jpeg,image/png,image/jpeg"').classes("w-full")
