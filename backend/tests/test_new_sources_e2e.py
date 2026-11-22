import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.config_loader import clear_config_cache
from ranker.ingest.expiry import purge_source
from ranker.ingest.pipeline import run_ingest
from ranker.ingest.sources import apiutil
from ranker.settings import Settings, get_settings
from ranker.worker.scheduler import build_scheduler, register_source_jobs

FIX = Path(__file__).parent / "fixtures"
HOST = "https://internshala.com"

pytestmark = pytest.mark.usefixtures("clean_db")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(seconds: float) -> None:
        return None

    monkeypatch.setattr(apiutil, "sleep", fake)
    clear_config_cache()


def _settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def _json(name: str) -> Any:
    return json.loads((FIX / name).read_text())


def _serve(source: str, ish_age: str = "3 days ago") -> None:
    if source == "arbeitnow":
        respx.get("https://www.arbeitnow.com/api/job-board-api").mock(
            side_effect=[
                httpx.Response(200, json=_json("arbeitnow_page1.json")),
                httpx.Response(200, json=_json("arbeitnow_page2.json")),
            ]
        )
    elif source == "remoteok":
        respx.get("https://remoteok.com/api").mock(
            return_value=httpx.Response(200, json=_json("remoteok.json"))
        )
    elif source == "jobicy":
        respx.get("https://jobicy.com/api/v2/remote-jobs").mock(
            return_value=httpx.Response(200, json=_json("jobicy.json"))
        )
    else:
        respx.get(f"{HOST}/robots.txt").mock(
            return_value=httpx.Response(200, text=(FIX / "internshala_robots.txt").read_text())
        )
        p1 = (FIX / "internshala_jobs_p1.html").read_text().replace("3 days ago", ish_age)
        p2 = (FIX / "internshala_jobs_p2.html").read_text().replace("3 days ago", ish_age)
        respx.get(f"{HOST}/jobs/python-jobs/").mock(return_value=httpx.Response(200, text=p1))
        respx.get(f"{HOST}/jobs/python-jobs/page-2/").mock(
            return_value=httpx.Response(200, text=p2)
        )
        respx.get(f"{HOST}/internships/python-internship/").mock(
            return_value=httpx.Response(
                200, text=(FIX / "internshala_internships_p1.html").read_text()
            )
        )
        respx.get(url__regex=r".*/(job|internship)/detail.*").mock(
            side_effect=AssertionError("detail page fetched")
        )


async def _run(engine: AsyncEngine, source: str) -> Any:
    return await run_ingest(engine, _settings(), source)


@pytest.fixture
def internshala_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import shutil

    import yaml

    from ranker.config_loader import config_dir

    dest = tmp_path / "config"
    shutil.copytree(config_dir(), dest)
    data = yaml.safe_load((dest / "sources.yaml").read_text())
    data["internshala"]["job_categories"] = ["python-jobs"]
    data["internshala"]["internship_categories"] = ["python-internship"]
    (dest / "sources.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setenv("CONFIG_DIR", str(dest))
    clear_config_cache()


EXPECT: dict[str, dict[str, Any]] = {
    "arbeitnow": {"fetched": 3, "inserted": 2, "prefix": "https://www.arbeitnow.com/"},
    "remoteok": {"fetched": 3, "inserted": 3, "prefix": "https://remoteok.com/remote-jobs/"},
    "jobicy": {"fetched": 2, "inserted": 2, "prefix": "https://jobicy.com/jobs/"},
    "internshala": {"fetched": 7, "inserted": 6, "prefix": f"{HOST}/"},
}


async def _jobs(engine: AsyncEngine, source: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT * FROM jobs WHERE source = :s ORDER BY source_job_id"), {"s": source}
        )
        return [dict(r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]


@respx.mock
@pytest.mark.usefixtures("internshala_config")
@pytest.mark.parametrize("source", sorted(EXPECT))
async def test_run_and_idempotent_rerun(engine: AsyncEngine, source: str) -> None:
    exp = EXPECT[source]
    _serve(source)
    first = await _run(engine, source)
    assert first.status == "succeeded"
    assert first.fetched == exp["fetched"] and first.inserted == exp["inserted"]
    async with engine.connect() as conn:
        run = (
            (
                await conn.execute(
                    text("SELECT * FROM ingest_runs WHERE id = :i"), {"i": first.run_id}
                )
            )
            .mappings()
            .one()
        )
    assert run["fetched"] == exp["fetched"] and run["inserted"] == exp["inserted"]
    assert run["in_scope"] == exp["inserted"]
    rows = await _jobs(engine, source)
    assert len(rows) == exp["inserted"]
    for r in rows:
        assert r["source"] == source and r["status"] == "active"
        assert r["url"].startswith(exp["prefix"]) and r["embedding"] is not None
    before = {r["source_job_id"]: (r["content_hash"], r["updated_at"]) for r in rows}

    respx.clear()
    _serve(source, ish_age="5 days ago")  # the age label changes; stored content must not
    second = await _run(engine, source)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, exp["inserted"])
    after = {
        r["source_job_id"]: (r["content_hash"], r["updated_at"])
        for r in await _jobs(engine, source)
    }
    assert after == before


@respx.mock
@pytest.mark.usefixtures("internshala_config")
async def test_internshala_stored_fields(engine: AsyncEngine) -> None:
    _serve("internshala")
    await _run(engine, "internshala")
    by = {r["source_job_id"]: r for r in await _jobs(engine, "internshala")}
    assert set(by) == {"1001", "1002", "1003", "1004", "2001", "2002"}  # 2003 is out of scope
    for r in by.values():
        assert r["country_code"] == "IN" and r["remote_eligibility"] == "india"
        assert r["skills"] and "ago" not in r["description_clean"]
    assert by["2001"]["employment_type"] == "internship"
    assert by["1001"]["salary_min_inr_annual"] == 300000
    assert by["1001"]["employment_type"] == "full_time"
    assert by["1003"]["employment_type"] == "part_time"
    assert by["1002"]["remote_type"] == "remote"


@respx.mock
@pytest.mark.usefixtures("internshala_config")
async def test_purge_isolated_per_source(engine: AsyncEngine) -> None:
    _serve("internshala")
    _serve("arbeitnow")
    await _run(engine, "internshala")
    await _run(engine, "arbeitnow")
    await purge_source(engine, "internshala")
    ish = await _jobs(engine, "internshala")
    assert ish and all(
        r["status"] == "removed"
        and r["url"] == ""
        and r["description_clean"] == ""
        and r["embedding"] is None
        for r in ish
    )
    arb = await _jobs(engine, "arbeitnow")
    assert arb and all(r["status"] == "active" and r["url"] for r in arb)


def test_schedules_registered_from_repo_config() -> None:
    scheduler = build_scheduler()
    register_source_jobs(
        scheduler, create_async_engine("postgresql+psycopg://u:p@localhost/x"), _settings()
    )
    expected = {
        "internshala": (("22", "35"), ("10", "35")),
        "arbeitnow": (("23", "5"), ("11", "5")),
        "remoteok": (("23", "15"), ("11", "15")),
        "jobicy": (("23", "20"), ("11", "20")),
    }
    for name, (morning, intraday) in expected.items():
        m = {f.name: str(f) for f in scheduler.get_job(f"ingest:{name}").trigger.fields}  # pyright: ignore
        i = {f.name: str(f) for f in scheduler.get_job(f"ingest:{name}:1").trigger.fields}  # pyright: ignore
        assert (m["hour"], m["minute"]) == morning
        assert (i["hour"], i["minute"]) == intraday
    for name in ("remoteok", "jobicy"):
        job = scheduler.get_job(f"expire_ttl:{name}")
        assert job is not None
        f = {x.name: str(x) for x in job.trigger.fields}  # pyright: ignore
        assert (f["hour"], f["minute"]) == ("0", "30")
    for name in ("arbeitnow", "internshala"):
        assert scheduler.get_job(f"expire_ttl:{name}") is None
    assert not any("himalayas" in j.id for j in scheduler.get_jobs())
