"""
Logging configuration.

Mostly based off http://www.structlog.org/en/16.1.0/standard-library.html.

"""

import logging
import os
import sys
import threading
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from pythonjsonlogger.jsonlogger import JsonFormatter

# TODO: Stop using this, `structlog.threadlocal` is deprecated
from structlog.threadlocal import wrap_dict

from inbox.config import is_debug


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


def _add_env_to_event_dict(
    logger: Any, name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    event_dict["env"] = os.environ.get("NYLAS_ENV")
    return event_dict


def _add_thread_id_to_event_dict(
    logger: Any, name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    event_dict["thread_id"] = hex(threading.get_native_id())
    return event_dict


def _get_structlog_processors() -> list[structlog.typing.Processor]:
    processors: list[structlog.typing.Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_env_to_event_dict,
        _add_thread_id_to_event_dict,
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
    wrapper_class=structlog.stdlib.BoundLogger,
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
