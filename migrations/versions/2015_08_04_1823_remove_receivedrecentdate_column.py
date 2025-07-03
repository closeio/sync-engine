"""
Remove receivedrecentdate column

Revision ID: 51ad0922ad8e
Revises: 69e93aef3e9
Create Date: 2015-08-04 18:23:40.588284

"""

# revision identifiers, used by Alembic.
revision = "51ad0922ad8e"
down_revision = "69e93aef3e9"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.drop_column("thread", "receivedrecentdate")
    op.drop_index(
        "ix_thread_namespace_id_receivedrecentdate", table_name="thread"
    )


def downgrade() -> None:
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
