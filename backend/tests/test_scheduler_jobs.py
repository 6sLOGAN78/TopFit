import json
import shutil
from pathlib import Path

import pytest
import structlog
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker import selfcheck
from ranker.config_loader import ConfigError, clear_config_cache, config_dir
from ranker.settings import Settings, get_settings
from ranker.worker import scheduler as sched
from ranker.worker.scheduler import (
    _enqueue_ingest,  # pyright: ignore[reportPrivateUsage]
    build_scheduler,
    reap_abandoned_runs,
    register_source_jobs,
)


@pytest.fixture(autouse=True)
def _fresh_config() -> None:
    clear_config_cache()
    get_settings.cache_clear()


def _settings(**update: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://u:p@localhost/x",
        session_secret="x" * 40,  # type: ignore[arg-type]
        **update,  # type: ignore[arg-type]
    )


def _engine() -> AsyncEngine:
    return create_async_engine("postgresql+psycopg://u:p@localhost/x")


def _fields(job: object) -> dict[str, str]:
    trigger = job.trigger  # type: ignore[attr-defined]
    return {f.name: str(f) for f in trigger.fields}


def _config_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: object = None
) -> dict[str, object]:
    dest = tmp_path / "config"
    shutil.copytree(config_dir(), dest)
    data = yaml.safe_load((dest / "sources.yaml").read_text())
    if callable(mutate):
        mutate(data)
    (dest / "sources.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setenv("CONFIG_DIR", str(dest))
    clear_config_cache()
    return data


def _register(settings: Settings | None = None) -> AsyncIOScheduler:
    scheduler = build_scheduler()
    register_source_jobs(scheduler, _engine(), settings or _settings())
    return scheduler


def test_repo_config_jobs() -> None:
    scheduler = _register()
    ids = {j.id for j in scheduler.get_jobs()}
    assert {"ingest:remotive", "ingest:remotive:1", "enrich_backlog", "reap_abandoned_runs"} <= ids
    assert "ingest:adzuna" not in ids
    first = _fields(scheduler.get_job("ingest:remotive"))
    assert (first["hour"], first["minute"]) == ("23", "30")
    second = _fields(scheduler.get_job("ingest:remotive:1"))
    assert (second["hour"], second["minute"]) == ("11", "30")
    backlog = _fields(scheduler.get_job("enrich_backlog"))
    assert (backlog["hour"], backlog["minute"]) == ("1", "30")


def _enable_adzuna(data: dict[str, object]) -> None:
    data["adzuna"]["enabled"] = True  # type: ignore[index]


def test_adzuna_enabled_and_keyed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config_copy(tmp_path, monkeypatch, _enable_adzuna)
    scheduler = _register(_settings(adzuna_app_id="i", adzuna_app_key="k"))  # type: ignore[arg-type]
    ad = _fields(scheduler.get_job("ingest:adzuna"))
    assert (ad["hour"], ad["minute"]) == ("23", "40")
    ttl = _fields(scheduler.get_job("expire_ttl:adzuna"))
    assert (ttl["hour"], ttl["minute"]) == ("0", "30")


def test_adzuna_enabled_without_keys_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_copy(tmp_path, monkeypatch, _enable_adzuna)
    with structlog.testing.capture_logs() as logs:
        scheduler = _register(_settings(adzuna_app_id=None, adzuna_app_key=None))
    assert scheduler.get_job("ingest:adzuna") is None
    assert any(e["event"] == "source_not_configured" for e in logs)


def _remotive(mutate: object) -> object:
    def apply(data: dict[str, object]) -> None:
        assert callable(mutate)
        mutate(data["remotive"])

    return apply


def test_no_morning_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def m(r: dict[str, object]) -> None:
        r["schedule"] = [{"hour": 23, "minute": 30}]

    _config_copy(tmp_path, monkeypatch, _remotive(m))
    with pytest.raises(ConfigError):
        _register()


def test_two_morning_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def m(r: dict[str, object]) -> None:
        r["schedule"] = [
            {"hour": 23, "minute": 30, "batch": "morning"},
            {"hour": 22, "minute": 30, "batch": "morning"},
        ]

    _config_copy(tmp_path, monkeypatch, _remotive(m))
    with pytest.raises(ConfigError):
        _register()


@pytest.mark.parametrize(
    ("hour", "minute", "ok"),
    [(3, 0, False), (23, 59, True), (21, 30, True), (21, 29, False), (0, 1, False), (0, 0, True)],
)
def test_morning_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hour: int, minute: int, ok: bool
) -> None:
    def m(r: dict[str, object]) -> None:
        r["schedule"] = [{"hour": hour, "minute": minute, "batch": "morning"}]

    _config_copy(tmp_path, monkeypatch, _remotive(m))
    if ok:
        _register()
    else:
        with pytest.raises(ConfigError):
            _register()


def test_remotive_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def m(r: dict[str, object]) -> None:
        r["schedule"] = [{"hour": 23, "minute": 30, "batch": "morning"}] + [
            {"hour": h, "minute": 0} for h in (1, 5, 9, 13)
        ]

    _config_copy(tmp_path, monkeypatch, _remotive(m))
    with pytest.raises(ConfigError):
        _register()


def test_window_constants() -> None:
    assert sched.MORNING_START.hour == 21
    assert sched.MORNING_LATEST_START.hour == 0


async def test_scheduled_job_enqueues_deduped(engine: AsyncEngine, clean_db: None) -> None:
    await _enqueue_ingest(engine, "remotive")
    await _enqueue_ingest(engine, "remotive")
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT kind, dedupe_key, payload FROM tasks"))).all()
    assert len(rows) == 1
    assert rows[0][0] == "ingest"
    assert rows[0][1] == "ingest:remotive"
    assert rows[0][2] == {"source": "remotive", "mode": "incremental"}


async def test_reaper(engine: AsyncEngine, clean_db: None) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ingest_runs (source, mode, started_at, status) VALUES "
                "('a', 'incremental', now() - interval '3 hours', 'running'), "
                "('b', 'incremental', now() - interval '5 minutes', 'running')"
            )
        )
    assert await reap_abandoned_runs(engine) == 1
    async with engine.connect() as conn:
        rows = {
            r[0]: r
            for r in (
                await conn.execute(
                    text("SELECT source, status, errors, finished_at FROM ingest_runs")
                )
            ).all()
        }
    assert rows["a"][1] == "failed"
    assert rows["a"][2][0]["error"] == "abandoned"
    assert rows["a"][3] is not None
    assert rows["b"][1] == "running"
    assert rows["b"][3] is None


def test_selfcheck_configs(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def __init__(self, *a: object) -> None: ...

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _ in texts]

    monkeypatch.setattr(selfcheck, "FastEmbedEmbedder", Fake)
    assert selfcheck.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert {
        "sources.yaml",
        "cities.yaml",
        "regions.yaml",
        "scope.yaml",
        "fx.yaml",
        "skills.yaml",
        "industries.yaml",
        "llm_prices.yaml",
    } <= set(out["configs"])


def test_selfcheck_empty_config_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def __init__(self, *a: object) -> None: ...

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _ in texts]

    monkeypatch.setattr(selfcheck, "FastEmbedEmbedder", Fake)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    clear_config_cache()
    assert selfcheck.main() == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "ConfigError"}
