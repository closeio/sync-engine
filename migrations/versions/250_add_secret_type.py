"""Add "authalligator" secret type.

Revision ID: 1d84676d7731
Revises: 36ce9c8635ef
Create Date: 2020-09-17 10:38:08.773405

"""

# revision identifiers, used by Alembic.
revision = "1d84676d7731"
down_revision = "36ce9c8635ef"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.alter_column(
        "secret",
        "type",
        type_=sa.Enum("password", "token", "authalligator"),
        existing_server_default=None,
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "secret",
        "type",
        type_=sa.Enum("password", "token"),
        existing_server_default=None,
        existing_nullable=False,
    )
