"""
fix_indexes

Revision ID: 2197bc4a7df5
Revises: 407abeb7398f
Create Date: 2018-04-06 16:31:55.397484

"""

# revision identifiers, used by Alembic.
revision = "2197bc4a7df5"
down_revision = "407abeb7398f"

from alembic import op


def upgrade():
    op.create_index(
        "ix_messagecontactassociation_contact_id",
        "messagecontactassociation",
        ["contact_id"],
        unique=False,
    )
    op.drop_index("ix_transaction_namespace_id", table_name="transaction")
    op.drop_index("idx_namespace", table_name="transaction")


def downgrade():
    op.drop_index(
        "ix_messagecontactassociation_contact_id",
        table_name="messagecontactassociation",
    )
    op.create_index(
        "ix_transaction_namespace_id",
        "transaction",
        ["namespace_id"],
        unique=False,
    )
    op.create_index(
        "idx_namespace", "transaction", ["namespace_id"], unique=False
    )
