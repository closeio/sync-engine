"""
Add calendar webhook columns

Revision ID: 9ea81ca0f64b
Revises: 7bb5c0ca93de
Create Date: 2022-11-09 10:11:17.902582

"""

# revision identifiers, used by Alembic.
revision = "9ea81ca0f64b"
down_revision = "7bb5c0ca93de"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column(
        "calendar",
        sa.Column("webhook_last_ping", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "calendar",
        sa.Column(
            "webhook_subscription_expiration", sa.DateTime(), nullable=True
        ),
    )

    op.execute(
        "UPDATE calendar SET webhook_last_ping = gpush_last_ping WHERE gpush_last_ping IS NOT NULL;"
    )
    op.execute(
        "UPDATE calendar SET webhook_subscription_expiration = gpush_expiration WHERE gpush_expiration IS NOT NULL;"
    )


def downgrade() -> None:
    op.drop_column("calendar", "webhook_subscription_expiration")
    op.drop_column("calendar", "webhook_last_ping")
