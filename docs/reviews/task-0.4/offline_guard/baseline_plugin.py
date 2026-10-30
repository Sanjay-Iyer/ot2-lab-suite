"""Pytest-only factory guard, installed before collection; not in simulator env."""
import sitecustomize
assert sitecustomize.GUARD_ACTIVE
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from src.core.config import Config
Config.get_llm = staticmethod(lambda *args, **kwargs: sitecustomize.blocked("model-factory"))
