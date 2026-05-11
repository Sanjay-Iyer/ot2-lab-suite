# Preflight Validation Tool

The `preflight.py` utility ensures that your protocols and configurations are safe to transfer from your Windows development environment to the Opentrons OT-2 (Linux).

## Usage

Run the tool from the project root:

```bash
# Check a single file
python -m src.utils.preflight src/printing/protocols/dilution_protocol.py

# Check a whole directory
python -m src.utils.preflight src/printing/

# Behavioral check (runs the Opentrons simulator)
python -m src.utils.preflight src/printing/protocols/dilution_protocol.py --simulate

# Strict mode (warnings become errors, exit code 1)
python -m src.utils.preflight src/printing/ --strict
```

## Checks Performed

- **Filesystem**: Drive letters (`C:\`), User paths (`/Users/Sanjay`), and Windows environment variables.
- **Encoding**: UTF-8 validation, BOM detection, and CRLF vs LF line endings.
- **Python**: Syntax errors, Windows-only imports (`winreg`), and suspect shebangs.
- **Opentrons**: Protocol metadata validation, `apiLevel` checks, and `run()` entry point presence.
- **JSON**: Structural validation and path-leak detection in string values.

## Configuration

You can customize the severity of checks in `src/utils/preflight_rules.json`.
Options for each rule: `ERROR` (fatal), `WARN` (warning), or `IGNORE`.

## Integration with SCP

You can chain the tool with your deployment scripts:

```bash
python -m src.utils.preflight my_protocol.py && scp my_protocol.py root@<robot-ip>:/var/lib/opentrons/
```

If the preflight check fails, the SCP command will not execute.
