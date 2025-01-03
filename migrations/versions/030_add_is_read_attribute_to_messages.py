"""
add is_read attribute to messages

Revision ID: 1b6ceae51b43
Revises: 52a9a976a2e0
Create Date: 2014-05-15 23:57:34.159260

"""

# revision identifiers, used by Alembic.
revision = "1b6ceae51b43"
down_revision = "52a9a976a2e0"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects import mysql  # type: ignore[import-untyped]
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    backref,
    relationship,
)


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.sql.expression.false(),
            nullable=False,
        ),
    )

    op.alter_column(
        "usertagitem",
        "created_at",
        existing_type=mysql.DATETIME(),
        nullable=False,
    )
    op.alter_column(
        "usertagitem",
        "updated_at",
        existing_type=mysql.DATETIME(),
        nullable=False,
    )

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Message(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["message"]

    class ImapUid(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["imapuid"]
        message = relationship(
            "Message",
            backref=backref(
                "imapuids",
                primaryjoin="and_("
                "Message.id == ImapUid.message_id, "
                "ImapUid.deleted_at == None)",
            ),
            primaryjoin="and_("
            "ImapUid.message_id == Message.id,"
            "Message.deleted_at == None)",
        )

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for uid in db_session.query(ImapUid).yield_per(500):
            if uid.is_seen:
                uid.message.is_read = True

        db_session.commit()


def downgrade() -> None:
    op.alter_column(
        "usertagitem",
        "updated_at",
        existing_type=mysql.DATETIME(),
        nullable=True,
    )
    op.alter_column(
        "usertagitem",
        "created_at",
        existing_type=mysql.DATETIME(),
        nullable=True,
    )
    op.drop_column("message", "is_read")
