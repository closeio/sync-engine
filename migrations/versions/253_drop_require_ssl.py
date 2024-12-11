"""
drop ssl_required field

Revision ID: 52783469ee6c
Revises: 32df3d8ff73e
Create Date: 2020-09-22 13:03:12.879960

"""

# revision identifiers, used by Alembic.
revision = "52783469ee6c"
down_revision = "32df3d8ff73e"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade() -> None:
    op.drop_column("genericaccount", "ssl_required")


def downgrade() -> None:
    op.add_column(
        "genericaccount",
        sa.Column(
            "ssl_required",
            mysql.TINYINT(display_width=1),
            autoincrement=False,
            nullable=True,
            server_default="1",
        ),
    )
