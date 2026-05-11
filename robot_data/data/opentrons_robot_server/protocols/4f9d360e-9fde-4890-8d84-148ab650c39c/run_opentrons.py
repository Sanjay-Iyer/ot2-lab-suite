from opentrons import protocol_api
import opentrons.execute
import sys, copy, os
os.environ["RUNNING_ON_PI"] = '1'
try:
    sample_dictionary = sys.argv[0]
except:
    print("No samples requested, loading default sample volumes")
sample_dictionary={'volume_a':[100,75,50,  25,  0,  0,  0, 0, 0],
                    'volume_b':[0,25,50, 75, 100, 75,  50, 25, 0],
                    'volume_c':[0, 0, 0,  0,   0, 25,  50, 75,100]}


metadata = {
    'apiLevel': '2.15',
    'protocolName': 'Mix next samples',
    'description': '''This protocol is to take a dictionary of measurement points and mix the samples''',
    'author': 'Devin Ryan'
    }

def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware(load_name='opentrons_96_tiprack_300ul', location=11)
    reservoir = protocol.load_labware(load_name='devin_3_reservoir_36480ul', location=8)
    #reservoir_b = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=5)
    #reservoir_c = protocol.load_labware(load_name='nest_12_reservoir_15ml', location=8)
    plate = protocol.load_labware(load_name='devin_96_wellplate_112ul', location=2)

    #Load pipettes, identify tip supply list
    left_pipette = protocol.load_instrument('p300_multi_gen2', 'right', tip_racks=[tips])


    left_pipette.distribute(volume=sample_dictionary['volume_a'], source=reservoir.wells('A1'), dest=plate.wells('A1','A2','A3','A4','A5','A6','A7','A8','A9'))

    left_pipette.distribute(volume=sample_dictionary['volume_b'], source=reservoir.wells('A2'), dest=plate.wells('A1','A2','A3','A4','A5','A6','A7','A8','A9'))

    left_pipette.distribute(volume=sample_dictionary['volume_c'], source=reservoir.wells('A3'), dest=plate.wells('A1','A2','A3','A4','A5','A6','A7','A8','A9'))


                        
    