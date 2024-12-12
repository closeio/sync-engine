"""
Store ImapUid<msg_uid as BigInteger instead of Integer

Revision ID: 519e462df171
Revises: 4fd291c6940c
Create Date: 2014-04-25 00:54:05.728375

"""

# revision identifiers, used by Alembic.
revision = "519e462df171"
down_revision = "4fd291c6940c"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade() -> None:
    op.alter_column("imapuid", "msg_uid", type_=mysql.BIGINT)


def downgrade() -> None:
    op.alter_column("imapuid", "msg_uid", type_=sa.Integer)
