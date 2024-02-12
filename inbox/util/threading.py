from operator import attrgetter

from sqlalchemy import desc
from sqlalchemy.orm import contains_eager, load_only

from inbox.models.message import Message
from inbox.models.thread import Thread
from inbox.util.misc import cleanup_subject

MAX_THREAD_LENGTH = 500
MAX_MESSAGES_SCANNED = 20000


def fetch_corresponding_thread(db_session, namespace_id, message):
    """Fetch a thread matching the corresponding message. Returns None if
    there's no matching thread.
    """
    # handle the case where someone is self-sending an email.
    if not message.from_addr or not message.to_addr:
        return None

    message_from = [t[1] for t in message.from_addr]
    message_to = [t[1] for t in message.to_addr]

    # FIXME: for performance reasons, we make the assumption that a reply
    # to a message always has a similar subject. This is only
    # right 95% of the time.
    clean_subject = cleanup_subject(message.subject)

    # XXX: It is much faster to sort client-side by message date. We therefore
    # use `contains_eager` and `outerjoin` to fetch the messages by thread in
    # no particular order (as opposed to `joinedload`, which would use the
    # order_by on the Message._thread backref).  We also use a limit to avoid
    # scanning too many / large threads.
    threads = (
        db_session.query(Thread)
        .filter(
            Thread.namespace_id == namespace_id,
            Thread._cleaned_subject == clean_subject,
        )
        .outerjoin(Message, Thread.messages)
        .order_by(desc(Thread.id))
        .options(
            load_only("id", "discriminator"),
            contains_eager(Thread.messages).load_only(
                "from_addr", "to_addr", "bcc_addr", "cc_addr", "received_date"
            ),
        )
        .limit(MAX_MESSAGES_SCANNED)
    )

    for thread in threads:
        messages = sorted(thread.messages, key=attrgetter("received_date"))
        for match in messages:
            # A lot of people BCC some address when sending mass
            # emails so ignore BCC.
            match_bcc = match.bcc_addr if match.bcc_addr else []
            message_bcc = message.bcc_addr if message.bcc_addr else []

            match_emails = {
                t[1].lower() for t in match.participants if t not in match_bcc
            }
            message_emails = {
                t[1].lower() for t in message.participants if t not in message_bcc
            }

            # A conversation takes place between two or more persons.
            # Are there more than two participants in common in this
            # thread? If yes, it's probably a related thread.
            if len(match_emails & message_emails) >= 2:
                # No need to loop through the rest of the messages
                # in the thread
                if len(messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread

            match_from = [t[1] for t in match.from_addr]
            match_to = [t[1] for t in match.from_addr]

            if (
                len(message_to) == 1
                and message_from == message_to
                and match_from == match_to
                and message_to == match_from
            ):
                # Check that we're not over max thread length in this case
                # No need to loop through the rest of the messages
                # in the thread.
                if len(messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread

    return None
