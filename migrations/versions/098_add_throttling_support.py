"""
add_throttling_support

Revision ID: 40b533a6f3e1
Revises: 248ec24a39f
Create Date: 2014-09-24 00:41:59.379523

"""

# revision identifiers, used by Alembic.
revision = "40b533a6f3e1"
down_revision = "248ec24a39f"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column(
            "throttled", sa.Boolean(), server_default="0", nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("account", "throttled")
