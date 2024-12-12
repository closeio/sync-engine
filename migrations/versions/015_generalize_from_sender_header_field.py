"""
generalize from/sender header field

Revision ID: 3fee2f161614
Revises: 563d405d1f99
Create Date: 2014-04-24 06:04:21.163229

"""

# revision identifiers, used by Alembic.
revision = "3fee2f161614"
down_revision = "563d405d1f99"


def upgrade() -> None:
    from inbox.models import Message
    from inbox.models.session import session_scope

    with session_scope() as db_session:
        results = db_session.query(Message).all()
        for message in results:
            message.from_addr = [message.from_addr]
            message.sender_addr = [message.sender_addr]
        db_session.commit()


def downgrade() -> None:
    from inbox.models import Message
    from inbox.models.session import session_scope

    with session_scope() as db_session:
        results = db_session.query(Message).all()
        for message in results:
            if message.from_addr:
                message.from_addr = message.from_addr[0]
            if message.sender_addr:
                message.sender_addr = message.sender_addr[0]
        db_session.commit()
