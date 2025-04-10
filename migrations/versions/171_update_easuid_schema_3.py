"""
update_easuid_schema_3.py

Revision ID: 584356bf23a3
Revises: 3ee78a8b1ac6
Create Date: 2015-05-19 21:21:36.593525

"""

# revision identifiers, used by Alembic.
revision = "584356bf23a3"
down_revision = "3ee78a8b1ac6"

import sqlalchemy as sa  # type: ignore[import-untyped]


def upgrade() -> None:
    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easuid"):
        return

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class EASUid(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["easuid"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        # STOPSHIP(emfree): determine if we need to batch this update on large
        # databases.
        db_session.query(EASUid).update(
            {"server_id": sa.func.concat(EASUid.fld_uid, ":", EASUid.msg_uid)},
            synchronize_session=False,
        )


def downgrade() -> None:
    pass
