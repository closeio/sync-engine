"""
add run state to non-eas folders

Revision ID: 48a1991e5dbd
Revises: 6e5b154d917
Create Date: 2015-05-28 00:43:37.868434

"""

# revision identifiers, used by Alembic.
revision = "48a1991e5dbd"
down_revision = "6e5b154d917"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    op.add_column(
        "imapfoldersyncstatus",
        sa.Column(
            "sync_should_run",
            sa.Boolean(),
            server_default=sa.sql.expression.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("imapfoldersyncstatus", "sync_should_run")
