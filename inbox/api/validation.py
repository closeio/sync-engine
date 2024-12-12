"""Utilities for validating user input to the API."""

import contextlib
from typing import Never

import arrow
from arrow.parser import ParserError
from flanker.addresslib import address
from flask_restful import reqparse
from sqlalchemy.orm.exc import NoResultFound

from inbox.api.err import (
    AccountInvalidError,
    AccountStoppedError,
    ConflictError,
    InputError,
    NotFoundError,
)
from inbox.api.kellogs import encode
from inbox.models import Block, Calendar, Category, Event, Message, Thread
from inbox.models.category import EPOCH
from inbox.models.constants import MAX_INDEXABLE_LENGTH
from inbox.models.when import parse_as_when
from inbox.util.addr import valid_email

MAX_LIMIT = 1000


class ValidatableArgument(reqparse.Argument):
    def handle_validation_error(self, error, bundle_errors) -> Never:
        raise InputError(str(error))


# Custom parameter types


def bounded_str(value, key):
    if len(value) > 255:
        raise ValueError(f"Value {value} for {key} is too long")
    return value


def comma_separated_email_list(value, key):
    addresses = value.split(",")
    # Note that something like "foo,bar"@example.com is technical a valid
    # email address, but in practice nobody does this (and they shouldn't!)

    if len(addresses) > 25:  # arbitrary limit
        raise InputError("Too many emails. The current limit is 25")

    good_emails = []
    for unvalidated_address in addresses:
        parsed = address.parse(unvalidated_address, addr_spec_only=True)
        if not isinstance(parsed, address.EmailAddress):
            raise InputError(
                f"Invalid recipient address {unvalidated_address}"
            )
        good_emails.append(parsed.address)

    return good_emails


def strict_bool(value, key):
    if value.lower() not in ["true", "false"]:
        raise ValueError(
            f'Value must be "true" or "false" (not "{value}") for {key}'
        )
    return value.lower() == "true"


def view(value, key):
    allowed_views = ["count", "ids", "expanded"]
    if value not in allowed_views:
        raise ValueError(f"Unknown view type {value}.")
    return value


def limit(value):
    try:
        value = int(value)
    except ValueError:
        raise ValueError("Limit parameter must be an integer.")
    if value < 0:
        raise ValueError("Limit parameter must be nonnegative.")
    if value > MAX_LIMIT:
        raise ValueError(
            f"Cannot request more than {MAX_LIMIT} resources at once."
        )
    return value


def offset(value):
    try:
        value = int(value)
    except ValueError:
        raise ValueError("Offset parameter must be an integer.")
    if value < 0:
        raise ValueError("Offset parameter must be nonnegative.")
    return value


def valid_public_id(value):
    if "_" in value:
        raise InputError(f"Invalid id: {value}")

    try:
        # raise ValueError on malformed public ids
        # raise TypeError if an integer is passed in
        int(value, 36)
    except (TypeError, ValueError):
        raise InputError(f"Invalid id: {value}")
    return value


def valid_account(namespace) -> None:
    if namespace.account.sync_state == "invalid":
        raise AccountInvalidError()
    if namespace.account.sync_state == "stopped":
        raise AccountStoppedError()


def valid_category_type(category_type, rule):
    if category_type not in rule:
        if category_type == "label":
            raise NotFoundError("GMail accounts don't support folders")
        elif category_type == "folder":
            raise NotFoundError("Non-GMail accounts don't support labels")
    return category_type


def timestamp(value, key):
    try:
        with contextlib.suppress(ValueError):
            value = float(value)

        return arrow.get(value).datetime
    except ValueError:
        raise ValueError(f"Invalid timestamp value {value} for {key}")
    except ParserError:
        raise ValueError(f"Invalid datetime value {value} for {key}")


def strict_parse_args(parser, raw_args):
    """
    Wrapper around parser.parse_args that raises a ValueError if unexpected
    arguments are present.

    """
    args = parser.parse_args()
    unexpected_params = set(raw_args) - {
        allowed_arg.name for allowed_arg in parser.args
    }
    if unexpected_params:
        raise InputError(f"Unexpected query parameters {unexpected_params}")
    return args


def get_sending_draft(draft_public_id, namespace_id, db_session):
    valid_public_id(draft_public_id)
    try:
        draft = (
            db_session.query(Message)
            .filter(
                Message.public_id == draft_public_id,
                Message.namespace_id == namespace_id,
            )
            .one()
        )
    except NoResultFound:
        raise NotFoundError(
            f"Couldn't find multi-send draft {draft_public_id}"
        )

    if draft.is_sent or not draft.is_sending:
        raise InputError(
            f"Message {draft_public_id} is not a multi-send draft"
        )
    return draft


def get_draft(draft_public_id, version, namespace_id, db_session):
    valid_public_id(draft_public_id)
    if version is None:
        raise InputError("Must specify draft version")
    try:
        version = int(version)
    except ValueError:
        raise InputError("Invalid draft version")
    try:
        draft = (
            db_session.query(Message)
            .filter(
                Message.public_id == draft_public_id,
                Message.namespace_id == namespace_id,
            )
            .one()
        )
    except NoResultFound:
        raise NotFoundError(f"Couldn't find draft {draft_public_id}")

    if draft.is_sent or not draft.is_draft:
        raise InputError(f"Message {draft_public_id} is not a draft")
    if draft.version != version:
        raise ConflictError(
            "Draft {}.{} has already been updated to version {}".format(
                draft_public_id, version, draft.version
            )
        )
    return draft


def get_attachments(block_public_ids, namespace_id, db_session):
    attachments: set[Block] = set()
    if block_public_ids is None:
        return attachments
    if not isinstance(block_public_ids, list):
        raise InputError(f"{block_public_ids} is not a list of block ids")
    for block_public_id in block_public_ids:
        # Validate public ids before querying with them
        valid_public_id(block_public_id)
        try:
            block = (
                db_session.query(Block)
                .filter(
                    Block.public_id == block_public_id,
                    Block.namespace_id == namespace_id,
                )
                .one()
            )
            # In the future we may consider discovering the filetype from the
            # data by using #magic.from_buffer(data, mime=True))
            attachments.add(block)
        except NoResultFound:
            raise InputError(f"Invalid block public id {block_public_id}")
    return attachments


def get_message(message_public_id, namespace_id, db_session):
    if message_public_id is None:
        return None
    valid_public_id(message_public_id)
    try:
        return (
            db_session.query(Message)
            .filter(
                Message.public_id == message_public_id,
                Message.namespace_id == namespace_id,
            )
            .one()
        )
    except NoResultFound:
        raise InputError(f"Invalid message public id {message_public_id}")


def get_thread(thread_public_id, namespace_id, db_session):
    if thread_public_id is None:
        return None
    valid_public_id(thread_public_id)
    try:
        return (
            db_session.query(Thread)
            .filter(
                Thread.public_id == thread_public_id,
                Thread.deleted_at.is_(None),
                Thread.namespace_id == namespace_id,
            )
            .one()
        )
    except NoResultFound:
        raise InputError(f"Invalid thread public id {thread_public_id}")


def get_recipients(recipients, field):
    if recipients is None:
        return None
    if not isinstance(recipients, list):
        raise InputError(f"Invalid {field} field")

    for r in recipients:
        if not (
            isinstance(r, dict)
            and "email" in r
            and isinstance(r["email"], str)
        ):
            raise InputError(f"Invalid {field} field")
        if "name" in r and not isinstance(r["name"], str):
            raise InputError(f"Invalid {field} field")

    return [(r.get("name", ""), r.get("email", "")) for r in recipients]


def get_calendar(calendar_public_id, namespace, db_session):
    valid_public_id(calendar_public_id)
    try:
        return (
            db_session.query(Calendar)
            .filter(
                Calendar.public_id == calendar_public_id,
                Calendar.namespace_id == namespace.id,
            )
            .one()
        )
    except NoResultFound:
        raise NotFoundError(f"Calendar {calendar_public_id} not found")


def valid_when(when) -> None:
    try:
        parse_as_when(when)
    except (ValueError, ParserError) as e:
        raise InputError(str(e))


def valid_event(event) -> None:
    if "when" not in event:
        raise InputError("Must specify 'when' when creating an event.")

    valid_when(event["when"])

    if (
        "busy" in event
        and event.get("busy") is not None
        and not isinstance(event.get("busy"), bool)
    ):
        # client libraries can send busy: None
        raise InputError("'busy' must be true or false")

    participants = event.get("participants")
    if participants is None:
        participants = []
    for p in participants:
        if "email" not in p:
            raise InputError("'participants' must must have email")

        if not valid_email(p["email"]):
            raise InputError("'{}' is not a valid email".format(p["email"]))

        if "status" in p and p["status"] not in (
            "yes",
            "no",
            "maybe",
            "noreply",
        ):
            raise InputError(
                "'participants' status must be one of: yes, no, maybe, noreply"
            )


def valid_event_update(event, namespace, db_session) -> None:
    if "when" in event:
        valid_when(event["when"])

    if "busy" in event and not isinstance(event.get("busy"), bool):
        raise InputError("'busy' must be true or false")

    participants = event.get("participants", [])
    for p in participants:
        if "email" not in p:
            raise InputError("'participants' must have email")
        if "status" in p and p["status"] not in (
            "yes",
            "no",
            "maybe",
            "noreply",
        ):
            raise InputError(
                "'participants' status must be one of: yes, no, maybe, noreply"
            )


def noop_event_update(event, data) -> bool:
    # Check whether the update is actually updating fields.
    # We do this by cloning the event, updating the fields and
    # comparing them. This is less cumbersome than having to think
    # about the multiple values of the `when` field.
    e = Event.create()
    e.update(event)
    e.namespace = event.namespace

    for attr in Event.API_MODIFIABLE_FIELDS:
        if attr in data:
            setattr(e, attr, data[attr])

    e1 = encode(event)
    e2 = encode(e)

    for attr in Event.API_MODIFIABLE_FIELDS:
        # We have to handle participants a bit differently because
        # it's a list which can be permuted.
        if attr == "participants":
            continue

        event_value = e1.get(attr)
        e_value = e2.get(attr)
        if event_value != e_value:
            return False

    e_participants = {p["email"]: p for p in e.participants}
    event_participants = {p["email"]: p for p in event.participants}
    if len(e_participants.keys()) != len(event_participants.keys()):
        return False

    for email in e_participants:
        if email not in event_participants:
            return False

        p1 = e_participants[email]
        p2 = event_participants[email]

        p1_status = p1.get("status")
        p2_status = p2.get("status")
        if p1_status != p2_status:
            return False

        p1_comment = p1.get("comment")
        p2_comment = p2.get("comment")
        if p1_comment != p2_comment:
            return False

    return True


def valid_delta_object_types(types_arg):
    types = [item.strip() for item in types_arg.split(",")]
    allowed_types = (
        "contact",
        "message",
        "event",
        "file",
        "thread",
        "calendar",
        "draft",
        "folder",
        "label",
    )
    for type_ in types:
        if type_ not in allowed_types:
            raise InputError(f"Invalid object type {type_}")
    return types


def validate_draft_recipients(draft) -> None:
    """
    Check that a draft has at least one recipient, and that all recipient
    emails are at least plausible email addresses, before we try to send it.

    """
    if not any((draft.to_addr, draft.bcc_addr, draft.cc_addr)):
        raise InputError("No recipients specified")
    for field in draft.to_addr, draft.bcc_addr, draft.cc_addr:
        if field is not None:
            for _, email_address in field:
                parsed = address.parse(email_address, addr_spec_only=True)
                if not isinstance(parsed, address.EmailAddress):
                    raise InputError(
                        f"Invalid recipient address {email_address}"
                    )


def valid_display_name(namespace_id, category_type, display_name, db_session):
    if display_name is None or not isinstance(display_name, str):
        raise InputError('"display_name" must be a valid string')

    display_name = display_name.rstrip()
    if len(display_name) > MAX_INDEXABLE_LENGTH:
        # Set as MAX_FOLDER_LENGTH, MAX_LABEL_LENGTH
        raise InputError('"display_name" is too long')

    if (
        db_session.query(Category)
        .filter(
            Category.namespace_id == namespace_id,
            Category.lowercase_name == display_name,
            Category.type_ == category_type,
            Category.deleted_at == EPOCH,
        )
        .first()
        is not None
    ):
        raise InputError(
            f'{category_type} with name "{display_name}" already exists'
        )

    return display_name
