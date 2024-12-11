"""add ix_imapuid_account_id_folder_id_msg_uid_desc

Revision ID: e3cf974d07a5
Revises: fe0488decbd1
Create Date: 2024-08-27 09:47:53.661863

"""

# revision identifiers, used by Alembic.
revision = "e3cf974d07a5"
down_revision = "fe0488decbd1"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_index(
        "ix_imapuid_account_id_folder_id_msg_uid_desc",
        "imapuid",
        ["account_id", "folder_id", sa.text("msg_uid DESC")],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_imapuid_account_id_folder_id_msg_uid_desc", table_name="imapuid"
    )
