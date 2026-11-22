import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typer.testing import CliRunner

from ranker import cli
from ranker.config_loader import clear_config_cache, config_dir
from ranker.ingest import boards
from ranker.ingest.boards import (
    BoardSeed,
    InvalidBoardSlug,
    active_boards,
    boards_report,
    checked_url,
    count_in_scope,
    record_board_check,
    sync_seeds,
    valid_slug,
)
from ranker.ingest.sources.base import RawJob
from ranker.settings import get_settings

SPIKE = Path(__file__).resolve().parents[2] / "docs" / "decisions" / "0001-sources-spike.json"


def _job(**kw: Any) -> RawJob:
    base: dict[str, Any] = {
        "source": "ashby",
        "source_job_id": "1",
        "url": "https://jobs.ashbyhq.com/x/1",
        "title": "Backend Engineer",
        "company_name": "X",
        "description_raw": "Python services.",
        "location_text": "Bengaluru",
        "country_hint": "IN",
    }
    base.update(kw)
    return RawJob.model_validate(base)


# --- slug validation and URL building -------------------------------------------------------


@pytest.mark.parametrize("slug", ["../x", "Foo", "a?b", "", "x" * 64, "-a", "a b", "a/b", "a\n"])
def test_invalid_slugs_rejected(slug: str) -> None:
    assert not valid_slug(slug)
    with pytest.raises(InvalidBoardSlug):
        checked_url("api.ashbyhq.com", "/posting-api/job-board/{slug}", slug)
    with pytest.raises(ValueError):
        BoardSeed(ats="ashby", slug=slug)


def test_valid_slugs_and_host_allow_list() -> None:
    assert valid_slug("a") and valid_slug("x" * 63) and valid_slug("acme-2")
    assert (
        checked_url("api.ashbyhq.com", "/posting-api/job-board/{slug}", "acme")
        == "https://api.ashbyhq.com/posting-api/job-board/acme"
    )
    with pytest.raises(ValueError):
        checked_url("evil.example.com", "/x/{slug}", "acme")


# --- registry transitions -------------------------------------------------------------------


@pytest.mark.usefixtures("clean_db")
async def test_seed_sync_is_idempotent_and_never_resurrects(engine: AsyncEngine) -> None:
    seeds = [BoardSeed(ats="ashby", slug="acme"), BoardSeed(ats="lever", slug="beta")]
    assert await sync_seeds(engine, seeds) == 2
    assert await sync_seeds(engine, seeds) == 0
    assert [b.slug for b in await active_boards(engine, "ashby")] == ["acme"]
    assert await record_board_check(engine, "ashby", "acme", status_code=404, in_scope_count=0) == (
        "dropped"
    )
    assert await sync_seeds(engine, seeds) == 0
    assert await active_boards(engine, "ashby") == []
    rows = {(b.ats, b.slug): b for b in await boards_report(engine)}
    assert rows[("ashby", "acme")].status == "dropped"
    assert rows[("ashby", "acme")].reason == "not_found"


@pytest.mark.usefixtures("clean_db")
async def test_zero_in_scope_twice_drops(engine: AsyncEngine) -> None:
    await sync_seeds(engine, [BoardSeed(ats="greenhouse", slug="acme")])
    assert await record_board_check(
        engine, "greenhouse", "acme", status_code=200, in_scope_count=0
    ) == ("active")
    # A non-empty check resets the counter.
    assert await record_board_check(
        engine, "greenhouse", "acme", status_code=200, in_scope_count=3
    ) == ("active")
    assert await record_board_check(
        engine, "greenhouse", "acme", status_code=200, in_scope_count=0
    ) == ("active")
    assert await record_board_check(
        engine, "greenhouse", "acme", status_code=200, in_scope_count=0
    ) == ("dropped")
    row = (await boards_report(engine))[0]
    assert row.reason == "zero_in_scope" and row.last_checked_at is not None


@pytest.mark.usefixtures("clean_db")
async def test_adapter_checks_do_not_advance_zero_counter(engine: AsyncEngine) -> None:
    await sync_seeds(engine, [BoardSeed(ats="greenhouse", slug="acme")])
    for _ in range(3):
        status = await record_board_check(
            engine, "greenhouse", "acme", status_code=200, in_scope_count=0, count_zero=False
        )
        assert status == "active"


@pytest.mark.usefixtures("clean_db")
async def test_record_unknown_board_and_server_error(engine: AsyncEngine) -> None:
    assert await record_board_check(engine, "ashby", "nope", status_code=200, in_scope_count=1) == (
        "unknown"
    )
    await sync_seeds(engine, [BoardSeed(ats="ashby", slug="acme")])
    assert await record_board_check(engine, "ashby", "acme", status_code=500, in_scope_count=0) == (
        "seed"
    )


@pytest.mark.usefixtures("clean_db")
async def test_db_check_constraints(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:  # ats ashby accepted
        await conn.execute(
            text(
                "INSERT INTO source_boards (ats, slug, status, origin) "
                "VALUES ('ashby', 'ok-1', 'seed', 'seed')"
            )
        )
    for slug, ats in (("../x", "ashby"), ("Foo", "ashby"), ("fine", "workday")):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO source_boards (ats, slug, status, origin) "
                        "VALUES (:a, :s, 'seed', 'seed')"
                    ),
                    {"a": ats, "s": slug},
                )


# --- seeds equal the spike-derived set ------------------------------------------------------


def test_yaml_seeds_match_spike() -> None:
    spike = json.loads(SPIKE.read_text())
    expected: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "ashby": set()}
    ok_status = {"200"}
    for b in spike["boards"]:
        if str(b["status"]) in ok_status and int(b["total"]) >= 1:
            expected[b["ats"]].add(b["slug"])
    cfg = yaml.safe_load((config_dir() / "sources.yaml").read_text())
    for ats in ("greenhouse", "lever", "ashby"):
        seeds = cfg[ats]["seeds"]
        assert set(seeds) == expected[ats], ats
        assert len(seeds) == len(set(seeds))
        assert all(valid_slug(s) for s in seeds)
    assert "remotecom" not in cfg["greenhouse"]["seeds"]
    four_oh_four = {(b["ats"], b["slug"]) for b in spike["boards"] if str(b["status"]) == "404"}
    for ats in ("greenhouse", "lever", "ashby"):
        assert not {(ats, s) for s in cfg[ats]["seeds"]} & four_oh_four
    assert cfg["greenhouse"]["india_candidates"]


# --- count_in_scope -------------------------------------------------------------------------


def test_count_in_scope() -> None:
    jobs = [
        _job(source_job_id="1"),  # India on-site tech
        _job(source_job_id="2", title="Sales Manager"),  # not tech
        _job(
            source_job_id="3",
            location_text="Luxembourg",
            country_hint="LU",
            workplace_hint="onsite",
        ),
        _job(
            source_job_id="4",
            location_text="Remote",
            country_hint=None,
            workplace_hint="remote",
            region_hint="Worldwide",
        ),
        _job(
            source_job_id="5",
            location_text="Remote",
            country_hint=None,
            workplace_hint="remote",
            region_hint="USA only",
        ),
    ]
    assert count_in_scope(jobs) == 2


# --- discovery, recheck, maintenance, CLI (Task 3) -----------------------------------------

GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LV = "https://api.lever.co/v0/postings/{}"
LV_EU = "https://api.eu.lever.co/v0/postings/{}"
AB = "https://api.ashbyhq.com/posting-api/job-board/{}"


async def _no_sleep(_: float) -> None:
    return None


def _india_titles(n: int) -> list[str]:
    return [f"Backend Engineer {i}" for i in range(n)]


def _gh_payload(n: int) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": 9000 + i,
                "title": title,
                "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{9000 + i}",
                "location": {"name": "Bengaluru, India"},
                "departments": [{"name": "Engineering"}],
                "content": "&lt;p&gt;Python services.&lt;/p&gt;",
            }
            for i, title in enumerate(_india_titles(n))
        ]
    }


def _lv_payload(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"00000000-0000-4000-8000-{i:012d}",
            "text": title,
            "categories": {
                "location": "Pune",
                "department": "Engineering",
                "allLocations": ["Pune"],
            },
            "country": "IN",
            "workplaceType": "onsite",
            "createdAt": 1763640000000,
            "hostedUrl": f"https://jobs.lever.co/x/{i}",
            "descriptionPlain": "Python services.",
        }
        for i, title in enumerate(_india_titles(n))
    ]


def _ab_payload(n: int) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": f"ab-{i}",
                "title": title,
                "department": "Engineering",
                "location": "Hyderabad",
                "isListed": True,
                "jobUrl": f"https://jobs.ashbyhq.com/x/ab-{i}",
                "descriptionPlain": "Python services.",
                "address": {"postalAddress": {"addressCountry": "India"}},
            }
            for i, title in enumerate(_india_titles(n))
        ]
    }


def _cfg(**kw: Any) -> boards.BoardsConfig:
    return boards.BoardsConfig(request_interval_seconds=1.0, **kw)


async def _discover(
    engine: AsyncEngine, cfg: boards.BoardsConfig, sleeps: list[float] | None = None
) -> boards.DiscoveryResult:
    async def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    async with boards.build_client() as client:
        return await boards.discover_boards(engine, get_settings(), cfg, client, sleep=sleep)


async def _add_job(engine: AsyncEngine, sid: str, url: str, description: str, company: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, url, title, company_id, description_raw, "
                "description_clean, content_hash, dedupe_key, normalizer_version, status) VALUES "
                "('remotive', :sid, :url, 'Backend Engineer', :c, :d, :d, 'h', :sid, '1', 'active')"
            ),
            {"sid": sid, "url": url, "d": description, "c": company},
        )


async def _add_company(engine: AsyncEngine, normalized: str) -> int:
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "INSERT INTO companies (name, normalized_name) VALUES (:n, :n) RETURNING id"
                    ),
                    {"n": normalized},
                )
            ).scalar_one()
        )


def test_extract_board_links() -> None:
    blob = (
        "Apply at https://boards.greenhouse.io/Acme/jobs/1 or "
        "https://job-boards.eu.greenhouse.io/eu-co/jobs/2 and https://jobs.lever.co/lvco/abc "
        "plus https://jobs.eu.lever.co/eulv and https://jobs.ashbyhq.com/ashco/xyz "
        "also https://jobs.lever.co/../evil and https://jobs.lever.co/?x=1 "
        "and https://boards.greenhouse.io/embed/job_board?for=zzz"
    )
    found = set(boards.extract_board_links(blob))
    assert found == {
        ("greenhouse", "acme", "global"),
        ("greenhouse", "eu-co", "global"),
        ("lever", "lvco", "global"),
        ("lever", "eulv", "eu"),
        ("ashby", "ashco", "global"),
    }
    # Hostile input stays linear and yields nothing.
    assert boards.extract_board_links("jobs.lever.co/" + "a" * 100_000)[0][1] == "a" * 63
    assert boards.extract_board_links("x" * 1_000_000) == []


def test_company_slug_variants() -> None:
    assert boards.company_slug_variants("acme labs") == ["acmelabs", "acme-labs", "acmelabshq"]
    assert boards.company_slug_variants("acme") == ["acme", "acmehq"]
    assert boards.company_slug_variants("!!!") == []


@pytest.mark.usefixtures("clean_db")
async def test_discovery_accept_reject_404_from_job_links(engine: AsyncEngine) -> None:
    # One job per company, so no company-name guesses are made (needs >= 3 active jobs).
    c1, c2, c3, c4 = [await _add_company(engine, f"linker{i}") for i in range(4)]
    await _add_job(engine, "1", "https://boards.greenhouse.io/goodco/jobs/1", "", c1)
    await _add_job(engine, "2", "https://example.com/2", "see jobs.lever.co/smallco/x", c2)
    await _add_job(engine, "3", "https://jobs.ashbyhq.com/gone/3", "", c3)
    await _add_job(engine, "4", "https://example.com/4", "jobs.ashbyhq.com/../../etc/passwd", c4)
    sleeps: list[float] = []
    with respx.mock(assert_all_called=True) as router:
        router.get(GH.format("goodco")).mock(httpx.Response(200, json=_gh_payload(5)))
        router.get(LV.format("smallco")).mock(httpx.Response(200, json=_lv_payload(2)))
        router.get(AB.format("gone")).mock(httpx.Response(404))
        result = await _discover(engine, _cfg(), sleeps)
    assert [a["slug"] for a in result.added] == ["goodco"] and result.added[0]["in_scope"] == 5
    assert {(r["slug"], r["reason"]) for r in result.rejected} == {
        ("smallco", "below_threshold:2"),
        ("gone", "not_found"),
    }
    assert result.probes == 3 and result.calls_used == 3  # one call per probe (lever 200 on global)
    assert sleeps == [1.0, 1.0]
    rows = {(b.ats, b.slug): b for b in await boards_report(engine)}
    good = rows[("greenhouse", "goodco")]
    assert (good.status, good.origin, good.in_scope_count) == ("active", "discovered", 5)
    assert rows[("lever", "smallco")].status == "rejected"
    assert rows[("lever", "smallco")].reason == "below_threshold:2"
    assert rows[("ashby", "gone")].reason == "not_found"
    assert (
        len(rows) == 3
    )  # nothing probed for the traversal link; the company "linker" is too small
    # Known pairs are not probed again (no routes mocked: any request would raise).
    with respx.mock():
        again = await _discover(engine, _cfg())
    assert again.probes == 0


@pytest.mark.usefixtures("clean_db")
async def test_company_name_variants_are_probed(engine: AsyncEngine) -> None:
    cid = await _add_company(engine, "zorbo")
    for i in range(3):
        await _add_job(engine, str(i), f"https://example.com/{i}", "", cid)
    with respx.mock() as router:
        for slug in ("zorbo", "zorbohq"):
            router.get(GH.format(slug)).mock(httpx.Response(404))
            router.get(LV.format(slug)).mock(httpx.Response(404))
            router.get(LV_EU.format(slug)).mock(httpx.Response(404))
            router.get(AB.format(slug)).mock(httpx.Response(200, json=_ab_payload(6)))
        result = await _discover(engine, _cfg())
    assert result.probes == 6
    assert {(a["ats"], a["slug"]) for a in result.added} == {
        ("ashby", "zorbo"),
        ("ashby", "zorbohq"),
    }


@pytest.mark.usefixtures("clean_db")
async def test_india_candidates_probed_on_every_ats(engine: AsyncEngine) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(GH.format("indico")).mock(httpx.Response(404))
        router.get(LV.format("indico")).mock(httpx.Response(404))
        eu = router.get(LV_EU.format("indico")).mock(httpx.Response(200, json=_lv_payload(5)))
        router.get(AB.format("indico")).mock(httpx.Response(200, json=_ab_payload(5)))
        result = await _discover(engine, _cfg(india_candidates=["indico"]))
    assert result.probes == 3 and eu.called
    assert {(a["ats"], a["slug"]) for a in result.added} == {
        ("lever", "indico"),
        ("ashby", "indico"),
    }
    rows = {(b.ats, b.slug): b for b in await boards_report(engine)}
    assert rows[("lever", "indico")].host == "eu" and rows[("lever", "indico")].status == "active"
    assert rows[("greenhouse", "indico")].status == "rejected"


@pytest.mark.usefixtures("clean_db")
async def test_probe_cap_rotation_and_reprobe_window(engine: AsyncEngine) -> None:
    slugs = [f"cand{i:02d}" for i in range(8)]  # 24 pairs
    cfg = _cfg(india_candidates=slugs, max_discovery_probes=5)
    seen: set[tuple[str, str]] = set()
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://boards-api\.greenhouse\.io/.*").mock(httpx.Response(404))
        router.get(url__regex=r"https://api\.ashbyhq\.com/.*").mock(httpx.Response(404))
        router.get(url__regex=r"https://api(\.eu)?\.lever\.co/.*").mock(httpx.Response(404))
        for _ in range(2):
            result = await _discover(engine, cfg)
            assert result.probes == 5
            keys = {(r["ats"], r["slug"]) for r in result.rejected}
            assert len(keys) == 5 and not keys & seen  # rotates through untried pairs
            seen |= keys
    # A rejected board is re-probed only once its last check is older than 30 days.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE source_boards SET last_checked_at = now() - interval '31 days' "
                "WHERE (ats, slug) = ('ashby', 'cand00')"
            )
        )
    tiny = _cfg(india_candidates=["cand00"], max_discovery_probes=40)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(AB.format("cand00")).mock(httpx.Response(404))
        router.get(LV.format("cand00")).mock(httpx.Response(404))
        router.get(LV_EU.format("cand00")).mock(httpx.Response(404))
        router.get(GH.format("cand00")).mock(httpx.Response(404))
        again = await _discover(engine, tiny)
    assert route.called
    assert ("ashby", "cand00") in {(r["ats"], r["slug"]) for r in again.rejected}


@pytest.mark.usefixtures("clean_db")
async def test_invalid_candidate_slugs_never_probed(engine: AsyncEngine) -> None:
    cfg = _cfg(india_candidates=["../x", "Bad Slug", "a?b=1", "ok-co"])
    with respx.mock(assert_all_called=True) as router:  # any other URL would raise
        router.get(GH.format("ok-co")).mock(httpx.Response(404))
        router.get(LV.format("ok-co")).mock(httpx.Response(404))
        router.get(LV_EU.format("ok-co")).mock(httpx.Response(404))
        router.get(AB.format("ok-co")).mock(httpx.Response(404))
        result = await _discover(engine, cfg)
    assert result.probes == 3


def _config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seeds: dict[str, list[str]], cands: list[str]
) -> None:
    dest = tmp_path / "config"
    shutil.copytree(config_dir(), dest)
    data = yaml.safe_load((dest / "sources.yaml").read_text())
    for ats in ("greenhouse", "lever", "ashby"):
        data[ats]["seeds"] = seeds.get(ats, [])
    data["greenhouse"]["india_candidates"] = cands
    (dest / "sources.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setenv("CONFIG_DIR", str(dest))
    clear_config_cache()


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_notes_and_recheck_drops(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_dir(tmp_path, monkeypatch, {"greenhouse": ["g1"], "lever": ["l1"]}, ["indico"])
    get_settings.cache_clear()

    def routes(router: respx.MockRouter) -> None:
        router.get(GH.format("g1")).mock(httpx.Response(200, json={"jobs": []}))
        router.get(LV.format("l1")).mock(httpx.Response(404))
        router.get(LV_EU.format("l1")).mock(httpx.Response(404))
        router.get(GH.format("indico")).mock(httpx.Response(404))
        router.get(LV.format("indico")).mock(httpx.Response(404))
        router.get(LV_EU.format("indico")).mock(httpx.Response(404))
        router.get(AB.format("indico")).mock(httpx.Response(200, json=_ab_payload(2)))

    with respx.mock(assert_all_called=False) as router:
        routes(router)
        first = await boards.run_board_maintenance(engine, get_settings(), sleep=_no_sleep)
    assert first.dropped == [{"ats": "lever", "slug": "l1", "reason": "not_found"}]
    assert len(first.rejected) == 3 and first.added == []

    with respx.mock(assert_all_called=False) as router:
        routes(router)
        second = await boards.run_board_maintenance(engine, get_settings(), sleep=_no_sleep)
    assert second.dropped == [{"ats": "greenhouse", "slug": "g1", "reason": "zero_in_scope"}]
    assert second.probes == 0  # recently rejected candidates are not re-probed

    async with engine.connect() as conn:
        runs = (await conn.execute(text("SELECT * FROM ingest_runs ORDER BY id"))).mappings().all()
    assert [r["source"] for r in runs] == ["board_discovery", "board_discovery"]
    assert all(r["mode"] == "discovery" and r["finished_at"] is not None for r in runs)
    assert runs[0]["calls_used"] > 0 and runs[0]["status"] == "succeeded"
    notes = runs[0]["notes"]
    assert set(notes) >= {"added", "rejected", "dropped"}
    assert notes["dropped"] == [{"ats": "lever", "slug": "l1", "reason": "not_found"}]
    assert {(r["ats"], r["slug"]) for r in notes["rejected"]} == {
        ("greenhouse", "indico"),
        ("lever", "indico"),
        ("ashby", "indico"),
    }
    assert runs[1]["notes"]["dropped"][0]["slug"] == "g1"
    rows = {(b.ats, b.slug): b for b in await boards_report(engine)}
    assert rows[("lever", "l1")].status == "dropped"
    assert rows[("greenhouse", "g1")].status == "dropped"


def test_cli_boards_report_and_discover(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def prep() -> None:
        eng = create_async_engine(database_url)
        try:
            async with eng.begin() as conn:
                await conn.execute(text("TRUNCATE source_boards, ingest_runs RESTART IDENTITY"))
                await conn.execute(
                    text(
                        "INSERT INTO source_boards (ats, slug, status, origin, in_scope_count, "
                        "reason) "
                        "VALUES ('lever', 'cred', 'active', 'seed', 7, NULL), "
                        "('ashby', 'old', 'dropped', 'discovered', 0, 'not_found')"
                    )
                )
        finally:
            await eng.dispose()

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    asyncio.run(prep())
    out = CliRunner().invoke(cli.app, ["boards", "report"])
    assert out.exit_code == 0
    lines = [json.loads(line) for line in out.stdout.splitlines()]
    assert [(r["ats"], r["slug"], r["status"]) for r in lines] == [
        ("ashby", "old", "dropped"),
        ("lever", "cred", "active"),
    ]
    assert set(lines[0]) == {
        "ats", "slug", "host", "status", "origin", "in_scope_count", "last_checked_at", "reason",
    }  # fmt: skip
    assert lines[1]["in_scope_count"] == 7 and lines[0]["reason"] == "not_found"

    _config_dir(tmp_path, monkeypatch, {}, [])
    with respx.mock() as router:
        router.get(LV.format("cred")).mock(httpx.Response(200, json=_lv_payload(6)))
        res = CliRunner().invoke(cli.app, ["boards", "discover"])
    assert res.exit_code == 0
    summary = json.loads(res.stdout.splitlines()[-1])
    assert summary["probes"] == 0 and summary["added"] == [] and summary["rechecked"] == 1


@pytest.mark.parametrize(
    "module", ["ranker.ingest.boards", "ranker.ingest.sources.ashby", "ranker.cli"]
)
def test_modules_import_standalone(module: str) -> None:
    """Each entry point imports in a fresh interpreter (guards against import cycles)."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-400:]
