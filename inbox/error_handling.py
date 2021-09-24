import json
import logging
import os

import nylas.logging.sentry
import rollbar
from nylas.logging import get_logger
from rollbar.logger import RollbarHandler

log = get_logger()

ROLLBAR_API_KEY = os.getenv("ROLLBAR_API_KEY", "")


class SyncEngineRollbarHandler(RollbarHandler):
    def emit(self, record):
        try:
            data = json.loads(record.msg)
        except ValueError:
            return super(SyncEngineRollbarHandler, self).emit(record)

        event = data.get("event")
        # Prevent uncaught exceptions from being duplicated in Rollbar.
        # Otherwise they would be reported twice.
        # Once from structlog to logging integration
        # and another time from handle_uncaught_exception
        if (
            event == "Uncaught error"
            or event == "Uncaught error thrown by Flask/Werkzeug"
            or event == "SyncbackWorker caught exception"
        ):
            return

        record.payload_data = {
            "fingerprint": event,
            "title": event,
        }

        return super(SyncEngineRollbarHandler, self).emit(record)


def handle_uncaught_exception(*args, **kwargs):
    rollbar.report_exc_info()


# This while hacky is the easiest way to report unhandeled exceptions for now.
# The code lives in nylas-production-python and
# assumes that they are handled by Sentry, but we monkeypatch it to Rollbar.
nylas.logging.sentry.sentry_alert = handle_uncaught_exception


def maybe_enable_rollbar():
    if not ROLLBAR_API_KEY:
        log.info("ROLLBAR_API_KEY environment variable empty, rollbar disabled")
        return

    application_environment = (
        "production" if os.getenv("NYLAS_ENV", "") == "prod" else "dev"
    )

    rollbar.init(
        ROLLBAR_API_KEY, application_environment, allow_logging_basic_config=False,
    )

    rollbar_handler = SyncEngineRollbarHandler()
    rollbar_handler.setLevel(logging.ERROR)
    logger = logging.getLogger()
    logger.addHandler(rollbar_handler)

    log.info("Rollbar enabled")
