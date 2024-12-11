import json
import logging
import os
import sys

import rollbar
import structlog
from rollbar.logger import RollbarHandler

from inbox.logging import create_error_log_context, get_logger

log = get_logger()

ROLLBAR_API_KEY = os.getenv("ROLLBAR_API_KEY", "")


class SyncEngineRollbarHandler(RollbarHandler):
    def emit(self, record):
        try:
            data = json.loads(record.msg)
        except ValueError:
            return super().emit(record)

        event = data.get("event")
        # Prevent uncaught exceptions from being duplicated in Rollbar.
        # Otherwise they would be reported twice.
        # Once from structlog to logging integration
        # and another time from handle_uncaught_exception
        if event in (
            "Uncaught error",
            "Uncaught error thrown by Flask/Werkzeug",
            "SyncbackWorker caught exception",
        ):
            return None

        record.payload_data = {"fingerprint": event, "title": event}

        return super().emit(record)


def log_uncaught_errors(logger=None, **kwargs):
    """
    Helper to log uncaught exceptions.

    Parameters
    ----------
    logger: structlog.BoundLogger, optional
        The logging object to write to.

    """
    logger = logger or get_logger()
    kwargs.update(create_error_log_context(sys.exc_info()))
    logger.error("Uncaught error", **kwargs)

    # extract interesting details from kwargs and fallback to logging context
    extra_data = {}
    context = structlog.get_context(logger)
    account_id = kwargs.get("account_id") or context.get("account_id")
    provider = kwargs.get("provider") or context.get("provider")
    folder = kwargs.get("folder") or context.get("folder")
    if account_id:
        extra_data["account_id"] = account_id
    if provider:
        extra_data["provider"] = provider
    if folder:
        extra_data["folder"] = folder

    rollbar.report_exc_info(extra_data=extra_data or None)


GROUP_EXCEPTION_CLASSES = [
    "ObjectDeletedError",
    "MailsyncError",
    "Timeout",
    "ReadTimeout",
    "ProgrammingError",
]


def payload_handler(payload, **kw):
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


def maybe_enable_rollbar():
    if not ROLLBAR_API_KEY:
        log.info(
            "ROLLBAR_API_KEY environment variable empty, rollbar disabled"
        )
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
