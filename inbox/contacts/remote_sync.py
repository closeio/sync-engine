from collections import Counter
from datetime import datetime

from sqlalchemy.orm.exc import NoResultFound

from inbox.contacts.google import GoogleContactsProvider
from inbox.contacts.icloud import ICloudContactsProvider
from inbox.logging import get_logger
from inbox.models import Account, Contact
from inbox.models.session import session_scope
from inbox.sync.base_sync import BaseSyncMonitor
from inbox.util.debug import bind_context

logger = get_logger()

CONTACT_SYNC_PROVIDER_MAP = {
    "gmail": GoogleContactsProvider,
    "icloud": ICloudContactsProvider,
}

CONTACT_SYNC_FOLDER_ID = -1
CONTACT_SYNC_FOLDER_NAME = "Contacts"


class ContactSync(BaseSyncMonitor):
    """
    Per-account contact sync engine.

    Parameters
    ----------
    account_id: int
        The ID for the user account for which to fetch contact data.

    poll_frequency: int
        In seconds, the polling frequency for querying the contacts provider
        for updates.

    Attributes
    ---------
    log: logging.Logger
        Logging handler.

    """

    def __init__(
        self, email_address, provider_name, account_id, namespace_id, poll_frequency=300
    ):
        bind_context(self, "contactsync", account_id)
        self.provider_name = provider_name

        provider_cls = CONTACT_SYNC_PROVIDER_MAP[self.provider_name]
        self.provider = provider_cls(account_id, namespace_id)

        BaseSyncMonitor.__init__(
            self,
            account_id,
            namespace_id,
            email_address,
            CONTACT_SYNC_FOLDER_ID,
            CONTACT_SYNC_FOLDER_NAME,
            provider_name,
            poll_frequency=poll_frequency,
            scope="contacts",
        )

    def sync(self):
        """Query a remote provider for updates and persist them to the
        database. This function runs every `self.poll_frequency`.

        """
        self.log.debug("syncing contacts")
        # Grab timestamp so next sync gets deltas from now
        sync_timestamp = datetime.utcnow()

        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)
            last_sync_dt = account.last_synced_contacts

            all_contacts = self.provider.get_items(sync_from_dt=last_sync_dt)

            # Do a batch insertion of every 100 contact objects
            change_counter = Counter()
            for new_contact in all_contacts:
                new_contact.namespace = account.namespace
                assert new_contact.uid is not None, "Got remote item with null uid"
                assert isinstance(new_contact.uid, basestring)

                if (
                    not new_contact.deleted
                    and db_session.query(Contact)
                    .filter(
                        Contact.namespace == account.namespace,
                        Contact.email_address == new_contact.email_address,
                        Contact.name == new_contact.name,
                    )
                    .first()
                ):
                    # Skip creating a new contact if we've already imported one
                    # (e.g., from mail).
                    continue

                try:
                    existing_contact = (
                        db_session.query(Contact)
                        .filter(
                            Contact.namespace == account.namespace,
                            Contact.provider_name == self.provider.PROVIDER_NAME,
                            Contact.uid == new_contact.uid,
                        )
                        .one()
                    )

                    # If the remote item was deleted, purge the corresponding
                    # database entries.
                    if new_contact.deleted:
                        db_session.delete(existing_contact)
                        change_counter["deleted"] += 1
                    else:
                        # Update fields in our old item with the new.
                        # Don't save the newly returned item to the database.
                        existing_contact.merge_from(new_contact)
                        change_counter["updated"] += 1

                except NoResultFound:
                    # We didn't know about this before! Add this item.
                    db_session.add(new_contact)
                    change_counter["added"] += 1

                if sum(change_counter.values()) % 10:
                    db_session.commit()

        # Update last sync
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(Account).get(self.account_id)
            account.last_synced_contacts = sync_timestamp

        self.log.debug(
            "synced contacts",
            added=change_counter["added"],
            updated=change_counter["updated"],
            deleted=change_counter["deleted"],
        )
