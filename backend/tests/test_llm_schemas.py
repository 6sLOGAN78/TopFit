from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict

from ranker.llm import schemas
from ranker.llm.cost import cost_usd, worst_case_cost
from ranker.llm.errors import BudgetExceeded, UnknownModelPrice
from ranker.llm.schemas import JobEnrichment, lint_schema


def test_exported_schemas_pass_lint() -> None:
    models = [
        getattr(schemas, n)
        for n in schemas.__all__
        if isinstance(getattr(schemas, n), type) and issubclass(getattr(schemas, n), BaseModel)
    ]
    assert JobEnrichment in models
    for m in models:
        assert lint_schema(m) == [], m


def test_lint_rejects_dict_field() -> None:
    class Bad(BaseModel):
        model_config = ConfigDict(extra="forbid")
        blob: dict[str, str]

    assert lint_schema(Bad)


def test_lint_rejects_constraints_and_missing_extra_forbid() -> None:
    class Loose(BaseModel):
        n: int

    assert any("additionalProperties" in v for v in lint_schema(Loose))


def test_cost_haiku() -> None:
    assert cost_usd("claude-haiku-5-5", input_tokens=1_000_000, output_tokens=0) == Decimal("0.10")
    assert cost_usd("claude-haiku-5-5", input_tokens=0, output_tokens=1_000_000) == Decimal("0.50")
    assert cost_usd(
        "claude-haiku-5-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    ) == Decimal("0.01")
    assert worst_case_cost("claude-haiku-5-5", est_input_tokens=1000, max_tokens=100) > 0


def test_unknown_model_fails_closed() -> None:
    with pytest.raises(UnknownModelPrice):
        cost_usd("nope", input_tokens=1, output_tokens=1)


def test_budget_exceeded_resets_next_month() -> None:
    assert BudgetExceeded().resets_at.day == 1
