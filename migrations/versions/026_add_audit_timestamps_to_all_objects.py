"""add audit timestamps to all objects

Revision ID: 146b1817e4a8
Revises: 59b42d0ac749
Create Date: 2014-05-09 22:16:00.387937

"""

# revision identifiers, used by Alembic.
revision = "146b1817e4a8"
down_revision = "59b42d0ac749"

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import column, table

table_names = {
    "account",
    "block",
    "contact",
    "folder",
    "folderitem",
    "foldersync",
    "imapuid",
    "internaltag",
    "lens",
    "message",
    "messagecontactassociation",
    "namespace",
    "searchsignal",
    "searchtoken",
    "thread",
    "transaction",
    "uidvalidity",
    "webhook",
}


def add_eas_tables():
    from inbox.ignition import main_engine

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()
    Base.metadata.reflect(engine)
    for table_name in ["easuid", "easfoldersync"]:
        if table_name in Base.metadata.tables:
            table_names.add(table_name)


def upgrade():
    add_eas_tables()

    # mysql 5.5 / sqlalchemy interactions necessitate doing this in steps
    for table_name in sorted(table_names):
        if table_name != "contact":
            op.add_column(
                table_name, sa.Column("created_at", sa.DateTime(), nullable=True)
            )
            op.add_column(
                table_name, sa.Column("updated_at", sa.DateTime(), nullable=True)
            )
        op.add_column(table_name, sa.Column("deleted_at", sa.DateTime(), nullable=True))

        t = table(
            table_name,
            column("created_at", sa.DateTime()),
            column("updated_at", sa.DateTime()),
        )
        op.execute(
            t.update().values(
                {"created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}
            )
        )

        op.alter_column(
            table_name, "created_at", existing_type=sa.DateTime(), nullable=False
        )
        op.alter_column(
            table_name, "updated_at", existing_type=sa.DateTime(), nullable=False
        )

    # missing from a prev revision
    op.create_index(
        "imapaccount_id_folder_id",
        "imapuid",
        ["imapaccount_id", "folder_id"],
        unique=False,
    )
    op.drop_index("imapuid_imapaccount_id_folder_name", table_name="imapuid")


def downgrade():
    add_eas_tables()

    for table_name in sorted(table_names):
        if table_name != "contact":
            op.drop_column(table_name, "updated_at")
            op.drop_column(table_name, "created_at")
        op.drop_column(table_name, "deleted_at")

    op.create_index(
        "imapuid_imapaccount_id_folder_name",
        "imapuid",
        ["imapaccount_id", "folder_id"],
        unique=False,
    )
    op.drop_index("imapaccount_id_folder_id", table_name="imapuid")
