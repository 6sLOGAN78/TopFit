"""Jev provider: TypeSafe's router, reached through OpenRouter (model ``typesafe/jev-router``).

It reuses the OpenRouter provider's HTTP, retry and strict-JSON-schema parse code and the
same ``OPENROUTER_API_KEY``; only the provider name (``jev``) and the per-call budget rule
differ. The router picks the underlying model per request and OpenRouter lists its price as
variable, so the gateway reserves ``JEV_RESERVE_USD`` and settles at the reply's ``usage.cost``
(see ``ExtraProvider.reserve_usd``). The reply's ``model`` field names the routed model.
"""

from ranker.llm.providers.openrouter import OpenRouterProvider

PROVIDER_NAME = "jev"
DEFAULT_MODEL = "typesafe/jev-router"


class JevProvider(OpenRouterProvider):
    """OpenRouter provider pinned to the Jev router; selected per request as ``jev``."""

    name = PROVIDER_NAME
