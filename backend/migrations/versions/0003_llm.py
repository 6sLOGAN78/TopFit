"""llm_calls log and llm_enrichment_cache (FND-06).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No prompt or response text columns, by design (privacy).
    op.execute(
        """
        CREATE TABLE llm_calls (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID,
            purpose TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INT,
            output_tokens INT,
            cache_read_tokens INT,
            cache_write_tokens INT,
            cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
            latency_ms INT,
            success BOOLEAN NOT NULL,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX llm_calls_created ON llm_calls (created_at)")
    op.execute(
        """
        CREATE TABLE llm_enrichment_cache (
            content_hash TEXT NOT NULL,
            purpose TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            result JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (content_hash, purpose, prompt_version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_enrichment_cache")
    op.execute("DROP TABLE IF EXISTS llm_calls")
