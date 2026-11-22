import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import config_dir
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
