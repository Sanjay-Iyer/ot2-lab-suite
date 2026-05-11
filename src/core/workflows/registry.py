import os
import yaml
from pathlib import Path
from typing import Dict, Any, Type, Callable, Optional
from dataclasses import dataclass
from pydantic import BaseModel
from src.utils.paths import DEFAULT_WORKFLOW_CONFIG_DIR as DEFAULT_CONFIG_DIR, validate_path_exists

@dataclass
class WorkflowEntry:
    schema: Type[BaseModel]
    default_config_path: Path
    protocol_generator: Callable[[Any], str]
    description: str

# This will be populated as we implement workflows
WORKFLOWS: Dict[str, WorkflowEntry] = {}

def register_workflow(
    name: str,
    schema: Type[BaseModel],
    default_config_path: Path,
    protocol_generator: Callable[[Any], str],
    description: str
):
    WORKFLOWS[name] = WorkflowEntry(
        schema=schema,
        default_config_path=default_config_path,
        protocol_generator=protocol_generator,
        description=description
    )

def load_workflow_config(name: str, config_path: Optional[Path] = None) -> BaseModel:
    """
    Resolve the workflow's schema, load the config file, validate, 
    and return the typed config object.
    """
    if name not in WORKFLOWS:
        raise ValueError(f"Workflow '{name}' not found in registry. Available: {list(WORKFLOWS.keys())}")
    
    entry = WORKFLOWS[name]
    path = config_path or entry.default_config_path
    
    # Use user-friendly validation
    validate_path_exists(path, f"Configuration for workflow '{name}'")
        
    with open(path, "r") as f:
        config_data = yaml.safe_load(f)
        
    # Pydantic validation
    return entry.schema(**config_data)

# --- Register Workflows ---
from src.core.models.config_models import DilutionWorkflowConfig, PrintingWorkflowConfig
from src.protocols.printing import generate_printing_protocol
from src.protocols.dilution import generate_dilution_protocol

register_workflow(
    name="dilution",
    schema=DilutionWorkflowConfig,
    default_config_path=DEFAULT_CONFIG_DIR / "dilution.yaml",
    protocol_generator=generate_dilution_protocol,
    description="Multi-step dilution series workflow"
)

register_workflow(
    name="printing",
    schema=PrintingWorkflowConfig,
    default_config_path=DEFAULT_CONFIG_DIR / "printing.yaml",
    protocol_generator=generate_printing_protocol,
    description="Unified nanoparticle printing workflow (Dilution + Mixing + Printing)"
)

def stub_generator(config):
    raise NotImplementedError("This workflow generator is not yet implemented.")

# Stubs for deferred workflows
register_workflow(
    name="austar",
    schema=BaseModel, # Placeholder
    default_config_path=DEFAULT_CONFIG_DIR / "austar.yaml",
    protocol_generator=stub_generator,
    description="AuStar synthesis (Deferred)"
)

register_workflow(
    name="cleanup",
    schema=BaseModel, # Placeholder
    default_config_path=DEFAULT_CONFIG_DIR / "cleanup.yaml",
    protocol_generator=stub_generator,
    description="System cleanup (Deferred)"
)
