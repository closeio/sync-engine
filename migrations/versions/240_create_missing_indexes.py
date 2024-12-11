"""
create missing indexes and drop redundant ones

Revision ID: 36ff8677e77
Revises: 1dfc65e583bf
Create Date: 2018-02-26 19:17:07.756565

"""

# revision identifiers, used by Alembic.
revision = "36ff8677e77"
down_revision = "1dfc65e583bf"

from alembic import op


def upgrade() -> None:
    # Thread table
    op.create_index(
        "ix_namespace_id__cleaned_subject",
        "thread",
        ["namespace_id", "_cleaned_subject"],
        unique=False,
        mysql_length={"_cleaned_subject": 80},
    )
    op.drop_index("ix_cleaned_subject", table_name="thread")
    op.drop_index("ix_thread_namespace_id", table_name="thread")

    op.drop_index("ix_thread_subject", "thread")
    op.create_index(
        "ix_thread_subject",
        "thread",
        ["subject"],
        unique=False,
        mysql_length=80,
    )

    # Message table
    op.create_index(
        "ix_message_thread_id", "message", ["thread_id"], unique=False
    )
    op.drop_index(
        "ix_message_namespace_id_message_id_header_subject",
        table_name="message",
    )

    op.drop_index("ix_message_subject", "message")
    op.create_index(
        "ix_message_subject",
        "message",
        ["subject"],
        unique=False,
        mysql_length=80,
    )

    conn = op.get_bind()
    conn.execute(
        "ALTER TABLE `message` CHANGE `data_sha256` `data_sha256` VARCHAR(64)  CHARACTER SET ascii  NULL  DEFAULT NULL"
    )

    op.drop_index(
        "ix_message_message_id_header_namespace_id", table_name="message"
    )
    op.create_index(
        "ix_message_message_id_header_namespace_id",
        "message",
        ["message_id_header", "namespace_id"],
        unique=False,
        mysql_length={"message_id_header": 80},
    )

    op.create_index(
        "ix_message_reply_to_message_id",
        "message",
        ["reply_to_message_id"],
        unique=False,
    )


def downgrade() -> None:
    # Thread table
    op.create_index(
        "ix_thread_namespace_id", "thread", ["namespace_id"], unique=False
    )
    op.create_index(
        "ix_cleaned_subject",
        "thread",
        ["_cleaned_subject"],
        unique=False,
        mysql_length={"_cleaned_subject": 191},
    )
    op.drop_index("ix_namespace_id__cleaned_subject", table_name="thread")

    op.drop_index("ix_thread_subject", "thread")
    op.create_index(
        "ix_thread_subject",
        "thread",
        ["subject"],
        unique=False,
        mysql_length=191,
    )

    # Message table
    op.create_index(
        "ix_message_namespace_id_message_id_header_subject",
        "message",
        ["namespace_id", "subject", "message_id_header"],
        unique=False,
        mysql_length={"subject": 191, "message_id_header": 191},
    )
    op.drop_index("ix_message_thread_id", table_name="message")
    op.drop_index("ix_message_subject", "message")
    op.create_index(
        "ix_message_subject",
        "message",
        ["subject"],
        unique=False,
        mysql_length=191,
    )

    op.drop_index("ix_message_data_sha256", "message")
    conn = op.get_bind()
    conn.execute(
        "ALTER TABLE `message` CHANGE `data_sha256` `data_sha256` VARCHAR(255)  NULL  DEFAULT NULL;"
    )
    op.create_index(
        "ix_message_data_sha256",
        "message",
        ["data_sha256"],
        unique=False,
        mysql_length=191,
    )

    op.drop_index(
        "ix_message_message_id_header_namespace_id", table_name="message"
    )
    op.create_index(
        "ix_message_message_id_header_namespace_id",
        "message",
        ["message_id_header", "namespace_id"],
        unique=False,
        mysql_length={"message_id_header": 191},
    )

    op.drop_index("ix_message_reply_to_message_id", table_name="message")
