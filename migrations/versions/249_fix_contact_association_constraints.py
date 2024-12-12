"""
fix_contact_association_constraints

Revision ID: 36ce9c8635ef
Revises: 203ae9bf0ddd
Create Date: 2019-09-12 01:34:32.867796

"""

# revision identifiers, used by Alembic.
revision = "36ce9c8635ef"
down_revision = "203ae9bf0ddd"

from alembic import op


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        "ALTER TABLE `eventcontactassociation` DROP FOREIGN KEY `eventcontactassociation_ibfk_2`"
    )
    connection.execute(
        "ALTER TABLE `eventcontactassociation` ADD CONSTRAINT `eventcontactassociation_ibfk_2` FOREIGN KEY (`event_id`) REFERENCES `event` (`id`) ON DELETE CASCADE"
    )
    connection.execute(
        "ALTER TABLE `messagecontactassociation` ADD CONSTRAINT `messagecontactassociation_ibfk_2` FOREIGN KEY (`contact_id`) REFERENCES `contact` (`id`)"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        "ALTER TABLE `eventcontactassociation` DROP FOREIGN KEY `eventcontactassociation_ibfk_2`"
    )
    connection.execute(
        "ALTER TABLE `eventcontactassociation` ADD CONSTRAINT `eventcontactassociation_ibfk_2` FOREIGN KEY (`event_id`) REFERENCES `event` (`id`)"
    )
    connection.execute(
        "ALTER TABLE `messagecontactassociation` DROP FOREIGN KEY `messagecontactassociation_ibfk_2`"
    )
