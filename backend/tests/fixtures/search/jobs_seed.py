"""Deterministic seeded jobs for search tests and the CI Schemathesis job.

Run ``python -m tests.fixtures.search.jobs_seed --n 50`` against a migrated DATABASE_URL.
"""

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.embeddings.service import vector_literal
from ranker.ingest.skills import default_matcher, skills_text

_INSERT_COMPANY = text(
    "INSERT INTO companies (name, normalized_name) VALUES (:n, :nn) "
    "ON CONFLICT (normalized_name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
)

_INSERT_JOB = text(
    "INSERT INTO jobs (source, source_job_id, url, title, company_id, description_raw, "
    "description_clean, city, cities, country_code, remote_type, remote_eligibility, "
    "seniority, employment_type, salary_min_inr_annual, salary_max_inr_annual, "
    "salary_is_estimate, skills, skills_text, ghost_reasons, posted_at, first_seen_at, "
    "last_seen_at, onsite_days_per_week, content_hash, dedupe_key, normalizer_version, "
    "embedding, embedding_model) "
    "VALUES (:source, :sid, :url, :title, :cid, :desc, :desc, :city, CAST(:cities AS text[]), "
    ":cc, :rt, :re, :sen, :et, :smin, :smax, :est, CAST(:skills AS text[]), :st, "
    "CAST(:ghost AS text[]), :posted, :first_seen, :last_seen, :onsite, 'h', :dk, 'test', "
    "CAST(:v AS vector), 'fake') RETURNING id"
)

_counter = 0


def _next() -> int:
    global _counter
    _counter += 1
    return _counter


async def add_job(
    engine: AsyncEngine,
    *,
    title: str,
    skills: Sequence[str] = (),
    description: str = "",
    company: str = "Acme",
    source: str = "remotive",
    city: str | None = None,
    cities: Sequence[str] = (),
    country_code: str | None = None,
    remote_type: str = "unknown",
    remote_eligibility: str = "unknown",
    seniority: str = "unknown",
    employment_type: str = "full_time",
    salary_min_inr: int | None = None,
    salary_max_inr: int | None = None,
    salary_is_estimate: bool = False,
    ghost_reasons: Sequence[str] = (),
    posted_days_ago: float = 1,
    last_seen_hours_ago: float = 2,
    onsite_days: int | None = None,
    embed_text: str | None = None,
) -> int:
    """Insert one active canonical job; skills are canonical names or raw skill spellings."""
    matcher = default_matcher()
    canonical = sorted({c for s in skills for c in (matcher.extract(s) or [s.lower()])})
    n = _next()
    now = datetime.now(UTC)
    vec = FakeEmbedder().embed([embed_text or f"{title} {description}"])[0]
    async with engine.begin() as conn:
        cid = (
            await conn.execute(
                _INSERT_COMPANY, {"n": company, "nn": company.lower().replace(" ", "")}
            )
        ).scalar_one()
        row = await conn.execute(
            _INSERT_JOB,
            {
                "source": source, "sid": f"seed-{n}", "url": f"https://jobs.test/{n}",
                "title": title, "cid": cid, "desc": description or title, "city": city,
                "cities": list(cities) or ([city] if city else []), "cc": country_code,
                "rt": remote_type, "re": remote_eligibility, "sen": seniority,
                "et": employment_type, "smin": salary_min_inr, "smax": salary_max_inr,
                "est": salary_is_estimate, "skills": canonical,
                "st": skills_text(canonical), "ghost": list(ghost_reasons),
                "posted": now - timedelta(days=posted_days_ago),
                "first_seen": now - timedelta(days=posted_days_ago),
                "last_seen": now - timedelta(hours=last_seen_hours_ago),
                "onsite": onsite_days, "dk": f"seed-{n}", "v": vector_literal(vec),
            },
        )  # fmt: skip
        return int(row.scalar_one())


async def seed_decoys(engine: AsyncEngine) -> dict[str, int]:
    """Tokenisation decoys: symbol skills versus plain-letter lookalikes."""
    specs: dict[str, dict[str, object]] = {
        "cpp": {"title": "C++ Developer", "skills": ["C++"], "embed_text": "C++ developer"},
        "c": {"title": "C Firmware Developer", "skills": ["C programming"]},
        "csharp": {"title": "C# .NET Developer", "skills": ["C#", ".NET"]},
        "go": {"title": "Go Backend Engineer", "skills": ["Golang"]},
        "node": {"title": "Node.js API Developer", "skills": ["Node.js"]},
        "senior": {
            "title": "Senior Backend Developer",
            "skills": ["Python", "Kafka"],
            "description": "We need a senior backend developer to build python and kafka services.",
        },
    }
    out: dict[str, int] = {}
    for label, spec in specs.items():
        out[label] = await add_job(engine, **spec)  # pyright: ignore[reportArgumentType]
    return out


async def seed_bulk(engine: AsyncEngine, n: int, *, city_every: int = 10) -> None:
    """``n`` active canonical jobs; every ``city_every``-th one is in Pune."""
    embedder = FakeEmbedder()
    now = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for i in range(n):
        title = f"Backend Engineer {i}"
        rows.append(
            {
                "source": "remotive", "sid": f"bulk-{i}", "url": f"https://jobs.test/bulk/{i}",
                "title": title, "cid": None, "desc": f"{title} building services", "city":
                "Pune" if i % city_every == 0 else "Bengaluru",
                "cities": ["Pune"] if i % city_every == 0 else ["Bengaluru"], "cc": "IN",
                "rt": "onsite", "re": "unknown", "sen": "mid", "et": "full_time",
                "smin": None, "smax": None, "est": False, "skills": ["python"],
                "st": "python", "ghost": [], "posted": now, "first_seen": now,
                "last_seen": now, "onsite": None, "dk": f"bulk-{i}",
                "v": vector_literal(embedder.embed([title + str(i)])[0]),
            }
        )  # fmt: skip
    async with engine.begin() as conn:
        await conn.execute(_INSERT_JOB, rows)


async def _main(n: int) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        await seed_decoys(engine)
        titles = ["Backend Engineer", "Data Scientist", "Frontend Developer", "DevOps Engineer"]
        cities = ["Bengaluru", "Pune", "Hyderabad", "Mumbai"]
        for i in range(max(0, n - 6)):
            await add_job(
                engine,
                title=f"{titles[i % 4]} {i}",
                skills=[["Python", "Django"], ["Python", "SQL"], ["React"], ["Docker", "AWS"]][
                    i % 4
                ],
                company=f"Company {i % 7}",
                source=["remotive", "greenhouse", "lever", "adzuna"][i % 4],
                city=cities[i % 4],
                country_code="IN",
                remote_type=["onsite", "hybrid", "remote", "unknown"][i % 4],
                seniority=["junior", "mid", "senior", "unknown"][i % 4],
                salary_min_inr=(600_000 + 100_000 * i) if i % 3 == 0 else None,
                salary_max_inr=(900_000 + 100_000 * i) if i % 3 == 0 else None,
                posted_days_ago=i % 30,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=50)
    asyncio.run(_main(parser.parse_args().n))
