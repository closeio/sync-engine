import calendar
import datetime
from json import JSONEncoder, dumps

import arrow
from flask import Response

from inbox.events.timezones import timezones_table
from inbox.logging import get_logger
from inbox.models import (
    Account,
    Block,
    Calendar,
    Category,
    Contact,
    Event,
    Message,
    Metadata,
    Namespace,
    Thread,
    When,
)
from inbox.models.calendar import is_default_calendar
from inbox.models.event import (
    InflatedEvent,
    RecurringEvent,
    RecurringEventOverride,
)

log = get_logger()


def format_address_list(addresses):
    if addresses is None:
        return []
    return [{"name": name, "email": email} for name, email in addresses]


def format_categories(categories):
    if categories is None:
        return []
    return [
        {
            "id": category.public_id,
            "name": category.name or None,
            "display_name": category.api_display_name,
        }
        for category in categories
        if category
    ]


def format_messagecategories(messagecategories):
    if messagecategories is None:
        return []
    return [
        {
            "id": message_category.category.public_id,
            "name": message_category.category.name or None,
            "display_name": message_category.category.api_display_name,
            "created_timestamp": message_category.created_at,
        }
        for message_category in messagecategories
        if message_category.category
    ]


def format_phone_numbers(phone_numbers):
    formatted_phone_numbers = []
    for number in phone_numbers:
        formatted_phone_numbers.append(
            {"type": number.type, "number": number.number}
        )
    return formatted_phone_numbers


def encode(obj, namespace_public_id=None, expand=False, is_n1=False):
    try:
        return _encode(obj, namespace_public_id, expand, is_n1=is_n1)
    except Exception as e:
        error_context = {
            "id": getattr(obj, "id", None),
            "cls": str(getattr(obj, "__class__", None)),
            "exception": e,
            "exc_info": True,
        }

        log.error("object encoding failure", **error_context)
        raise


def _convert_timezone_to_iana_tz(original_tz):
    if original_tz is None:
        return None

    try:
        return timezones_table[original_tz]
    except KeyError:
        log.error("Bad IANA timezone identifier", original_tz=original_tz)
        return original_tz


def _encode(obj, namespace_public_id=None, expand=False, is_n1=False):
    """
    Returns a dictionary representation of a Nylas model object obj, or
    None if there is no such representation defined. If the optional
    namespace_public_id parameter is passed, it will used instead of fetching
    the namespace public id for each object. This improves performance when
    serializing large numbers of objects, but also means that you must take
    care to ONLY serialize objects that belong to the given namespace!

    Parameters
    ----------
    namespace_public_id: string, optional
        public id of the namespace to which the object to serialize belongs.

    Returns
    -------
    dictionary or None

    """

    def _get_namespace_public_id(obj):
        return namespace_public_id or obj.namespace.public_id

    def _format_participant_data(participant):
        """
        Event.participants is a JSON blob which may contain internal data.
        This function returns a dict with only the data we want to make
        public.
        """
        dct = {}
        for attribute in ["name", "status", "email", "comment"]:
            dct[attribute] = participant.get(attribute)

        return dct

    def _get_lowercase_class_name(obj):
        return type(obj).__name__.lower()

    # Flask's jsonify() doesn't handle datetimes or json arrays as primary
    # objects.
    if isinstance(obj, datetime.datetime):
        return calendar.timegm(obj.utctimetuple())

    if isinstance(obj, datetime.date):
        return obj.isoformat()

    if isinstance(obj, arrow.arrow.Arrow):
        return encode(obj.datetime)

    if isinstance(obj, Namespace):  # These are now "accounts"
        acc_state = obj.account.sync_state
        if acc_state is None:
            acc_state = "running"

        if is_n1 and acc_state not in ["running", "invalid"]:
            acc_state = "running"

        resp = {
            "id": obj.public_id,
            "object": "account",
            "account_id": obj.public_id,
            "email_address": obj.account.email_address if obj.account else "",
            "name": obj.account.name,
            "provider": obj.account.provider,
            "organization_unit": obj.account.category_type,
            "sync_state": acc_state,
        }

        # Gmail accounts do not set the `server_settings`
        if expand and obj.account.server_settings:
            resp["server_settings"] = obj.account.server_settings
        return resp

    elif isinstance(obj, Account):
        raise Exception("Should never be serializing accounts")

    elif isinstance(obj, Message):
        thread_public_id = None
        if obj.thread:
            thread_public_id = obj.thread.public_id

        resp = {
            "id": obj.public_id,
            "object": "message",
            "account_id": _get_namespace_public_id(obj),
            "subject": obj.subject,
            "from": format_address_list(obj.from_addr),
            "reply_to": format_address_list(obj.reply_to),
            "to": format_address_list(obj.to_addr),
            "cc": format_address_list(obj.cc_addr),
            "bcc": format_address_list(obj.bcc_addr),
            "date": obj.received_date,
            "thread_id": thread_public_id,
            "snippet": obj.snippet,
            "body": obj.body,
            "unread": not obj.is_read,
            "starred": obj.is_starred,
            "files": obj.api_attachment_metadata,
            "events": [encode(e) for e in obj.events],
        }

        categories = format_messagecategories(obj.messagecategories)
        if obj.namespace.account.category_type == "folder":
            resp["folder"] = categories[0] if categories else None
        else:
            resp["labels"] = categories

        # If the message is a draft (Nylas-created or otherwise):
        if obj.is_draft:
            resp["object"] = "draft"
            resp["version"] = obj.version
            if obj.reply_to_message is not None:
                resp["reply_to_message_id"] = obj.reply_to_message.public_id
            else:
                resp["reply_to_message_id"] = None

        if expand:
            resp["headers"] = {
                "Message-Id": obj.message_id_header,
                "In-Reply-To": obj.in_reply_to,
                "References": obj.references,
            }

        return resp

    elif isinstance(obj, Thread):
        base = {
            "id": obj.public_id,
            "object": "thread",
            "account_id": _get_namespace_public_id(obj),
            "subject": obj.subject,
            "participants": format_address_list(obj.participants),
            "last_message_timestamp": obj.recentdate,
            "last_message_received_timestamp": obj.most_recent_received_date,
            "last_message_sent_timestamp": obj.most_recent_sent_date,
            "first_message_timestamp": obj.subjectdate,
            "snippet": obj.snippet,
            "unread": obj.unread,
            "starred": obj.starred,
            "has_attachments": obj.has_attachments,
            "version": obj.version,
        }

        categories = format_categories(obj.categories)
        if obj.namespace.account.category_type == "folder":
            base["folders"] = categories
        else:
            base["labels"] = categories

        if not expand:
            base["message_ids"] = [
                m.public_id for m in obj.messages if not m.is_draft
            ]
            base["draft_ids"] = [m.public_id for m in obj.drafts]
            return base

        # Expand messages within threads
        all_expanded_messages = []
        all_expanded_drafts = []
        for msg in obj.messages:
            resp = {
                "id": msg.public_id,
                "object": "message",
                "account_id": _get_namespace_public_id(msg),
                "subject": msg.subject,
                "from": format_address_list(msg.from_addr),
                "reply_to": format_address_list(msg.reply_to),
                "to": format_address_list(msg.to_addr),
                "cc": format_address_list(msg.cc_addr),
                "bcc": format_address_list(msg.bcc_addr),
                "date": msg.received_date,
                "thread_id": obj.public_id,
                "snippet": msg.snippet,
                "unread": not msg.is_read,
                "starred": msg.is_starred,
                "files": msg.api_attachment_metadata,
                "headers": {
                    "Message-Id": msg.message_id_header,
                    "In-Reply-To": msg.in_reply_to,
                    "References": msg.references,
                },
            }
            categories = format_messagecategories(msg.messagecategories)
            if obj.namespace.account.category_type == "folder":
                resp["folder"] = categories[0] if categories else None
            else:
                resp["labels"] = categories

            if msg.is_draft:
                resp["object"] = "draft"
                resp["version"] = msg.version
                if msg.reply_to_message is not None:
                    resp["reply_to_message_id"] = (
                        msg.reply_to_message.public_id
                    )
                else:
                    resp["reply_to_message_id"] = None
                all_expanded_drafts.append(resp)
            else:
                all_expanded_messages.append(resp)

        base["messages"] = all_expanded_messages
        base["drafts"] = all_expanded_drafts
        return base

    elif isinstance(obj, Contact):
        return {
            "id": obj.public_id,
            "object": "contact",
            "account_id": _get_namespace_public_id(obj),
            "name": obj.name,
            "email": obj.email_address,
            "phone_numbers": format_phone_numbers(obj.phone_numbers),
        }

    elif isinstance(obj, Event):
        resp = {
            "id": obj.public_id,
            "object": "event",
            "account_id": _get_namespace_public_id(obj),
            "calendar_id": obj.calendar.public_id if obj.calendar else None,
            "message_id": obj.message.public_id if obj.message else None,
            "title": obj.title,
            "email_addresses_from_title": obj.emails_from_title,
            "description": obj.description,
            "email_addresses_from_description": obj.emails_from_description,
            "owner": obj.owner,
            "is_owner": obj.is_owner,
            "participants": [
                _format_participant_data(participant)
                for participant in obj.participants
            ],
            "read_only": obj.read_only,
            "location": obj.location,
            "when": encode(obj.when),
            "busy": obj.busy,
            "status": obj.status,
            "visibility": obj.visibility,
            "uid": obj.uid,
            "calendar_event_link": obj.calendar_event_link,
            "conference_data": obj.conference_data,
        }
        if isinstance(obj, RecurringEvent):
            resp["recurrence"] = {
                "rrule": obj.recurring,
                "timezone": _convert_timezone_to_iana_tz(obj.start_timezone),
            }
        if isinstance(obj, RecurringEventOverride):
            resp["original_start_time"] = encode(obj.original_start_time)
            resp["master_event_uid"] = obj.master_event_uid
            if obj.master:
                resp["master_event_id"] = obj.master.public_id
        if isinstance(obj, InflatedEvent):
            del resp["message_id"]
            if obj.master:
                resp["master_event_id"] = obj.master.public_id

                if obj.master.calendar:
                    resp["calendar_id"] = obj.master.calendar.public_id
        return resp

    elif isinstance(obj, Calendar):
        return {
            "id": obj.public_id,
            "object": "calendar",
            "account_id": _get_namespace_public_id(obj),
            "name": obj.name,
            "description": obj.description,
            "read_only": obj.read_only,
            "uid": obj.uid,
            "default": is_default_calendar(obj),
        }

    elif isinstance(obj, When):
        # Get time dictionary e.g. 'start_time': x, 'end_time': y or 'date': z
        times = obj.get_time_dict()
        resp = {k: encode(v) for k, v in times.items()}
        resp["object"] = _get_lowercase_class_name(obj)
        return resp

    elif isinstance(obj, Block):  # ie: Attachments/Files
        resp = {
            "id": obj.public_id,
            "object": "file",
            "account_id": _get_namespace_public_id(obj),
            "content_type": obj.content_type,
            "size": obj.size,
            "filename": obj.filename,
        }
        if len(obj.parts):
            # if obj is actually a message attachment (and not merely an
            # uploaded file), set additional properties
            resp.update(
                {"message_ids": [p.message.public_id for p in obj.parts]}
            )

            content_ids = list(
                {p.content_id for p in obj.parts if p.content_id is not None}
            )
            content_id = None
            if len(content_ids) > 0:
                content_id = content_ids[0]

            resp.update({"content_id": content_id})

        return resp

    elif isinstance(obj, Category):
        # 'object' is set to 'folder' or 'label'
        resp = {
            "id": obj.public_id,
            "object": obj.type,
            "account_id": _get_namespace_public_id(obj),
            "name": obj.name or None,
            "display_name": obj.api_display_name,
        }
        return resp

    elif isinstance(obj, Metadata):
        resp = {
            "id": obj.public_id,
            "account_id": _get_namespace_public_id(obj),
            "application_id": obj.app_client_id,
            "object_type": obj.object_type,
            "object_id": obj.object_public_id,
            "version": obj.version,
            "value": obj.value,
        }
        return resp
    return None


class APIEncoder:
    """
    Provides methods for serializing Nylas objects. If the optional
    namespace_public_id parameter is passed, it will be bound and used instead
    of fetching the namespace public id for each object. This improves
    performance when serializing large numbers of objects, but also means that
    you must take care to ONLY serialize objects that belong to the given
    namespace!

    Parameters
    ----------
    namespace_public_id: string, optional
        public id of the namespace to which the object to serialize belongs.

    """

    def __init__(
        self, namespace_public_id=None, expand=False, is_n1=False
    ) -> None:
        self.encoder_class = self._encoder_factory(
            namespace_public_id, expand, is_n1=is_n1
        )

    def _encoder_factory(self, namespace_public_id, expand, is_n1=False):
        class InternalEncoder(JSONEncoder):
            def default(self, obj):
                custom_representation = encode(
                    obj, namespace_public_id, expand=expand, is_n1=is_n1
                )
                if custom_representation is not None:
                    return custom_representation
                # Let the base class default method raise the TypeError
                return JSONEncoder.default(self, obj)

        return InternalEncoder

    def cereal(self, obj, pretty=False):
        """
        Returns the JSON string representation of obj.

        Parameters
        ----------
        obj: serializable object
        pretty: bool, optional
            Whether to pretty-print the string (with 4-space indentation).

        Raises
        ------
        TypeError
            If obj is not serializable.

        """
        if pretty:
            return dumps(
                obj,
                sort_keys=True,
                indent=4,
                separators=(",", ": "),
                cls=self.encoder_class,
            )
        return dumps(obj, cls=self.encoder_class)

    def jsonify(self, obj):
        """
        Returns a Flask Response object encapsulating the JSON
        representation of obj.

        Parameters
        ----------
        obj: serializable object

        Raises
        ------
        TypeError
            If obj is not serializable.

        """
        return Response(
            self.cereal(obj, pretty=True), mimetype="application/json"
        )
