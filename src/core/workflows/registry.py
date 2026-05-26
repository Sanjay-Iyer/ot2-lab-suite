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
from src.protocols.printing_demo_protocol import PrintingDemoConfig

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

def generate_printing_demo_protocol(config) -> str:
    """Reads printing_demo_protocol.py, injects the config dict, and returns it."""
    import json
    template_path = Path(__file__).resolve().parent.parent.parent / "protocols" / "printing_demo_protocol.py"
    with open(template_path, "r", encoding="utf-8") as f:
        code = f.read()
    
    config_dict = config.model_dump()
    config_json = json.dumps(config_dict, indent=2)
    return code.replace("CONFIG = None", f"CONFIG = {config_json}")

register_workflow(
    name="printing_demo",
    schema=PrintingDemoConfig,
    default_config_path=DEFAULT_CONFIG_DIR / "printing_demo.yaml",
    protocol_generator=generate_printing_demo_protocol,
    description="OT-2 Paper Printing Demo Workflow with Integrated Camera Capture"
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
