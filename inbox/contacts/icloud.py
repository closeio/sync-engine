"""Provide iCloud contacts"""

import contextlib

import lxml.etree as ET  # type: ignore[import-untyped]  # noqa: N812

from inbox.contacts.abc import AbstractContactsProvider
from inbox.contacts.carddav import supports_carddav
from inbox.contacts.vcard import vcard_from_string
from inbox.logging import get_logger
from inbox.models import Contact
from inbox.models.backends.generic import GenericAccount
from inbox.models.session import session_scope

from .carddav import CardDav

logger = get_logger()

ICLOUD_CONTACTS_URL = "https://contacts.icloud.com"


class ICloudContactsProvider(AbstractContactsProvider):
    """
    Base class to fetch and parse iCloud contacts
    """

    PROVIDER_NAME = "icloud"

    def __init__(  # type: ignore[no-untyped-def]
        self, account_id, namespace_id
    ) -> None:
        supports_carddav(ICLOUD_CONTACTS_URL)
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.log = logger.new(
            account_id=account_id,
            component="contacts sync",
            provider=self.PROVIDER_NAME,
        )

    def _vCard_raw_to_contact(  # type: ignore[no-untyped-def]  # noqa: N802
        self, cardstring
    ):
        card = vcard_from_string(cardstring)

        def _x(  # type: ignore[no-untyped-def]
            key,
        ):  # Ugly parsing helper for ugly formats
            if key in card:
                with contextlib.suppress(IndexError):
                    return card[key][0][0]
            return None

        # Skip contact groups for now
        if _x("X-ADDRESSBOOKSERVER-KIND") == "group":
            return None

        uid = _x("UID")
        name = _x("FN")
        email_address = _x("EMAIL")
        # TODO add these later
        # street_address = _x('ADR')
        # phone_number = _x('TEL')
        # organization = _x('ORG')

        return Contact(  # type: ignore[call-arg]
            namespace_id=self.namespace_id,
            provider_name=self.PROVIDER_NAME,
            uid=uid,
            name=name,
            email_address=email_address,
            raw_data=cardstring,
        )

    def get_items(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, sync_from_dt=None, max_results: int = 100000
    ):
        with session_scope(self.namespace_id) as db_session:
            account = db_session.query(GenericAccount).get(self.account_id)
            email_address = account.email_address
            password = account.password
            if account.provider != "icloud":
                self.log.error(
                    "Can't sync contacts for non iCloud provider",
                    account_id=account.id,
                    provider=account.provider,
                )
                return []

        c = CardDav(email_address, password, ICLOUD_CONTACTS_URL)

        # Get the `principal` URL for the users's CardDav endpont
        principal = c.get_principal_url()

        # Get addressbook home URL on user's specific iCloud shard/subdomain
        home_url = c.get_address_book_home(ICLOUD_CONTACTS_URL + principal)
        self.log.info(f"Home URL for user's contacts: {home_url}")
        self.log.debug("Requesting cards for user")

        # This request is limited to returning 5000 items
        returned_cards = c.get_cards(home_url + "card/")

        root = ET.XML(returned_cards)

        all_contacts = []
        for refprop in root.iterchildren():
            try:
                cardstring = refprop[1][0][1].text
            except IndexError:
                # This can happen when there are errors or other responses.
                # Currently if there are over 5000 contacts, it trigger the
                # response number-of-matches-within-limits
                # TODO add paging for requesting all
                self.log.error(
                    "Error parsing CardDav response into contact: "
                    f"{ET.tostring(refprop)}"
                )
                continue

            new_contact = self._vCard_raw_to_contact(cardstring)
            if new_contact:
                all_contacts.append(new_contact)

        self.log.info(f"Saving {len(all_contacts)} contacts from iCloud sync")
        return all_contacts
