"""
add a retries column to the actionlog

Revision ID: 5709063bff01
Revises: 2f97277cd86d
Create Date: 2014-10-07 10:34:29.302936

"""

# revision identifiers, used by Alembic.
revision = "5709063bff01"
down_revision = "2f97277cd86d"

from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.sql import text  # type: ignore[import-untyped]


def upgrade() -> None:
    op.add_column(
        "actionlog",
        sa.Column("retries", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "actionlog",
        sa.Column(
            "status",
            sa.Enum("pending", "successful", "failed"),
            server_default="pending",
        ),
    )

    conn = op.get_bind()
    conn.execute(
        text("UPDATE actionlog SET status='successful' WHERE executed is TRUE")
    )

    op.drop_column("actionlog", "executed")


def downgrade() -> Never:
    raise Exception("Can't.")
