"""
Dont specially store mailing list headers

Revision ID: 5143154fb1a2
Revises: 3795b2a97af1
Create Date: 2014-07-16 22:19:27.152773

"""

# revision identifiers, used by Alembic.
revision = "5143154fb1a2"
down_revision = "3795b2a97af1"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade() -> None:
    op.drop_column("message", "mailing_list_headers")
    op.drop_column("thread", "mailing_list_headers")


def downgrade() -> None:
    # downgrade method provided for convenience, but we won't get the data
    # back. Didn't need it anyway...
    op.add_column(
        "thread",
        sa.Column("mailing_list_headers", mysql.TEXT(), nullable=True),
    )
    op.add_column(
        "message",
        sa.Column("mailing_list_headers", mysql.TEXT(), nullable=True),
    )
