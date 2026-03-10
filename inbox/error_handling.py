import logging
import os

import sentry_sdk
import sentry_sdk.integrations.logging
import sentry_sdk.scrubber

from inbox.constants import ERROR_REPORTING_SCRUB_FIELDS
from inbox.logging import get_logger

log = get_logger()

SENTRY_DSN = os.getenv("SENTRY_DSN", "")


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
    maybe_enable_sentry()


def maybe_enable_sentry() -> None:
    if not SENTRY_DSN:
        log.info("SENTRY_DSN not configured - Sentry disabled.")
        return

    application_environment = (
        "production" if os.getenv("NYLAS_ENV", "") == "prod" else "dev"
    )
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=application_environment,
        release=os.environ.get("DEPLOYMENT_GIT_SHA", "unknown"),
        # TODO: Remove `sample_rate` once we've reduced the number
        # of errors sync-engine is reporting.
        sample_rate=0.1,
        integrations=[
            sentry_sdk.integrations.logging.LoggingIntegration(
                level=logging.INFO,  # Capture INFO+
                event_level=logging.ERROR,  # Send ERROR+ to Sentry
            )
        ],
        event_scrubber=sentry_sdk.scrubber.EventScrubber(
            denylist=sorted(
                ERROR_REPORTING_SCRUB_FIELDS
                | set(sentry_sdk.scrubber.DEFAULT_DENYLIST)
            )
        ),
        attach_stacktrace=True,
    )
