"""Add account webhook columns

Revision ID: 93cc6f4ce113
Revises: 9ea81ca0f64b
Create Date: 2022-11-09 11:12:26.241416

"""

# revision identifiers, used by Alembic.
revision = "93cc6f4ce113"
down_revision = "9ea81ca0f64b"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "gmailaccount",
        sa.Column("webhook_calendar_list_last_ping", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("webhook_calendar_list_expiration", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("gmailaccount", "webhook_calendar_list_expiration")
    op.drop_column("gmailaccount", "webhook_calendar_list_last_ping")
