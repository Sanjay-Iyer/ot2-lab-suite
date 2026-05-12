import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import shutil
import os
from src.agents.tools import (
    check_robot_connection, 
    deploy_protocol_to_robot, 
    execute_protocol_on_robot,
    simulate_protocol,
    _load_simulation_records
)
from src.core.config import Config
from src.utils.paths import SIMULATION_RECORDS_PATH, DEPLOY_BASE_DIR

class TestRobotAutomation(unittest.TestCase):

    def setUp(self):
        # Ensure clean state for each test
        if SIMULATION_RECORDS_PATH.exists():
            SIMULATION_RECORDS_PATH.unlink()
        if DEPLOY_BASE_DIR.exists():
            shutil.rmtree(DEPLOY_BASE_DIR)
        DEPLOY_BASE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Create a dummy protocol file
        self.dummy_protocol = Path("dummy_protocol.py")
        self.dummy_protocol.write_text("metadata = {'apiLevel': '2.15'}\ndef run(ctx): pass")
        self.dummy_hash = "f5f5f5f5" # Mocked later

    def tearDown(self):
        if self.dummy_protocol.exists():
            self.dummy_protocol.unlink()
        if SIMULATION_RECORDS_PATH.exists():
            SIMULATION_RECORDS_PATH.unlink()

    @patch("subprocess.run")
    def test_check_robot_connection_success(self, mock_run):
        # Mock successful SSH checks
        mock_run.return_value = MagicMock(returncode=0, stdout="/usr/bin/opentrons_execute", stderr="")
        
        with patch.object(Config, 'ROBOT_IP', '169.254.46.57'):
            result = check_robot_connection.func()
            self.assertIn("Connectivity PASSED", result)

    @patch("subprocess.run")
    def test_check_robot_connection_fail(self, mock_run):
        # Mock SSH failure
        mock_run.return_value = MagicMock(returncode=255, stderr="Permission denied (publickey).")
        
        with patch.object(Config, 'ROBOT_IP', '169.254.46.57'):
            result = check_robot_connection.func()
            self.assertIn("Connectivity FAILED", result)

    @patch("subprocess.run")
    @patch("src.agents.tools.hash_file")
    def test_simulate_protocol_saves_record(self, mock_hash, mock_run):
        mock_hash.return_value = "hash123"
        mock_run.return_value = MagicMock(returncode=0, stdout="Sim output", stderr="")
        
        result = simulate_protocol.func(str(self.dummy_protocol))
        self.assertIn("SIMULATION PASSED", result)
        
        # Verify record exists
        records = _load_simulation_records()
        self.assertIn("hash123", records)
        self.assertEqual(records["hash123"]["status"], "PASS")

    @patch("subprocess.run")
    @patch("src.agents.tools.hash_file")
    def test_deploy_protocol_creates_manifest(self, mock_hash, mock_run):
        mock_hash.return_value = "hash123"
        mock_run.return_value = MagicMock(returncode=0)
        
        with patch.object(Config, 'ROBOT_IP', '169.254.46.57'):
            result = deploy_protocol_to_robot.func(str(self.dummy_protocol))
            self.assertIn("Deployment SUCCESS", result)
            
            # Find the run folder
            run_folders = list(DEPLOY_BASE_DIR.glob("run_*"))
            self.assertEqual(len(run_folders), 1)
            manifest_path = run_folders[0] / "manifest.json"
            self.assertTrue(manifest_path.exists())
            
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
                self.assertEqual(manifest["protocol_hash"], "hash123")

    @patch("subprocess.run")
    def test_execute_on_robot_blocks_unsimulated_hash(self, mock_run):
        result = execute_protocol_on_robot.func("/remote/path", "unknown_hash")
        self.assertIn("Error: No simulation record found", result)

    @patch("subprocess.run")
    @patch("src.agents.tools.hash_file")
    def test_execute_on_robot_blocks_failed_simulation(self, mock_hash, mock_run):
        # Record a failed simulation
        mock_hash.return_value = "badhash"
        mock_run.return_value = MagicMock(returncode=1, stderr="Sim error")
        simulate_protocol.func(str(self.dummy_protocol))
        
        # Attempt to run on robot
        result = execute_protocol_on_robot.func("/remote/path", "badhash")
        self.assertIn("Refusing to run on physical hardware", result)

    @patch("subprocess.run")
    @patch("src.agents.tools.hash_file")
    def test_execute_on_robot_success(self, mock_hash, mock_run):
        # 1. Record a passing simulation
        mock_hash.return_value = "goodhash"
        mock_run.return_value = MagicMock(returncode=0, stdout="Sim success")
        simulate_protocol.func(str(self.dummy_protocol))
        
        # 2. Mock successful remote execution
        mock_run.return_value = MagicMock(returncode=0, stdout="Robot Run OK")
        
        with patch.object(Config, 'ROBOT_IP', '169.254.46.57'):
            result = execute_protocol_on_robot.func("/remote/path", "goodhash")
            self.assertIn("Execution COMPLETE", result)

if __name__ == "__main__":
    unittest.main()
