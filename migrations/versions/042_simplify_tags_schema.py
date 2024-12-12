"""
simplify tags schema

Revision ID: 459dbc29648
Revises: 159609404baf
Create Date: 2014-06-23 18:37:56.183884

"""

# revision identifiers, used by Alembic.
revision = "459dbc29648"
down_revision = "159609404baf"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.drop_column("tag", "user_mutable")


def downgrade():
    op.add_column(
        "tag",
        sa.Column(
            "user_mutable",
            mysql.TINYINT(display_width=1),
            server_default="1",
            nullable=False,
        ),
    )
