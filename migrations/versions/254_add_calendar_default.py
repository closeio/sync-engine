"""add calendar default

Revision ID: 52783469ee6c
Revises: 32df3d8ff73e
Create Date: 2022-10-01 13:03:12.879960

"""

# revision identifiers, used by Alembic.
revision = "7bb5c0ca93de"
down_revision = "52783469ee6c"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.add_column(
        "calendar",
        sa.Column(
            "default",
            mysql.TINYINT(display_width=1),
            autoincrement=False,
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("calendar", "default")
