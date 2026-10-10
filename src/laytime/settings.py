"""Runtime settings and logging.

Defaults live here; any value set in `.env` or the environment overrides them.
"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pythonjsonlogger.json import JsonFormatter


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Only needed for LLM calls; SecretStr keeps it out of logs and reprs.
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    triage_model: str = "google/gemini-3.5-flash-lite"
    extract_model: str = "google/gemini-3.8-flash"
    temperature: float = 0.0
    max_retries: int = 1

    cache_dir: Path = Path(".cache")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def setup_logging(level: str) -> None:
    """One JSON object per line: time, level, logger, message, plus any `extra` fields.

    Callers add context with `extra`, e.g.
    `log.info("set aside: not our fleet", extra={"stage": "gate", "email": "email_14.eml"})`.
    Never log secrets, email addresses, names or email bodies; refer to emails by file name.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "{asctime}{levelname}{name}{message}",
            style="{",
            rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
