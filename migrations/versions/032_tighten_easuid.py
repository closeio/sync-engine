"""
Tighten EAS constraints and fix easfoldersync state enum.

Revision ID: 3f96e92953e1
Revises: 55f0ff54c776
Create Date: 2014-05-21 17:43:44.556716

"""

# revision identifiers, used by Alembic.
revision = "3f96e92953e1"
down_revision = "55f0ff54c776"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.ext.declarative import declarative_base


def upgrade() -> None:
    from inbox.ignition import main_engine

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()
    Base.metadata.reflect(engine)

    if "easfoldersync" in Base.metadata.tables:
        op.alter_column(
            "easfoldersync",
            "state",
            type_=sa.Enum(
                "initial",
                "initial keyinvalid",
                "poll",
                "poll keyinvalid",
                "finish",
            ),
            existing_nullable=False,
            server_default="initial",
        )

    if "easuid" in Base.metadata.tables:
        op.alter_column(
            "easuid", "message_id", existing_type=sa.Integer(), nullable=False
        )
        op.alter_column(
            "easuid", "fld_uid", existing_type=sa.Integer(), nullable=False
        )
        op.alter_column(
            "easuid", "msg_uid", existing_type=sa.Integer(), nullable=False
        )


def downgrade() -> None:
    from inbox.ignition import main_engine

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()
    Base.metadata.reflect(engine)

    if "easfoldersync" in Base.metadata.tables:
        op.alter_column(
            "easfoldersync",
            "state",
            type_=sa.Enum(
                "initial",
                "initial uidinvalid",
                "poll",
                "poll uidinvalid",
                "finish",
            ),
            existing_nullable=False,
        )

    if "easuid" in Base.metadata.tables:
        op.alter_column(
            "easuid", "message_id", existing_type=sa.Integer(), nullable=True
        )
        op.alter_column(
            "easuid", "fld_uid", existing_type=sa.Integer(), nullable=True
        )
        op.alter_column(
            "easuid", "msg_uid", existing_type=sa.Integer(), nullable=True
        )
