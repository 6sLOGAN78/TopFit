import json
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from typer.testing import CliRunner

from ranker import cli
from ranker.ingest.pipeline import RunQuotaReached, RunSummary, SourceLocked
from ranker.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _db_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Engine creation is lazy, so a dummy URL never connects (run_ingest is patched).
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:1/x")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def fake(*_a: Any, **_k: Any) -> RunSummary:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli, "run_ingest", fake)


def _summary(status: str) -> RunSummary:
    return RunSummary(
        source="remotive", run_id=7, status=status, fetched=8, inserted=6, skipped=2, calls_used=1
    )


@pytest.mark.parametrize(("status", "code"), [("succeeded", 0), ("partial", 0), ("failed", 1)])
def test_exit_codes(monkeypatch: pytest.MonkeyPatch, status: str, code: int) -> None:
    _patch(monkeypatch, _summary(status))
    res = runner.invoke(cli.app, ["ingest", "remotive"])
    assert res.exit_code == code
    line = json.loads(res.stdout.strip().splitlines()[-1])
    assert line["status"] == status and line["inserted"] == 6 and line["run_id"] == 7


@pytest.mark.parametrize("exc", [SourceLocked("remotive"), RunQuotaReached("remotive")])
def test_nothing_done_exit_3(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    _patch(monkeypatch, exc)
    assert runner.invoke(cli.app, ["ingest", "remotive"]).exit_code == 3


def test_invalid_mode() -> None:
    assert runner.invoke(cli.app, ["ingest", "remotive", "--mode", "x"]).exit_code == 2


def test_unknown_source_subprocess(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    proc = subprocess.run(
        [sys.executable, "-m", "ranker.cli", "ingest", "nope"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
