"""
Add eas_thrid index

Revision ID: 3c02d8204335
Revises:43cd2de5ad85
Create Date: 2014-08-02 03:12:47.504963

"""

# revision identifiers, used by Alembic.
revision = "3c02d8204335"
down_revision = "43cd2de5ad85"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine()
    Base = sa.ext.declarative.declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easthread" in Base.metadata.tables:
        op.create_index(
            "ix_easthread_eas_thrid",
            "easthread",
            ["eas_thrid"],
            unique=False,
            mysql_length=256,
        )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine()
    Base = sa.ext.declarative.declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easthread" in Base.metadata.tables:
        op.drop_index("ix_easthread_eas_thrid", table_name="easthread")
