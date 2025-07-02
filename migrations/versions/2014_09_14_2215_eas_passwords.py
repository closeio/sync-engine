"""
EAS passwords

Revision ID: 427812c1e849
Revises:159607944f52
Create Date: 2014-09-14 22:15:51.225342

"""

# revision identifiers, used by Alembic.
revision = "427812c1e849"
down_revision = "159607944f52"

from datetime import datetime

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    # Do nothing if the affected table isn't present.
    if not engine.has_table("easaccount"):
        return

    # Do not define foreign key constraint here; that's done for all account
    # tables in the next migration.
    op.add_column(
        "easaccount",
        sa.Column("password_id", sa.Integer(), sa.ForeignKey("secret.id")),
    )
    Base = sa.ext.declarative.declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)
    from inbox.models.session import session_scope

    class EASAccount(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["easaccount"]
        secret = sa.orm.relationship(
            "Secret", primaryjoin="EASAccount.password_id == Secret.id"
        )

    class Secret(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["secret"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        accounts = db_session.query(EASAccount).all()
        print("# EAS accounts: ", len(accounts))

        for account in accounts:
            secret = Secret()
            # Need to set non-nullable attributes.
            secret.created_at = datetime.utcnow()
            secret.updated_at = datetime.utcnow()
            secret.type = 0
            secret.acl_id = 0

            secret.secret = account.password
            account.secret = secret

        db_session.commit()

    op.alter_column(
        "easaccount", "password_id", existing_type=sa.Integer(), nullable=False
    )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easaccount"):
        return
    op.drop_constraint("easaccount_ibfk_2", "easaccount", type_="foreignkey")
    op.drop_column("easaccount", "password_id")
