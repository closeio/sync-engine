"""Remove account old webhook columns

Revision ID: e9e932c6c55e
Revises: 4af0d2f17967
Create Date: 2022-11-10 14:45:07.831731

"""

# revision identifiers, used by Alembic.
revision = "e9e932c6c55e"
down_revision = "4af0d2f17967"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.drop_column("gmailaccount", "gpush_calendar_list_expiration")
    op.drop_column("gmailaccount", "gpush_calendar_list_last_ping")


def downgrade():
    op.add_column(
        "gmailaccount",
        sa.Column("gpush_calendar_list_last_ping", mysql.DATETIME(), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("gpush_calendar_list_expiration", mysql.DATETIME(), nullable=True),
    )
