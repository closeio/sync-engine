"""
add more indexes

Revision ID: 1eab2619cc4f
Revises: 3f96e92953e1
Create Date: 2014-05-23 22:43:51.885795

"""

# revision identifiers, used by Alembic.
revision = "1eab2619cc4f"
down_revision = "3f96e92953e1"

from alembic import op
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declarative_base,
)


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easuid" in Base.metadata.tables:
        op.create_index(
            "ix_easuid_msg_uid", "easuid", ["msg_uid"], unique=False
        )

    op.create_index("ix_imapuid_msg_uid", "imapuid", ["msg_uid"], unique=False)
    op.create_index(
        "ix_transaction_table_name",
        "transaction",
        ["table_name"],
        unique=False,
    )


def downgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    if "easuid" in Base.metadata.tables:
        op.drop_index("ix_easuid_msg_uid", table_name="easuid")

    op.drop_index("ix_transaction_table_name", table_name="transaction")
    op.drop_index("ix_imapuid_msg_uid", table_name="imapuid")
