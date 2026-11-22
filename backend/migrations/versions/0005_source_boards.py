"""source_boards: registry of public Greenhouse / Lever / Ashby boards (ING-13, D-03).

Revision ID: 0005
Revises: 0004

The slug CHECK mirrors ranker.ingest.boards.SLUG_RE so a bad slug can never be stored, and so
can never reach a request URL (T-02-49).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_boards (
            id BIGSERIAL PRIMARY KEY,
            ats TEXT NOT NULL CHECK (ats IN ('greenhouse','lever','ashby')),
            slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
            host TEXT NOT NULL DEFAULT 'global' CHECK (host IN ('global','eu')),
            status TEXT NOT NULL CHECK (status IN ('seed','active','rejected','dropped')),
            origin TEXT NOT NULL CHECK (origin IN ('seed','discovered')),
            in_scope_count INT NOT NULL DEFAULT 0,
            zero_checks INT NOT NULL DEFAULT 0,
            last_checked_at TIMESTAMPTZ,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (ats, slug)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_boards")
