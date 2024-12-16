"""
Enforce length limit of 255 on Message subjects

Revision ID: 4af5952e8a5b
Revises: 4b4c5579c083
Create Date: 2014-07-18 22:00:45.339930

"""

# revision identifiers, used by Alembic.
revision = "4af5952e8a5b"
down_revision = "4f57260602c9"

from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def truncate_subject(obj) -> None:  # type: ignore[no-untyped-def]
    if obj.subject is None:
        return
    if len(obj.subject) > 255:
        obj.subject = obj.subject[:255]
    return


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)

    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Message(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["message"]

    class Thread(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["thread"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        count = 0
        for msg in (
            db_session.query(Message)
            .options(sa.orm.load_only("subject"))
            .yield_per(500)
        ):
            truncate_subject(msg)
            count += 1
            if count > 500:
                db_session.commit()
                count = 0
        db_session.commit()

        for thread in (
            db_session.query(Thread)
            .options(sa.orm.load_only("subject"))
            .yield_per(500)
        ):
            truncate_subject(thread)
            count += 1
            if count > 500:
                db_session.commit()
                count = 0
        db_session.commit()

    op.alter_column(
        "message", "subject", type_=sa.String(255), existing_nullable=True
    )
    op.alter_column(
        "thread", "subject", type_=sa.String(255), existing_nullable=True
    )


def downgrade() -> Never:
    raise Exception("Not supported!")
