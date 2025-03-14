"""
store secrets in local vault

Revision ID: 1925c535a52d
Revises: 29217fad3f46
Create Date: 2014-07-07 15:03:27.386981

"""

# revision identifiers, used by Alembic.
revision = "1925c535a52d"
down_revision = "29217fad3f46"

from datetime import datetime

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    op.create_table(
        "secret",
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("acl_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("secret", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "gmailaccount",
        sa.Column("refresh_token_id", sa.Integer(), nullable=True),
    )

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Account(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["account"]

    class ImapAccount(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["imapaccount"]

    class GmailAccount(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["gmailaccount"]

    class Secret(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["secret"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for acct in db_session.query(GmailAccount):
            secret = Secret(
                acl_id=0,
                type=0,
                secret=acct.refresh_token,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db_session.add(secret)
            db_session.commit()

            acct.refresh_token_id = secret.id
            db_session.add(acct)
            db_session.commit()

    op.alter_column(
        "secret",
        "created_at",
        existing_type=sa.DateTime(),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "secret",
        "updated_at",
        existing_type=sa.DateTime(),
        existing_nullable=True,
        nullable=False,
    )

    op.drop_column("gmailaccount", "refresh_token")


def downgrade() -> None:
    from sqlalchemy.ext.declarative import declarative_base

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Account(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["account"]

    class ImapAccount(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["imapaccount"]

    class GmailAccount(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["gmailaccount"]

    class Secret(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["secret"]

    op.add_column(
        "gmailaccount",
        sa.Column("refresh_token", sa.String(length=512), nullable=True),
    )

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for acct in db_session.query(GmailAccount):
            secret = (
                db_session.query(Secret)
                .filter_by(id=acct.refresh_token_id)
                .one()
            )
            acct.refresh_token = secret.secret
            db_session.add(acct)
        db_session.commit()

    op.drop_column("gmailaccount", "refresh_token_id")
    op.drop_table("secret")
