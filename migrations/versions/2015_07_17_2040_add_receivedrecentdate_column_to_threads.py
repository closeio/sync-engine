"""
add receivedrecentdate column to threads

Revision ID: 2758cefad87d
Revises: 246a6bf050bc
Create Date: 2015-07-17 20:40:31.951910

"""

# revision identifiers, used by Alembic.
revision = "2758cefad87d"
down_revision = "47aec237051e"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("thread"):
        return
    op.add_column(
        "thread",
        sa.Column(
            "receivedrecentdate",
            sa.DATETIME(),
            server_default=sa.sql.null(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_thread_namespace_id_receivedrecentdate",
        "thread",
        ["namespace_id", "receivedrecentdate"],
        unique=False,
    )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("thread"):
        return
    op.drop_column("thread", "receivedrecentdate")
    op.drop_index(
        "ix_thread_namespace_id_receivedrecentdate", table_name="thread"
    )
