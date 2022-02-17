import sys
import traceback

import rollbar
from flask import jsonify, make_response, request

from inbox.logging import create_error_log_context, get_logger

log = get_logger()

from inbox.config import is_live_env


def get_request_uid(headers):
    return headers.get("X-Unique-ID")


def log_exception(exc_info, **kwargs):
    """ Add exception info to the log context for the request.

    We do not log in a separate log statement in order to make debugging
    easier. As a bonus, this reduces log volume somewhat.

    """
    rollbar.report_exc_info(exc_info)

    if not is_live_env():
        print()
        traceback.print_exc()
        print()

    new_log_context = create_error_log_context(exc_info)
    new_log_context.update(kwargs)

    # guard against programming errors overriding log fields (confusing!)
    if set(new_log_context.keys()).intersection(
        set(request.environ.get("log_context", {}))
    ):
        log.warning(
            "attempt to log more than one error to HTTP request",
            request_uid=get_request_uid(request.headers),
            **new_log_context
        )
    else:
        request.environ.setdefault("log_context", {}).update(new_log_context)


class APIException(Exception):
    status_code = 500


class InputError(APIException):
    """Raised on invalid user input (missing required parameter, value too
    long, etc.)"""

    status_code = 400

    def __init__(self, message):
        self.message = message
        super(InputError, self).__init__(message)


class NotFoundError(APIException):
    """Raised when a requested resource doesn't exist."""

    status_code = 404

    def __init__(self, message):
        self.message = message
        super(NotFoundError, self).__init__(message)


class ConflictError(APIException):
    status_code = 409

    def __init__(self, message):
        self.message = message
        super(ConflictError, self).__init__(message)


class AccountInvalidError(APIException):
    """ Raised when an account's credentials are not valid. """

    status_code = 403
    message = (
        "This action can't be performed because the account's "
        "credentials are out of date. Please reauthenticate and try "
        "again."
    )


class AccountStoppedError(APIException):
    """ Raised when an account has been stopped. """

    status_code = 403
    message = (
        "This action can't be performed because the account's sync "
        "has been stopped. Please contact support@nylas.com to resume "
        "sync."
    )


class AccountDoesNotExistError(APIException):
    """ Raised when an account does not exist (for example, if it was deleted). """

    status_code = 404
    message = "The account does not exist."


def err(http_code, message, **kwargs):
    """ Handle unexpected errors, including sending the traceback to Rollbar. """
    log_exception(sys.exc_info(), user_error_message=message, **kwargs)
    resp = {"type": "api_error", "message": message}
    resp.update(kwargs)
    return make_response(jsonify(resp), http_code)
