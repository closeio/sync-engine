"""
Remove notion of 'remote' contact and drop contact 'source' column

Revision ID: 3ab34bc85c8d
Revises: 3f01a3f1b4cc
Create Date: 2015-02-16 16:03:45.288539

"""

# revision identifiers, used by Alembic.
revision = "3ab34bc85c8d"
down_revision = "3f01a3f1b4cc"

from typing import Never

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

    class Contact_Old(Base):  # type: ignore[misc, valid-type]  # noqa: N801
        __table__ = Base.metadata.tables["contact"]

    # Delete the "remote" contacts. This is just a server cache for comparing
    # any changes, now handled by the previous "local" contacts
    with session_scope() as db_session:  # type: ignore[call-arg]
        db_session.query(Contact_Old).filter_by(source="remote").delete()

    op.drop_column("contact", "source")


def downgrade() -> Never:
    raise Exception("Can't roll back. Migration removed data.")
