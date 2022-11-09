"""Add calendar webhook columns

Revision ID: 9ea81ca0f64b
Revises: 7bb5c0ca93de
Create Date: 2022-11-09 10:11:17.902582

"""

# revision identifiers, used by Alembic.
revision = "9ea81ca0f64b"
down_revision = "7bb5c0ca93de"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "calendar", sa.Column("webhook_last_ping", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "calendar",
        sa.Column("webhook_subscription_expiration", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("calendar", "webhook_subscription_expiration")
    op.drop_column("calendar", "webhook_last_ping")
