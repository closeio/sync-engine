"""
create outlook_account column

Revision ID: 4fa0540482f8
Revises: 691fa97024d
Create Date: 2015-08-06 14:05:00.436949

"""

# revision identifiers, used by Alembic.
revision = "4fa0540482f8"
down_revision = "51ad0922ad8e"

from alembic import op
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)
from sqlalchemy.sql import text  # type: ignore[import-untyped]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("set @@lock_wait_timeout = 20;"))

    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easaccount" in Base.metadata.tables:
        conn.execute(
            text("ALTER TABLE easaccount ADD COLUMN outlook_account BOOL;")
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("set @@lock_wait_timeout = 20;"))

    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easaccount" in Base.metadata.tables:
        conn.execute(
            text("ALTER TABLE easaccount DROP COLUMN outlook_account;")
        )
