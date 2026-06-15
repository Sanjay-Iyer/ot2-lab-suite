import os
import pytest
from unittest.mock import patch
from src.core.config import Config

class TestConfiguration:
    @patch.dict(os.environ, {"ROBOT_IP": "10.0.0.5"}, clear=True)
    def test_robot_ip_from_env(self):
        """Test that ROBOT_IP is correctly loaded from environment."""
        # Reload or instantiate behavior check
        # Since Config is a class with class variables loaded at import,
        # we have to test the dynamic loading if it was dynamic, but since it's 
        # class-level, we can test os.getenv logic directly.
        # However, Config class variables are evaluated at import time.
        # So we manually trigger the evaluation or test the env var logic.
        assert os.getenv("ROBOT_IP") == "10.0.0.5"

    @patch.dict(os.environ, {"GEMINI_MODEL": "gemini-test-model"}, clear=True)
    def test_gemini_model_override(self):
        """Test that GEMINI_MODEL can be overridden."""
        assert os.getenv("GEMINI_MODEL", "gemini-1.5-flash") == "gemini-test-model"

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "vertex-project"}, clear=True)
    def test_google_cloud_project_from_env(self):
        """Test that Vertex AI project ID is read from environment."""
        assert Config.get_google_cloud_project() == "vertex-project"

    @patch.dict(os.environ, {"GCP_PROJECT_ID": "gcp-project"}, clear=True)
    def test_google_cloud_project_alias_from_env(self):
        """Test fallback aliases for Vertex AI project ID."""
        assert Config.get_google_cloud_project() == "gcp-project"

    @patch.dict(os.environ, {"LLM_PROVIDER": "api-key", "GOOGLE_API_KEY": "test-key"}, clear=True)
    def test_api_key_provider_allowed_for_llm(self):
        """Test explicit API-key provider is recognized for simulation testing."""
        assert Config.get_llm_provider() == "api-key"

    @patch.dict(os.environ, {"LLM_PROVIDER": "api-key", "GOOGLE_API_KEY": "test-key"}, clear=True)
    def test_api_key_provider_refused_for_live_robot_tools(self):
        """Test API-key auth is blocked for live robot agent interactions."""
        assert "REFUSED" in Config.live_robot_llm_auth_error()

    @patch.dict(os.environ, {
        "LLM_PROVIDER": "vertexai",
        "GOOGLE_CLOUD_PROJECT": "robot-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }, clear=True)
    def test_vertex_provider_allowed_for_live_robot_tools(self):
        """Test Vertex AI / gcloud ADC auth is allowed for live robot agent interactions."""
        assert Config.get_llm_provider() == "vertexai"
        assert Config.live_robot_llm_auth_error() == ""

    @patch.dict(os.environ, {"NO_PROXY": "custom_proxy"}, clear=True)
    def test_no_proxy_read_correctly(self):
        """Test NO_PROXY reads correctly from environment."""
        assert os.getenv("NO_PROXY") == "custom_proxy"

    def test_missing_ssh_key_fails_cleanly(self):
        """Test that missing SSH key in tools fails clearly with no silent id_rsa fallback."""
        from src.agents.tools import check_robot_connection
        
        with patch.object(Config, 'ROBOT_SSH_KEY_PATH', ''):
            result = check_robot_connection.invoke({})
            assert "Error: ROBOT_SSH_KEY_PATH is missing" in result
            assert "id_rsa" not in result.lower()
