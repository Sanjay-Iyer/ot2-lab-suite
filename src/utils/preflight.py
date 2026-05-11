#!/usr/bin/env python3
"""
Preflight Validation Tool for Opentrons OT-2 Deployment
======================================================

Validates that files (protocols, configs) are safe to SCP to the robot.
Checks for Windows-specific leaks, encoding issues, and syntax errors.

Usage:
    python -m src.utils.preflight <file_or_dir> [--strict] [--simulate]
"""

import ast
import json
import os
import re
import sys
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Optional

# Constants
DEFAULT_RULES_PATH = pathlib.Path(__file__).parent / "preflight_rules.json"

@dataclass
class Finding:
    severity: str  # ERROR, WARN
    message: str
    line: Optional[int] = None
    column: Optional[int] = None

@dataclass
class FileResult:
    path: pathlib.Path
    findings: List[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "ERROR" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "WARN" for f in self.findings)

class PreflightEngine:
    def __init__(self, rules_path: pathlib.Path = DEFAULT_RULES_PATH):
        self.rules = self._load_rules(rules_path)
        self.checks: List[Callable[[pathlib.Path, str, List[Finding]], None]] = []
        
        self._register_filesystem_checks()
        self._register_encoding_checks()
        self._register_python_checks()
        self._register_opentrons_checks()
        self._register_json_checks()

    def _load_rules(self, path: pathlib.Path) -> Dict:
        if path.exists():
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception: pass
        return {}

    def get_severity(self, category: str, check_name: str) -> str:
        return self.rules.get(category, {}).get(check_name, "WARN")

    # ─── Registry ────────────────────────────────────────────────────────

    def _register_filesystem_checks(self):
        self.checks.append(self.check_absolute_paths)
        self.checks.append(self.check_filename_integrity)

    def _register_encoding_checks(self):
        self.checks.append(self.check_file_encoding)

    def _register_python_checks(self):
        self.checks.append(self.check_python_syntax_and_imports)
        self.checks.append(self.check_python_windows_artifacts)

    def _register_opentrons_checks(self):
        self.checks.append(self.check_opentrons_protocol)

    def _register_json_checks(self):
        self.checks.append(self.check_json_content)

    # ─── Filesystem Checks ───────────────────────────────────────────────

    def check_absolute_paths(self, path: pathlib.Path, content: str, findings: List[Finding]):
        severity = self.get_severity("filesystem", "absolute_paths")
        user_severity = self.get_severity("filesystem", "user_paths")
        
        drive_pattern = re.compile(r'[a-zA-Z]:[\\/]')
        user_pattern = re.compile(r'[\\/]Users[\\/][^\\/\s]+')
        env_pattern = re.compile(r'%[a-zA-Z_]+%')

        for i, line in enumerate(content.splitlines(), 1):
            if drive_pattern.search(line):
                findings.append(Finding(severity, f"Hardcoded drive-letter path: '{line.strip()}'", i))
            if user_pattern.search(line):
                findings.append(Finding(user_severity, f"Sensitive user path: '{line.strip()}'", i))
            if env_pattern.search(line):
                findings.append(Finding(self.get_severity("filesystem", "env_vars"), f"Windows env var: '{line.strip()}'", i))

    def check_filename_integrity(self, path: pathlib.Path, content: str, findings: List[Finding]):
        name = path.name
        if name.endswith(" ") or name.endswith("."):
            findings.append(Finding(self.get_severity("filesystem", "illegal_filenames"), "Filename has trailing space/dot"))
        if len(path.suffixes) > 1:
            findings.append(Finding("WARN", f"Double extension: '{name}'"))
        if not name.isascii():
            findings.append(Finding(self.get_severity("filesystem", "non_ascii_filenames"), f"Non-ASCII filename: '{name}'"))

    # ─── Encoding Checks ────────────────────────────────────────────────

    def check_file_encoding(self, path: pathlib.Path, content: str, findings: List[Finding]):
        try:
            with open(path, 'rb') as f:
                raw = f.read(3)
                if raw.startswith(b'\xef\xbb\xbf'):
                    findings.append(Finding(self.get_severity("encoding", "bom_present"), "UTF-8 BOM detected"))
        except Exception: pass

        if "\r\n" in content:
            findings.append(Finding(self.get_severity("encoding", "line_endings_crlf"), "CRLF line endings detected"))

    # ─── Python Checks ──────────────────────────────────────────────────

    def check_python_syntax_and_imports(self, path: pathlib.Path, content: str, findings: List[Finding]):
        if path.suffix != ".py": return
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            findings.append(Finding(self.get_severity("python", "syntax_valid"), f"Syntax Error: {e.msg}", e.lineno, e.offset))
            return

        win_modules = {"winreg", "winsound", "msvcrt", "pywin32", "win32api", "win32con", "win32com"}
        robot_warn_modules = {"pandas", "matplotlib", "scipy", "tkinter"}

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module]
                for mod in modules:
                    if mod in win_modules:
                        findings.append(Finding(self.get_severity("python", "windows_imports"), f"Windows-only import: {mod}", node.lineno))
                    if mod in robot_warn_modules:
                        findings.append(Finding(self.get_severity("python", "missing_robot_deps"), f"Module might be missing on robot: {mod}", node.lineno))

    def check_python_windows_artifacts(self, path: pathlib.Path, content: str, findings: List[Finding]):
        if path.suffix != ".py": return
        lines = content.splitlines()
        if lines and lines[0].startswith("#!"):
            if "python" in lines[0] and "env python3" not in lines[0] and "/usr/bin/" not in lines[0]:
                findings.append(Finding(self.get_severity("python", "shebang_valid"), f"Suspect shebang: '{lines[0]}'", 1))
        if "os.sep" in content or "os.pathsep" in content:
            findings.append(Finding(self.get_severity("python", "os_sep_usage"), "os.sep detected. Use pathlib."))

    # ─── Opentrons Checks ───────────────────────────────────────────────

    def check_opentrons_protocol(self, path: pathlib.Path, content: str, findings: List[Finding]):
        if path.suffix != ".py" or "opentrons" not in content: return
        if "metadata" not in content or "apiLevel" not in content:
            findings.append(Finding(self.get_severity("opentrons", "metadata_missing"), "Protocol missing metadata/apiLevel"))
        if "def run" not in content:
            findings.append(Finding(self.get_severity("opentrons", "run_function_missing"), "Protocol missing 'def run'"))

    # ─── JSON Checks ────────────────────────────────────────────────────

    def check_json_content(self, path: pathlib.Path, content: str, findings: List[Finding]):
        if path.suffix != ".json": return
        try:
            data = json.loads(content)
            def scan(d):
                if isinstance(d, dict):
                    for v in d.values(): scan(v)
                elif isinstance(d, list):
                    for v in d: scan(v)
                elif isinstance(d, str) and re.search(r'[a-zA-Z]:[\\/]', d):
                    findings.append(Finding(self.get_severity("json", "path_leak"), f"Hardcoded path in JSON: '{d}'"))
            scan(data)
        except json.JSONDecodeError as e:
            findings.append(Finding(self.get_severity("json", "parse_valid"), f"JSON Error: {e.msg}"))

    # ─── Runner ──────────────────────────────────────────────────────────

    def validate_file(self, file_path: pathlib.Path) -> FileResult:
        result = FileResult(file_path)
        try:
            with open(file_path, "r", encoding="utf-8", newline="") as f:
                content = f.read()
        except UnicodeDecodeError:
            result.findings.append(Finding(self.get_severity("encoding", "utf8_required"), "Not valid UTF-8"))
            return result
        except Exception as e:
            result.findings.append(Finding("ERROR", f"Read error: {e}"))
            return result
        for check in self.checks:
            check(file_path, content, result.findings)
        return result

def print_result(result: FileResult, use_color: bool):
    colors = {"ERROR": "\033[91m", "WARN": "\033[93m", "GREEN": "\033[92m", "RESET": "\033[0m"} if use_color else {"ERROR": "", "WARN": "", "GREEN": "", "RESET": ""}
    
    print(f"\n-- {result.path} --------------------------")
    if result.passed:
        status = f"{colors['GREEN']}[PASS]{colors['RESET']}" if not result.has_warnings else f"{colors['WARN']}[WARN]{colors['RESET']}"
        print(f"  {status} {'No issues' if not result.has_warnings else 'Warnings found'}")
    else:
        print(f"  {colors['ERROR']}[FAIL]{colors['RESET']} {len([f for f in result.findings if f.severity == 'ERROR'])} error(s)")
    
    for f in result.findings:
        c = colors.get(f.severity, "")
        loc = f"line {f.line}: " if f.line else ""
        print(f"    {c}{f.severity:<7}{colors['RESET']} {loc}{f.message}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Preflight check for OT-2 deployment")
    parser.add_argument("targets", nargs="+", help="Files or directories")
    parser.add_argument("--strict", action="store_true", help="Warnings = Errors")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    use_color = sys.stdout.isatty() and os.name != "nt" # Color off on Windows CMD by default for safety
    engine = PreflightEngine()
    results = []

    for target in args.targets:
        path = pathlib.Path(target)
        if path.is_file(): results.append(engine.validate_file(path))
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and not f.name.startswith(".") and f.suffix in [".py", ".json"]:
                    results.append(engine.validate_file(f))

    if args.json:
        print(json.dumps([{"path": str(r.path), "passed": r.passed, "findings": [vars(f) for f in r.findings]} for r in results], indent=2))
        sys.exit(0 if all(r.passed for r in results) else 1)

    for r in results: print_result(r, use_color)
    
    passed_all = all(r.passed for r in results)
    if args.strict and any(r.has_warnings for r in results): passed_all = False
    sys.exit(0 if passed_all else 1)

if __name__ == "__main__":
    main()
