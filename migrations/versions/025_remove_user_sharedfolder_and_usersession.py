"""
Remove User, SharedFolder, and UserSession

Revision ID: 59b42d0ac749
Revises: 4c1eb89f6bed
Create Date: 2014-05-09 07:47:54.866524

"""

# revision identifiers, used by Alembic.
revision = "59b42d0ac749"
down_revision = "4c1eb89f6bed"

from typing import Never

from alembic import op


def upgrade() -> None:
    op.drop_constraint("account_ibfk_1", "account", type_="foreignkey")
    op.drop_constraint("usersession_ibfk_1", "usersession", type_="foreignkey")
    op.drop_constraint(
        "sharedfolder_ibfk_1", "sharedfolder", type_="foreignkey"
    )
    op.drop_constraint(
        "sharedfolder_ibfk_2", "sharedfolder", type_="foreignkey"
    )

    op.drop_table("user")
    op.drop_table("sharedfolder")
    op.drop_table("usersession")
    op.drop_column("account", "user_id")


def downgrade() -> Never:
    raise Exception("Not supported! You didn't need those tables anyway.")
