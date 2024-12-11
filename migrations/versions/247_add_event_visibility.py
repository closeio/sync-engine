"""add visibility to event

Revision ID: 53b532fda984
Revises: 69c4b13c806
Create Date: 2019-07-11 21:29:39.635787

"""

# revision identifiers, used by Alembic.
revision = "53b532fda984"
down_revision = "69c4b13c806"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "event",
        sa.Column("visibility", sa.Enum("private", "public"), nullable=True),
    )


def downgrade():
    op.drop_column("event", "visibility")
