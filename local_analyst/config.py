"""One local model configuration; CLI override > environment > JSON configuration."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "ollama_config.json"


def load_project_environment():
    """Load local credentials without replacing existing environment variables."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


load_project_environment()


def settings():
    path = Path(os.environ.get("LOCAL_ANALYST_CONFIG", DEFAULT_CONFIG)).expanduser()
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_model(override=None, purpose="model"):
    value = override if override is not None else os.environ.get("OLLAMA_MODEL")
    if value is None:
        config = settings()
        value = config.get(purpose, config.get("model"))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Set a nonempty model tag via --model, OLLAMA_MODEL, or ollama_config.json")
    return value.strip()
