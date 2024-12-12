"""
longer event summaries.
Revision ID: 4e3e8abea884

Revises: 5901bf556d83
Create Date: 2014-08-14 21:47:43.934044

"""

# revision identifiers, used by Alembic.
revision = "4e3e8abea884"
down_revision = "5901bf556d83"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.alter_column("event", "subject", type_=sa.String(1024))


def downgrade() -> None:
    op.alter_column("event", "subject", type_=sa.String(255))
