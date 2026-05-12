import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from src.utils.paths import PROJECT_ROOT, ROBOT_DATA_MIRROR, LOG_DIR, ROBOT_DATA_DIR, DEPLOY_BASE_DIR

# Load .env from root
load_dotenv(PROJECT_ROOT / ".env")

# ─── Inject Proxies if Configured ───
if os.getenv("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.getenv("HTTP_PROXY")
if os.getenv("HTTPS_PROXY"):
    os.environ["HTTPS_PROXY"] = os.getenv("HTTPS_PROXY")

_no_proxy_default = "localhost,127.0.0.1"
_configured_robot_ip = os.getenv("ROBOT_IP", "127.0.0.1")
_no_proxy_combined = os.getenv("NO_PROXY", f"{_no_proxy_default},{_configured_robot_ip}")
if _no_proxy_combined:
    os.environ["NO_PROXY"] = _no_proxy_combined

class Config:
    """Central Configuration and Factory for the OT-2 Lab Suite."""
    ROOT = PROJECT_ROOT
    ROBOT_DATA = ROBOT_DATA_DIR
    DEPLOY_BASE_DIR = DEPLOY_BASE_DIR
    REMOTE_USER_STORAGE = os.getenv("ROBOT_REMOTE_RUN_DIR", "/var/lib/opentrons/user_storage/ot2_runs")
    
    # ─── Robot Connection ─────────────
    ROBOT_IP = os.getenv("ROBOT_IP", "127.0.0.1")
    ROBOT_SSH_USER = os.getenv("ROBOT_SSH_USER", "root")
    ROBOT_SSH_KEY_PATH = os.getenv("ROBOT_SSH_KEY_PATH", "")
    
    # ─── AI Models ────────────────────
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "").strip()

    if GEMINI_BASE_URL:
        _parsed = urlparse(GEMINI_BASE_URL)
        if not _parsed.scheme or not _parsed.netloc:
            raise ValueError(f"Malformed GEMINI_BASE_URL: '{GEMINI_BASE_URL}'. Must be a valid URL with scheme and host.")

    # ─── Opentrons API Config ─────────
    # Fallback to a project-relative default if unset
    OT_API_CONFIG_DIR = os.getenv("OT_API_CONFIG_DIR")
    if not OT_API_CONFIG_DIR:
        OT_API_CONFIG_DIR = str(ROBOT_DATA_MIRROR / "ot2_config")
    
    # Set it in the environment so subprocesses (like opentrons.simulate) inherit it
    os.environ["OT_API_CONFIG_DIR"] = OT_API_CONFIG_DIR

    @staticmethod
    def get_llm(temperature: float = 0, max_retries: int = 5):
        """Factory method to get a resilient Gemini model instance."""
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not found in environment variables.")
            
        kwargs = {
            "model": Config.GEMINI_MODEL,
            "google_api_key": api_key,
            "temperature": temperature,
            "max_retries": max_retries,
        }
        if Config.GEMINI_BASE_URL:
            from google.api_core.client_options import ClientOptions
            # Strip trailing slash if present to avoid double slashes in SDKs
            base_url = Config.GEMINI_BASE_URL.rstrip('/')
            kwargs["client_options"] = ClientOptions(api_endpoint=base_url)

        return ChatGoogleGenerativeAI(**kwargs)
