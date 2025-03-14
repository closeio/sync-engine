"""
Kill SpoolMessage

Revision ID: 4f3a1f6eaee3
Revises: 2e6120c97485
Create Date: 2014-07-21 17:35:46.026443

"""

# revision identifiers, used by Alembic.
revision = "4f3a1f6eaee3"
down_revision = "2e6120c97485"

from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    op.add_column(
        "message",
        sa.Column(
            "is_created",
            sa.Boolean,
            server_default=sa.sql.expression.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "message",
        sa.Column(
            "is_sent",
            sa.Boolean,
            server_default=sa.sql.expression.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "message",
        sa.Column(
            "state", sa.Enum("draft", "sending", "sending failed", "sent")
        ),
    )
    op.add_column("message", sa.Column("is_reply", sa.Boolean()))
    op.add_column(
        "message",
        sa.Column("resolved_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "message_ibfk_2", "message", "message", ["resolved_message_id"], ["id"]
    )

    op.add_column(
        "message", sa.Column("parent_draft_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "message_ibfk_3", "message", "message", ["parent_draft_id"], ["id"]
    )

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Message(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["message"]

    class SpoolMessage(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["spoolmessage"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for sm in db_session.query(SpoolMessage).yield_per(250):
            m = db_session.query(Message).get(sm.id)

            m.is_sent = sm.is_sent
            m.state = sm.state
            m.is_reply = sm.is_reply
            m.resolved_message_id = sm.resolved_message_id
            m.parent_draft_id = sm.parent_draft_id

        db_session.commit()

    op.drop_table("spoolmessage")


def downgrade() -> Never:
    raise Exception("No going back.")
