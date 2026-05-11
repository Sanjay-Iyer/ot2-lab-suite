import os
import requests
import sys
from dotenv import load_dotenv
from pathlib import Path

# --- Root-Aware Discovery ---
# This file is in src/agents, so root is 2 levels up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from src.utils.paths import PROJECT_ROOT

# 1. Load your API key from the root .env
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

api_key = os.getenv("GOOGLE_API_KEY")

# 2. Ask Google's API for the list of available models
print("Fetching available models from Google...\n")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
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