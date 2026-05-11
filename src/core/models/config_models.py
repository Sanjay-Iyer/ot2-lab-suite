from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any

# --- Workflow Config Models ---

class LabwareRoleConfig(BaseModel):
    name: str
    slot: int

class WorkflowLabwareConfig(BaseModel):
    source: LabwareRoleConfig
    dilution: Optional[LabwareRoleConfig] = None
    destination: LabwareRoleConfig
    tiprack: Optional[LabwareRoleConfig] = None

class DilutionParameters(BaseModel):
    steps: int = Field(gt=0)
    factors: List[float]
    final_volume_ul: float = Field(gt=0)
    replicates: int = Field(ge=1)

class PipetteSelection(BaseModel):
    name: str
    mount: str
    allow_split_transfers: bool = True

class SimulationConfig(BaseModel):
    local_only: bool = True
    connect_to_robot: bool = False

class DilutionWorkflowConfig(BaseModel):
    workflow_type: str = "dilution"
    description: Optional[str] = None
    labware: WorkflowLabwareConfig
    dilution: DilutionParameters
    pipette: PipetteSelection
    simulation: SimulationConfig

    def to_base_config(self) -> Dict[str, Any]:
        """Converts this nested config to the flatter BaseWorkflowConfig format for protocol generators."""
        return {
            "workflow": "dilution",
            "apiLevel": "2.15",
            "simulate": self.simulation.local_only,
            "metadata": {"protocolName": "Automated Dilution"},
            "deck": {
                "labware": [
                    {"slot": self.labware.source.slot, "type": self.labware.source.name, "name": "source_plate"},
                    {"slot": self.labware.dilution.slot, "type": self.labware.dilution.name, "name": "dilution_plate"},
                    {"slot": self.labware.destination.slot, "type": self.labware.destination.name, "name": "destination_plate"},
                    {"slot": self.labware.tiprack.slot, "type": self.labware.tiprack.name, "name": "tip_rack"} if self.labware.tiprack else None
                ],
                "pipettes": [
                    {
                        "mount": self.pipette.mount, 
                        "type": self.pipette.name, 
                        "tip_rack_slots": [self.labware.tiprack.slot]
                    } if self.labware.tiprack else None
                ],
                "modules": []
            },
            "dilution_steps": self.dilution.steps,
            "dilution_factors": self.dilution.factors,
            "final_volume": self.dilution.final_volume_ul,
            "replicates": self.dilution.replicates
        }

class ProcessParams(BaseModel):
    reagent_source: str
    reagent_concentration: float
    concentration_unit: str = "ug/mL"
    dilution_factors: List[float]
    target_final_volume_ul: float
    mix_after: bool = True
    mix_volume_ul: float
    mix_count: int

class PrintingParams(BaseModel):
    total_print_positions: int
    volume_per_print_ul: float
    replicates_per_position: int = 1
    starting_well: str = "A1"

class PrintingLabwareConfig(BaseModel):
    source: LabwareRoleConfig
    reservoir: LabwareRoleConfig
    destination: LabwareRoleConfig
    tiprack: LabwareRoleConfig

class PrintingWorkflowConfig(BaseModel):
    workflow_type: str = "printing"
    description: Optional[str] = None
    labware: PrintingLabwareConfig
    process: ProcessParams
    printing: PrintingParams
    pipette: PipetteSelection
    simulation: SimulationConfig

    def to_base_config(self) -> Dict[str, Any]:
        """Converts this nested config to the flatter BaseWorkflowConfig format."""
        return {
            "workflow": "printing",
            "apiLevel": "2.15",
            "simulate": self.simulation.local_only,
            "metadata": {"protocolName": "Automated Printing"},
            "deck": {
                "labware": [
                    {"slot": self.labware.source.slot, "type": self.labware.source.name, "name": "stock_reagent_rack"},
                    {"slot": self.labware.reservoir.slot, "type": self.labware.reservoir.name, "name": "solvent_reservoir"},
                    {"slot": self.labware.destination.slot, "type": self.labware.destination.name, "name": "printer_tray"},
                    {"slot": self.labware.tiprack.slot, "type": self.labware.tiprack.name, "name": "tip_rack"}
                ],
                "pipettes": [
                    {
                        "mount": self.pipette.mount, 
                        "type": self.pipette.name, 
                        "tip_rack_slots": [self.labware.tiprack.slot]
                    }
                ],
                "modules": []
            },
            "reagent_source": "stock_reagent_rack",
            "total_print_positions": self.printing.total_print_positions,
            "target_final_volume_ul": self.process.target_final_volume_ul,
            "volume_per_print_ul": self.printing.volume_per_print_ul
        }

# --- Constraint Config Models ---

class LabwareConstraint(BaseModel):
    display_name: str
    format: str
    max_well_volume_ul: float = Field(gt=0)
    recommended_max_working_volume_ul: Optional[float] = None
    min_working_volume_ul: Optional[float] = None
    compatible_workflows: List[str] = []
    is_tip_rack: bool = False
    notes: Optional[str] = None

class PipetteConstraint(BaseModel):
    min_volume_ul: float = Field(gt=0)
    max_volume_ul: float = Field(gt=0)
    recommended_min_volume_ul: float = Field(gt=0)
    recommended_max_volume_ul: float = Field(gt=0)
    mounts_allowed: List[str]

class DeckConstraint(BaseModel):
    valid_slots: List[int]
    reserved_slots: List[int] = []
    allowed_mounts: List[str]

class WorkflowConstraint(BaseModel):
    required_labware_roles: List[str]
    require_unique_slots: bool = True
    allow_robot_connection: bool = False
    default_simulation_only: bool = True

# --- Validation Result Models ---

class ValidationIssue(BaseModel):
    severity: str  # "error" or "warning"
    field: str
    message: str
    suggested_fix: Optional[str] = None

class ValidationResult(BaseModel):
    valid: bool
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
