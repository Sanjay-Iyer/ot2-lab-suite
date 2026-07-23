import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from src.utils.paths import PROJECT_ROOT, ROBOT_DATA_MIRROR, LOG_DIR, ROBOT_DATA_DIR, DEPLOY_BASE_DIR
from src.utils.ot2_ssh import parse_bool

# Load .env from root
load_dotenv(PROJECT_ROOT / ".env")

# ─── Inject Proxies if Configured ───
if os.getenv("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = os.getenv("HTTP_PROXY")
if os.getenv("HTTPS_PROXY"):
    os.environ["HTTPS_PROXY"] = os.getenv("HTTPS_PROXY")

_no_proxy_default = "localhost,127.0.0.1"
_configured_robot_ip = os.getenv("OT2_ROBOT_HOST", "")
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
    # Deprecated compatibility attribute. Robot-facing code must call
    # src.lab.robot_connection.resolve_host(), whose source of truth is robot.yaml.
    ROBOT_IP = os.getenv("OT2_ROBOT_HOST", "")
    ROBOT_SSH_USER = os.getenv("ROBOT_SSH_USER", "root")
    ROBOT_SSH_KEY_PATH = os.getenv("ROBOT_SSH_KEY_PATH", "")
    ROBOT_SSH_IDENTITIES_ONLY = parse_bool(
        os.getenv("ROBOT_SSH_IDENTITIES_ONLY"),
        default=True,
    )
    ROBOT_SSH_LEGACY_RSA = parse_bool(
        os.getenv("ROBOT_SSH_LEGACY_RSA"),
        default=False,
    )
    
    # ─── AI Models ────────────────────
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "").strip()
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
    GOOGLE_CLOUD_LOCATION = os.getenv(
        "GOOGLE_CLOUD_LOCATION",
        os.getenv("GOOGLE_CLOUD_REGION", "us-central1"),
    ).strip()

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
    def _first_env(*names: str) -> str:
        """Return the first non-empty environment variable from a list."""
        for name in names:
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def get_google_cloud_project() -> str:
        """Project ID for Vertex AI / Google Cloud ADC auth."""
        return Config._first_env(
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_PROJECT_ID",
            "GCP_PROJECT_ID",
            "VERTEXAI_PROJECT",
        )

    @staticmethod
    def get_google_cloud_location() -> str:
        """Vertex AI region/location for Google Cloud ADC auth."""
        return (
            os.getenv(
                "GOOGLE_CLOUD_LOCATION",
                os.getenv("GOOGLE_CLOUD_REGION", Config.GOOGLE_CLOUD_LOCATION),
            ).strip()
            or "us-central1"
        )

    @staticmethod
    def get_gemini_model() -> str:
        """Gemini model name from the current environment."""
        return os.getenv("GEMINI_MODEL", Config.GEMINI_MODEL).strip() or "gemini-1.5-flash"

    @staticmethod
    def get_vertex_api_transport() -> str:
        """Transport for Vertex AI calls; REST avoids flaky Windows gRPC DNS."""
        transport = Config._first_env(
            "GOOGLE_VERTEX_API_TRANSPORT",
            "VERTEXAI_API_TRANSPORT",
            "GOOGLE_CLOUD_API_TRANSPORT",
        ).lower()
        if transport in {"grpc", "rest"}:
            return transport
        return "rest"

    @staticmethod
    def get_llm_provider() -> str:
        """Return the effective LLM auth provider: vertexai or api-key."""
        provider = os.getenv("LLM_PROVIDER", Config.LLM_PROVIDER).strip().lower()
        if provider in {"vertex", "vertexai", "gcloud", "google-cloud"}:
            return "vertexai"
        if provider in {"api-key", "google-api-key", "gemini-api", "genai"}:
            return "api-key"
        if not provider and not os.getenv("GOOGLE_API_KEY") and Config.get_google_cloud_project():
            return "vertexai"
        return "api-key"

    @staticmethod
    def describe_llm_auth() -> str:
        """Human-readable LLM auth summary with no secrets."""
        provider = Config.get_llm_provider()
        lines = [
            f"LLM provider: {provider}",
            f"Gemini model: {Config.get_gemini_model()}",
        ]
        if provider == "vertexai":
            project = Config.get_google_cloud_project() or "(missing)"
            lines.extend([
                f"Google Cloud project: {project}",
                f"Google Cloud location: {Config.get_google_cloud_location()}",
                f"Vertex API transport: {Config.get_vertex_api_transport()}",
                "Auth method: gcloud ADC / Vertex AI",
            ])
        else:
            lines.append(
                "Auth method: GOOGLE_API_KEY "
                f"({'set' if os.getenv('GOOGLE_API_KEY') else 'missing'})"
            )
        return "\n".join(lines)

    @staticmethod
    def live_robot_llm_auth_error() -> str:
        """Return an error if the current LLM auth is not allowed for live robot tools."""
        if Config.get_llm_provider() == "vertexai" and Config.get_google_cloud_project():
            return ""
        return (
            "REFUSED: live OT-2 agent interactions require Vertex AI / gcloud ADC auth. "
            "GOOGLE_API_KEY is allowed only for simulation-laptop testing. On the real "
            "robot laptop, set LLM_PROVIDER=vertexai, GOOGLE_CLOUD_PROJECT, and "
            "GOOGLE_CLOUD_LOCATION in .env."
        )

    @staticmethod
    def _get_vertex_llm(temperature: float, max_retries: int):
        """Create a Gemini chat model through Vertex AI and ADC."""
        project = Config.get_google_cloud_project()
        if not project:
            raise ValueError(
                "Vertex AI LLM selected, but no Google Cloud project was found. "
                "Set GOOGLE_CLOUD_PROJECT in .env."
            )

        try:
            from langchain_google_vertexai import ChatVertexAI
        except ImportError as exc:
            raise ImportError(
                "Vertex AI LLM selected, but langchain-google-vertexai is not installed. "
                "Run: pip install langchain-google-vertexai"
            ) from exc

        kwargs = {
            "project": project,
            "location": Config.get_google_cloud_location(),
            "temperature": temperature,
            "max_retries": max_retries,
            "api_transport": Config.get_vertex_api_transport(),
        }
        model = Config.get_gemini_model()
        for model_key in ("model", "model_name"):
            try:
                return ChatVertexAI(**{model_key: model}, **kwargs)
            except TypeError as exc:
                if "api_transport" in str(exc):
                    kwargs.pop("api_transport", None)
                    return ChatVertexAI(**{model_key: model}, **kwargs)
                if model_key == "model_name":
                    raise
        raise RuntimeError("Unable to initialize ChatVertexAI.")

    @staticmethod
    def _get_api_key_llm(temperature: float, max_retries: int):
        """Create a Gemini chat model through the Gemini API key path."""
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "No LLM credentials found. Set GOOGLE_API_KEY for the Gemini API, "
                "or set LLM_PROVIDER=vertexai and GOOGLE_CLOUD_PROJECT for Google Cloud ADC."
            )

        kwargs = {
            "model": Config.get_gemini_model(),
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

    @staticmethod
    def get_llm(temperature: float = 0, max_retries: int = 5):
        """Factory method to get a resilient Gemini model instance."""
        provider = Config.get_llm_provider()
        if provider == "vertexai":
            return Config._get_vertex_llm(temperature, max_retries)

        return Config._get_api_key_llm(temperature, max_retries)
