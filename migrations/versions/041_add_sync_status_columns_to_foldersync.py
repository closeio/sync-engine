"""
Add sync status columns to foldersync

Revision ID: 159609404baf
Revises: 1d7374c286c5
Create Date: 2014-06-10 19:50:59.005478

"""

# revision identifiers, used by Alembic.
revision = "159609404baf"
down_revision = "4085dd542739"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    from inbox.sqlalchemy_ext.util import JSON, MutableDict

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    op.add_column(
        "foldersync",
        sa.Column(
            "_sync_status", MutableDict.as_mutable(JSON()), nullable=True
        ),
    )

    if "easfoldersync" in Base.metadata.tables:
        op.add_column(
            "easfoldersync",
            sa.Column(
                "_sync_status", MutableDict.as_mutable(JSON()), nullable=True
            ),
        )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    op.drop_column("foldersync", "_sync_status")

    if "easfoldersync" in Base.metadata.tables:
        op.drop_column("easfoldersync", "_sync_status")
