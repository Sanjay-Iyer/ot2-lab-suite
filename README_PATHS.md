# Path Management System

All file paths in this project are derived from `PROJECT_ROOT`, which is discovered dynamically from the location of the source code.

## Central Utility: `src/utils/paths.py`

Use this module to access any directory or file within the project. Do **not** use hardcoded strings or absolute paths.

### Usage Example:
```python
from src.utils.paths import USER_CONFIG_DIR

config_path = USER_CONFIG_DIR / "my_config.yaml"
```

## Critical Paths:
- `CONFIG_DIR`: `configs/`
- `DEFAULT_CONFIG_DIR`: `configs/defaults/`
- `USER_CONFIG_DIR`: `configs/user/`
- `ROBOT_DATA_DIR`: `robot_data/`
- `LOG_DIR`: `robot_data/data/logs/`
- `AGENT_LOG_DIR`: `robot_data/data/logs/agents/`
- `PROTOCOL_DIR`: `src/protocols/`
- `GENERATED_PROTOCOL_DIR`: `src/protocols/generated/`

## Audit Tool: `scripts/audit_paths.py`
Run this tool to ensure no hardcoded paths are introduced:
```powershell
python -m scripts.audit_paths
```

## Portability:
The project is fully portable. It can be moved to any directory or operating system (Windows/Linux) and will resolve all paths correctly.
