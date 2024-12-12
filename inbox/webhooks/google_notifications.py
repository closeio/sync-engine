from flask import Blueprint, g, jsonify, make_response, request
from sqlalchemy.orm.exc import NoResultFound

from inbox.api.err import APIException, InputError, NotFoundError
from inbox.api.validation import valid_public_id
from inbox.logging import get_logger

log = get_logger()
import limitlion  # noqa: E402

from inbox.models import Calendar  # noqa: E402
from inbox.models.backends.gmail import GmailAccount  # noqa: E402
from inbox.models.session import global_session_scope  # noqa: E402

app = Blueprint("google_webhooks", "google_webhooks_api", url_prefix="/w")

GOOGLE_CHANNEL_ID_STRING = "X-Goog-Channel-ID"
GOOGLE_RESOURCE_STATE_STRING = "X-Goog-Resource-State"
GOOGLE_RESOURCE_ID_STRING = "X-Goog-Resource-ID"


def resp(http_code, message=None, **kwargs):  # noqa: ANN201
    resp = kwargs
    if message:
        resp["message"] = message
    if http_code == 204:
        body = ""
    else:
        body = jsonify(resp)
    return make_response(body, http_code)


@app.before_request
def start():  # noqa: ANN201
    try:
        watch_state = request.headers[GOOGLE_RESOURCE_STATE_STRING]
        g.watch_channel_id = request.headers[GOOGLE_CHANNEL_ID_STRING]
        g.watch_resource_id = request.headers[GOOGLE_RESOURCE_ID_STRING]
    except KeyError:
        raise InputError("Malformed headers")  # noqa: B904

    request.environ.setdefault("log_context", {}).update(
        {
            "watch_state": watch_state,
            "watch_channel_id": g.watch_channel_id,
            "watch_resource_id": g.watch_resource_id,
        }
    )

    if watch_state == "sync":
        return resp(204)
    return None


@app.errorhandler(APIException)
def handle_input_error(error):  # noqa: ANN201
    response = jsonify(message=error.message, type="invalid_request_error")
    response.status_code = error.status_code
    return response


@app.route("/calendar_list_update/<account_public_id>", methods=["POST"])
def calendar_update(account_public_id):  # noqa: ANN201
    request.environ["log_context"]["account_public_id"] = account_public_id
    try:
        valid_public_id(account_public_id)
        allowed, tokens, sleep = limitlion.throttle(  # noqa: F841
            f"gcal_account:{account_public_id}", rps=0.5
        )
        if allowed:
            with global_session_scope() as db_session:
                account = (
                    db_session.query(GmailAccount)
                    .filter(GmailAccount.public_id == account_public_id)
                    .one()
                )
                account.handle_webhook_notification()
                db_session.commit()
        return resp(200)
    except ValueError:
        raise InputError("Invalid public ID")  # noqa: B904
    except NoResultFound:
        raise NotFoundError(  # noqa: B904
            f"Couldn't find account `{account_public_id}`"
        )


@app.route("/calendar_update/<calendar_public_id>", methods=["POST"])
def event_update(calendar_public_id):  # noqa: ANN201
    request.environ["log_context"]["calendar_public_id"] = calendar_public_id
    try:
        valid_public_id(calendar_public_id)
        allowed, tokens, sleep = limitlion.throttle(  # noqa: F841
            f"gcal_calendar:{calendar_public_id}", rps=0.5
        )
        if allowed:
            with global_session_scope() as db_session:
                calendar = (
                    db_session.query(Calendar)
                    .filter(Calendar.public_id == calendar_public_id)
                    .with_for_update()
                    .one()
                )
                calendar.handle_webhook_notification()
                db_session.commit()
        return resp(200)
    except ValueError:
        raise InputError("Invalid public ID")  # noqa: B904
    except NoResultFound:
        raise NotFoundError(  # noqa: B904
            f"Couldn't find calendar `{calendar_public_id}`"
        )
