import gzip
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.evals.snapshot import (
    SHA_NAME,
    SNAPSHOT_NAME,
    SnapshotError,
    export_snapshot,
    load_snapshot,
)
from tests.fixtures.search.jobs_seed import add_job

pytestmark = pytest.mark.usefixtures("clean_db")

_STATE = (
    "SELECT id, title, tsv::text AS tsv, skills_tsv::text AS skills_tsv, "
    "embedding::text AS emb, status, canonical_job_id FROM jobs ORDER BY id"
)


async def _seed(engine: AsyncEngine) -> list[int]:
    ids: list[int] = []
    for i in range(8):
        ids.append(
            await add_job(
                engine,
                title=f"Backend Engineer {i}",
                skills=["Python", "C++"] if i % 2 else ["Go"],
                description=f"Build service number {i} with unique words alpha{i}",
                company=f"Company {i % 3}",
                embed_text=f"unique-{i}",
            )
        )
    a = await add_job(engine, title="Dup A", embed_text="dupkey-a", company="Acme")
    b = await add_job(engine, title="Dup B", embed_text="dupkey-b", company="Acme")
    c = await add_job(engine, title="Near C", embed_text="near-same", company="Acme")
    await add_job(engine, title="Near D", embed_text="near-same", company="Acme")
    expired = await add_job(engine, title="Gone", embed_text="gone")
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET dedupe_key = (SELECT dedupe_key FROM jobs WHERE id = :a) "
                 "WHERE id = :b"),
            {"a": a, "b": b},
        )  # fmt: skip
        await conn.execute(text("UPDATE jobs SET status = 'expired' WHERE id = :i"), {"i": expired})
    return [*ids, a, c]  # ids expected to survive (lowest id wins)


@pytest.fixture
async def fresh_db(
    database_url: str, engine: AsyncEngine
) -> AsyncIterator[tuple[str, AsyncEngine]]:
    """A brand-new, UNMIGRATED database on the test container."""
    name = f"snap_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    dsn = make_url(database_url).set(database=name).render_as_string(hide_password=False)
    eng = create_async_engine(dsn)
    try:
        yield dsn, eng
    finally:
        await eng.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_export_dedupes_and_writes_artifacts(engine: AsyncEngine, tmp_path: Path) -> None:
    expected = await _seed(engine)
    meta = await export_snapshot(engine, tmp_path)
    assert meta.rows == 10
    with gzip.open(tmp_path / SNAPSHOT_NAME, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh.read().splitlines()]
    assert [r["id"] for r in rows] == sorted(expected)
    first = rows[0]
    assert "description_raw" not in first
    for key in ("title", "description_clean", "skills_text", "skills", "company_name", "city"):
        assert key in first
    assert len(first["embedding"]) == 384
    sha_line = (tmp_path / SHA_NAME).read_text()
    assert sha_line == f"{meta.sha256}  {SNAPSHOT_NAME}\n"
    stored = json.loads((tmp_path / "snapshot.meta.json").read_text())
    assert stored["rows"] == 10
    assert stored["sha256"] == meta.sha256
    assert stored["as_of"].endswith("+00:00")
    assert stored["embed_model"] == "fake"


async def test_load_into_unmigrated_db_roundtrips(
    engine: AsyncEngine, fresh_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    await _seed(engine)
    async with engine.connect() as conn:
        before = {r.id: r for r in (await conn.execute(text(_STATE))).all()}
    out1 = tmp_path / "a"
    meta = await export_snapshot(engine, out1)

    dsn, target = fresh_db
    n = await load_snapshot(target, out1 / SNAPSHOT_NAME, out1 / SHA_NAME, dsn=dsn)
    assert n == 10
    async with target.connect() as conn:
        after = {r.id: r for r in (await conn.execute(text(_STATE))).all()}
        companies = (await conn.execute(text("SELECT count(*) FROM companies"))).scalar_one()
        seq_next = (await conn.execute(text("SELECT nextval('jobs_id_seq')"))).scalar_one()
    assert len(after) == 10 and all(r.status == "active" for r in after.values())
    assert all(r.canonical_job_id is None for r in after.values())
    assert companies == 4  # Company 0..2 + Acme
    assert seq_next > max(after)
    for job_id, row in after.items():
        assert row.tsv == before[job_id].tsv
        assert row.skills_tsv == before[job_id].skills_tsv
        assert row.skills_tsv or row.title.startswith(("Dup", "Near"))  # populated for skilled jobs
        assert row.emb == before[job_id].emb

    meta2 = await export_snapshot(target, tmp_path / "b")
    assert meta2.sha256 == meta.sha256


async def test_corrupted_file_aborts_before_any_insert(
    engine: AsyncEngine, fresh_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    await _seed(engine)
    await export_snapshot(engine, tmp_path)
    gz = tmp_path / SNAPSHOT_NAME
    data = bytearray(gz.read_bytes())
    data[len(data) // 2] ^= 0xFF
    gz.write_bytes(bytes(data))
    dsn, target = fresh_db
    with pytest.raises(SnapshotError, match="hash"):
        await load_snapshot(target, gz, tmp_path / SHA_NAME, dsn=dsn)
    async with target.connect() as conn:
        assert (await conn.execute(text("SELECT to_regclass('public.jobs')"))).scalar_one() is None


async def test_non_empty_target_requires_replace(engine: AsyncEngine, tmp_path: Path) -> None:
    await _seed(engine)
    await export_snapshot(engine, tmp_path)
    with pytest.raises(SnapshotError, match="replace"):
        await load_snapshot(
            engine,
            tmp_path / SNAPSHOT_NAME,
            tmp_path / SHA_NAME,
            dsn=engine.url.render_as_string(hide_password=False),
        )
