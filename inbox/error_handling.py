from __future__ import absolute_import

import functools
import json
import logging
import os
import random
import re
import sys

import rollbar
from rollbar.logger import RollbarHandler

from inbox.logging import create_error_log_context, get_logger

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
    rollbar.report_exc_info()


def ignore_handler(message_filters, payload, **kw):
    title = payload["data"].get("title")
    exception_message = (
        payload["data"]
        .get("body", {})
        .get("trace", {})
        .get("exception", {})
        .get("message")
    )

    if not (title or exception_message):
        return payload

    for regex, threshold in message_filters:
        if regex.search(title or exception_message) and random.random() >= threshold:
            return False

    return payload


def get_message_filters():
    try:
        message_filters = json.loads(os.getenv("ROLLBAR_MESSAGE_FILTERS", "[]"))
    except ValueError:
        log.error("Could not JSON parse ROLLBAR_MESSAGE_FILTERS environment variable")
        return []

    try:
        message_filters = [
            (re.compile(filter_["regex"]), float(filter_["threshold"]))
            for filter_ in message_filters
        ]
    except Exception:
        log.error("Error while compiling ROLLBAR_MESSAGE_FILTERS")
        return []

    return message_filters


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

    message_filters = get_message_filters()
    if message_filters:
        rollbar.events.add_payload_handler(
            functools.partial(ignore_handler, message_filters)
        )

    log.info("Rollbar enabled")
