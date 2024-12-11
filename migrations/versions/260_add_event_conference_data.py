"""add event.conference_data

Revision ID: fe0488decbd1
Revises: f9dab5e44c0f
Create Date: 2023-07-03 14:50:25.020134

"""

# revision identifiers, used by Alembic.
revision = "fe0488decbd1"
down_revision = "f9dab5e44c0f"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "event",
        sa.Column("conference_data", sa.Text(length=4194304), nullable=True),
    )


def downgrade():
    op.drop_column("event", "conference_data")
