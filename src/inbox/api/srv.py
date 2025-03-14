from typing import Any

from flask import Flask, g, jsonify, make_response, request
from flask_restful import reqparse  # type: ignore[import-untyped]
from sqlalchemy.orm.exc import NoResultFound  # type: ignore[import-untyped]
from werkzeug.exceptions import HTTPException, default_exceptions

from inbox.api.err import APIException, InputError, NotFoundError
from inbox.api.kellogs import APIEncoder
from inbox.api.validation import (
    ValidatableArgument,
    bounded_str,
    limit,
    strict_parse_args,
    valid_public_id,
)
from inbox.auth.generic import GenericAccountData, GenericAuthHandler
from inbox.auth.google import GoogleAccountData, GoogleAuthHandler
from inbox.auth.microsoft import MicrosoftAccountData, MicrosoftAuthHandler
from inbox.logging import get_logger
from inbox.models import Account, Namespace
from inbox.models.backends.generic import GenericAccount
from inbox.models.backends.gmail import GOOGLE_EMAIL_SCOPES, GmailAccount
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.secret import SecretType
from inbox.models.session import global_session_scope
from inbox.util.logging_helper import reconfigure_logging
from inbox.webhooks.google_notifications import app as google_webhooks_api
from inbox.webhooks.microsoft_notifications import (
    app as microsoft_webhooks_api,
)

from .metrics_api import app as metrics_api
from .ns_api import DEFAULT_LIMIT
from .ns_api import app as ns_api

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
# Handle both /endpoint and /endpoint/ without redirecting.
# Note that we need to set this *before* registering the blueprint.
app.url_map.strict_slashes = False

reconfigure_logging()


@app.errorhandler(APIException)
def handle_input_error(error):  # type: ignore[no-untyped-def]  # noqa: ANN201
    response = jsonify(message=error.message, type="invalid_request_error")
    response.status_code = error.status_code
    return response


def default_json_error(ex):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Exception -> flask JSON responder"""
    logger = get_logger()
    logger.error("Uncaught error thrown by Flask/Werkzeug", exc_info=ex)
    response = jsonify(message=str(ex), type="api_error")
    response.status_code = (
        ex.code  # type: ignore[assignment]
        if isinstance(ex, HTTPException)
        else 500
    )
    return response


# Patch all error handlers in werkzeug
for code in default_exceptions:
    app.error_handler_spec[None][
        code
    ] = default_json_error  # type: ignore[assignment]


@app.before_request
def auth():  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Check for account ID on all non-root URLS"""
    if (
        request.path == "/"
        or request.path.startswith("/accounts")
        or request.path.startswith("/w/")
        or request.path.startswith("/metrics")
    ):
        return None

    if not request.authorization or not request.authorization.username:
        AUTH_ERROR_MSG = (  # noqa: N806
            "Could not verify access credential.",
            401,
            {"WWW-Authenticate": 'Basic realm="API Access Token Required"'},
        )

        auth_header = request.headers.get("Authorization", None)

        if not auth_header:
            return make_response(AUTH_ERROR_MSG)

        parts = auth_header.split()

        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            return make_response(AUTH_ERROR_MSG)
        namespace_public_id = parts[1]

    else:
        namespace_public_id = request.authorization.username

    with global_session_scope() as db_session:
        try:
            valid_public_id(namespace_public_id)
            namespace = (
                db_session.query(Namespace)
                .filter(Namespace.public_id == namespace_public_id)
                .one()
            )
            g.namespace_id = namespace.id
            g.account_id = namespace.account.id
        except NoResultFound:
            return make_response(
                (
                    "Could not verify access credential.",
                    401,
                    {
                        "WWW-Authenticate": 'Basic realm="API Access Token Required"'
                    },
                )
            )


@app.after_request
def finish(response):  # type: ignore[no-untyped-def]  # noqa: ANN201
    origin = request.headers.get("origin")
    if origin:  # means it's just a regular request
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization,Content-Type"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET,PUT,POST,DELETE,OPTIONS,PATCH"
        )
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.route("/accounts/", methods=["GET"])
def ns_all():  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Return all namespaces"""
    # We do this outside the blueprint to support the case of an empty
    # public_id.  However, this means the before_request isn't run, so we need
    # to make our own session
    with global_session_scope() as db_session:
        parser = reqparse.RequestParser(argument_class=ValidatableArgument)
        parser.add_argument(
            "limit", default=DEFAULT_LIMIT, type=limit, location="args"
        )
        parser.add_argument("offset", default=0, type=int, location="args")
        parser.add_argument("email_address", type=bounded_str, location="args")
        args = strict_parse_args(parser, request.args)

        query = db_session.query(Namespace)
        if args["email_address"]:
            query = query.join(Account)
            query = query.filter_by(email_address=args["email_address"])

        query = query.limit(args["limit"])
        if args["offset"]:
            query = query.offset(args["offset"])

        namespaces = query.all()
        encoder = APIEncoder()
        return encoder.jsonify(namespaces)


def _get_account_data_for_generic_account(  # type: ignore[no-untyped-def]
    data,
):
    email_address = data["email_address"]
    sync_email = data.get("sync_email", True)
    smtp_server_host = data.get("smtp_server_host", "localhost")
    smtp_server_port = data.get("smtp_server_port", 25)
    smtp_username = data.get("smtp_username", "dummy")
    smtp_password = data.get("smtp_password", "dummy")

    return GenericAccountData(
        email=email_address,
        imap_server_host=data["imap_server_host"],
        imap_server_port=data["imap_server_port"],
        imap_username=data["imap_username"],
        imap_password=data["imap_password"],
        smtp_server_host=smtp_server_host,
        smtp_server_port=smtp_server_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        sync_email=sync_email,
    )


def _get_account_data_for_google_account(
    data: dict[str, Any]
) -> GoogleAccountData:
    email_address = data["email_address"]
    scopes = data.get("scopes", " ".join(GOOGLE_EMAIL_SCOPES))
    client_id = data.get("client_id")

    sync_email = data.get("sync_email", True)
    sync_calendar = data.get("sync_calendar", False)
    sync_contacts = data.get("sync_contacts", False)

    refresh_token = data.get("refresh_token")
    authalligator = data.get("authalligator")

    if authalligator:
        secret_type = SecretType.AuthAlligator
        secret_value = authalligator
    elif refresh_token:
        secret_type = SecretType.Token
        secret_value = refresh_token
    else:
        raise InputError("Authentication information missing.")

    return GoogleAccountData(
        email=email_address,
        secret_type=secret_type,
        secret_value=secret_value,
        client_id=client_id,
        scope=scopes,
        sync_email=sync_email,
        sync_events=sync_calendar,
        sync_contacts=sync_contacts,
    )


def _get_account_data_for_microsoft_account(
    data: dict[str, Any]
) -> MicrosoftAccountData:
    email_address = data["email_address"]
    scopes = data["scopes"]
    client_id = data.get("client_id")

    refresh_token = data.get("refresh_token")
    authalligator = data.get("authalligator")

    sync_email = data.get("sync_email", True)
    sync_calendar = data.get("sync_calendar", False)

    if authalligator:
        secret_type = SecretType.AuthAlligator
        secret_value = authalligator
    elif refresh_token:
        secret_type = SecretType.Token
        secret_value = refresh_token
    else:
        raise InputError("Authentication information missing.")

    return MicrosoftAccountData(
        email=email_address,
        secret_type=secret_type,
        secret_value=secret_value,
        client_id=client_id,
        scope=scopes,
        sync_email=sync_email,
        sync_events=sync_calendar,
    )


@app.route("/accounts/", methods=["POST"])
def create_account():  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Create a new account"""
    data = request.get_json(force=True)

    auth_handler: GenericAuthHandler | GoogleAuthHandler | MicrosoftAuthHandler
    if data["type"] == "generic":
        auth_handler = GenericAuthHandler()
        account_data = _get_account_data_for_generic_account(data)
    elif data["type"] == "gmail":
        auth_handler = GoogleAuthHandler()
        account_data = _get_account_data_for_google_account(data)
    elif data["type"] == "microsoft":
        auth_handler = MicrosoftAuthHandler()
        account_data = _get_account_data_for_microsoft_account(data)
    else:
        raise ValueError("Account type not supported.")

    with global_session_scope() as db_session:
        account = auth_handler.create_account(account_data)
        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(account.namespace)  # type: ignore[union-attr]


@app.route("/accounts/<namespace_public_id>/", methods=["PUT"])
def modify_account(namespace_public_id):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Modify an existing account

    This stops syncing an account until it is explicitly resumed.
    """
    data = request.get_json(force=True)

    with global_session_scope() as db_session:
        namespace = (
            db_session.query(Namespace)
            .filter(Namespace.public_id == namespace_public_id)
            .one()
        )
        account = namespace.account

        auth_handler: (
            GenericAuthHandler | GoogleAuthHandler | MicrosoftAuthHandler
        )
        if isinstance(account, GenericAccount):
            auth_handler = GenericAuthHandler()
            account_data = _get_account_data_for_generic_account(data)
        elif isinstance(account, GmailAccount):
            auth_handler = GoogleAuthHandler()
            account_data = _get_account_data_for_google_account(data)
        elif isinstance(account, OutlookAccount):
            auth_handler = MicrosoftAuthHandler()
            account_data = _get_account_data_for_microsoft_account(data)
        else:
            raise ValueError("Account type not supported.")

        account = auth_handler.update_account(account, account_data)
        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(account.namespace)


@app.route("/accounts/<namespace_public_id>/", methods=["DELETE"])
def delete_account(namespace_public_id):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Mark an existing account for deletion."""
    try:
        with global_session_scope() as db_session:
            namespace = (
                db_session.query(Namespace)
                .filter(Namespace.public_id == namespace_public_id)
                .one()
            )
            account = namespace.account
            account.mark_for_deletion()
            db_session.commit()
    except NoResultFound:
        raise NotFoundError(  # noqa: B904
            f"Couldn't find account `{namespace_public_id}` "
        )

    encoder = APIEncoder()
    return encoder.jsonify({})


@app.route("/")
def home() -> str:
    return "Nylas ready.\n"


@app.route("/logout")
def logout():  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Utility function used to force browsers to reset cached HTTP Basic Auth
    credentials
    """  # noqa: D401
    return make_response(
        (
            "<meta http-equiv='refresh' content='0; url=/''>.",
            401,
            {"WWW-Authenticate": 'Basic realm="API Access Token Required"'},
        )
    )


app.register_blueprint(metrics_api)
app.register_blueprint(ns_api)
app.register_blueprint(google_webhooks_api)  # /w/...
app.register_blueprint(microsoft_webhooks_api)  # /w/microsoft/...
