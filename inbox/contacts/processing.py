import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session  # type: ignore[import-untyped]

from inbox.contacts.crud import INBOX_PROVIDER_NAME
from inbox.models import (
    Contact,
    EventContactAssociation,
    MessageContactAssociation,
)
from inbox.util.addr import canonicalize_address as canonicalize
from inbox.util.addr import valid_email

if TYPE_CHECKING:
    from inbox.models.message import Message


def _get_contact_map(
    db_session: Session,
    namespace_id: int,
    all_addresses: list[tuple[str, str]],
) -> dict[str, Contact]:
    """
    Retrieves or creates contacts for the given address pairs, returning a dict
    with the canonicalized emails mapped to Contact objects.
    """  # noqa: D401
    canonicalized_addresses = [
        canonicalize(address) for _, address in all_addresses
    ]

    if not canonicalized_addresses:
        return {}

    existing_contacts = (
        db_session.query(Contact)
        .filter(
            Contact._canonicalized_address.in_(canonicalized_addresses),
            Contact.namespace_id == namespace_id,
        )
        .all()
    )

    contact_map = {c._canonicalized_address: c for c in existing_contacts}
    for name, email_address in all_addresses:
        canonicalized_address = canonicalize(email_address)
        if isinstance(name, list):  # type: ignore[unreachable]
            name = name[0].strip()  # type: ignore[unreachable]
        if canonicalized_address not in contact_map:
            new_contact = Contact(  # type: ignore[call-arg]
                name=name,
                email_address=email_address,
                namespace_id=namespace_id,
                provider_name=INBOX_PROVIDER_NAME,
                uid=uuid.uuid4().hex,
            )
            contact_map[canonicalized_address] = new_contact
    return contact_map


def _get_contact_from_map(
    contact_map: dict[str, Contact], name: str, email_address: str
) -> Contact | None:
    if not valid_email(email_address):
        return None

    canonicalized_address = canonicalize(email_address)
    contact = contact_map.get(canonicalized_address)  # type: ignore[arg-type]
    assert contact

    # Hackily address the condition that you get mail from e.g.
    # "Ben Gotow (via Google Drive) <drive-shares-noreply@google.com"
    # "Christine Spang (via Google Drive) <drive-shares-noreply@google.com"
    # and so on: rather than creating many contacts with
    # varying name, null out the name for the existing contact.
    if (
        contact.name != name
        and "noreply" in canonicalized_address  # type: ignore[operator]
    ):
        contact.name = None

    return contact


def update_contacts_from_message(
    db_session: Session, message: "Message", namespace_id: int
) -> None:
    with db_session.no_autoflush:
        # First create Contact objects for any email addresses that we haven't
        # seen yet. We want to dedupe by canonicalized address, so this part is
        # a bit finicky.
        all_addresses: list[tuple[str, str]] = []
        for field in (
            message.from_addr,
            message.to_addr,
            message.cc_addr,
            message.bcc_addr,
            message.reply_to,
        ):
            # We generally require these attributes to be non-null, but only
            # set them to the default empty list at flush time. So it's better
            # to be safe here.
            if field is not None:
                all_addresses.extend(field)

        if not all_addresses:
            return

        contact_map = _get_contact_map(db_session, namespace_id, all_addresses)

        # Now associate each contact to the message.
        for field_name in (
            "from_addr",
            "to_addr",
            "cc_addr",
            "bcc_addr",
            "reply_to",
        ):
            field = getattr(message, field_name)
            if field is None:
                continue
            for name, email_address in field:
                contact = _get_contact_from_map(
                    contact_map, name, email_address
                )
                if not contact:
                    continue

                message.contacts.append(  # type: ignore[attr-defined]
                    MessageContactAssociation(  # type: ignore[call-arg]
                        contact=contact, field=field_name
                    )
                )


def update_contacts_from_event(  # type: ignore[no-untyped-def]
    db_session, event, namespace_id
) -> None:
    with db_session.no_autoflush:
        # First create Contact objects for any email addresses that we haven't
        # seen yet. We want to dedupe by canonicalized address, so this part is
        # a bit finicky.
        title_emails = set(event.emails_from_title)
        title_addrs = [("", email) for email in title_emails]

        description_emails = set(event.emails_from_description)
        description_addrs = [("", email) for email in description_emails]

        owner = (event.organizer_name or "", event.organizer_email)
        owner_addrs = [owner] if owner[1] else []

        participant_addrs = [
            (participant["name"], participant["email"])
            for participant in event.participants
        ]

        # Note that title & description emails are purposefully at the end here
        # since they have no name, and we want _get_contact_map to create a
        # contact with a name if possible.
        all_addresses = (
            participant_addrs + owner_addrs + title_addrs + description_addrs
        )

        # delete any previous EventContactAssociation for the event
        db_session.execute(
            EventContactAssociation.__table__.delete().where(  # type: ignore[attr-defined]
                EventContactAssociation.event_id == event.id
            )
        )

        if not all_addresses:
            return

        contact_map = _get_contact_map(db_session, namespace_id, all_addresses)
        db_session.add_all(contact_map.values())
        db_session.flush()

        # Now associate each contact to the event.
        values = []
        for field_name, addrs in (
            ("title", title_addrs),
            ("description", description_addrs),
            ("owner", owner_addrs),
            ("participant", participant_addrs),
        ):
            for name, email in addrs:
                contact = _get_contact_from_map(contact_map, name, email)
                if not contact:
                    continue

                values.append(
                    {
                        "event_id": event.id,
                        "contact_id": contact.id,
                        "field": field_name,
                    }
                )

        if values:
            db_session.execute(
                EventContactAssociation.__table__.insert().values(  # type: ignore[attr-defined]
                    values
                )
            )
