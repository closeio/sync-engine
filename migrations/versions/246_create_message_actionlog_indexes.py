"""
create_message_actionlog_indexes

Revision ID: 69c4b13c806
Revises: 1449eededf1
Create Date: 2018-06-19 14:11:06.448247

"""

# revision identifiers, used by Alembic.
revision = "69c4b13c806"
down_revision = "1449eededf1"

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_message_namespace_id_received_date",
        "message",
        ["namespace_id", "received_date"],
    )

    op.create_index(
        "ix_actionlog_namespace_id_status_type",
        "actionlog",
        ["namespace_id", "status", "type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_namespace_id_received_date", table_name="message"
    )

    op.drop_index(
        "ix_actionlog_namespace_id_status_type", table_name="actionlog"
    )
