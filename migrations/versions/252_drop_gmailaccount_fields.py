"""
drop_gmailaccount_fields

Revision ID: 32df3d8ff73e
Revises: 50407c7fe030
Create Date: 2020-09-22 12:42:04.425673

"""

# revision identifiers, used by Alembic.
revision = "32df3d8ff73e"
down_revision = "50407c7fe030"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects import mysql  # type: ignore[import-untyped]


def upgrade() -> None:
    op.drop_column("gmailaccount", "picture")
    op.drop_column("gmailaccount", "g_id")
    op.drop_column("gmailaccount", "family_name")
    op.drop_column("gmailaccount", "locale")
    op.drop_column("gmailaccount", "gender")
    op.drop_column("gmailaccount", "home_domain")
    op.drop_column("gmailaccount", "access_type")
    op.drop_column("gmailaccount", "g_user_id")
    op.drop_column("gmailaccount", "given_name")
    op.drop_column("gmailaccount", "client_secret")
    op.drop_column("gmailaccount", "link")
    op.drop_column("gmailaccount", "g_id_token")


def downgrade() -> None:
    op.add_column(
        "gmailaccount",
        sa.Column("g_id_token", mysql.VARCHAR(length=2048), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("link", mysql.VARCHAR(length=256), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("client_secret", mysql.VARCHAR(length=256), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("given_name", mysql.VARCHAR(length=256), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("g_user_id", mysql.VARCHAR(length=32), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("access_type", mysql.VARCHAR(length=64), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("home_domain", mysql.VARCHAR(length=256), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("gender", mysql.VARCHAR(length=16), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("locale", mysql.VARCHAR(length=8), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("family_name", mysql.VARCHAR(length=256), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("g_id", mysql.VARCHAR(length=32), nullable=True),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("picture", mysql.VARCHAR(length=1024), nullable=True),
    )
