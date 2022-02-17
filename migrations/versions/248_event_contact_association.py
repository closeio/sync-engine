"""event_contact_association

Revision ID: 203ae9bf0ddd
Revises: 53b532fda984
Create Date: 2019-08-27 21:47:16.396607

"""

# revision identifiers, used by Alembic.
revision = "203ae9bf0ddd"
down_revision = "53b532fda984"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_table(
        "eventcontactassociation",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "field",
            sa.Enum("participant", "title", "description", "owner"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"],),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"],),
        sa.PrimaryKeyConstraint("id", "contact_id", "event_id"),
    )
    op.create_index(
        "ix_eventcontactassociation_created_at",
        "eventcontactassociation",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_eventcontactassociation_contact_id",
        "eventcontactassociation",
        ["contact_id"],
        unique=False,
    )


def downgrade():
    op.drop_table("eventcontactassociation")
