"""drop folder_id unique constraint

Revision ID: ac2d6f8489bb
Revises: e3cf974d07a5
Create Date: 2024-09-03 09:23:07.779381

"""

# revision identifiers, used by Alembic.
revision = "ac2d6f8489bb"
down_revision = "e3cf974d07a5"

from alembic import op


def upgrade():
    op.create_index("folder_id_new", "imapuid", ["folder_id"])
    op.drop_index("folder_id", table_name="imapuid")
    op.execute("ALTER TABLE imapuid RENAME INDEX folder_id_new TO folder_id")


def downgrade():
    op.create_index(
        "folder_id_old", "imapuid", ["folder_id", "msg_uid", "account_id"], unique=True
    )
    op.drop_index("folder_id", table_name="imapuid")
    op.execute("ALTER TABLE imapuid RENAME INDEX folder_id_old TO folder_id")
