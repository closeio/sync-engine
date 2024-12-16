"""
add run state to eas folders

Revision ID: 2b9dd6f7593a
Revises: 48a1991e5dbd
Create Date: 2015-05-28 00:47:47.636511

"""

# revision identifiers, used by Alembic.
revision = "2b9dd6f7593a"
down_revision = "48a1991e5dbd"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easfoldersyncstatus"):
        return
    op.add_column(
        "easfoldersyncstatus",
        sa.Column(
            "sync_should_run",
            sa.Boolean(),
            server_default=sa.sql.expression.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easfoldersyncstatus"):
        return
    op.drop_column("easfoldersyncstatus", "sync_should_run")
