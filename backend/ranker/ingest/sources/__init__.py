"""Source adapter registry. Adapter modules are imported explicitly for registration."""

from . import remotive as remotive
from .base import (
    AdapterContext,
    AdapterFactory,
    BaseSourceConfig,
    CronSpec,
    FetchResult,
    RawJob,
    SourceAdapter,
    SourcesConfig,
    UnknownSource,
    get_adapter_factory,
    register,
    registered_sources,
)

__all__ = [
    "AdapterContext",
    "AdapterFactory",
    "BaseSourceConfig",
    "CronSpec",
    "FetchResult",
    "RawJob",
    "SourceAdapter",
    "SourcesConfig",
    "UnknownSource",
    "get_adapter_factory",
    "register",
    "registered_sources",
]
