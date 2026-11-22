"""Source adapter registry. Adapter modules are imported explicitly for registration."""

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
