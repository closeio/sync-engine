"""
recompute snippets

Revision ID: 4e93522b5b62
Revises: 2525c5245cc2
Create Date: 2014-07-31 09:37:48.099402

"""

# revision identifiers, used by Alembic.
revision = "4e93522b5b62"
down_revision = "3bb5d61c895c"

from sqlalchemy.ext.declarative import declarative_base


# solution from http://stackoverflow.com/a/1217947
def page_query(q):  # noqa: ANN201
    CHUNK_SIZE = 1000  # noqa: N806
    offset = 0
    while True:
        r = False
        for elem in q.limit(CHUNK_SIZE).offset(offset):
            r = True
            yield elem
        offset += CHUNK_SIZE
        if not r:
            break


def upgrade() -> None:
    from inbox.ignition import main_engine
    from inbox.models.session import session_scope
    from inbox.util.html import strip_tags

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    SNIPPET_LENGTH = 191  # noqa: N806

    class Message(Base):
        __table__ = Base.metadata.tables["message"]

    def calculate_html_snippet(msg, text) -> None:
        text = (
            text.replace("<br>", " ")
            .replace("<br/>", " ")
            .replace("<br />", " ")
        )
        text = strip_tags(text)
        calculate_plaintext_snippet(msg, text)

    def calculate_plaintext_snippet(msg, text) -> None:
        msg.snippet = " ".join(text.split())[:SNIPPET_LENGTH]

    with session_scope(versioned=False) as db_session:
        for message in page_query(db_session.query(Message)):
            if not message.decode_error:
                calculate_html_snippet(message, message.sanitized_body)
    db_session.commit()


def downgrade() -> None:
    pass
