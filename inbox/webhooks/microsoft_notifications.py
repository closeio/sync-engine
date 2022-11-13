from flask import Blueprint

app = Blueprint(
    "microsoft_webhooks", "microsoft_webhooks_api", url_prefix="/w/microsoft"
)
