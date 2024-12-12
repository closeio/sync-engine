"""
add message index

Revision ID: 4270a032b943
Revises:13faec74da45
Create Date: 2015-02-06 05:14:00.041485

"""

# revision identifiers, used by Alembic.
revision = "4270a032b943"
down_revision = "13faec74da45"

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_message_namespace_id_deleted_at",
        "message",
        ["namespace_id", "deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_message_namespace_id_deleted_at", table_name="message")
