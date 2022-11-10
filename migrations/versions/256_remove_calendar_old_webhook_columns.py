"""Remove calendar old webhook columns

Revision ID: 4af0d2f17967
Revises: 9ea81ca0f64b
Create Date: 2022-11-10 12:21:52.306392

"""

# revision identifiers, used by Alembic.
revision = "4af0d2f17967"
down_revision = "9ea81ca0f64b"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.drop_column("calendar", "gpush_expiration")
    op.drop_column("calendar", "gpush_last_ping")


def downgrade():
    op.add_column(
        "calendar", sa.Column("gpush_last_ping", mysql.DATETIME(), nullable=True)
    )
    op.add_column(
        "calendar", sa.Column("gpush_expiration", mysql.DATETIME(), nullable=True)
    )
