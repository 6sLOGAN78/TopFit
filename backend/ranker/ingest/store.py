"""All ingest SQL (bound params only, no ORM).

Upsert form: ONE ``INSERT ... SELECT FROM jsonb_to_recordset(CAST(:rows AS jsonb))`` per batch
of <= 300 rows, classified with PG18 ``RETURNING old/new``. Values travel as a single JSON
bound parameter, so nothing from a source is ever interpolated into SQL text.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ranker.ingest.normalize.job import NormalizedJob

BATCH_SIZE = 300

# (column, SQL type in the recordset definition). Order is irrelevant to correctness.
_CONTENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_scope", "text"),
    ("url", "text"),
    ("title", "text"),
    ("company_id", "bigint"),
    ("description_raw", "text"),
    ("description_clean", "text"),
    ("description_truncated", "boolean"),
    ("location_text", "text"),
    ("city", "text"),
    ("cities", "text[]"),
    ("country_code", "char(2)"),
    ("remote_type", "text"),
    ("remote_eligibility", "text"),
    ("remote_regions", "text[]"),
    ("employment_type", "text"),
    ("seniority", "text"),
    ("onsite_days_per_week", "smallint"),
    ("industry", "text"),
    ("company_stage", "text"),
    ("company_size", "text"),
    ("visa_sponsorship", "text"),
    ("relocation", "text"),
    ("salary_min", "numeric"),
    ("salary_max", "numeric"),
    ("salary_currency", "char(3)"),
    ("salary_period", "text"),
    ("salary_min_inr_annual", "numeric"),
    ("salary_max_inr_annual", "numeric"),
    ("salary_text", "text"),
    ("salary_is_estimate", "boolean"),
    ("salary_fx_as_of", "date"),
    ("skills", "text[]"),
    ("skills_text", "text"),
    ("category", "text"),
    ("posted_at", "timestamptz"),
    ("dedupe_key", "text"),
)
_KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source", "text"),
    ("source_job_id", "text"),
    ("content_hash", "text"),
    ("normalizer_version", "text"),
)

_RECORDSET = ", ".join(f"{c} {t}" for c, t in (*_KEY_COLUMNS, *_CONTENT_COLUMNS))
_INSERT_COLS = ", ".join(
    [c for c, _ in (*_KEY_COLUMNS, *_CONTENT_COLUMNS)]
    + [
        "first_seen_at",
        "last_seen_at",
        "first_seen_run_id",
        "last_seen_run_id",
        "missed_runs",
        "status",
        "created_at",
        "updated_at",
    ]
)
_SELECT_COLS = ", ".join(
    [f"r.{c}" for c, _ in (*_KEY_COLUMNS, *_CONTENT_COLUMNS)]
    + [":now", ":now", ":run_id", ":run_id", "0", "'active'", ":now", ":now"]
)
_CHANGED = (
    "(jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash "
    "OR jobs.normalizer_version IS DISTINCT FROM EXCLUDED.normalizer_version)"
)
_CASE_SET = ",\n    ".join(
    f"{c} = CASE WHEN {_CHANGED} THEN EXCLUDED.{c} ELSE jobs.{c} END"
    for c, _ in (("content_hash", ""), ("normalizer_version", ""), *_CONTENT_COLUMNS)
)

UPSERT_JOBS_SQL = f"""
INSERT INTO jobs ({_INSERT_COLS})
SELECT {_SELECT_COLS}
FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r({_RECORDSET})
ON CONFLICT (source, source_job_id) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at,
    last_seen_run_id = EXCLUDED.last_seen_run_id,
    missed_runs = 0,
    status = CASE WHEN jobs.status = 'expired' THEN 'active' ELSE jobs.status END,
    {_CASE_SET},
    embedding = CASE WHEN {_CHANGED} THEN NULL ELSE jobs.embedding END,
    embedding_model = CASE WHEN {_CHANGED} THEN NULL ELSE jobs.embedding_model END,
    updated_at = CASE WHEN {_CHANGED} THEN EXCLUDED.updated_at ELSE jobs.updated_at END
RETURNING new.id,
    (old.id IS NULL) AS inserted,
    (old.id IS NOT NULL AND (old.content_hash IS DISTINCT FROM new.content_hash
        OR old.normalizer_version IS DISTINCT FROM new.normalizer_version)) AS changed
"""  # noqa: S608 - only module constants are interpolated


async def create_run(conn: AsyncConnection, source: str, mode: str, now: datetime) -> int:
    row = await conn.execute(
        text(
            "INSERT INTO ingest_runs (source, mode, started_at, status) "
            "VALUES (:source, :mode, :now, 'running') RETURNING id"
        ),
        {"source": source, "mode": mode, "now": now},
    )
    return int(row.scalar_one())


async def finish_run(
    conn: AsyncConnection,
    run_id: int,
    *,
    status: str,
    fetched: int,
    inserted: int,
    updated: int,
    unchanged: int,
    skipped: int,
    failed: int,
    calls_used: int,
    in_scope: int,
    errors: Sequence[dict[str, Any]],
    notes: dict[str, Any],
    now: datetime,
    expired: int = 0,
    duplicates_grouped: int = 0,
    ghost_flagged: int = 0,
    reposts: int = 0,
) -> None:
    await conn.execute(
        text(
            "UPDATE ingest_runs SET status = :status, finished_at = :now, fetched = :fetched, "
            "inserted = :inserted, updated = :updated, unchanged = :unchanged, "
            "skipped = :skipped, failed = :failed, calls_used = :calls_used, "
            "in_scope = :in_scope, expired = :expired, duplicates_grouped = :dups, "
            "ghost_flagged = :ghosts, reposts = :reposts, errors = CAST(:errors AS jsonb), "
            "notes = CAST(:notes AS jsonb) WHERE id = :run_id"
        ),
        {
            "run_id": run_id,
            "status": status,
            "now": now,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
            "failed": failed,
            "calls_used": calls_used,
            "in_scope": in_scope,
            "expired": expired,
            "dups": duplicates_grouped,
            "ghosts": ghost_flagged,
            "reposts": reposts,
            "errors": json.dumps(list(errors), default=str),
            "notes": json.dumps(notes, default=str),
        },
    )


async def runs_started_since(conn: AsyncConnection, source: str, since: datetime) -> int:
    row = await conn.execute(
        text("SELECT count(*) FROM ingest_runs WHERE source = :source AND started_at >= :since"),
        {"source": source, "since": since},
    )
    return int(row.scalar_one())


async def prefetch_versions(
    conn: AsyncConnection, source: str, ids: Sequence[str]
) -> dict[str, tuple[str, str]]:
    if not ids:
        return {}
    rows = await conn.execute(
        text(
            "SELECT source_job_id, content_hash, normalizer_version FROM jobs "
            "WHERE source = :source AND source_job_id = ANY(CAST(:ids AS text[]))"
        ),
        {"source": source, "ids": list(ids)},
    )
    return {str(r[0]): (str(r[1]), str(r[2])) for r in rows}


async def touch_unchanged(
    conn: AsyncConnection, run_id: int, source: str, ids: Sequence[str], now: datetime
) -> int:
    if not ids:
        return 0
    result = await conn.execute(
        text(
            "UPDATE jobs SET last_seen_at = :now, last_seen_run_id = :run_id, missed_runs = 0, "
            "status = CASE WHEN status = 'expired' THEN 'active' ELSE status END "
            "WHERE source = :source AND source_job_id = ANY(CAST(:ids AS text[]))"
        ),
        {"now": now, "run_id": run_id, "source": source, "ids": list(ids)},
    )
    return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def upsert_companies(
    conn: AsyncConnection, rows: Sequence[tuple[str, str, bool]]
) -> dict[str, int]:
    """rows: (name, normalized_name, is_placeholder). Returns normalized_name -> id."""
    unique: dict[str, tuple[str, bool]] = {}
    for name, normalized, placeholder in rows:
        unique.setdefault(normalized, (name, placeholder))
    if not unique:
        return {}
    payload = json.dumps(
        [{"name": n, "normalized_name": k, "is_placeholder": p} for k, (n, p) in unique.items()]
    )
    result = await conn.execute(
        text(
            "INSERT INTO companies (name, normalized_name, is_placeholder) "
            "SELECT r.name, r.normalized_name, r.is_placeholder "
            "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
            "AS r(name text, normalized_name text, is_placeholder boolean) "
            "ON CONFLICT (normalized_name) DO UPDATE SET name = companies.name "
            "RETURNING id, normalized_name"
        ),
        {"rows": payload},
    )
    return {str(r[1]): int(r[0]) for r in result}


def _row_payload(job: NormalizedJob, company_id: int | None) -> dict[str, Any]:
    data = job.model_dump(mode="json")
    data["company_id"] = company_id
    # Decimals dump as strings in json mode; numeric accepts them.
    return data


async def upsert_jobs(
    conn: AsyncConnection,
    run_id: int,
    rows: Sequence[NormalizedJob],
    company_ids: dict[str, int],
    now: datetime,
) -> tuple[int, int]:
    """Returns (inserted, updated). Rows are de-duplicated by source_job_id first."""
    unique: dict[tuple[str, str], NormalizedJob] = {}
    for job in rows:
        unique[(job.source, job.source_job_id)] = job
    ordered = list(unique.values())
    inserted = updated = 0
    for start in range(0, len(ordered), BATCH_SIZE):
        chunk = ordered[start : start + BATCH_SIZE]
        payload = json.dumps(
            [_row_payload(j, company_ids.get(j.company_normalized)) for j in chunk]
        )
        result = await conn.execute(
            text(UPSERT_JOBS_SQL), {"rows": payload, "run_id": run_id, "now": now}
        )
        for row in result:
            if row[1]:
                inserted += 1
            elif row[2]:
                updated += 1
    return inserted, updated
