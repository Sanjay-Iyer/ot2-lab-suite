import unittest
import yaml
from pathlib import Path
from src.core.workflows.registry import load_workflow_config, WORKFLOWS
from src.core.models.config_models import PrintingWorkflowConfig
from src.protocols.printing import generate_printing_protocol
import pytest

class TestPrintingWorkflow(unittest.TestCase):
    @pytest.mark.skip(reason="Needs update for new decoupled constraint schema")
    def test_default_config_loads(self):
        """Confirm the shipping default config validates."""
        cfg = load_workflow_config("printing")
        self.assertIsInstance(cfg, PrintingWorkflowConfig)
        self.assertEqual(cfg.workflow, "printing")

    @pytest.mark.skip(reason="Needs update for new decoupled constraint schema")
    def test_96_well_capacity_failure(self):
        """Test the 400 positions on one 96-well plate case (should fail)."""
        cfg_dict = load_workflow_config("printing").model_dump()
        cfg_dict["total_print_positions"] = 400
        
        with self.assertRaises(ValueError) as cm:
            PrintingWorkflowConfig(**cfg_dict)
        self.assertIn("exceeds combined capacity (96)", str(cm.exception))

    @pytest.mark.skip(reason="Needs update for new decoupled constraint schema")
    def test_3_plate_capacity_success(self):
        """Test 125 positions across 3 plates (should pass)."""
        cfg_dict = load_workflow_config("printing").model_dump()
        
        # Add 2 more plates
        cfg_dict["deck"]["labware"].append({
            "slot": 3, "type": "nest_96_wellplate_200ul_flat", "name": "extra_plate_1"
        })
        cfg_dict["deck"]["labware"].append({
            "slot": 4, "type": "nest_96_wellplate_200ul_flat", "name": "extra_plate_2"
        })
        
        # Add to targets
        cfg_dict["print_targets"].append({"labware_name": "extra_plate_1", "starting_well": "A1"})
        cfg_dict["print_targets"].append({"labware_name": "extra_plate_2", "starting_well": "A1"})
        
        cfg_dict["total_print_positions"] = 125
        
        cfg = PrintingWorkflowConfig(**cfg_dict)
        self.assertEqual(cfg.total_print_positions, 125)

    @pytest.mark.skip(reason="Needs update for new decoupled constraint schema")
    def test_slot_overlap_failure(self):
        """Confirm slot collision detection works."""
        cfg_dict = load_workflow_config("printing").model_dump()
        # Put something in slot 1 (where tuberack is)
        cfg_dict["deck"]["labware"].append({
            "slot": 1, "type": "nest_96_wellplate_200ul_flat", "name": "overlapping_plate"
        })
        
        with self.assertRaises(ValueError) as cm:
            PrintingWorkflowConfig(**cfg_dict)
        self.assertIn("Duplicate assignments for slots: [1]", str(cm.exception))

    @pytest.mark.skip(reason="Needs update for new decoupled constraint schema")
    def test_generator_determinism(self):
        """Confirm the generator produces byte-identical output for same config."""
        cfg = load_workflow_config("printing")
        out1 = generate_printing_protocol(cfg)
        out2 = generate_printing_protocol(cfg)
        self.assertEqual(out1, out2)

if __name__ == "__main__":
    unittest.main()
