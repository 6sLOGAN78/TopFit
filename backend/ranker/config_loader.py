"""Typed, cached YAML config loader.

Deliberately does not import ``ranker.settings`` so pure normalisers run without
DATABASE_URL.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

_cache: dict[tuple[str, type[BaseModel]], BaseModel] = {}


class ConfigError(RuntimeError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"config {name}: {reason}")
        self.name = name
        self.reason = reason


def config_dir() -> Path:
    override = os.environ.get("CONFIG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "config"


def clear_config_cache() -> None:
    _cache.clear()


def load_config[M: BaseModel](name: str, model: type[M]) -> M:
    path = config_dir() / name
    key = (str(path), model)
    cached = _cache.get(key)
    if isinstance(cached, model):
        return cached
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(name, f"unreadable ({type(exc).__name__})") from exc
    try:
        data: object = yaml.safe_load(text)  # pyright: ignore[reportUnknownMemberType]
    except yaml.YAMLError as exc:
        raise ConfigError(name, "invalid YAML") from exc
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(name, f"{exc.error_count()} validation error(s)") from exc
    _cache[key] = parsed
    return parsed
