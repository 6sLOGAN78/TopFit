"""Board-source expiry: expire on drop, guarded age fallback, outage safety (plan 02-18)."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import time_machine
import yaml
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import clear_config_cache, config_dir, load_config
from ranker.ingest import boards
from ranker.ingest.expiry import (
    ExpiryConfig,
    expire_dropped_board,
    expire_stale_board_jobs,
    reactivate_run,
)
from ranker.ingest.pipeline import run_ingest
from ranker.ingest.sources.base import AdapterContext, SourcesConfig
from ranker.ingest.sources.boardbase import BoardsConfig
from ranker.ingest.sources.greenhouse import GreenhouseAdapter
from ranker.settings import get_settings

pytestmark = pytest.mark.usefixtures("clean_db")

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
GH_FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_board.json"
REMOTIVE_FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"


async def add_run(engine: AsyncEngine, source: str, fetched: int, *, when: datetime) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO ingest_runs (source, status, fetched, started_at, finished_at, notes) "
                "VALUES (:s, 'succeeded', :f, :t, :t, CAST(:n AS jsonb)) RETURNING id"
            ),
            {"s": source, "f": fetched, "t": when, "n": json.dumps({"scope_counts": {}})},
        )
        return int(row.scalar_one())


async def add_job(
    engine: AsyncEngine, sid: str, *, source: str, scope: str, last_seen: datetime
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, source_scope, url, title, "
                "description_raw, description_clean, content_hash, dedupe_key, "
                "normalizer_version, last_seen_at, first_seen_at) VALUES (:s, :sid, :sc, "
                "'https://x.test', 't', '', 'd', 'h', :dk, '1', :ls, :ls) RETURNING id"
            ),
            {"s": source, "sid": sid, "sc": scope, "dk": f"{source}:{sid}", "ls": last_seen},
        )
        return int(row.scalar_one())


async def add_board(
    engine: AsyncEngine, ats: str, slug: str, status: str, checked: datetime | None
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO source_boards (ats, slug, status, origin, last_checked_at) "
                "VALUES (:a, :s, :st, 'seed', :c)"
            ),
            {"a": ats, "s": slug, "st": status, "c": checked},
        )


async def status_of(engine: AsyncEngine, job_id: int) -> tuple[str, int | None]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, expired_by_run_id FROM jobs WHERE id = :i"), {"i": job_id}
            )
        ).one()
        return str(row[0]), (None if row[1] is None else int(row[1]))


# --- config ---------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 10_000])
def test_board_unseen_days_bounds(bad: int) -> None:
    with pytest.raises(ValidationError):
        ExpiryConfig(board_unseen_days=bad)


def test_board_sections_load_board_unseen_days() -> None:
    clear_config_cache()
    sections = load_config("sources.yaml", SourcesConfig).root
    for ats in ("greenhouse", "lever", "ashby"):
        cfg = ExpiryConfig.model_validate(sections[ats].model_dump())
        assert cfg.board_unseen_days == 7
    assert ExpiryConfig().board_unseen_days == 7


# --- expire_dropped_board --------------------------------------------------------------------


async def test_expire_dropped_board(engine: AsyncEngine) -> None:
    run_id = await add_run(engine, "board_discovery", 0, when=NOW)
    await add_board(engine, "lever", "acme", "dropped", NOW)
    await add_board(engine, "lever", "live", "active", NOW)
    target = await add_job(engine, "1", source="lever", scope="acme", last_seen=NOW)
    other_scope = await add_job(engine, "2", source="lever", scope="other", last_seen=NOW)
    other_source = await add_job(engine, "3", source="greenhouse", scope="acme", last_seen=NOW)
    live = await add_job(engine, "4", source="lever", scope="live", last_seen=NOW)
    async with engine.begin() as conn:
        assert await expire_dropped_board(conn, source="lever", slug="acme", run_id=run_id) == 1
        assert await expire_dropped_board(conn, source="lever", slug="live", run_id=run_id) == 0
    assert await status_of(engine, target) == ("expired", run_id)
    for jid in (other_scope, other_source, live):
        assert await status_of(engine, jid) == ("active", None)
    assert await reactivate_run(engine, run_id) == 1
    assert await status_of(engine, target) == ("active", None)


# --- expire_stale_board_jobs -----------------------------------------------------------------


async def _stale_world(engine: AsyncEngine) -> dict[str, int]:
    await add_board(engine, "greenhouse", "a", "active", NOW - timedelta(hours=1))
    await add_board(engine, "greenhouse", "b", "active", NOW - timedelta(days=5))
    await add_board(engine, "greenhouse", "c", "dropped", NOW - timedelta(days=1))
    await add_board(engine, "greenhouse", "d", "rejected", NOW - timedelta(hours=1))
    ids = {
        "a_8d": await add_job(
            engine, "a8", source="greenhouse", scope="a", last_seen=NOW - timedelta(days=8)
        ),
        "a_6d": await add_job(
            engine, "a6", source="greenhouse", scope="a", last_seen=NOW - timedelta(days=6)
        ),
        "b_10d": await add_job(
            engine, "b10", source="greenhouse", scope="b", last_seen=NOW - timedelta(days=10)
        ),
        "c_new": await add_job(engine, "c1", source="greenhouse", scope="c", last_seen=NOW),
        "d_old": await add_job(
            engine, "d1", source="greenhouse", scope="d", last_seen=NOW - timedelta(days=30)
        ),
        "nobody": await add_job(
            engine, "n1", source="greenhouse", scope="ghost", last_seen=NOW - timedelta(days=30)
        ),
        "lever_a": await add_job(
            engine, "l1", source="lever", scope="a", last_seen=NOW - timedelta(days=30)
        ),
    }
    return ids


async def test_stale_board_fallback_rules(engine: AsyncEngine) -> None:
    ids = await _stale_world(engine)
    run_id = await add_run(engine, "greenhouse", 1, when=NOW)
    with time_machine.travel(NOW, tick=False):
        async with engine.begin() as conn:
            n = await expire_stale_board_jobs(
                conn, source="greenhouse", run_id=run_id, now=NOW, cfg=ExpiryConfig()
            )
    assert n == 2
    assert await status_of(engine, ids["a_8d"]) == ("expired", run_id)
    assert await status_of(engine, ids["c_new"]) == ("expired", run_id)
    # Recent enough, failing board (outage safety), rejected board, unregistered scope, other ATS.
    for key in ("a_6d", "b_10d", "d_old", "nobody", "lever_a"):
        assert (await status_of(engine, ids[key]))[0] == "active", key
    assert await reactivate_run(engine, run_id) == 2


async def test_stale_board_fallback_custom_days(engine: AsyncEngine) -> None:
    ids = await _stale_world(engine)
    run_id = await add_run(engine, "greenhouse", 1, when=NOW)
    async with engine.begin() as conn:
        await expire_stale_board_jobs(
            conn,
            source="greenhouse",
            run_id=run_id,
            now=NOW,
            cfg=ExpiryConfig(board_unseen_days=3),
        )
    assert (await status_of(engine, ids["a_6d"]))[0] == "expired"
    assert (await status_of(engine, ids["b_10d"]))[0] == "active"


# --- pipeline wiring ---------------------------------------------------------------------------


def _gh_payload() -> dict[str, Any]:
    return json.loads(GH_FIXTURE.read_text())


def _gh_adapter(engine: AsyncEngine) -> GreenhouseAdapter:
    async def sleep(_: float) -> None:
        return None

    get_settings.cache_clear()
    ctx = AdapterContext(
        settings=get_settings(),
        engine=engine,
        config=BoardsConfig(seeds=["acme"], request_interval_seconds=0),
        mode="incremental",
        now=NOW,
    )
    return GreenhouseAdapter(ctx, sleep=sleep)


async def _pipeline_world(engine: AsyncEngine, history: list[int]) -> dict[str, int]:
    for i, fetched in enumerate(history):
        await add_run(engine, "greenhouse", fetched, when=NOW - timedelta(days=3 + i))
    await add_board(engine, "greenhouse", "gone", "dropped", NOW - timedelta(days=2))
    return {
        "stale": await add_job(
            engine, "s1", source="greenhouse", scope="acme", last_seen=NOW - timedelta(days=8)
        ),
        "recent": await add_job(
            engine, "r1", source="greenhouse", scope="acme", last_seen=NOW - timedelta(days=6)
        ),
        "dropped": await add_job(engine, "g1", source="greenhouse", scope="gone", last_seen=NOW),
    }


async def _notes(engine: AsyncEngine, run_id: int) -> dict[str, Any]:
    async with engine.connect() as conn:
        return dict(
            (
                await conn.execute(
                    text("SELECT notes FROM ingest_runs WHERE id = :i"), {"i": run_id}
                )
            ).scalar_one()
        )


async def test_pipeline_fallback_on_succeeded_guarded_run(engine: AsyncEngine) -> None:
    ids = await _pipeline_world(engine, [5, 5])
    with respx.mock(assert_all_called=True) as router:
        router.get(GH.format("acme")).mock(httpx.Response(200, json=_gh_payload()))
        with time_machine.travel(NOW, tick=False):
            s = await run_ingest(engine, get_settings(), "greenhouse", adapter=_gh_adapter(engine))
    assert s.status == "succeeded"
    assert await status_of(engine, ids["stale"]) == ("expired", s.run_id)
    assert await status_of(engine, ids["dropped"]) == ("expired", s.run_id)
    assert (await status_of(engine, ids["recent"]))[0] == "active"
    notes = await _notes(engine, s.run_id)
    assert notes["guard"]["allowed"] is True and notes["guard"]["board_fallback"] == 2
    assert s.expired >= 2


@pytest.mark.parametrize(("history", "reason"), [([], "cold_start"), ([1000, 1000], "below_50pct")])
async def test_pipeline_guard_blocked_expires_nothing(
    engine: AsyncEngine, history: list[int], reason: str
) -> None:
    ids = await _pipeline_world(engine, history)
    with respx.mock(assert_all_called=True) as router:
        router.get(GH.format("acme")).mock(httpx.Response(200, json=_gh_payload()))
        with time_machine.travel(NOW, tick=False):
            s = await run_ingest(engine, get_settings(), "greenhouse", adapter=_gh_adapter(engine))
    assert s.status == "succeeded" and s.expired == 0
    notes = await _notes(engine, s.run_id)
    assert notes["guard"]["reason"] == reason and "board_fallback" not in notes["guard"]
    for jid in ids.values():
        assert (await status_of(engine, jid))[0] == "active"


async def test_pipeline_failed_run_expires_nothing(engine: AsyncEngine) -> None:
    ids = await _pipeline_world(engine, [5, 5])
    with respx.mock(assert_all_called=True) as router:
        router.get(GH.format("acme")).mock(httpx.Response(500))
        with time_machine.travel(NOW, tick=False):
            s = await run_ingest(engine, get_settings(), "greenhouse", adapter=_gh_adapter(engine))
    assert s.status == "failed" and s.expired == 0
    for jid in ids.values():
        assert (await status_of(engine, jid))[0] == "active"


async def test_pipeline_remotive_never_runs_board_fallback(engine: AsyncEngine) -> None:
    for i in range(2):
        await add_run(engine, "remotive", 1, when=NOW - timedelta(days=3 + i))
    old = await add_job(
        engine, "old", source="remotive", scope="all", last_seen=NOW - timedelta(days=30)
    )
    get_settings.cache_clear()
    with respx.mock(assert_all_called=True) as router:
        router.get(REMOTIVE_URL).mock(
            httpx.Response(200, json=json.loads(REMOTIVE_FIXTURE.read_text()))
        )
        with time_machine.travel(NOW, tick=False):
            s = await run_ingest(engine, get_settings(), "remotive")
    assert s.status == "succeeded"
    notes = await _notes(engine, s.run_id)
    assert notes["guard"]["allowed"] is True and "board_fallback" not in notes["guard"]
    assert (await status_of(engine, old))[0] == "active"  # one miss only, never age-expired


# --- board maintenance -------------------------------------------------------------------------


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "config"
    shutil.copytree(config_dir(), dest)
    data = yaml.safe_load((dest / "sources.yaml").read_text())
    for ats in ("greenhouse", "lever", "ashby"):
        data[ats]["seeds"] = []
    data["greenhouse"]["seeds"] = ["g1"]
    data["greenhouse"]["india_candidates"] = []
    (dest / "sources.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setenv("CONFIG_DIR", str(dest))
    clear_config_cache()
    get_settings.cache_clear()


async def _no_sleep(_: float) -> None:
    return None


async def test_maintenance_expires_on_drop_and_reactivates(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)
    jid = await add_job(engine, "x1", source="greenhouse", scope="g1", last_seen=NOW)
    with respx.mock(assert_all_called=False) as router:
        router.get(GH.format("g1")).mock(httpx.Response(404))
        result = await boards.run_board_maintenance(engine, get_settings(), sleep=_no_sleep)
    assert result.dropped == [{"ats": "greenhouse", "slug": "g1", "reason": "not_found"}]
    async with engine.connect() as conn:
        run = (
            (await conn.execute(text("SELECT id, notes FROM ingest_runs ORDER BY id DESC")))
            .mappings()
            .first()
        )
    assert run is not None
    assert run["notes"]["expired_on_drop"] == 1
    assert await status_of(engine, jid) == ("expired", run["id"])
    assert await reactivate_run(engine, int(run["id"])) == 1
    assert (await status_of(engine, jid))[0] == "active"


async def test_maintenance_server_error_drops_nothing(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)
    jid = await add_job(engine, "x1", source="greenhouse", scope="g1", last_seen=NOW)
    with respx.mock(assert_all_called=False) as router:
        router.get(GH.format("g1")).mock(httpx.Response(500))
        result = await boards.run_board_maintenance(engine, get_settings(), sleep=_no_sleep)
    assert result.dropped == []
    async with engine.connect() as conn:
        notes = (
            await conn.execute(text("SELECT notes FROM ingest_runs ORDER BY id DESC LIMIT 1"))
        ).scalar_one()
    assert notes["expired_on_drop"] == 0
    assert await status_of(engine, jid) == ("active", None)
