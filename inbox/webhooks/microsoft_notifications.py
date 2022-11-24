from functools import wraps
from typing import List, cast

from flask import Blueprint, make_response, request
from sqlalchemy.orm.exc import NoResultFound

from inbox.config import config
from inbox.events.microsoft.graph_types import (
    MsGraphChangeNotification,
    MsGraphChangeNotificationCollection,
    MsGraphType,
)
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.calendar import Calendar
from inbox.models.event import Event, RecurringEvent
from inbox.models.session import global_session_scope

app = Blueprint(
    "microsoft_webhooks", "microsoft_webhooks_api", url_prefix="/w/microsoft"
)


def handle_initial_validation_response(view_function):
    @wraps(view_function)
    def _handle_initial_validation_response(*args, **kwargs):
        """
        Handle initial validation of webhook endpoint.

        When subscription is created Microsoft Office365 servers
        immediately contact the endpoint provided POSTing to it with
        validationToken GET argument set. Endpoints are supposed
        to answer with 200 OK, text/plain MIME type and body set to
        validationToken. If an endpoint is unreachable or does not
        respond correctly subscription creation won't succeed.
        Subsequent POSTs contain change notifications.

        https://learn.microsoft.com/en-us/graph/webhooks#notification-endpoint-validation
        """
        validation_token = request.args.get("validationToken")
        if validation_token is not None:
            response = make_response(validation_token)
            response.mimetype = "text/plain"
            return response

        return view_function(*args, **kwargs)

    return _handle_initial_validation_response


def validate_webhook_payload_factory(type: MsGraphType):
    def validate_webhook_payload(view_function):
        @wraps(view_function)
        def _validate_webhook_payload(*args, **kwargs):
            """
            Validate webhook payload.

            Checks weather clientState matches MICROSOFT_SUBSCRIPTION_SECRET
            which we use to create subscriptions. Also checks @odata.type as we
            have two separate endpoints, one for calendar changes and one for
            event changes.
            """
            if request.json is None:
                return ("Malformed JSON payload", 400)

            change_notifications: List[MsGraphChangeNotification] = cast(
                MsGraphChangeNotificationCollection, request.json
            )["value"]

            if any(
                notification["clientState"] != config["MICROSOFT_SUBSCRIPTION_SECRET"]
                for notification in change_notifications
            ):
                return (
                    "'clientState' did not match one provided when creating subscription",
                    400,
                )

            if any(
                notification["resourceData"]["@odata.type"] != type
                for notification in change_notifications
            ):
                return f"Expected '@odata.type' to be '{type}'", 400

            return view_function(*args, **kwargs)

        return _validate_webhook_payload

    return validate_webhook_payload


@app.route("/calendar_list_update/<account_public_id>", methods=["POST"])
@handle_initial_validation_response
@validate_webhook_payload_factory("#Microsoft.Graph.Calendar")
def calendar_update(account_public_id):
    """Handle calendar list update for given account."""
    with global_session_scope() as db_session:
        try:
            account = (
                db_session.query(OutlookAccount)
                .filter(OutlookAccount.public_id == account_public_id)
                .one()
            )
        except NoResultFound:
            return f"Couldn't find account '{account_public_id}'", 404

        account.handle_webhook_notification()
        db_session.commit()

    return "", 200


@app.route("/calendar_update/<calendar_public_id>", methods=["POST"])
@handle_initial_validation_response
@validate_webhook_payload_factory("#Microsoft.Graph.Event")
def event_update(calendar_public_id):
    """Handle events update for given calendar."""
    with global_session_scope() as db_session:
        try:
            calendar = (
                db_session.query(Calendar)
                .filter(Calendar.public_id == calendar_public_id)
                .one()
            )
        except NoResultFound:
            return f"Couldn't find calendar '{calendar_public_id}'", 404

        change_notifications: List[MsGraphChangeNotification] = cast(
            MsGraphChangeNotificationCollection, request.json
        )["value"]

        handle_event_deletions(db_session, calendar, change_notifications)
        calendar.handle_webhook_notification()
        db_session.commit()

    return "", 200


def handle_event_deletions(
    db_session,
    calendar: Calendar,
    change_notifications: List[MsGraphChangeNotification],
) -> None:
    deleted_event_uids = [
        change_notification["resourceData"]["id"]
        for change_notification in change_notifications
        if change_notification["changeType"] == "deleted"
    ]
    if not deleted_event_uids:
        return

    deleted_events = (
        db_session.query(Event)
        .filter(
            Event.namespace_id == calendar.namespace_id,
            Event.calendar_id == calendar.id,
            Event.uid.in_(deleted_event_uids),
        )
        .all()
    )
    if not deleted_events:
        return

    for deleted_event in deleted_events:
        deleted_event.status = "cancelled"
        if isinstance(deleted_event, RecurringEvent):
            for override in deleted_event.overrides:
                override.status = "cancelled"
