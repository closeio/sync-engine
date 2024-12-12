"""
Add ON DELETE CASCADE to part.block_id.

Revision ID: 1d93c9f9f506
Revises: 3bb4a941639c
Create Date: 2015-02-04 10:03:54.828708

"""

# revision identifiers, used by Alembic.
revision = "1d93c9f9f506"
down_revision = "3bb4a941639c"

from alembic import op


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        """
        ALTER TABLE part DROP FOREIGN KEY part_ibfk_1;
        ALTER TABLE part ADD CONSTRAINT part_ibfk_1 FOREIGN KEY (block_id) REFERENCES block(id) ON DELETE CASCADE;
        """
    )


def downgrade() -> None:
    # NOTE: there is no going back since it would be a mismatch between the
    # code and the db! -siro
    pass
