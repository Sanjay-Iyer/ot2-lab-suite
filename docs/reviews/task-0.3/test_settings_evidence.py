"""Temporary fixtures only; no application, credentials, network or robot imports."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("settings_evidence", Path(__file__).with_name("settings_evidence.py"))
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class SettingsEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for folder in evidence.ROOTS:
            (self.root / folder).mkdir(parents=True)
        (self.root / "configs/robot.yaml").write_text("robot: example\n")

    def test_secrets_never_serialized_or_printed(self):
        secret = "TASK03_SECRET_CANARY_a931"
        (self.root / ".env").write_text("GOOGLE_API_KEY=" + secret)
        (self.root / "configs/credentials.json").write_text(secret)
        (self.root / "configs/passwords").mkdir()
        (self.root / "configs/passwords/ordinary.json").write_text(secret)
        output = io.StringIO()
        with patch.dict(os.environ, {key: secret for key in evidence.PRESENCE_KEYS}), contextlib.redirect_stdout(output):
            report = evidence.collect(self.root, "HOME")
        self.assertNotIn(secret, json.dumps(report) + output.getvalue())
        self.assertEqual(list(report["files"]), ["configs/robot.yaml"])
        self.assertEqual(report["credential_evidence"]["repo_dotenv"]["status"], "present")

    def test_changed_missing_added(self):
        before = evidence.collect(self.root, "WORK")
        file = self.root / "configs/robot.yaml"
        file.write_text("robot: changed\n")
        self.assertEqual(evidence.compare(before, evidence.collect(self.root, "WORK"))["changed"], ["configs/robot.yaml"])
        file.unlink()
        (self.root / "configs/new.yaml").write_text("example: true")
        diff = evidence.compare(before, evidence.collect(self.root, "WORK"))
        self.assertEqual(diff["missing"], ["configs/robot.yaml"])
        self.assertEqual(diff["added"], ["configs/new.yaml"])

    def test_identity_mismatches_refused(self):
        before = evidence.collect(self.root, "HOME")
        for key, value in (("schema", 99), ("declared_role", "WORK"), ("root", "elsewhere")):
            after = copy.deepcopy(before)
            after[key] = value
            with self.assertRaises(ValueError):
                evidence.compare(before, after)

    def test_no_application_imports(self):
        self.assertFalse(any(name in evidence.__dict__ for name in ("Config", "opentrons", "requests", "src")))
        with patch.object(evidence.importlib.metadata, "version", return_value="fixture"):
            self.assertEqual(evidence.collect(self.root, "HOME")["packages"]["opentrons"], "fixture")

    def test_missing_root_and_unknown_role(self):
        with self.assertRaises(ValueError):
            evidence.collect(self.root, "UNKNOWN")
        (self.root / "configs/robot.yaml").unlink()
        (self.root / "configs").rmdir()
        self.assertIn({"path": "configs", "status": "missing"}, evidence.collect(self.root, "HOME")["issues"])

    def test_intermediate_link_not_traversed(self):
        # Simulate a reparse point without requiring Windows symlink privileges.
        linked = self.root / "src"
        (self.root / "src/labware/ordinary.json").write_text("must not be opened")
        original_lstat = Path.lstat
        original_walk = evidence.os.walk
        original_is_link = evidence.is_link
        marker = object()
        def lstat(path, *args, **kwargs):
            return marker if path == linked else original_lstat(path, *args, **kwargs)
        def walk(path, *args, **kwargs):
            self.assertNotEqual(path, self.root / "src/labware")
            return original_walk(path, *args, **kwargs)
        with patch.object(Path, "lstat", lstat), patch.object(evidence, "is_link", lambda info: info is marker or original_is_link(info)), patch.object(evidence.os, "walk", walk):
            report = evidence.collect(self.root, "HOME")
        self.assertIn({"path": "src", "status": "link-not-inspected"}, report["issues"])
        self.assertNotIn("src/labware/ordinary.json", report["files"])


if __name__ == "__main__":
    unittest.main()
