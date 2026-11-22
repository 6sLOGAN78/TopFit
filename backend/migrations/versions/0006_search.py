"""search: skills_tsv column (SRCH-02) and search_logs (SRCH-07).

Revision ID: 0006
Revises: 0005

skills_tsv uses the ``simple`` config over skills_text so symbol skills (cplusplus, csharp,
golang, dotnet, nodejs) are matched unstemmed. search_logs has no foreign keys yet: users and
conversations arrive in a later phase.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_SURFACES = ("search", "feed", "chat")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v + "'" for v in values)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE jobs ADD COLUMN skills_tsv TSVECTOR "
        "GENERATED ALWAYS AS (to_tsvector('simple', skills_text)) STORED"
    )
    op.execute("CREATE INDEX jobs_skills_tsv_gin ON jobs USING gin (skills_tsv)")
    op.execute(
        f"""
        CREATE TABLE search_logs (
            id UUID PRIMARY KEY DEFAULT uuidv7(),
            guest_id UUID,
            user_id UUID,
            surface TEXT NOT NULL DEFAULT 'search' CHECK (surface IN ({_in(_SURFACES)})),
            query_text TEXT,
            filters JSONB NOT NULL DEFAULT '{{}}',
            parse JSONB NOT NULL DEFAULT '{{}}',
            pipeline_version TEXT NOT NULL,
            results JSONB NOT NULL,
            ranked_ids BIGINT[] NOT NULL DEFAULT '{{}}',
            set_aside JSONB NOT NULL DEFAULT '{{}}',
            lex_hits INT,
            vec_hits INT,
            degraded BOOLEAN NOT NULL DEFAULT false,
            latency_ms INT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX search_logs_created ON search_logs (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS search_logs_created")
    op.execute("DROP TABLE IF EXISTS search_logs")
    op.execute("DROP INDEX IF EXISTS jobs_skills_tsv_gin")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS skills_tsv")
