"""
Drop eas_state

Revision ID: 3cea90bfcdea
Revises: 118b3cdd0185
Create Date: 2014-10-01 19:31:24.110587

"""

# revision identifiers, used by Alembic.
revision = "3cea90bfcdea"
down_revision = "118b3cdd0185"

from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine()
    Base = sa.ext.declarative.declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easaccount" in Base.metadata.tables:
        op.drop_column("easaccount", "eas_state")


def downgrade() -> Never:
    raise Exception("Won't")
