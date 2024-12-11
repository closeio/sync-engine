"""
empty Change imapfolderinfo uidnext to bigint from int

Revision ID: 34815f9e639c
Revises: 53e6a7446c45
Create Date: 2016-10-14 23:13:19.620120

"""

# revision identifiers, used by Alembic.
revision = "34815f9e639c"
down_revision = "53e6a7446c45"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.alter_column(
        "imapfolderinfo",
        "uidnext",
        type_=sa.BigInteger,
        existing_type=sa.Integer,
        existing_server_default=sa.sql.expression.null(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "imapfolderinfo",
        "uidnext",
        type_=sa.Integer,
        existing_type=sa.BigInteger,
        existing_server_default=sa.sql.expression.null(),
        existing_nullable=True,
    )
