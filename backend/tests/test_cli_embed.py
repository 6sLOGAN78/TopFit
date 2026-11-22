import json
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from typer.testing import CliRunner

from ranker import cli
from ranker.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _db_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:1/x")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch(monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, Any]]) -> None:
    async def fake_embed(
        _engine: Any, _embedder: Any, model: str, *, batch_size: int = 32, **_k: Any
    ) -> int:
        calls.append(("embed", (model, batch_size)))
        return 7

    async def fake_invalidate(_engine: Any, model: str) -> int:
        calls.append(("invalidate", model))
        return 3

    monkeypatch.setattr(cli, "embed_missing", fake_embed)
    monkeypatch.setattr(cli, "invalidate_other_models", fake_invalidate)


def test_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    _patch(monkeypatch, calls)
    res = runner.invoke(cli.app, ["embed", "--missing"])
    assert res.exit_code == 0
    line = json.loads(res.stdout.strip().splitlines()[-1])
    assert line == {"embedded": 7, "model": get_settings().embed_model}
    assert calls == [("embed", (get_settings().embed_model, 32))]


def test_all_with_model_invalidates_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    _patch(monkeypatch, calls)
    res = runner.invoke(cli.app, ["embed", "--all", "--model", "model-x", "--batch-size", "8"])
    assert res.exit_code == 0
    assert json.loads(res.stdout.strip().splitlines()[-1]) == {"embedded": 7, "model": "model-x"}
    assert calls == [("invalidate", "model-x"), ("embed", ("model-x", 8))]


@pytest.mark.parametrize("args", [["--missing", "--all"], []])
def test_mutually_exclusive(args: list[str]) -> None:
    assert runner.invoke(cli.app, ["embed", *args]).exit_code == 2


def test_missing_subprocess_real_db(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url, "EMBEDDER": "fake"}
    proc = subprocess.run(
        [sys.executable, "-m", "ranker.cli", "embed", "--missing"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    line = json.loads(proc.stdout.strip().splitlines()[-1])
    assert set(line) == {"embedded", "model"}
