import unittest
import pathlib
import os
import shutil
import json
from src.utils.preflight import PreflightEngine, Finding

class TestPreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = pathlib.Path("tests/preflight_fixtures")
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        cls.engine = PreflightEngine()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)

    def create_fixture(self, name, content):
        path = self.test_dir / name
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return path

    def test_absolute_path_leak(self):
        path = self.create_fixture("bad_path.py", "BASE_DIR = 'C:\\Users\\Sanjay\\data'\nprint(BASE_DIR)")
        result = self.engine.validate_file(path)
        has_drive_err = any("drive-letter" in f.message for f in result.findings)
        self.assertTrue(has_drive_err)

    def test_crlf_warning(self):
        path = self.test_dir / "crlf.txt"
        with open(path, "wb") as f:
            f.write(b"Hello\r\nWorld\r\n")
        result = self.engine.validate_file(path)
        has_crlf = any("CRLF" in f.message for f in result.findings)
        self.assertTrue(has_crlf)

    def test_python_syntax_error(self):
        path = self.create_fixture("syntax_err.py", "if True\n    print('fail')")
        result = self.engine.validate_file(path)
        has_syntax = any("Syntax Error" in f.message for f in result.findings)
        self.assertTrue(has_syntax)

    def test_windows_import(self):
        path = self.create_fixture("win_imp.py", "import winreg\nprint('bad')")
        result = self.engine.validate_file(path)
        has_win_imp = any("Windows-only module" in f.message for f in result.findings)
        self.assertTrue(has_win_imp)

    def test_json_leak(self):
        path = self.create_fixture("leak.json", json.dumps({"path": "C:\\data\\test.json"}))
        result = self.engine.validate_file(path)
        has_leak = any("Hardcoded path in JSON" in f.message for f in result.findings)
        self.assertTrue(has_leak)

    def test_opentrons_metadata(self):
        path = self.create_fixture("protocol_bad.py", "from opentrons import protocol_api\ndef run(p): pass")
        result = self.engine.validate_file(path)
        has_meta_err = any("metadata" in f.message for f in result.findings)
        self.assertTrue(has_meta_err)

if __name__ == "__main__":
    unittest.main()
