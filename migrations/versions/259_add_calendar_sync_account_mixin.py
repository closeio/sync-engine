"""Add Calendar Sync Account Mixin

Revision ID: f9dab5e44c0f
Revises: e9e932c6c55e
Create Date: 2022-11-10 20:27:32.209744

"""

# revision identifiers, used by Alembic.
revision = "f9dab5e44c0f"
down_revision = "e9e932c6c55e"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.add_column(
        "outlookaccount",
        sa.Column("last_calendar_list_sync", mysql.DATETIME(), nullable=True),
    )
    op.add_column(
        "outlookaccount",
        sa.Column("webhook_calendar_list_last_ping", mysql.DATETIME(), nullable=True),
    )
    op.add_column(
        "outlookaccount",
        sa.Column("webhook_calendar_list_expiration", mysql.DATETIME(), nullable=True),
    )


def downgrade():
    op.drop_column("outlookaccount", "webhook_calendar_list_expiration")
    op.drop_column("outlookaccount", "webhook_calendar_list_last_ping")
    op.drop_column("outlookaccount", "last_calendar_list_sync")
