"""rate_limit_counters: Postgres fixed-window counters for guest search (SEC-04).

Revision ID: 0007
Revises: 0006

UNLOGGED because the counters are disposable: a crash may reset limits, which is harmless.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNLOGGED TABLE rate_limit_counters ("
        "bucket TEXT NOT NULL, "
        "window_start TIMESTAMPTZ NOT NULL, "
        "count INT NOT NULL, "
        "PRIMARY KEY (bucket, window_start))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE rate_limit_counters")
