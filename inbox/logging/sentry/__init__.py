import os
import sys
from pkgutil import extend_path

from urllib2 import URLError

# Allow out-of-tree submodules.
__path__ = extend_path(__path__, __name__)

import raven
import raven.processors

from inbox.logging.log import MAX_EXCEPTION_LENGTH, create_error_log_context, get_logger

_sentry_client = None


def sentry_exceptions_enabled():
    return "SENTRY_DSN" in os.environ and os.environ["SENTRY_DSN"] != ""


def get_sentry_client():
    global _sentry_client
    if _sentry_client is None:
        _sentry_client = raven.Client(
            processors=("inbox.logging.sentry.TruncatingProcessor",)
        )
    return _sentry_client


class TruncatingProcessor(raven.processors.Processor):
    """Truncates the exception value string"""

    # Note(emfree): raven.processors.Processor provides a filter_stacktrace
    # method to implement, but won't actually call it correctly. We can
    # simplify this if it gets fixed upstream.
    def process(self, data, **kwargs):
        if "exception" not in data:
            return data
        if "values" not in data["exception"]:
            return data
        for item in data["exception"]["values"]:
            item["value"] = item["value"][:MAX_EXCEPTION_LENGTH]
        return data


def sentry_alert(*args, **kwargs):
    if sentry_exceptions_enabled():
        try:
            get_sentry_client().captureException(*args, **kwargs)
        except URLError:
            logger = get_logger()
            logger.error("Error occured when sending exception to Sentry")


def log_uncaught_errors(logger=None, **kwargs):
    """
    Helper to log uncaught exceptions.

    All additional kwargs supplied will be sent to Sentry as extra data.

    Parameters
    ----------
    logger: structlog.BoundLogger, optional
        The logging object to write to.

    """
    logger = logger or get_logger()
    kwargs.update(create_error_log_context(sys.exc_info()))
    logger.error("Uncaught error", **kwargs)
    sentry_alert(tags=kwargs)
