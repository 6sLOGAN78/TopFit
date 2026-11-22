"""Token cost maths from config/llm_prices.yaml. Unknown models fail closed."""

from decimal import Decimal

from pydantic import BaseModel

from ranker.config_loader import load_config
from ranker.llm.errors import UnknownModelPrice

_MTOK = Decimal(1_000_000)


class ModelPrice(BaseModel):
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal = Decimal(0)
    cache_read: Decimal = Decimal(0)
    long_context_threshold: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None


class LlmPrices(BaseModel):
    as_of: str
    models: dict[str, ModelPrice]


def _price(model: str) -> ModelPrice:
    prices = load_config("llm_prices.yaml", LlmPrices)
    try:
        return prices.models[model]
    except KeyError as exc:
        raise UnknownModelPrice(model) from exc


def _rates(p: ModelPrice, prompt_tokens: int) -> tuple[Decimal, Decimal]:
    if (
        p.long_context_threshold is not None
        and prompt_tokens > p.long_context_threshold
        and p.long_input is not None
        and p.long_output is not None
    ):
        return p.long_input, p.long_output
    return p.input, p.output


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    p = _price(model)
    p_in, p_out = _rates(p, input_tokens + cache_read_tokens + cache_write_tokens)
    return (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_write_tokens * p.cache_write_5m
        + cache_read_tokens * p.cache_read
    ) / _MTOK


def worst_case_cost(model: str, *, est_input_tokens: int, max_tokens: int) -> Decimal:
    p = _price(model)
    p_in, p_out = _rates(p, est_input_tokens)
    return (est_input_tokens * p_in + max_tokens * p_out) / _MTOK
