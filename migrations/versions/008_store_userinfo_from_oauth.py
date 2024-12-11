"""
Store UserInfo from Oauth

Revision ID: 3c11391b5eb0
Revises: 1c3f1812f2d9
Create Date: 2014-04-04 00:55:47.813888

"""

# revision identifiers, used by Alembic.
revision = "3c11391b5eb0"
down_revision = "1c3f1812f2d9"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "account",
        sa.Column("family_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "account", sa.Column("g_gender", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "account", sa.Column("g_locale", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "account",
        sa.Column("g_picture_url", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "account",
        sa.Column("g_plus_url", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "account",
        sa.Column("given_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "account", sa.Column("google_id", sa.String(length=255), nullable=True)
    )
    ### end Alembic commands ###


def downgrade() -> None:
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("account", "google_id")
    op.drop_column("account", "given_name")
    op.drop_column("account", "g_plus_url")
    op.drop_column("account", "g_picture_url")
    op.drop_column("account", "g_locale")
    op.drop_column("account", "g_gender")
    op.drop_column("account", "family_name")
    ### end Alembic commands ###
