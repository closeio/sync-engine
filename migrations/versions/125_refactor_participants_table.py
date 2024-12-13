"""
refactor participants table

Revision ID: 955792afd00
Revises: 526eefc1d600
Create Date: 2014-12-15 14:58:09.922649

"""

# revision identifiers, used by Alembic.
revision = "955792afd00"
down_revision = "40ad73aa49df"

from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.sql import text  # type: ignore[import-untyped]


def upgrade() -> None:
    from inbox.sqlalchemy_ext.util import JSON

    op.add_column(
        "event", sa.Column("participants_by_email", JSON(), nullable=False)
    )
    op.drop_table("eventparticipant")

    conn = op.get_bind()
    conn.execute(text("UPDATE event SET participants_by_email='{}'"))


def downgrade() -> Never:
    raise Exception("Won't.")
