"""
Cascade delete secrets

Revision ID: 1449eededf1
Revises: 2c67046c548d
Create Date: 2018-06-15 19:57:58.139979

"""

# revision identifiers, used by Alembic.
revision = "1449eededf1"
down_revision = "2c67046c548d"

from alembic import op


def upgrade():
    conn = op.get_bind()
    conn.execute(
        """
        ALTER TABLE `genericaccount` DROP FOREIGN KEY `genericaccount_ibfk_2`;
        ALTER TABLE `genericaccount` ADD CONSTRAINT `genericaccount_ibfk_2` FOREIGN KEY (`imap_password_id`) REFERENCES `secret` (`id`) ON DELETE CASCADE;
        ALTER TABLE `genericaccount` DROP FOREIGN KEY `genericaccount_ibfk_3`;
        ALTER TABLE `genericaccount` ADD CONSTRAINT `genericaccount_ibfk_3` FOREIGN KEY (`smtp_password_id`) REFERENCES `secret` (`id`) ON DELETE CASCADE;
    """
    )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        """
        ALTER TABLE `genericaccount` DROP FOREIGN KEY `genericaccount_ibfk_2`;
        ALTER TABLE `genericaccount` ADD CONSTRAINT `genericaccount_ibfk_2` FOREIGN KEY (`imap_password_id`) REFERENCES `secret` (`id`);
        ALTER TABLE `genericaccount` DROP FOREIGN KEY `genericaccount_ibfk_3`;
        ALTER TABLE `genericaccount` ADD CONSTRAINT `genericaccount_ibfk_3` FOREIGN KEY (`smtp_password_id`) REFERENCES `secret` (`id`);
    """
    )
