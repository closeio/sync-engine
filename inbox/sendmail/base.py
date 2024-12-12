import re
from datetime import datetime
from typing import Any

from inbox import VERSION
from inbox.api.err import InputError
from inbox.api.validation import (
    get_attachments,
    get_message,
    get_recipients,
    get_thread,
)
from inbox.contacts.processing import update_contacts_from_message
from inbox.models import Message, Part
from inbox.models.account import Account
from inbox.models.action_log import schedule_action
from inbox.sqlalchemy_ext.util import generate_public_id


class SendMailException(Exception):
    """
    Raised when sending fails.

    Parameters
    ----------
    message: string
        A descriptive error message.
    http_code: int
        An appropriate HTTP error code for the particular type of failure.
    server_error: string, optional
        The error returned by the mail server.
    failures: dict, optional
        If sending only failed for some recipients, information on the specific
        failures.

    """

    def __init__(self, message, http_code, server_error=None, failures=None):
        self.message = message
        self.http_code = http_code
        self.server_error = server_error
        self.failures = failures
        super().__init__(message, http_code, server_error, failures)


def get_sendmail_client(account):
    from inbox.sendmail import module_registry

    sendmail_mod = module_registry.get(account.provider)
    sendmail_cls = getattr(sendmail_mod, sendmail_mod.SENDMAIL_CLS)
    sendmail_client = sendmail_cls(account)
    return sendmail_client


def create_draft_from_mime(
    account: Account, raw_mime: bytes, db_session: Any
) -> Message:
    our_uid = generate_public_id()  # base-36 encoded string
    new_headers = (
        f"X-INBOX-ID: {our_uid}-0\r\n"
        f"Message-Id: <{our_uid}-0@mailer.nylas.com>\r\n"
        f"User-Agent: NylasMailer/{VERSION}\r\n"
    ).encode()
    new_body = new_headers + raw_mime

    with db_session.no_autoflush:
        msg = Message.create_from_synced(
            account, "", "", datetime.utcnow(), new_body
        )

        if msg.from_addr and len(msg.from_addr) > 1:
            raise InputError("from_addr field can have at most one item")
        if msg.reply_to and len(msg.reply_to) > 1:
            raise InputError("reply_to field can have at most one item")

        if msg.subject is not None and not isinstance(msg.subject, str):
            raise InputError('"subject" should be a string')

        if not isinstance(msg.body, str):
            raise InputError('"body" should be a string')

        if msg.references or msg.in_reply_to:
            msg.is_reply = True

        thread_cls = account.thread_cls
        msg.thread = thread_cls(
            subject=msg.subject,
            recentdate=msg.received_date,
            namespace=account.namespace,
            subjectdate=msg.received_date,
        )

        msg.is_created = True
        msg.is_sent = True
        msg.is_draft = False
        msg.is_read = True
    db_session.add(msg)
    db_session.flush()
    return msg


def block_to_part(block, message, namespace):
    inline_image_uri = rf"cid:{block.public_id}"
    is_inline = re.search(inline_image_uri, message.body) is not None
    # Create a new Part object to associate to the message object.
    # (You can't just set block.message, because if block is an
    # attachment on an existing message, that would dissociate it from
    # the existing message.)
    part = Part(block=block)
    part.content_id = block.public_id if is_inline else None
    part.namespace_id = namespace.id
    part.content_disposition = "inline" if is_inline else "attachment"
    part.is_inboxapp_attachment = True
    return part


def create_message_from_json(data, namespace, db_session, is_draft):
    """
    Construct a Message instance from `data`, a dictionary representing the
    POST body of an API request. All new objects are added to the session, but
    not committed.
    """
    # Validate the input and get referenced objects (thread, attachments)
    # as necessary.
    to_addr = get_recipients(data.get("to"), "to")
    cc_addr = get_recipients(data.get("cc"), "cc")
    bcc_addr = get_recipients(data.get("bcc"), "bcc")
    from_addr = get_recipients(data.get("from"), "from")
    reply_to = get_recipients(data.get("reply_to"), "reply_to")

    if from_addr and len(from_addr) > 1:
        raise InputError("from_addr field can have at most one item")
    if reply_to and len(reply_to) > 1:
        raise InputError("reply_to field can have at most one item")

    subject = data.get("subject")
    if subject is not None and not isinstance(subject, str):
        raise InputError('"subject" should be a string')
    body = data.get("body", "")
    if not isinstance(body, str):
        raise InputError('"body" should be a string')
    blocks = get_attachments(data.get("file_ids"), namespace.id, db_session)
    reply_to_thread = get_thread(
        data.get("thread_id"), namespace.id, db_session
    )
    reply_to_message = get_message(
        data.get("reply_to_message_id"), namespace.id, db_session
    )
    if (
        reply_to_message is not None
        and reply_to_thread is not None
        and reply_to_message not in reply_to_thread.messages
    ):
        raise InputError(
            "Message {} is not in thread {}".format(
                reply_to_message.public_id, reply_to_thread.public_id
            )
        )

    with db_session.no_autoflush:
        account = namespace.account
        dt = datetime.utcnow()
        uid = generate_public_id()
        to_addr = to_addr or []
        cc_addr = cc_addr or []
        bcc_addr = bcc_addr or []
        blocks = blocks or []
        if subject is None:
            # If this is a reply with no explicitly specified subject, set the
            # subject from the prior message/thread by default.
            # TODO(emfree): Do we want to allow changing the subject on a reply
            # at all?
            if reply_to_message is not None:
                subject = reply_to_message.subject
            elif reply_to_thread is not None:
                subject = reply_to_thread.subject
        subject = subject or ""

        message = Message()
        message.namespace = namespace
        message.is_created = True
        message.is_draft = is_draft
        message.from_addr = (
            from_addr if from_addr else [(account.name, account.email_address)]
        )
        # TODO(emfree): we should maybe make received_date nullable, so its
        # value doesn't change in the case of a drafted-and-later-reconciled
        # message.
        message.received_date = dt
        message.subject = subject
        message.body = body
        message.to_addr = to_addr
        message.cc_addr = cc_addr
        message.bcc_addr = bcc_addr
        message.reply_to = reply_to
        # TODO(emfree): this is different from the normal 'size' value of a
        # message, which is the size of the entire MIME message.
        message.size = len(body)
        message.is_read = True
        message.is_sent = False
        message.public_id = uid
        message.version = 0
        message.regenerate_nylas_uid()

        # Set the snippet
        message.snippet = message.calculate_html_snippet(body)

        # Associate attachments to the draft message
        for block in blocks:
            message.parts.append(block_to_part(block, message, namespace))

        update_contacts_from_message(db_session, message, namespace.id)

        if reply_to_message is not None:
            message.is_reply = True
            _set_reply_headers(message, reply_to_message)
            thread = reply_to_message.thread
            message.reply_to_message = reply_to_message
        elif reply_to_thread is not None:
            message.is_reply = True
            thread = reply_to_thread
            # Construct the in-reply-to and references headers from the last
            # message currently in the thread.
            previous_messages = [m for m in thread.messages if not m.is_draft]
            if previous_messages:
                last_message = previous_messages[-1]
                message.reply_to_message = last_message
                _set_reply_headers(message, last_message)
        else:
            # If this isn't a reply to anything, create a new thread object for
            # the draft.  We specialize the thread class so that we can, for
            # example, add the g_thrid for Gmail later if we reconcile a synced
            # message with this one. This is a huge hack, but works.
            message.is_reply = False
            thread_cls = account.thread_cls
            thread = thread_cls(
                subject=message.subject,
                recentdate=message.received_date,
                namespace=namespace,
                subjectdate=message.received_date,
            )

        message.thread = thread

    db_session.add(message)
    if is_draft:
        schedule_action(
            "save_draft",
            message,
            namespace.id,
            db_session,
            version=message.version,
        )
    db_session.flush()
    return message


def update_draft(
    db_session,
    account,
    draft,
    to_addr=None,
    subject=None,
    body=None,
    blocks=None,
    cc_addr=None,
    bcc_addr=None,
    from_addr=None,
    reply_to=None,
):
    """
    Update draft with new attributes.
    """

    def update(attr, value=None):
        if value is not None:
            setattr(draft, attr, value)

            if attr == "body":
                # Update size, snippet too
                draft.size = len(value)
                draft.snippet = draft.calculate_html_snippet(value)

    update("to_addr", to_addr)
    update("cc_addr", cc_addr)
    update("bcc_addr", bcc_addr)
    update("reply_to", reply_to)
    update("from_addr", from_addr)
    update("subject", subject if subject else None)
    update("body", body if body else None)
    update("received_date", datetime.utcnow())

    # Remove any attachments that aren't specified
    new_block_ids = [b.id for b in blocks]
    for part in [x for x in draft.parts if x.block_id not in new_block_ids]:
        draft.parts.remove(part)
        db_session.delete(part)

    # Parts require special handling
    for block in blocks:
        # Don't re-add attachments that are already attached
        if block.id in [p.block_id for p in draft.parts]:
            continue
        draft.parts.append(block_to_part(block, draft, account.namespace))

    thread = draft.thread
    if len(thread.messages) == 1:
        # If there are no prior messages on the thread, update its subject and
        # dates to match the draft.
        thread.subject = draft.subject
        thread.subjectdate = draft.received_date
        thread.recentdate = draft.received_date

    # Remove previous message-contact associations, and create new ones.
    draft.contacts = []
    update_contacts_from_message(db_session, draft, account.namespace.id)

    # The draft we're updating may or may not be one authored through the API:
    # - Ours: is_created = True, Message-Id = public_id+version
    # - Not Ours: is_created = False, Message-Id = ???

    # Mark that the draft is now created by us
    draft.is_created = True

    # Save the current Message-Id so we know which draft to delete in syncback
    old_message_id_header = draft.message_id_header

    # Increment version and rebuild the message ID header.
    draft.version += 1
    draft.regenerate_nylas_uid()

    # Sync to remote
    schedule_action(
        "update_draft",
        draft,
        draft.namespace.id,
        db_session,
        version=draft.version,
        old_message_id_header=old_message_id_header,
    )
    db_session.commit()
    return draft


def delete_draft(db_session, account, draft):
    """Delete the given draft."""
    thread = draft.thread
    assert draft.is_draft

    # Delete remotely.
    schedule_action(
        "delete_draft",
        draft,
        draft.namespace.id,
        db_session,
        nylas_uid=draft.nylas_uid,
        message_id_header=draft.message_id_header,
    )

    db_session.delete(draft)

    # Delete the thread if it would now be empty.
    if not thread.messages:
        db_session.delete(thread)

    db_session.commit()


def generate_attachments(message, blocks):
    attachment_dicts = []
    for block in blocks:
        content_disposition = "attachment"
        for part in block.parts:
            if (
                part.message_id == message.id
                and part.content_disposition == "inline"
            ):
                content_disposition = "inline"
                break

        attachment_dicts.append(
            {
                "block_id": block.public_id,
                "filename": block.filename,
                "data": block.data,
                "content_type": block.content_type,
                "content_disposition": content_disposition,
            }
        )
    return attachment_dicts


def _set_reply_headers(new_message, previous_message):
    """
    When creating a draft in reply to a thread, set the In-Reply-To and
    References headers appropriately, if possible.
    """
    if previous_message.message_id_header:
        new_message.in_reply_to = previous_message.message_id_header
        if previous_message.references:
            new_message.references = previous_message.references + [
                previous_message.message_id_header
            ]
        else:
            new_message.references = [previous_message.message_id_header]
