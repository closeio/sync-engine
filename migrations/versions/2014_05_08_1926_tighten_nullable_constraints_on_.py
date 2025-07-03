"""
Tighten nullable constraints on ImapUids.

Will help prevent future heisenbugs.


Revision ID: 4e04f752b7ad
Revises: 2c313b6ddd9b
Create Date: 2014-05-08 19:26:07.253333

"""

# revision identifiers, used by Alembic.
revision = "4e04f752b7ad"
down_revision = "2c313b6ddd9b"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class ImapUid(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["imapuid"]

    print("Deleting imapuid objects with NULL message_id...")

    with session_scope(versioned=False) as session:  # type: ignore[call-arg]
        session.query(ImapUid).filter_by(message_id=None).delete()
        session.commit()

    print("Tightening NULL constraints...")

    op.alter_column(
        "imapuid", "message_id", existing_type=sa.Integer(), nullable=False
    )
    # unrelated to current bugs, but no reason this should be NULLable either
    op.alter_column(
        "imapuid", "msg_uid", existing_type=sa.BigInteger(), nullable=False
    )


def downgrade() -> None:
    op.alter_column("imapuid", "message_id", nullable=True)
    op.alter_column("imapuid", "msg_uid", nullable=True)
