from typing import Dict, Any, List, Optional
from src.core.models.config_models import (
    ValidationResult, 
    ValidationIssue, 
    DilutionWorkflowConfig,
    PrintingWorkflowConfig,
    LabwareConstraint,
    PipetteConstraint,
    DeckConstraint,
    WorkflowConstraint
)
from src.core.constraints_manager import get_constraints

def validate_workflow_against_constraints(workflow_config_dict: dict) -> ValidationResult:
    """
    Validates a workflow configuration against the system constraints.
    Returns a ValidationResult object with any errors or warnings found.
    """
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    
    constraints = get_constraints()
    
    # 1. Parse workflow config to ensure basic structure
    wf_type = workflow_config_dict.get("workflow_type")
    try:
        if wf_type == "dilution":
            wf = DilutionWorkflowConfig(**workflow_config_dict)
            final_vol = wf.dilution.final_volume_ul
        elif wf_type == "printing":
            wf = PrintingWorkflowConfig(**workflow_config_dict)
            final_vol = wf.process.target_final_volume_ul
        else:
            return ValidationResult(
                valid=False, 
                errors=[ValidationIssue(severity="error", field="workflow_type", message=f"Unsupported workflow type: {wf_type}")]
            )
    except Exception as e:
        return ValidationResult(
            valid=False, 
            errors=[ValidationIssue(severity="error", field="root", message=f"Pydantic parsing error: {str(e)}")]
        )

    # Load constraint data into models
    all_lw_constraints = {k: LabwareConstraint(**v) for k, v in constraints.get("labware", {}).items()}
    all_p_constraints = {k: PipetteConstraint(**v) for k, v in constraints.get("pipettes", {}).items()}
    deck_constraints = DeckConstraint(**constraints.get("deck", {}).get("ot2", {}))
    wf_constraints_dict = constraints.get("workflows", {}).get(wf_type, {})
    
    if not wf_constraints_dict:
        errors.append(ValidationIssue(severity="error", field="workflow_type", message=f"Workflow type '{wf_type}' has no defined constraints."))
        return ValidationResult(valid=False, errors=errors)
    
    wf_constraints = WorkflowConstraint(**wf_constraints_dict)

    # 2. Labware Specific Checks
    slots_used = []
    # Identify all labware roles defined in the config's labware section
    labware_section = wf.labware.model_dump()
    
    for role, entry_dict in labware_section.items():
        if not entry_dict: continue
        
        lw_name = entry_dict["name"]
        slot = entry_dict["slot"]
        
        # Exists in constraints
        if lw_name not in all_lw_constraints:
            errors.append(ValidationIssue(severity="error", field=f"labware.{role}", message=f"Labware '{lw_name}' is not defined in system constraints."))
            continue
            
        spec = all_lw_constraints[lw_name]
        
        # Compatibility (Skip for tip racks)
        if not spec.is_tip_rack:
            if wf_type not in spec.compatible_workflows:
                errors.append(ValidationIssue(severity="error", field=f"labware.{role}", message=f"Labware '{lw_name}' is not compatible with '{wf_type}' workflow."))

        # Slot Validity
        if slot not in deck_constraints.valid_slots:
            errors.append(ValidationIssue(severity="error", field=f"labware.{role}.slot", message=f"Slot {slot} is not a valid OT-2 deck slot."))
        slots_used.append(slot)

        # Volume Checks (Destination primarily)
        if role == "destination":
            if final_vol > spec.max_well_volume_ul:
                errors.append(ValidationIssue(
                    severity="error", 
                    field="final_volume", 
                    message=f"{final_vol} µL exceeds the maximum well volume of {lw_name}, which is {spec.max_well_volume_ul} µL.",
                    suggested_fix=f"Use a deep-well plate such as nest_96_wellplate_2ml_deep, reduce the volume, or choose a compatible labware definition."
                ))
            elif spec.recommended_max_working_volume_ul and final_vol > spec.recommended_max_working_volume_ul:
                warnings.append(ValidationIssue(
                    severity="warning", 
                    field="final_volume", 
                    message=f"{final_vol} µL exceeds the recommended working volume of {lw_name} ({spec.recommended_max_working_volume_ul} µL)."
                ))

    # Unique Slots
    if wf_constraints.require_unique_slots and len(slots_used) != len(set(slots_used)):
        errors.append(ValidationIssue(severity="error", field="labware.slots", message=f"Duplicate deck slot assignments found: {slots_used}"))

    # 3. Pipette Checks
    p_name = wf.pipette.name
    if p_name not in all_p_constraints:
        errors.append(ValidationIssue(severity="error", field="pipette.name", message=f"Pipette '{p_name}' is not defined in system constraints."))
    else:
        p_spec = all_p_constraints[p_name]
        
        if wf.pipette.mount not in p_spec.mounts_allowed:
            errors.append(ValidationIssue(severity="error", field="pipette.mount", message=f"Mount '{wf.pipette.mount}' is not allowed for pipette '{p_name}'."))

        # Volume thresholds
        if final_vol > p_spec.max_volume_ul:
            if wf.pipette.allow_split_transfers:
                warnings.append(ValidationIssue(
                    severity="warning", 
                    field="final_volume", 
                    message=f"{final_vol} µL exceeds the {p_name} maximum of {p_spec.max_volume_ul} µL. Split transfers are enabled, so this will be divided into multiple pipetting actions."
                ))
            else:
                errors.append(ValidationIssue(
                    severity="error", 
                    field="final_volume", 
                    message=f"{final_vol} µL exceeds the {p_name} maximum of {p_spec.max_volume_ul} µL and split transfers are disabled."
                ))
        
        if final_vol < p_spec.min_volume_ul:
            errors.append(ValidationIssue(severity="error", field="final_volume", message=f"{final_vol} µL is below the minimum volume of {p_name} ({p_spec.min_volume_ul} µL)."))

    # 4. Simulation & Connection
    if wf.simulation.connect_to_robot and not wf_constraints.allow_robot_connection:
        errors.append(ValidationIssue(severity="error", field="simulation.connect_to_robot", message="Physical robot connection is disallowed for this workflow by system constraints."))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )
