# -*- coding: utf-8 -*-
from inbox.models.thread import Thread
from sqlalchemy import desc
from sqlalchemy.orm import joinedload, load_only
from inbox.util.misc import cleanup_subject


MAX_THREAD_LENGTH = 500
MAX_MESSAGES_SCANNED = 20000


def fetch_corresponding_thread(db_session, namespace_id, message):
    """
    Fetch a thread matching the corresponding message.

    Returns None if there's no matching thread.
    """
    # FIXME: for performance reasons, we make the assumption that a reply
    # to a message always has a similar subject. This is only
    # right 95% of the time.
    clean_subject = cleanup_subject(message.subject)
    threads = db_session.query(Thread). \
        filter(Thread.namespace_id == namespace_id,
               Thread._cleaned_subject == clean_subject). \
        order_by(desc(Thread.id)). \
        options(load_only('id', 'discriminator'),
                joinedload(Thread.messages).load_only(
                    'from_addr', 'to_addr', 'bcc_addr', 'cc_addr'))

    num_messages_scanned = 0
    for thread in threads:
        for match in thread.messages:
            # If we've scanned more than `MAX_MESSAGES_SCANNED`, give up and
            # assume that a matching thread doesn't exist. We do this because
            # the number of threads and messages we iterate over can get really
            # out of hand for some transactional emails.
            num_messages_scanned += 1
            if num_messages_scanned > MAX_MESSAGES_SCANNED:
                return

            # A lot of people BCC some address when sending mass
            # emails so ignore BCC.
            match_bcc = match.bcc_addr if match.bcc_addr else []
            message_bcc = message.bcc_addr if message.bcc_addr else []

            match_emails = set([t[1].lower() for t in match.participants
                                if t not in match_bcc])
            message_emails = set([t[1].lower() for t in message.participants
                                  if t not in message_bcc])

            # A conversation takes place between two or more persons.
            # Are there more than two participants in common in this
            # thread? If yes, it's probably a related thread.
            if len(match_emails & message_emails) >= 2:
                # No need to loop through the rest of the messages
                # in the thread
                if len(thread.messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread

            # handle the case where someone is self-sending an email.
            if not message.from_addr or not message.to_addr:
                return

            match_from = [t[1] for t in match.from_addr]
            match_to = [t[1] for t in match.from_addr]
            message_from = [t[1] for t in message.from_addr]
            message_to = [t[1] for t in message.to_addr]

            if (len(message_to) == 1 and message_from == message_to and
                    match_from == match_to and message_to == match_from):
                # Check that we're not over max thread length in this case
                # No need to loop through the rest of the messages
                # in the thread.
                if len(thread.messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread
