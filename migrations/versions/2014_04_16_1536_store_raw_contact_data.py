"""
Store raw contact data.

Revision ID: 3b511977a01f
Revises: 169cac0cd87e
Create Date: 2014-04-16 15:36:22.188971

"""

# revision identifiers, used by Alembic.
revision = "3b511977a01f"
down_revision = "169cac0cd87e"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column("contact", sa.Column("raw_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contact", "raw_data")
