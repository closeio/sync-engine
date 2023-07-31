"""
Logging configuration.

Mostly based off http://www.structlog.org/en/16.1.0/standard-library.html.

"""


import contextlib
import logging
import os
import re
import sys
import traceback
from types import TracebackType
from typing import Any, Dict, Optional, Tuple, Type

import colorlog
import gevent
import structlog
from structlog.threadlocal import wrap_dict

MAX_EXCEPTION_LENGTH = 10000


def find_first_app_frame_and_name(ignores=None):
    """
    Remove ignorable calls and return the relevant app frame. Borrowed from
    structlog, but fixes an issue when the stack includes an 'exec' statement
    or similar (f.f_globals doesn't have a '__name__' key in that case).

    Parameters
    ----------
    ignores: list, optional
        Additional names with which the first frame must not start.

    Returns
    -------
    tuple of (frame, name)
    """
    ignores = ignores or []
    f = sys._getframe()
    name = f.f_globals.get("__name__")
    while (
        f is not None
        and f.f_back is not None
        and (name is None or any(name.startswith(i) for i in ignores))
    ):
        f = f.f_back
        name = f.f_globals.get("__name__")
    return f, name


def _record_level(logger, name, event_dict):
    """Processor that records the log level ('info', 'warning', etc.) in the
    structlog event dictionary."""
    event_dict["level"] = name
    return event_dict


def _record_module(logger, name, event_dict):
    """Processor that records the module and line where the logging call was
    invoked."""
    f, name = find_first_app_frame_and_name(
        ignores=[
            "structlog",
            "inbox.logging",
            "inbox.sqlalchemy_ext.util",
            "inbox.models.session",
            "sqlalchemy",
            "gunicorn.glogging",
        ]
    )
    event_dict["module"] = f"{name}:{f.f_lineno}"
    return event_dict


def safe_format_exception(etype, value, tb, limit=None):
    """Similar to structlog._format_exception, but truncate the exception part.
    This is because SQLAlchemy exceptions can sometimes have ludicrously large
    exception strings."""
    if tb:
        list = ["Traceback (most recent call last):\n"]
        list = list + traceback.format_tb(tb, limit)
    elif etype and value:
        list = []
    else:
        return None
    exc_only = traceback.format_exception_only(etype, value)
    # Normally exc_only is a list containing a single string.  For syntax
    # errors it may contain multiple elements, but we don't really need to
    # worry about that here.
    exc_only[0] = exc_only[0][:MAX_EXCEPTION_LENGTH]
    list = list + exc_only
    return "".join(list)


def _is_log_in_same_fn_scope(exc_tb):
    """

    exc_info returns exception data according to the following spec:

        If the current stack frame is not handling an exception, the
        information is taken from the calling stack frame, or its caller,
        and so on until a stack frame is found that is handling an
        exception.  Here, “handling an exception” is defined as
        “executing or having executed an except clause.” For any stack
        frame, only information about the most recently handled exception
        is accessible.

    The default behavior we want, however, is only logging exceptions if
    the user is inside or immediately next to the frame to log. This
    detects of the log statement and the exception share the same
    function.
    """
    cur_stack = traceback.extract_stack()
    calling_fn = None
    for _, _, fn_name, code in reversed(cur_stack):
        if code and re.search(r"log\.(error|exception)", code):
            calling_fn = fn_name
            break

    exc_tb_stack = traceback.extract_tb(exc_tb)
    for _, _, fn_name, _ in exc_tb_stack:
        if fn_name == calling_fn:
            return True
    return False


def _get_exc_info_if_in_scope():
    exc_info = sys.exc_info()
    if _is_log_in_same_fn_scope(exc_info[2]):
        return exc_info
    return (None, None, None)


def _safe_exc_info_renderer(_, __, event_dict):
    """Processor that formats exception info safely."""
    error = event_dict.pop("error", None)
    exc_info = event_dict.pop("exc_info", None)
    include_exception = event_dict.pop("include_exception", None)

    if error:
        # If an `error` is passed, merge that into exc_info
        if isinstance(error, Exception):
            # If that error is an Exception, we just need to grab the
            # traceback (since exceptions don't include tracebacks in
            # Python)
            __, __, exc_tb = _get_exc_info_if_in_scope()
            exc_info = (type(error), error, exc_tb)
        else:
            # Sometimes people pass stings and ints as the error. We
            # normalize these to be an error object's message, or if
            # we're not in an error frame, just an `error_message`
            exc_info = _get_exc_info_if_in_scope()
            if exc_info[1]:
                exc_info[1].message = error
            else:
                event_dict["error_message"] = error
                exc_info = (None, None, None)
    elif exc_info is False or include_exception is False:
        # This means someone explicitly asked us to not include any error
        # info. We force it to be none.
        exc_info = (None, None, None)
    elif (
        exc_info is None
        and include_exception is None
        and event_dict.get("level", None) == "error"
    ):
        # This means people called `log.error` or `log.exception` without
        # passing any error arguments. In this case attempt to
        # optimistically grab the exception (if it's available) and add
        # it to our logging.
        exc_info = _get_exc_info_if_in_scope()
    elif exc_info is True or include_exception is True:
        # This means we explicitly asked to always grab the error
        # traceback if it's available. We assume that the caller knows
        # what their doing and is aware that tracebacks can be fetched
        # from recently handled exceptions
        exc_info = sys.exc_info()
    elif isinstance(exc_info, tuple):
        # This supports passing the value of `sys.exc_info()` to the
        # `exc_info` argument. If that's the case, we take it verbatim.
        pass
    else:
        # All other cases of normal log types that didn't pass any any
        # exception info.
        exc_info = (None, None, None)

    event_dict.update(create_error_log_context(exc_info))
    return event_dict


def _safe_encoding_renderer(_, __, event_dict):
    """Processor that converts all strings to unicode.
       Note that we ignore conversion errors.
    """
    for key in event_dict:
        entry = event_dict[key]
        if isinstance(entry, bytes):
            event_dict[key] = entry.decode(encoding="utf-8", errors="replace")

    return event_dict


class BoundLogger(structlog.stdlib.BoundLogger):
    """ BoundLogger which always adds greenlet_id and env to positional args """

    def _proxy_to_logger(self, method_name, event, *event_args, **event_kw):
        event_kw["greenlet_id"] = id(gevent.getcurrent())

        # 'prod', 'staging', 'dev' ...
        env = os.environ.get("NYLAS_ENV")
        if env is not None:
            event_kw["env"] = env

        return super()._proxy_to_logger(method_name, event, *event_args, **event_kw)


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _record_level,
        _safe_exc_info_renderer,
        _safe_encoding_renderer,
        _record_module,
        structlog.processors.JSONRenderer(),
    ],
    context_class=wrap_dict(dict),
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=BoundLogger,
    cache_logger_on_first_use=True,
)
get_logger = structlog.get_logger

# Convenience map to let users set level with a string
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def json_excepthook(etype, value, tb):
    log = get_logger()
    log.error(**create_error_log_context((etype, value, tb)))


class ConditionalFormatter(logging.Formatter):
    def format(self, record):
        if (
            record.name == "__main__"
            or record.name == "inbox"
            or record.name.startswith("inbox.")
            or record.name == "gunicorn"
            or record.name.startswith("gunicorn.")
            or record.name == "gevent.pywsgi"
            or record.name == "werkzeug"
        ):
            style = "%(message)s"
        else:
            style = "%(name)s - %(levelname)s: %(message)s"

        self._style._fmt = style

        return super().format(record)


def configure_logging(log_level=None):
    """ Idempotently configure logging.

    Infers options based on whether or not the output is a TTY.

    Sets the root log level to INFO if not otherwise specified.

    Overrides top-level exceptions to also print as JSON, rather than
    printing to stderr as plaintext.

    """
    sys.excepthook = json_excepthook

    # Set loglevel INFO if not otherwise specified. (We don't set a
    # default in the case that you're loading a value from a config and
    # may be passing in None explicitly if it's not defined.)
    if log_level is None:
        log_level = logging.INFO
    log_level = LOG_LEVELS.get(log_level, log_level)

    tty_handler = logging.StreamHandler(sys.stdout)
    if sys.stdout.isatty():
        # Use a more human-friendly format.
        formatter = colorlog.ColoredFormatter(
            "%(log_color)s[%(levelname)s]%(reset)s %(message)s",
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red",
            },
        )
    else:
        formatter = ConditionalFormatter()
    tty_handler.setFormatter(formatter)
    tty_handler._nylas = True  # type: ignore

    # Configure the root logger.
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        # If the handler was previously installed, remove it so that repeated
        # calls to configure_logging() are idempotent.
        if getattr(handler, "_nylas", False):
            root_logger.removeHandler(handler)
    root_logger.addHandler(tty_handler)
    root_logger.setLevel(log_level)

    imapclient_logger = logging.getLogger("imapclient")
    imapclient_logger.setLevel(logging.ERROR)
    urllib_logger = logging.getLogger("urllib3.connectionpool")
    urllib_logger.setLevel(logging.ERROR)
    sqlalchemy_pool_logger = logging.getLogger("inbox.sqlalchemy_ext")
    sqlalchemy_pool_logger.setLevel(logging.ERROR)


MAX_ERROR_MESSAGE_LENGTH = 1024


def create_error_log_context(
    exc_info: Tuple[Optional[Type], Any, Optional[TracebackType]]
) -> Dict[str, Any]:
    exc_type, exc_value, exc_tb = exc_info
    out: Dict[str, Any] = {}

    if exc_type is None and exc_value is None and exc_tb is None:
        return out

    # Break down the info as much as Python gives us, for easier aggregation of
    # similar error types.
    if exc_type and hasattr(exc_type, "__name__"):
        out["error_name"] = exc_type.__name__

    if hasattr(exc_value, "code"):
        out["error_code"] = exc_value.code

    if hasattr(exc_value, "args") and hasattr(exc_value.args, "__getitem__"):
        error_message = None
        with contextlib.suppress(IndexError):
            error_message = exc_value.args[0]

        if (
            isinstance(error_message, str)
            and len(error_message) > MAX_ERROR_MESSAGE_LENGTH
        ):
            error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH] + "..."

        if error_message:
            out["error_message"] = error_message

    with contextlib.suppress(Exception):
        if exc_tb:
            tb = safe_format_exception(exc_type, exc_value, exc_tb)
            if tb:
                out["error_traceback"] = tb

    return out
