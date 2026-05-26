import pytest
import yaml
from pathlib import Path
from src.protocols.printing_demo_protocol import PrintingDemoConfig, is_valid_well

# Resolve paths
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "workflows" / "defaults" / "printing_demo.yaml"

def test_well_validator():
    """Verify is_valid_well utility function."""
    assert is_valid_well("A1") is True
    assert is_valid_well("H12") is True
    assert is_valid_well("A13") is False
    assert is_valid_well("I1") is False
    assert is_valid_well("a1") is False  # Row must be capitalized
    assert is_valid_well("A0") is False

def test_default_config_validates():
    """Verify shipping defaults are fully valid and pass Pydantic schema."""
    assert DEFAULT_CONFIG_PATH.exists()
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    
    config_obj = PrintingDemoConfig(**config_data)
    assert config_obj.demo_mode == "dilution_print"
    assert config_obj.plate.slot == 2
    assert config_obj.printing.paper_slot == 3

def test_overlap_validation_food_coloring_and_water():
    """Verify validation error raises when food coloring and water source wells overlap."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    
    # Induce overlap
    config_data["layout"]["food_coloring_source_wells"] = ["A1", "A2", "A3"]
    config_data["layout"]["water_source_wells"] = ["A3", "A4", "A5"] # Overlaps A3
    
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "overlap" in str(exc.value).lower()

def test_overlap_validation_dilution_and_stock():
    """Verify validation error raises when dilution destinations overlap source stock."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    
    # Induce overlap
    config_data["layout"]["food_coloring_source_wells"] = ["A1", "A2"]
    config_data["layout"]["dilution_destination_wells"] = ["A1", "B1"] # Overlaps A1
    
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "overlap" in str(exc.value).lower()

def test_invalid_well_format():
    """Verify validation fails if layout contains invalid well name formatting."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    config_data["layout"]["food_coloring_source_wells"] = ["A1", "Z1"] # Z1 is invalid row
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "invalid well format" in str(exc.value).lower()

def test_dilution_unprepared_destination_aspiration():
    """Verify error raises if we try to aspirate from a dilution destination well before it has been prepared."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    # Set step to aspirate from dilution_destination well B8, but B8 is only prepared in step 2 (index 1), 
    # but we attempt to use it as stock source in step 1 (index 0).
    config_data["dilution"]["steps"][0]["food_coloring_source_well"] = "B8" 
    
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "unprepared" in str(exc.value).lower()

def test_dispense_into_source_well():
    """Verify validation error raises if a dilution step attempts to dispense into a food coloring or water source well."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    # Attempt to dispense into stock source A1
    config_data["dilution"]["steps"][0]["destination_well"] = "A1"
    
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "dispense" in str(exc.value).lower()

def test_negative_volumes_fail():
    """Verify negative pipetting volumes trigger validation error."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    config_data["dilution"]["steps"][0]["water_volume_ul"] = -10.0
    with pytest.raises(ValueError):
        PrintingDemoConfig(**config_data)

def test_invalid_mode_fail():
    """Verify invalid demo_mode values trigger validation error."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    config_data["demo_mode"] = "invalid_print_mode"
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "invalid demo_mode" in str(exc.value).lower()

def test_generated_protocol_simulation_guard():
    """Verify that the generated protocol contains the simulation guard and the curl command."""
    # Read the main protocol file template
    template_path = Path(__file__).resolve().parent.parent / "src" / "protocols" / "printing_demo_protocol.py"
    assert template_path.exists()
    with open(template_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Verify that the template contains the simulation guard check
    assert "protocol.is_simulating()" in code
    assert "http://localhost:31950/camera/picture" in code
    
    # Verify capture_image function structure
    assert "def capture_image(" in code
    
    # Make sure we check that `is_simulating()` returns early before calling subprocess/curl
    lines = code.splitlines()
    capture_func_start = -1
    is_simulating_check = -1
    subprocess_call = -1
    for idx, line in enumerate(lines):
        if "def capture_image(" in line:
            capture_func_start = idx
        elif capture_func_start != -1:
            if "protocol.is_simulating()" in line:
                is_simulating_check = idx
            if "curl" in line and "subprocess" in line:
                subprocess_call = idx
            # If we hit the next main def block, stop searching
            if line.startswith("def ") and not line.startswith("def capture_image"):
                break
                
    assert capture_func_start != -1, "Could not find capture_image function in protocol"
    assert is_simulating_check != -1, "Could not find is_simulating check in capture_image"
    # If there is a subprocess call in capture_image, the simulation check must happen BEFORE it
    if subprocess_call != -1:
        assert is_simulating_check < subprocess_call, "is_simulating check must precede curl command"

def test_calibration_only_mode():
    """Verify that calibration_only mode shifts execution steps correctly."""
    from src.protocols.printing_demo_protocol import build_flat_step_log
    
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    config_data["printing"]["calibration_only"] = True
    config_obj = PrintingDemoConfig(**config_data)
    assert config_obj.printing.calibration_only is True
    
    steps = build_flat_step_log(config_data)
    
    # In calibration_only mode, we should NOT have pipetting steps or dilution steps in the flat log
    pipette_water_steps = [s for s in steps if s["action"] == "pipette_water"]
    pipette_stock_steps = [s for s in steps if s["action"] == "pipette_stock"]
    print_droplet_steps = [s for s in steps if s["action"] == "print_droplet"]
    calibrate_steps = [s for s in steps if s["action"] == "calibrate_coordinate"]
    
    assert len(pipette_water_steps) == 0
    assert len(pipette_stock_steps) == 0
    assert len(print_droplet_steps) == 0
    assert len(calibrate_steps) == len(config_data["printing"]["print_positions"])

def test_tip_strategy_validates_loaded_tip_count_gt_0():
    """Verify that loaded_tip_count must be >= 1."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    
    config_data["tip_strategy"]["loaded_tip_count"] = 0
    with pytest.raises(ValueError):
        PrintingDemoConfig(**config_data)

def test_tip_strategy_validates_starting_tip():
    """Verify starting_tip validates well format."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    config_data["tip_strategy"]["starting_tip"] = "Z1" # Invalid well
    with pytest.raises(ValueError) as exc:
        PrintingDemoConfig(**config_data)
    assert "starting_tip must be a valid well" in str(exc.value)
