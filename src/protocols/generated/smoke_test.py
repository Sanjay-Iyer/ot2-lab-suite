from opentrons import protocol_api

metadata = {
    "protocolName": "OT-2 Smoke Test - Home Only",
    "author": "Sanjay / ot2-lab-suite",
    "description": "Minimal smoke test that homes the OT-2 and exits.",
}

requirements = {"robotType": "OT-2", "apiLevel": "2.13"}

def run(protocol: protocol_api.ProtocolContext):
    print("DEBUG: smoke_test.py entered run()")
    print("DEBUG: homing robot now")
    protocol.home()
    print("DEBUG: robot home complete")
    print("DEBUG: smoke test finished successfully")
