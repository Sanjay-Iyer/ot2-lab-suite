"""raman_lib - reusable Raman spectrum processing library.

All analysis behaviour is driven by YAML config files (see ../../configs).
Nothing about which peaks to look for, thresholds, or preprocessing is
hardcoded here - the modules only implement the *mechanics*.
"""

__all__ = [
    "analysis_workflow",
    "config",
    "detection",
    "io_utils",
    "naming",
    "plotting",
    "preprocessing",
    "workflow_config",
]

__version__ = "0.2.0"
