"""
canonicalize addresses

Revision ID: 3795b2a97af1
Revises:358d0320397f
Create Date: 2014-07-15 22:11:38.037716

"""

# revision identifiers, used by Alembic.
revision = "3795b2a97af1"
down_revision = "358d0320397f"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects import mysql  # type: ignore[import-untyped]


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column(
            "_canonicalized_address", sa.String(length=191), nullable=True
        ),
    )
    op.add_column(
        "account",
        sa.Column("_raw_address", sa.String(length=191), nullable=True),
    )
    op.create_index(
        "ix_account__canonicalized_address",
        "account",
        ["_canonicalized_address"],
        unique=False,
    )
    op.create_index(
        "ix_account__raw_address", "account", ["_raw_address"], unique=False
    )

    op.add_column(
        "contact",
        sa.Column(
            "_canonicalized_address", sa.String(length=191), nullable=True
        ),
    )
    op.add_column(
        "contact",
        sa.Column("_raw_address", sa.String(length=191), nullable=True),
    )
    op.create_index(
        "ix_contact__canonicalized_address",
        "contact",
        ["_canonicalized_address"],
        unique=False,
    )
    op.create_index(
        "ix_contact__raw_address", "contact", ["_raw_address"], unique=False
    )

    from flanker.addresslib import address  # type: ignore[import-untyped]

    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    from inbox.models.session import session_scope

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    def canonicalize_address(addr):  # type: ignore[no-untyped-def]
        """Gmail addresses with and without periods are the same."""
        parsed_address = address.parse(addr, addr_spec_only=True)
        if not isinstance(parsed_address, address.EmailAddress):
            return addr
        local_part = parsed_address.mailbox
        if parsed_address.hostname in ("gmail.com", "googlemail.com"):
            local_part = local_part.replace(".", "")
        return "@".join((local_part, parsed_address.hostname))

    class Account(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["account"]

    class Contact(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["contact"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for acct in db_session.query(Account):
            acct._raw_address = acct.email_address
            acct._canonicalized_address = canonicalize_address(
                acct.email_address
            )
        db_session.commit()

        for contact in db_session.query(Contact):
            if contact.email_address is not None:
                contact._raw_address = contact.email_address
                contact._canonicalized_address = canonicalize_address(
                    contact.email_address
                )
        db_session.commit()

    op.drop_index("ix_account_email_address", table_name="account")
    op.drop_index("ix_contact_email_address", table_name="contact")
    op.drop_column("account", "email_address")
    op.drop_column("contact", "email_address")


def downgrade() -> None:
    op.add_column(
        "account",
        sa.Column("email_address", mysql.VARCHAR(length=191), nullable=True),
    )
    op.add_column(
        "contact",
        sa.Column("email_address", mysql.VARCHAR(length=191), nullable=True),
    )
    op.create_index(
        "ix_account_email_address", "account", ["email_address"], unique=False
    )
    op.create_index(
        "ix_contact_email_address", "contact", ["email_address"], unique=False
    )
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    from sqlalchemy.ext.declarative import declarative_base

    from inbox.models.session import session_scope

    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Account(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["account"]

    class Contact(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["contact"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for acct in db_session.query(Account):
            acct.email_address = acct._raw_address
        db_session.commit()
        for contact in db_session.query(Account):
            contact.email_address = contact._raw_address
        db_session.commit()

    op.drop_index("ix_account__raw_address", table_name="account")
    op.drop_index("ix_account__canonicalized_address", table_name="account")
    op.drop_column("account", "_raw_address")
    op.drop_column("account", "_canonicalized_address")
