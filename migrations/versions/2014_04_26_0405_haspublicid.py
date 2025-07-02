"""
HasPublicID

Revision ID: 2c9f3a06de09
Revises: 5093433b073
Create Date: 2014-04-26 04:05:57.715053

"""

# revision identifiers, used by Alembic.
revision = "2c9f3a06de09"
down_revision = "5093433b073"

import sys
from gc import collect as garbage_collect

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects import mysql  # type: ignore[import-untyped]

chunk_size = 500


def upgrade() -> None:
    # These all inherit HasPublicID
    from inbox.models import (  # type: ignore[attr-defined]
        Account,
        Block,
        Contact,
        HasPublicID,
        Message,
        Namespace,
        SharedFolder,
        Thread,
        User,
        UserSession,
    )
    from inbox.models.session import session_scope
    from inbox.sqlalchemy_ext.util import generate_public_id

    classes = [
        Account,
        Block,
        Contact,
        Message,
        Namespace,
        SharedFolder,
        Thread,
        User,
        UserSession,
    ]

    for c in classes:
        assert issubclass(c, HasPublicID)
        print(f"[{c.__tablename__}] adding public_id column... "),
        sys.stdout.flush()
        op.add_column(
            c.__tablename__,
            sa.Column("public_id", mysql.BINARY(16), nullable=False),
        )

        print("adding index... "),
        op.create_index(
            f"ix_{c.__tablename__}_public_id",
            c.__tablename__,
            ["public_id"],
            unique=False,
        )

        print("Done!")
        sys.stdout.flush()

    print("Finished adding columns. \nNow generating public_ids")

    with session_scope() as db_session:  # type: ignore[call-arg]
        count = 0
        for c in classes:
            garbage_collect()
            print(f"[{c.__name__}] Loading rows. "),
            sys.stdout.flush()
            print("Generating public_ids"),
            sys.stdout.flush()
            for r in db_session.query(c).yield_per(chunk_size):
                count += 1
                r.public_id = generate_public_id()
                if not count % chunk_size:
                    sys.stdout.write(".")
                    sys.stdout.flush()
                    db_session.commit()
                    garbage_collect()
            sys.stdout.write(" Saving. ".format()),
            # sys.stdout.flush()
            sys.stdout.flush()
            db_session.commit()
            sys.stdout.write("Done!\n")
            sys.stdout.flush()
        print("\nUpdgraded OK!\n")


def downgrade() -> None:
    # These all inherit HasPublicID
    from inbox.models import (  # type: ignore[attr-defined]
        Account,
        Block,
        Contact,
        HasPublicID,
        Message,
        Namespace,
        SharedFolder,
        Thread,
        User,
        UserSession,
    )

    classes = [
        Account,
        Block,
        Contact,
        Message,
        Namespace,
        SharedFolder,
        Thread,
        User,
        UserSession,
    ]

    for c in classes:
        assert issubclass(c, HasPublicID)
        print(f"[{c.__tablename__}] Dropping public_id column... "),
        op.drop_column(c.__tablename__, "public_id")

        print("Dropping index... "),
        op.drop_index(
            f"ix_{c.__tablename__}_public_id", table_name=c.__tablename__
        )

        print("Done.")
