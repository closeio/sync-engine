"""
Utility functions for creating, reading, updating and deleting contacts.
Called by the API.
"""

import uuid
from typing import Never

from inbox.models import Contact

INBOX_PROVIDER_NAME = "inbox"


def create(namespace, db_session, name, email):  # type: ignore[no-untyped-def]  # noqa: ANN201
    contact = Contact(  # type: ignore[call-arg]
        namespace=namespace,
        provider_name=INBOX_PROVIDER_NAME,
        uid=uuid.uuid4().hex,
        name=name,
        email_address=email,
    )
    db_session.add(contact)
    db_session.commit()
    return contact


def read(  # type: ignore[no-untyped-def]  # noqa: ANN201
    namespace, db_session, contact_public_id
):
    return (
        db_session.query(Contact)
        .filter(
            Contact.public_id == contact_public_id,
            Contact.namespace_id == namespace.id,
        )
        .first()
    )


def update(  # type: ignore[no-untyped-def]
    namespace, db_session, contact_public_id, name, email
) -> Never:
    raise NotImplementedError


def delete(  # type: ignore[no-untyped-def]
    namespace, db_session, contact_public_id
) -> Never:
    raise NotImplementedError
