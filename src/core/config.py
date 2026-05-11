import os
from pathlib import Path
from dotenv import load_dotenv
from src.utils.paths import PROJECT_ROOT, ROBOT_DATA_MIRROR, LOG_DIR

# Load .env from root
load_dotenv(PROJECT_ROOT / ".env")

class Config:
    """Central Configuration and Factory for the OT-2 Lab Suite."""
    ROOT = PROJECT_ROOT
    
    # ─── Robot Connection ─────────────
    ROBOT_IP = os.getenv("ROBOT_IP", "127.0.0.1")
    
    # ─── AI Models ────────────────────
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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
            
        return ChatGoogleGenerativeAI(
            model=Config.GEMINI_MODEL,
            google_api_key=api_key,
            temperature=temperature,
            max_retries=max_retries,
        )
