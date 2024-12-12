"""
create messagecategory index

Revision ID: 407abeb7398f
Revises: 36ff8677e77
Create Date: 2018-03-29 19:48:04.499376

"""

# revision identifiers, used by Alembic.
revision = "407abeb7398f"
down_revision = "36ff8677e77"

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_messagecategory_category_id",
        "messagecategory",
        ["category_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_messagecategory_category_id", table_name="messagecategory"
    )
