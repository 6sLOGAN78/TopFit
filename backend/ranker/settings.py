"""Typed application settings read from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    session_secret: SecretStr
    cookie_secure: bool = True
    embedder: Literal["fastembed", "fake"] = "fastembed"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_threads: int = 2
    log_level: str = "INFO"
    worker_heartbeat_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
