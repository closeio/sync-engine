"""
Logging configuration.

Mostly based off http://www.structlog.org/en/16.1.0/standard-library.html.

"""

import contextlib
import logging
import os
import sys
import threading
import traceback
from types import TracebackType
from typing import Any

import structlog
from pythonjsonlogger.jsonlogger import JsonFormatter

# TODO: Stop using this, `structlog.threadlocal` is deprecated
from structlog.threadlocal import wrap_dict

from inbox.config import is_debug

MAX_EXCEPTION_LENGTH = 10000


def find_first_app_frame_and_name(  # type: ignore[no-untyped-def]  # noqa: ANN201
    ignores=None,
):
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
        f is not None  # type: ignore[redundant-expr]
        and f.f_back is not None
        and (name is None or any(name.startswith(i) for i in ignores))
    ):
        f = f.f_back
        name = f.f_globals.get("__name__")
    return f, name


def _record_module(logger, name, event_dict):  # type: ignore[no-untyped-def]
    """
    Processor that records the module and line where the logging call was
    invoked.
    """
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


def safe_format_exception(  # type: ignore[no-untyped-def]  # noqa: ANN201
    etype, value, tb, limit=None
):
    """
    Similar to structlog._format_exception, but truncate the exception part.
    This is because SQLAlchemy exceptions can sometimes have ludicrously large
    exception strings.
    """
    if tb:
        list = ["Traceback (most recent call last):\n"]  # noqa: A001
        list = list + traceback.format_tb(tb, limit)  # noqa: A001
    elif etype and value:
        list = []  # noqa: A001
    else:
        return None
    exc_only = traceback.format_exception_only(etype, value)
    # Normally exc_only is a list containing a single string.  For syntax
    # errors it may contain multiple elements, but we don't really need to
    # worry about that here.
    exc_only[0] = exc_only[0][:MAX_EXCEPTION_LENGTH]
    list = list + exc_only  # noqa: A001
    return "".join(list)


class BoundLogger(structlog.stdlib.BoundLogger):
    """BoundLogger which always adds thread_id and env to positional args"""

    def _proxy_to_logger(  # type: ignore[no-untyped-def, override]
        self, method_name, event, *event_args, **event_kw
    ):
        event_kw["thread_id"] = hex(threading.get_native_id())

        # 'prod', 'staging', 'dev' ...
        env = os.environ.get("NYLAS_ENV")
        if env is not None:
            event_kw["env"] = env

        return super()._proxy_to_logger(
            method_name, event, *event_args, **event_kw
        )


def _get_structlog_processors() -> list[structlog.typing.Processor]:
    processors: list[structlog.typing.Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if is_debug():
        # This breaks Sentry reporting - errors still show up,
        # but with broken stack traces and potentially missing
        # metadata. We don't mind that much, because the debug
        # mode is only enabled in dev environments.
        processors.append(structlog.processors.format_exc_info)
        processors.append(
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
        )
    else:
        processors.append(structlog.stdlib.render_to_log_kwargs)

    return processors


structlog.configure(
    processors=_get_structlog_processors(),
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


def configure_logging(log_level=None) -> None:  # type: ignore[no-untyped-def]
    """
    Idempotently configure logging.

    Infers options based on whether or not the output is a TTY.

    Sets the root log level to INFO if not otherwise specified.
    """
    # Set loglevel INFO if not otherwise specified. (We don't set a
    # default in the case that you're loading a value from a config and
    # may be passing in None explicitly if it's not defined.)
    if log_level is None:
        log_level = logging.INFO
    log_level = LOG_LEVELS.get(log_level, log_level)

    nylas_handler = logging.StreamHandler(sys.stdout)
    nylas_handler.setFormatter(
        logging.Formatter() if is_debug() else JsonFormatter()
    )
    nylas_handler._nylas = True  # type: ignore[attr-defined]

    # Configure the root logger.
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        # If the handler was previously installed, remove it so that repeated
        # calls to configure_logging() are idempotent.
        if getattr(handler, "_nylas", False):
            root_logger.removeHandler(handler)
    root_logger.addHandler(nylas_handler)
    root_logger.setLevel(log_level)

    imapclient_logger = logging.getLogger("imapclient")
    imapclient_logger.setLevel(logging.ERROR)
    urllib_logger = logging.getLogger("urllib3.connectionpool")
    urllib_logger.setLevel(logging.ERROR)
    sqlalchemy_pool_logger = logging.getLogger("inbox.sqlalchemy_ext")
    sqlalchemy_pool_logger.setLevel(logging.ERROR)


MAX_ERROR_MESSAGE_LENGTH = 1024


def create_error_log_context(
    exc_info: tuple[type | None, Any, TracebackType | None]
) -> dict[str, Any]:
    exc_type, exc_value, exc_tb = exc_info
    out: dict[str, Any] = {}

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
