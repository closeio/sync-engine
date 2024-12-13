"""
add supports_condstore column to generic account

Revision ID: 3c74cbe7882e
Revises: 3c02d8204335
Create Date: 2014-08-06 14:41:01.072742

"""

# revision identifiers, used by Alembic.
revision = "3c74cbe7882e"
down_revision = "3de3979f94bd"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column(
        "genericaccount",
        sa.Column("supports_condstore", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("genericaccount", "supports_condstore")
