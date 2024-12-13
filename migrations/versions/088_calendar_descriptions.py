"""
calendars

Revision ID: 24e9afe91349
Revises: 1ac03cab7a24
Create Date: 2014-08-28 05:27:28.498786

"""

# revision identifiers, used by Alembic.
revision = "24e9afe91349"
down_revision = "565c7325c51d"

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op


def upgrade() -> None:
    from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
        declarative_base,
    )

    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)

    op.alter_column(
        "calendar",
        "notes",
        new_column_name="description",
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.add_column(
        "calendar",
        sa.Column("provider_name", sa.String(length=64), nullable=False),
    )

    op.alter_column(
        "event",
        "subject",
        new_column_name="title",
        existing_type=sa.String(1024),
        existing_nullable=True,
    )

    op.alter_column(
        "event",
        "body",
        new_column_name="description",
        existing_type=sa.Text(),
        existing_nullable=True,
    )

    # We're changing the structure of the calendar name so that
    # the provider can be split out from the name as it was previously
    # overloaded. Nobody should have any existing inbox calendars though
    # so we don't have to worry about a user with a calendar name with
    # a dash ('-') in it. These calendars are read_only as they come from
    # a provider.
    #
    # Also, any already synced events are read only as nobody has created
    # events yet.
    Base = declarative_base()  # noqa: N806
    Base.metadata.reflect(engine)

    class Calendar(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["calendar"]

    class Event(Base):  # type: ignore[misc, valid-type]
        __table__ = Base.metadata.tables["event"]

    with session_scope(  # type: ignore[call-arg]
        versioned=False
    ) as db_session:
        for calendar in db_session.query(Calendar):
            if calendar.name and "-" in calendar.name:
                provider_name, name = calendar.name.split("-")
                calendar.provider_name = provider_name
                calendar.name = name
                calendar.read_only = True
        for event in db_session.query(Event):
            event.read_only = True
        db_session.commit()

    op.drop_constraint("calendar_ibfk_1", "calendar", type_="foreignkey")
    op.drop_constraint("uuid", "calendar", type_="unique")

    op.create_unique_constraint(
        "uuid", "calendar", ["name", "provider_name", "account_id"]
    )

    op.create_foreign_key(
        None, "calendar", "account", ["account_id"], ["id"], ondelete="CASCADE"
    )

    op.drop_constraint("event_ibfk_2", "event", type_="foreignkey")
    op.create_foreign_key(
        "event_ibfk_2",
        "event",
        "calendar",
        ["calendar_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.alter_column(
        "calendar",
        "description",
        new_column_name="notes",
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.drop_column("calendar", "provider_name")

    op.drop_constraint("calendar_ibfk_1", "calendar", type_="foreignkey")
    op.drop_constraint("uuid", "calendar", type_="unique")

    op.create_unique_constraint("uuid", "calendar", ["name", "account_id"])
    op.create_foreign_key(None, "calendar", "account", ["account_id"], ["id"])
