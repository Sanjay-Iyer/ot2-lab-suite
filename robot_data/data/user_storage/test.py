from opentrons import protocol_api

# Metadata is required by the robot to know how to read the script
metadata = {
    'protocolName': 'Hello World OT-2',
    'author': 'Your Name',
    'description': 'A simple script to test SSH connectivity and hardware execution.',
    'apiLevel': '2.14' # Tells the robot which version of the Python API to use
}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Print a message to your SSH terminal
    print("==================================================")
    print("HELLO WORLD! The OT-2 is connected and listening!")
    print("==================================================")

    # 2. Blink the robot's rail lights to give you a physical visual sign
    print("Blinking lights...")
    protocol.set_rail_lights(False)
    protocol.delay(seconds=1)
    protocol.set_rail_lights(True)
    protocol.delay(seconds=1)
    protocol.set_rail_lights(False)
    protocol.delay(seconds=1)
    protocol.set_rail_lights(True)

    # 3. Home the robot (moves the gantry safely to the back right corner)
    print("Homing the robot motors...")
    protocol.home()

    print("Test complete! Your setup is working perfectly.")