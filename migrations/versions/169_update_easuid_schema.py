"""
update eas schema

Revision ID: 281b07fa75bb
Revises:1740b45aa815
Create Date: 2015-05-19 01:08:57.101681

"""

# revision identifiers, used by Alembic.
revision = "281b07fa75bb"
down_revision = "1740b45aa815"

from alembic import op


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]

    engine = main_engine(pool_size=1, max_overflow=0)
    if not engine.has_table("easuid"):
        return
    conn = op.get_bind()
    conn.execute(
        """ALTER TABLE easuid
        ADD COLUMN server_id VARCHAR(64) DEFAULT NULL,
        ADD COLUMN easfoldersyncstatus_id INT(11) DEFAULT NULL,
        ADD INDEX easfoldersyncstatus_id (easfoldersyncstatus_id),
        ADD CONSTRAINT easuid_ibfk_4 FOREIGN KEY (easfoldersyncstatus_id)
            REFERENCES easfoldersyncstatus (id) ON DELETE CASCADE,
        ADD INDEX ix_easuid_server_id (server_id)
        """
    )


def downgrade() -> None:
    pass
