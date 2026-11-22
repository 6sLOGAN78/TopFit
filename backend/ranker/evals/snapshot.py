"""Frozen job-pool snapshot for the ranking eval (EVAL-01, D-15).

``export_snapshot`` writes active canonical jobs (deduplicated by ``dedupe_key`` and by
embedding cosine > 0.97) with their precomputed embeddings as deterministic gzip JSONL, plus
``snapshot.sha256`` (``sha256sum`` format) and ``snapshot.meta.json``. ``load_snapshot``
verifies the hash first, migrates the target database itself and loads the rows with their
original ids so the generated ``tsv`` / ``skills_tsv`` columns regenerate identically.

Only job data leaves the server: ``description_raw`` is never selected and no user tables are
touched (T-03-30).
"""

import asyncio
import gzip
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.service import vector_literal
from ranker.settings import get_settings

SNAPSHOT_NAME = "job_pool_snapshot.jsonl.gz"
SHA_NAME = "snapshot.sha256"
META_NAME = "snapshot.meta.json"
NEAR_DUPLICATE_COS = 0.97
LOAD_BATCH = 500
BACKEND_DIR = Path(__file__).resolve().parents[2]

# (column, SQL cast on load). description_raw is deliberately absent; it is NOT NULL, so the
# loader writes an empty string.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source", "text"),
    ("source_job_id", "text"),
    ("source_scope", "text"),
    ("url", "text"),
    ("title", "text"),
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
    ("first_seen_at", "timestamptz"),
    ("last_seen_at", "timestamptz"),
    ("expires_at", "timestamptz"),
    ("repost_count", "int"),
    ("ghost_reasons", "text[]"),
    ("group_sources", "text[]"),
    ("content_hash", "text"),
    ("dedupe_key", "text"),
    ("normalizer_version", "text"),
    ("embedding_model", "text"),
)
_ARRAYS = {c for c, t in _COLUMNS if t.endswith("[]")}

_SELECT = text(
    "SELECT j.id, c.name AS company_name, c.normalized_name AS company_normalized_name, "  # noqa: S608
    + ", ".join(f"j.{c}" for c, _ in _COLUMNS)
    + ", j.embedding::text AS embedding FROM jobs j "
    "LEFT JOIN companies c ON c.id = j.company_id "
    "WHERE j.status = 'active' AND j.canonical_job_id IS NULL AND j.embedding IS NOT NULL "
    "ORDER BY j.id"
)


class SnapshotError(ValueError):
    """Hash mismatch, malformed snapshot or unsafe load target."""


@dataclass(frozen=True)
class SnapshotMeta:
    rows: int
    as_of: str
    embed_model: str
    sha256: str
    path: Path

    def as_json(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "as_of": self.as_of,
            "embed_model": self.embed_model,
            "sha256": self.sha256,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the lowest id per dedupe_key, then drop embedding near-duplicates (cos > 0.97)."""
    seen_keys: set[str] = set()
    by_key: list[dict[str, Any]] = []
    for r in rows:
        if r["dedupe_key"] in seen_keys:
            continue
        seen_keys.add(r["dedupe_key"])
        by_key.append(r)
    if not by_key:
        return []
    mat = np.asarray([json.loads(r["embedding"]) for r in by_key], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat = mat / np.where(norms == 0, 1.0, norms)
    kept_idx: list[int] = []
    kept = np.empty_like(mat)
    for i in range(len(by_key)):
        if kept_idx and float((kept[: len(kept_idx)] @ mat[i]).max()) > NEAR_DUPLICATE_COS:
            continue
        kept[len(kept_idx)] = mat[i]
        kept_idx.append(i)
    return [by_key[i] for i in kept_idx]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
    ):
        for r in rows:
            gz.write((json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


async def export_snapshot(engine: AsyncEngine, out_dir: Path) -> SnapshotMeta:
    async with engine.connect() as conn:
        result = (await conn.execute(_SELECT)).mappings().all()
    rows: list[dict[str, Any]] = []
    for r in _dedupe([dict(m) for m in result]):
        row = {k: _jsonable(v) for k, v in r.items()}
        row["embedding"] = json.loads(r["embedding"])
        rows.append(row)
    gz_path = out_dir / SNAPSHOT_NAME
    await asyncio.to_thread(_write_gz, gz_path, rows)
    digest = await asyncio.to_thread(_sha256_file, gz_path)
    models = sorted({str(r["embedding_model"]) for r in rows if r["embedding_model"]})
    meta = SnapshotMeta(
        rows=len(rows),
        as_of=datetime.now(UTC).isoformat(),
        embed_model=",".join(models),
        sha256=digest,
        path=gz_path,
    )
    (out_dir / SHA_NAME).write_text(f"{digest}  {SNAPSHOT_NAME}\n", encoding="utf-8")
    (out_dir / META_NAME).write_text(
        json.dumps(meta.as_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return meta


def verify_hash(gz_path: Path, sha256_file: Path) -> str:
    """Raise SnapshotError unless the file's sha256 equals the first token of the sha256 file."""
    if not gz_path.is_file() or not sha256_file.is_file():
        raise SnapshotError("snapshot or sha256 file not found")
    parts = sha256_file.read_text(encoding="utf-8").split()
    if not parts:
        raise SnapshotError("empty sha256 file")
    actual = _sha256_file(gz_path)
    if actual != parts[0].lower():
        raise SnapshotError("snapshot hash mismatch")
    return actual


def _migrate(dsn: str) -> None:
    """``alembic upgrade head`` on ``dsn``; env.py reads the URL from settings, so swap it."""
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev
        get_settings.cache_clear()


def _read_rows(gz_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SnapshotError(f"{gz_path.name}:{n}: invalid JSON") from exc
    return out


def _insert_sql() -> Any:
    cols = ", ".join(c for c, _ in _COLUMNS)
    vals = ", ".join(f"CAST(:{c} AS {t})" for c, t in _COLUMNS)
    return text(
        f"INSERT INTO jobs (id, company_id, description_raw, embedding, {cols}) "  # noqa: S608
        f"VALUES (:id, :company_id, '', CAST(:embedding AS vector), {vals})"
    )


def _array_literal(values: list[str] | None) -> str | None:
    if values is None:
        return None
    esc = ['"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    return "{" + ",".join(esc) + "}"


async def _jobs_present(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        exists = (await conn.execute(text("SELECT to_regclass('public.jobs')"))).scalar_one()
        if exists is None:
            return False
        return bool((await conn.execute(text("SELECT EXISTS (SELECT 1 FROM jobs)"))).scalar_one())


async def load_snapshot(
    engine: AsyncEngine,
    gz_path: Path,
    sha256_file: Path,
    *,
    dsn: str,
    replace: bool = False,
    migrate: Callable[[str], None] = _migrate,
) -> int:
    """Verify, migrate and load. Returns the number of jobs loaded."""
    await asyncio.to_thread(verify_hash, gz_path, sha256_file)
    if await _jobs_present(engine) and not replace:
        raise SnapshotError("target database already has jobs; pass --replace on an eval DB")
    await asyncio.to_thread(migrate, dsn)
    rows = await asyncio.to_thread(_read_rows, gz_path)
    insert = _insert_sql()
    async with engine.begin() as conn:
        if replace:
            await conn.execute(text("TRUNCATE jobs, companies RESTART IDENTITY CASCADE"))
        company_ids: dict[str, int] = {}
        for r in rows:
            name, norm = r.get("company_name"), r.get("company_normalized_name")
            if name and norm and norm not in company_ids:
                company_ids[norm] = (
                    await conn.execute(
                        text(
                            "INSERT INTO companies (name, normalized_name) VALUES (:n, :nn) "
                            "ON CONFLICT (normalized_name) DO UPDATE SET name = EXCLUDED.name "
                            "RETURNING id"
                        ),
                        {"n": name, "nn": norm},
                    )
                ).scalar_one()
        for start in range(0, len(rows), LOAD_BATCH):
            batch: list[dict[str, Any]] = []
            for r in rows[start : start + LOAD_BATCH]:
                params: dict[str, Any] = {
                    c: (_array_literal(r[c]) if c in _ARRAYS else r[c]) for c, _ in _COLUMNS
                }
                params["id"] = r["id"]
                params["company_id"] = company_ids.get(r.get("company_normalized_name") or "")
                params["embedding"] = vector_literal(r["embedding"])
                batch.append(params)
            await conn.execute(insert, batch)
        await conn.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('jobs', 'id'), "
                "GREATEST((SELECT COALESCE(MAX(id), 1) FROM jobs), 1))"
            )
        )
    return len(rows)
