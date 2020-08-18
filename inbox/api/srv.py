from flask import Flask, g, jsonify, make_response, request
from flask.ext.restful import reqparse
from metrics_api import app as metrics_api
from ns_api import DEFAULT_LIMIT, app as ns_api
from nylas.logging import get_logger
from sqlalchemy.orm.exc import NoResultFound
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
from inbox.auth.generic import GenericAuthHandler
from inbox.auth.gmail import GmailAuthHandler
from inbox.models import Account, Namespace
from inbox.models.backends.generic import GenericAccount
from inbox.models.backends.gmail import GOOGLE_EMAIL_SCOPE, GmailAccount
from inbox.models.session import global_session_scope
from inbox.util.logging_helper import reconfigure_logging
from inbox.webhooks.gpush_notifications import app as webhooks_api

app = Flask(__name__)
# Handle both /endpoint and /endpoint/ without redirecting.
# Note that we need to set this *before* registering the blueprint.
app.url_map.strict_slashes = False

reconfigure_logging()


@app.errorhandler(APIException)
def handle_input_error(error):
    response = jsonify(message=error.message, type="invalid_request_error")
    response.status_code = error.status_code
    return response


def default_json_error(ex):
    """ Exception -> flask JSON responder """
    logger = get_logger()
    logger.error("Uncaught error thrown by Flask/Werkzeug", exc_info=ex)
    response = jsonify(message=str(ex), type="api_error")
    response.status_code = ex.code if isinstance(ex, HTTPException) else 500
    return response


# Patch all error handlers in werkzeug
for code in default_exceptions.iterkeys():
    app.error_handler_spec[None][code] = default_json_error


@app.before_request
def auth():
    """ Check for account ID on all non-root URLS """
    if (
        request.path == "/"
        or request.path.startswith("/accounts")
        or request.path.startswith("/w/")
        or request.path.startswith("/metrics")
    ):
        return

    if not request.authorization or not request.authorization.username:

        AUTH_ERROR_MSG = (
            "Could not verify access credential.",
            401,
            {"WWW-Authenticate": 'Basic realm="API ' 'Access Token Required"'},
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
                    {"WWW-Authenticate": 'Basic realm="API ' 'Access Token Required"'},
                )
            )


@app.after_request
def finish(response):
    origin = request.headers.get("origin")
    if origin:  # means it's just a regular request
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
        response.headers[
            "Access-Control-Allow-Methods"
        ] = "GET,PUT,POST,DELETE,OPTIONS,PATCH"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.route("/accounts/", methods=["GET"])
def ns_all():
    """ Return all namespaces """
    # We do this outside the blueprint to support the case of an empty
    # public_id.  However, this means the before_request isn't run, so we need
    # to make our own session
    with global_session_scope() as db_session:
        parser = reqparse.RequestParser(argument_class=ValidatableArgument)
        parser.add_argument("limit", default=DEFAULT_LIMIT, type=limit, location="args")
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


@app.route("/accounts/", methods=["POST"])
def create_account():
    """ Create a new account """
    data = request.get_json(force=True)

    provider = data.get("provider", "custom")
    email_address = data["email_address"]

    sync_email = data.get("sync_email", True)
    sync_calendar = data.get("sync_calendar", False)

    if data["type"] == "generic":
        auth_handler = GenericAuthHandler(provider)
        account = auth_handler.create_account(
            email_address,
            {
                "name": "",
                "email": email_address,
                "imap_server_host": data["imap_server_host"],
                "imap_server_port": data["imap_server_port"],
                "imap_username": data["imap_username"],
                "imap_password": data["imap_password"],
                # Make Nylas happy with dummy values
                "smtp_server_host": "localhost",
                "smtp_server_port": 25,
                "smtp_username": "dummy",
                "smtp_password": "dummy",
                "sync_email": sync_email,
            },
        )

    elif data["type"] == "gmail":
        scopes = data.get("scopes", GOOGLE_EMAIL_SCOPE)
        auth_handler = GmailAuthHandler(provider)
        account = auth_handler.create_account(
            email_address,
            {
                "name": "",
                "email": email_address,
                "refresh_token": data["refresh_token"],
                "scope": scopes,
                "id_token": "",
                "contacts": False,
                "sync_email": sync_email,
                "events": sync_calendar,
            },
        )

    else:
        raise ValueError("Account type not supported.")

    with global_session_scope() as db_session:
        # By default, don't enable accounts so we have the ability to set a
        # custom sync host.
        account.sync_should_run = False
        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(account.namespace)


@app.route("/accounts/<namespace_public_id>/", methods=["PUT"])
def modify_account(namespace_public_id):
    """
    Modify an existing account

    This stops syncing an account until it is explicitly resumed.
    """

    data = request.get_json(force=True)

    provider = data.get("provider", "custom")
    email_address = data["email_address"]

    sync_email = data.get("sync_email", True)
    sync_calendar = data.get("sync_calendar", False)

    with global_session_scope() as db_session:
        namespace = (
            db_session.query(Namespace)
            .filter(Namespace.public_id == namespace_public_id)
            .one()
        )
        account = namespace.account

        if isinstance(account, GenericAccount):
            if "refresh_token" in data:
                raise InputError(
                    "Cannot change the refresh token on a password account."
                )

            auth_handler = GenericAuthHandler(provider)
            auth_handler.update_account(
                account,
                {
                    "name": "",
                    "email": email_address,
                    "imap_server_host": data["imap_server_host"],
                    "imap_server_port": data["imap_server_port"],
                    "imap_username": data["imap_username"],
                    "imap_password": data["imap_password"],
                    # Make Nylas happy with dummy values
                    "smtp_server_host": "localhost",
                    "smtp_server_port": 25,
                    "smtp_username": "dummy",
                    "smtp_password": "dummy",
                    "sync_email": sync_email,
                },
            )

        elif isinstance(account, GmailAccount):
            scopes = data.get("scopes", GOOGLE_EMAIL_SCOPE)
            auth_handler = GmailAuthHandler(provider)
            if "refresh_token" in data:
                account = auth_handler.update_account(
                    account,
                    {
                        "name": "",
                        "email": email_address,
                        "refresh_token": data["refresh_token"],
                        "scope": scopes,
                        "id_token": "",
                        "sync_email": sync_email,
                        "contacts": False,
                        "events": sync_calendar,
                    },
                )
            else:
                if (
                    "imap_server_host" in data
                    or "imap_server_port" in data
                    or "imap_username" in data
                    or "imap_password" in data
                ):
                    raise InputError("Cannot change IMAP fields on a Gmail account.")

        else:
            raise ValueError("Account type not supported.")

        # By default, don't enable accounts so we have the ability to set a
        # custom sync host.
        account.disable_sync("modified-account")
        db_session.add(account)
        db_session.commit()

        encoder = APIEncoder()
        return encoder.jsonify(account.namespace)


@app.route("/accounts/<namespace_public_id>/", methods=["DELETE"])
def delete_account(namespace_public_id):
    """ Mark an existing account for deletion. """
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
        raise NotFoundError("Couldn't find account `{0}` ".format(namespace_public_id))

    encoder = APIEncoder()
    return encoder.jsonify({})


@app.route("/")
def home():
    return "Nylas ready.\n"


@app.route("/logout")
def logout():
    """ Utility function used to force browsers to reset cached HTTP Basic Auth
        credentials """
    return make_response(
        (
            "<meta http-equiv='refresh' content='0; url=/''>.",
            401,
            {"WWW-Authenticate": 'Basic realm="API Access Token Required"'},
        )
    )


app.register_blueprint(metrics_api)
app.register_blueprint(ns_api)
app.register_blueprint(webhooks_api)  # /w/...
