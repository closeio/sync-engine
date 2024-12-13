"""
migrate body format

Revision ID: 3d4f5741e1d7
Revises: 29698176aa8d
Create Date: 2015-05-10 03:16:04.846781

"""

# revision identifiers, used by Alembic.
revision = "3d4f5741e1d7"
down_revision = "29698176aa8d"

import sqlalchemy as sa  # type: ignore[import-untyped]
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)
from sqlalchemy.orm import load_only  # type: ignore[import-untyped]

CHUNK_SIZE = 1000


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope
    from inbox.security.blobstorage import encode_blob

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Message(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["message"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        (max_id,) = db_session.query(sa.func.max(Message.id)).one()
        if max_id is None:
            max_id = 0
        for i in range(0, max_id, CHUNK_SIZE):
            messages = (
                db_session.query(Message)
                .filter(Message.id > i, Message.id <= i + CHUNK_SIZE)
                .options(load_only("_compacted_body", "sanitized_body"))
            )
            for message in messages:
                if message._compacted_body is None:
                    message._compacted_body = encode_blob(
                        message.sanitized_body.encode("utf-8")
                    )
            db_session.commit()


def downgrade() -> None:
    pass
