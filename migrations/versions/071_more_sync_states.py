"""
more sync states

Revision ID: 3bb5d61c895c
Revises: 2525c5245cc2
Create Date: 2014-07-31 12:44:39.338556

"""

# revision identifiers, used by Alembic.
revision = "3bb5d61c895c"
down_revision = "2525c5245cc2"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.alter_column(
        "account",
        "sync_state",
        existing_type=sa.Enum("running", "stopped", "killed"),
        type_=sa.Enum("running", "stopped", "killed", "invalid", "connerror"),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "account",
        "sync_state",
        type_=sa.Enum("running", "stopped", "killed"),
        existing_type=sa.Enum(
            "running", "stopped", "killed", "invalid", "connerror"
        ),
        existing_nullable=True,
    )
