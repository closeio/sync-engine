"""
switch to server-side creation timestamps

Revision ID: 1dfc65e583bf
Revises: 1b0b4e6fdf96
Create Date: 2018-02-08 23:06:09.384416

"""

# revision identifiers, used by Alembic.
revision = "1dfc65e583bf"
down_revision = "1b0b4e6fdf96"

from alembic import op
from sqlalchemy.sql import text  # type: ignore[import-untyped]

# SELECT table_name FROM information_schema.columns WHERE table_schema='inbox' AND column_name='created_at'
TABLES = [
    "account",
    "accounttransaction",
    "actionlog",
    "block",
    "calendar",
    "category",
    "contact",
    "contactsearchindexcursor",
    "dataprocessingcache",
    "event",
    "folder",
    "gmailauthcredentials",
    "imapfolderinfo",
    "imapfoldersyncstatus",
    "imapuid",
    "label",
    "labelitem",
    "message",
    "messagecategory",
    "messagecontactassociation",
    "metadata",
    "namespace",
    "part",
    "phonenumber",
    "secret",
    "thread",
    "transaction",
]


def upgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(
            text(
                f"ALTER TABLE `{table}` MODIFY COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(
            text(
                f"ALTER TABLE `{table}` MODIFY COLUMN `created_at` DATETIME NOT NULL"
            )
        )
