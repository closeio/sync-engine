"""Add message_id_header index

Revision ID: 1b0b4e6fdf96
Revises: 780b1dabd51
Create Date: 2018-01-12 21:24:00.000000

"""

# revision identifiers, used by Alembic.
revision = "1b0b4e6fdf96"
down_revision = "780b1dabd51"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.create_index(
        "ix_message_message_id_header_namespace_id",
        "message",
        ["message_id_header", "namespace_id"],
        unique=False,
        mysql_length={"message_id_header": 191},
    )


def downgrade():
    op.drop_index(
        "ix_message_message_id_header_namespace_id", table_name="message"
    )
