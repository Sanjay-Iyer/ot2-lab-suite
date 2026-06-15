import os
import requests
import sys
from dotenv import load_dotenv
from pathlib import Path

# --- Root-Aware Discovery ---
# This file is in src/agents, so root is 2 levels up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m src.agents.check_models")
    sys.exit(1)

from src.utils.paths import PROJECT_ROOT

# 1. Load your API key from the root .env
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

from src.core.config import Config

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    project = Config.get_google_cloud_project()
    if project:
        print(
            "GOOGLE_API_KEY is not set. Vertex AI / gcloud ADC appears configured "
            f"for project '{project}'. This helper only lists Gemini API-key models."
        )
        sys.exit(0)
    print("GOOGLE_API_KEY is not set, and no GOOGLE_CLOUD_PROJECT was found.")
    sys.exit(1)

# 2. Ask Google's API for the list of available models
print("Fetching available models from Google...\n")
base_url = Config.GEMINI_BASE_URL.rstrip('/') if Config.GEMINI_BASE_URL else "https://generativelanguage.googleapis.com"
url = f"{base_url}/v1beta/models?key={api_key}"
response = requests.get(url)

# 3. Print out the names
if response.status_code == 200:
    models = response.json().get("models", [])
    for m in models:
        # We only care about the models that generate text/chat
        if "generateContent" in m.get("supportedGenerationMethods", []):
            # Strip the 'models/' prefix
            model_name = m['name'].replace('models/', '')
            print(f"- {model_name}")
else:
    print(f"Error fetching models: {response.text}")
