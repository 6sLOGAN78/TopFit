"""tasks queue (FND-07).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tasks (
            id BIGSERIAL PRIMARY KEY,
            kind TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','failed','dead')),
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 5,
            run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_at TIMESTAMPTZ,
            locked_by TEXT,
            last_error TEXT,
            dedupe_key TEXT,
            user_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX tasks_dedupe_pending ON tasks (dedupe_key) "
        "WHERE status IN ('queued','running')"
    )
    op.execute("CREATE INDEX tasks_ready ON tasks (run_after) WHERE status = 'queued'")
    # Claim pattern used by ranker.tasks.queue.claim: FOR UPDATE SKIP LOCKED


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tasks")
