from functools import wraps

from flask import Blueprint, make_response, request

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


@app.route("/calendar_list_update/<account_public_id>", methods=["POST"])
@handle_initial_validation_response
def calendar_update(account_public_id):
    return "calendar_list_update"


@app.route("/calendar_update/<calendar_public_id>", methods=["POST"])
@handle_initial_validation_response
def event_update(calendar_public_id):
    return "calendar_list_update"
