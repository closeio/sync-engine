"""
Add index for cursor queries

Revision ID: 2c67046c548d
Revises: 2c47d9226de6
Create Date: 2018-04-23 16:39:39.990250

"""

# revision identifiers, used by Alembic.
revision = "2c67046c548d"
down_revision = "2c47d9226de6"

from alembic import op


def upgrade():
    op.create_index(
        "ix_transaction_namespace_id_object_type_id",
        "transaction",
        ["namespace_id", "object_type", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_transaction_namespace_id_object_type_id", table_name="transaction"
    )
