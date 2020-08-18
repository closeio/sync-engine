"""switch to server-side creation timestamps

Revision ID: 1dfc65e583bf
Revises: 1b0b4e6fdf96
Create Date: 2018-02-08 23:06:09.384416

"""

# revision identifiers, used by Alembic.
revision = "1dfc65e583bf"
down_revision = "1b0b4e6fdf96"

from alembic import op
from sqlalchemy.sql import text

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


def upgrade():
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(
            text(
                "ALTER TABLE `{}` MODIFY COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP".format(
                    table
                )
            )
        )


def downgrade():
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(
            text(
                "ALTER TABLE `{}` MODIFY COLUMN `created_at` DATETIME NOT NULL".format(
                    table
                )
            )
        )
