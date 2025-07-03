"""
extra transaction data

Revision ID: 5093433b073
Revises: 3fee2f161614
Create Date: 2014-04-25 23:23:36.442325

"""

# revision identifiers, used by Alembic.
revision = "5093433b073"
down_revision = "3fee2f161614"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column(
        "transaction",
        sa.Column("additional_data", sa.Text(4194304), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction", "additional_data")
