"""New unique constraint index on imapuid table

Revision ID: 41e576226cf5
Revises: 52783469ee6c
Create Date: 2022-03-04 10:40:21.868269

"""

# revision identifiers, used by Alembic.
revision = "41e576226cf5"
down_revision = "52783469ee6c"

from alembic import op


def upgrade():
    op.create_index(
        "account_id", "imapuid", ["account_id", "folder_id", "msg_uid"], unique=True,
    )


def downgrade():
    op.drop_constraint("account_id", "imapuid", type_="unique")
