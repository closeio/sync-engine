import json
import logging
import os

import rollbar  # type: ignore[import-untyped]
import sentry_sdk
from rollbar.logger import RollbarHandler  # type: ignore[import-untyped]
from sentry_sdk.integrations.logging import LoggingIntegration

from inbox.logging import get_logger

log = get_logger()

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
ROLLBAR_API_KEY = os.getenv("ROLLBAR_API_KEY", "")


class SyncEngineRollbarHandler(RollbarHandler):
    def emit(self, record):  # type: ignore[no-untyped-def]  # noqa: ANN201
        try:
            data = json.loads(record.msg)
        except ValueError:
            return super().emit(record)

        event = data.get("event")
        record.payload_data = {"fingerprint": event, "title": event}

        return super().emit(record)


GROUP_EXCEPTION_CLASSES = [
    "ObjectDeletedError",
    "MailsyncError",
    "Timeout",
    "ReadTimeout",
    "ProgrammingError",
]


def payload_handler(payload, **kw):  # type: ignore[no-untyped-def]  # noqa: ANN201
    title = payload["data"].get("title")
    exception = (
        payload["data"].get("body", {}).get("trace", {}).get("exception", {})
    )
    # On Python 3 exceptions are organized in chains
    if not exception:
        trace_chain = payload["data"].get("body", {}).get("trace_chain")
        exception = trace_chain[0].get("exception", {}) if trace_chain else {}

    exception_message = exception.get("message")
    exception_class = exception.get("class")

    if not (title or exception_message or exception_class):
        return payload

    if exception_class in GROUP_EXCEPTION_CLASSES:
        payload["data"]["fingerprint"] = exception_class

    return payload


def maybe_enable_error_reporting() -> None:
    maybe_enable_rollbar()
    maybe_enable_sentry()


def maybe_enable_rollbar() -> None:
    if not ROLLBAR_API_KEY:
        log.info("ROLLBAR_API_KEY not configured - Rollbar disabled.")
        return

    application_environment = (
        "production" if os.getenv("NYLAS_ENV", "") == "prod" else "dev"
    )

    rollbar.init(
        ROLLBAR_API_KEY,
        application_environment,
        allow_logging_basic_config=False,
    )

    rollbar_handler = SyncEngineRollbarHandler()
    rollbar_handler.setLevel(logging.ERROR)
    logger = logging.getLogger()
    logger.addHandler(rollbar_handler)

    rollbar.events.add_payload_handler(payload_handler)

    log.info("Rollbar enabled")


def maybe_enable_sentry() -> None:
    if not SENTRY_DSN:
        log.info("SENTRY_DSN not configured - Sentry disabled.")
        return

    application_environment = (
        "production" if os.getenv("NYLAS_ENV", "") == "prod" else "dev"
    )
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=True,
        environment=application_environment,
        integrations=[
            LoggingIntegration(
                level=logging.INFO,  # Capture INFO+
                event_level=logging.ERROR,  # Send ERROR+ to Sentry
            )
        ],
        attach_stacktrace=True,
    )
