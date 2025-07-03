"""
add thread order column

Revision ID: 3de3979f94bd
Revises: 322c2800c401
Create Date: 2014-07-25 15:38:30.254843

"""

# revision identifiers, used by Alembic.
revision = "3de3979f94bd"
down_revision = "1763103db266"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column(
        "message", sa.Column("thread_order", sa.Integer, nullable=False)
    )


def downgrade() -> None:
    op.drop_column("message", "thread_order")
