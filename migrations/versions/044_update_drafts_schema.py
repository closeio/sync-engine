"""update drafts schema

Revision ID: 247cd689758c
Revises:5a136610b50b
Create Date: 2014-06-19 19:09:48.387937

"""

# revision identifiers, used by Alembic.
revision = "247cd689758c"
down_revision = "5a136610b50b"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def upgrade():
    op.add_column(
        "spoolmessage",
        sa.Column(
            "is_reply",
            sa.Boolean(),
            server_default=sa.sql.expression.false(),
            nullable=False,
        ),
    )
    # Drop draft_copied_from and replyto_thread_id foreign key constraints.
    op.drop_constraint(
        "spoolmessage_ibfk_4", "spoolmessage", type_="foreignkey"
    )
    op.drop_constraint(
        "spoolmessage_ibfk_5", "spoolmessage", type_="foreignkey"
    )
    op.drop_column("spoolmessage", "draft_copied_from")
    op.drop_column("spoolmessage", "replyto_thread_id")
    op.drop_table("draftthread")


def downgrade():
    op.add_column(
        "spoolmessage",
        sa.Column(
            "replyto_thread_id", mysql.INTEGER(display_width=11), nullable=True
        ),
    )
    op.add_column(
        "spoolmessage",
        sa.Column(
            "draft_copied_from", mysql.INTEGER(display_width=11), nullable=True
        ),
    )
    op.drop_column("spoolmessage", "is_reply")
    op.create_table(
        "draftthread",
        sa.Column("created_at", mysql.DATETIME(), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(), nullable=False),
        sa.Column("deleted_at", mysql.DATETIME(), nullable=True),
        sa.Column("public_id", sa.BINARY(length=16), nullable=False),
        sa.Column("id", mysql.INTEGER(display_width=11), nullable=False),
        sa.Column("master_public_id", sa.BINARY(length=16), nullable=False),
        sa.Column(
            "thread_id",
            mysql.INTEGER(display_width=11),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "message_id",
            mysql.INTEGER(display_width=11),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["message.id"], name="draftthread_ibfk_2"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["thread.id"], name="draftthread_ibfk_1"
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_default_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
