"""
add versions

Revision ID: 1f746c93e8fd
Revises:39fa82d3168e
Create Date: 2015-02-06 03:46:14.342310

"""

# revision identifiers, used by Alembic.
revision = "1f746c93e8fd"
down_revision = "39fa82d3168e"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.drop_column("message", "version")
    op.add_column(
        "message",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "thread",
        sa.Column("version", sa.Integer(), server_default="0", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message", "version")
    op.add_column("message", sa.Column("version", sa.BINARY(), nullable=True))
    op.drop_column("thread", "version")
