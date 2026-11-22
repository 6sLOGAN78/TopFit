"""Jobs index: companies, jobs (vector, tsv, partial HNSW) and ingest_runs.

Revision ID: 0004
Revises: 0003

No CHECK on jobs.source (ING-04): source names are validated against the adapter
registry in code, so a new source never needs a migration.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Value sets mirror ranker.llm.schemas.RemoteEligibility and
# ranker.ingest.normalize.dealbreakers (tests assert equality).
_ELIGIBILITY = ("worldwide", "india", "apac", "us_only", "eu_only", "other_region", "unknown")
_INDUSTRY = (
    "fintech", "edtech", "healthtech", "ecommerce", "saas", "devtools", "ai_ml", "gaming",
    "media", "logistics", "mobility", "crypto", "hrtech", "adtech", "cybersecurity",
    "consumer_internet", "it_services", "other", "unknown",
)  # fmt: skip
_STAGE = ("early", "growth", "late", "public", "unknown")
_SIZE = ("1-10", "11-50", "51-200", "201-1000", "1001-5000", "5001+", "unknown")
_OFFERED = ("offered", "not_offered", "unknown")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v + "'" for v in values)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"""
        CREATE TABLE companies (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            industry TEXT,
            stage TEXT DEFAULT 'unknown' CHECK (stage IN ({_in(_STAGE)})),
            website TEXT,
            is_placeholder BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE jobs (
            id BIGSERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            source_scope TEXT NOT NULL DEFAULT 'all',
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company_id BIGINT REFERENCES companies(id),
            description_raw TEXT NOT NULL,
            description_clean TEXT NOT NULL,
            description_truncated BOOLEAN NOT NULL DEFAULT false,
            location_text TEXT,
            city TEXT,
            cities TEXT[] NOT NULL DEFAULT '{{}}',
            country_code CHAR(2),
            remote_type TEXT NOT NULL DEFAULT 'unknown'
                CHECK (remote_type IN ('onsite','hybrid','remote','unknown')),
            remote_eligibility TEXT NOT NULL DEFAULT 'unknown'
                CHECK (remote_eligibility IN ({_in(_ELIGIBILITY)})),
            remote_regions TEXT[] NOT NULL DEFAULT '{{}}',
            employment_type TEXT NOT NULL DEFAULT 'unknown'
                CHECK (employment_type IN
                    ('full_time','part_time','contract','internship','unknown')),
            seniority TEXT NOT NULL DEFAULT 'unknown'
                CHECK (seniority IN ('intern','junior','mid','senior','lead','unknown')),
            onsite_days_per_week SMALLINT
                CHECK (onsite_days_per_week BETWEEN 0 AND 7),
            industry TEXT NOT NULL DEFAULT 'unknown'
                CHECK (industry IN ({_in(_INDUSTRY)})),
            company_stage TEXT NOT NULL DEFAULT 'unknown'
                CHECK (company_stage IN ({_in(_STAGE)})),
            company_size TEXT NOT NULL DEFAULT 'unknown'
                CHECK (company_size IN ({_in(_SIZE)})),
            visa_sponsorship TEXT NOT NULL DEFAULT 'unknown'
                CHECK (visa_sponsorship IN ({_in(_OFFERED)})),
            relocation TEXT NOT NULL DEFAULT 'unknown'
                CHECK (relocation IN ({_in(_OFFERED)})),
            salary_min NUMERIC,
            salary_max NUMERIC,
            salary_currency CHAR(3),
            salary_period TEXT CHECK (salary_period IN ('year','month','hour')),
            salary_min_inr_annual NUMERIC,
            salary_max_inr_annual NUMERIC,
            salary_text TEXT,
            salary_is_estimate BOOLEAN NOT NULL DEFAULT false,
            salary_fx_as_of DATE,
            skills TEXT[] NOT NULL DEFAULT '{{}}',
            skills_text TEXT NOT NULL DEFAULT '',
            category TEXT,
            posted_at TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            first_seen_run_id BIGINT,
            last_seen_run_id BIGINT,
            expired_by_run_id BIGINT,
            missed_runs SMALLINT NOT NULL DEFAULT 0,
            expires_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','expired','removed')),
            content_hash TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            canonical_job_id BIGINT REFERENCES jobs(id),
            repost_count INT NOT NULL DEFAULT 0,
            ghost_reasons TEXT[] NOT NULL DEFAULT '{{}}',
            group_sources TEXT[] NOT NULL DEFAULT '{{}}',
            normalizer_version TEXT NOT NULL,
            tsv TSVECTOR GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(skills_text, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(description_clean, '')), 'B')
            ) STORED,
            embedding VECTOR(384),
            embedding_model TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source, source_job_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX jobs_embedding_hnsw ON jobs
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            WHERE status = 'active' AND canonical_job_id IS NULL
        """
    )
    op.execute("CREATE INDEX jobs_tsv_gin ON jobs USING gin (tsv)")
    op.execute("CREATE INDEX jobs_skills_gin ON jobs USING gin (skills)")
    op.execute(
        "CREATE INDEX jobs_active_posted ON jobs (posted_at DESC) "
        "WHERE status = 'active' AND canonical_job_id IS NULL"
    )
    op.execute("CREATE INDEX jobs_dedupe_key ON jobs (dedupe_key) WHERE status = 'active'")
    op.execute("CREATE INDEX jobs_city_remote ON jobs (city, remote_type) WHERE status = 'active'")
    op.execute("CREATE INDEX jobs_source_seen ON jobs (source, last_seen_at)")
    op.execute("CREATE INDEX jobs_title_trgm ON jobs USING gin (title gin_trgm_ops)")
    op.execute(
        "CREATE INDEX jobs_source_scope_active ON jobs (source, source_scope) "
        "WHERE status = 'active'"
    )
    op.execute(
        """
        CREATE TABLE ingest_runs (
            id BIGSERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'incremental',
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            status TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
            fetched INT NOT NULL DEFAULT 0,
            inserted INT NOT NULL DEFAULT 0,
            updated INT NOT NULL DEFAULT 0,
            unchanged INT NOT NULL DEFAULT 0,
            expired INT NOT NULL DEFAULT 0,
            failed INT NOT NULL DEFAULT 0,
            skipped INT NOT NULL DEFAULT 0,
            calls_used INT NOT NULL DEFAULT 0,
            in_scope INT NOT NULL DEFAULT 0,
            duplicates_grouped INT NOT NULL DEFAULT 0,
            ghost_flagged INT NOT NULL DEFAULT 0,
            reposts INT NOT NULL DEFAULT 0,
            errors JSONB NOT NULL DEFAULT '[]',
            notes JSONB NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX ingest_runs_source_started ON ingest_runs (source, started_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingest_runs")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS companies")
