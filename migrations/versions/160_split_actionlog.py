"""Split ActionLog.

Revision ID: 182f2b40fa36
Revises: 4e6eedda36af
Create Date: 2015-04-20 21:22:20.523261

"""


# revision identifiers, used by Alembic.
revision = "182f2b40fa36"
down_revision = "4e6eedda36af"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import contains_eager


def upgrade():
    from inbox.ignition import main_engine

    op.add_column("actionlog", sa.Column("type", sa.String(16)))

    # Update action_log entries
    from inbox.models import Account, ActionLog, Namespace
    from inbox.models.session import session_scope

    with session_scope() as db_session:
        q = (
            db_session.query(ActionLog)
            .join(Namespace)
            .join(Account)
            .filter(
                ActionLog.status == "pending", Account.discriminator != "easaccount"
            )
            .options(contains_eager(ActionLog.namespace, Namespace.account))
        )

        print(f"Updating {q.count()} action_log entries")

        for a in q.all():
            a.type = "actionlog"

        db_session.commit()

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easaccount"):
        return

    op.create_table(
        "easactionlog",
        sa.Column("id", sa.Integer()),
        sa.Column(
            "secondary_status",
            sa.Enum("pending", "successful", "failed"),
            server_default="pending",
        ),
        sa.Column(
            "secondary_retries", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["id"], ["actionlog.id"], ondelete="CASCADE"),
    )


def downgrade():
    from inbox.ignition import main_engine

    op.drop_column("actionlog", "type")

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easaccount"):
        return

    op.drop_table("easactionlog")
