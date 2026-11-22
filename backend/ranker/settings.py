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
    task_poll_seconds: float = 2.0
    task_lease_seconds: int = 1800
    llm_provider: Literal["anthropic", "openrouter", "fake"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "nvidia/nemotron-3.5-super-vl:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model_fast: str = "claude-haiku-5-5"
    llm_model_agent: str = "claude-sonnet-5-5"
    llm_monthly_budget_usd: float = 5.0
    llm_timeout_seconds: float = 30.0
    jev_enabled: bool = False
    jev_model: str = "typesafe/jev-router"
    jev_reserve_usd: float = 0.001
    parse_fallback_timeout_seconds: float = 1.2
    rate_limit_enabled: bool = True
    rate_limit_guest_per_hour: int = 60
    rate_limit_ip_per_hour: int = 300
    rate_limit_trust_xff: bool = False
    adzuna_app_id: SecretStr | None = None
    adzuna_app_key: SecretStr | None = None
    admin_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
