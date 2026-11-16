"""Runtime configuration. One API key drives every agent. Any OpenAI compatible endpoint works."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def settings() -> dict:
    return {
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL") or None,
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "timeout": float(os.getenv("LLM_TIMEOUT", "90")),
    }


def configured() -> bool:
    return bool(settings()["api_key"])
