"""fix_action_log_indexes

Revision ID: 2c47d9226de6
Revises: 2197bc4a7df5
Create Date: 2018-04-12 01:16:34.441222

"""

# revision identifiers, used by Alembic.
revision = "2c47d9226de6"
down_revision = "2197bc4a7df5"

from alembic import op


def upgrade():
    op.create_index(
        "ix_actionlog_status_namespace_id_record_id",
        "actionlog",
        ["status", "namespace_id", "record_id"],
        unique=False,
    )
    op.drop_index("idx_actionlog_status_type", table_name="actionlog")
    op.drop_index("ix_actionlog_status_retries", table_name="actionlog")


def downgrade():
    op.create_index(
        "idx_actionlog_status_type", "actionlog", ["status", "type"], unique=False
    )
    op.create_index(
        "ix_actionlog_status_retries", "actionlog", ["status", "retries"], unique=False
    )
    op.drop_index("ix_actionlog_status_namespace_id_record_id", table_name="actionlog")
