"""
Add references column to message table

Revision ID: 4fd291c6940c
Revises: 10ef1d46f016
Create Date: 2014-04-25 00:51:04.825531

"""

# revision identifiers, used by Alembic.
revision = "4fd291c6940c"
down_revision = "10ef1d46f016"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.sqlalchemy_ext.util import JSON

    op.add_column("message", sa.Column("references", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("message", "references")
