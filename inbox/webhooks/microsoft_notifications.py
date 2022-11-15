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
from inbox.models.session import global_session_scope

app = Blueprint(
    "microsoft_webhooks", "microsoft_webhooks_api", url_prefix="/w/microsoft"
)


def handle_initial_validation_response(view_function):
    @wraps(view_function)
    def _handle_initial_validation_response(*args, **kwargs):
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
    return "calendar_list_update"
