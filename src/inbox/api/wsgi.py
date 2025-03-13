import logging
import sys

import json_log_formatter  # type: ignore[import-untyped]
from gunicorn.workers.gthread import (  # type: ignore[import-untyped]
    ThreadWorker,
)

from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging, get_logger

log = get_logger()


class NylasWSGIWorker(ThreadWorker):
    """Custom worker class for gunicorn."""

    def init_process(self) -> None:
        print("Python", sys.version, file=sys.stderr)  # noqa: T201

        maybe_enable_rollbar()

        configure_logging(log_level=LOGLEVEL)

        super().init_process()


from inbox.config import config  # noqa: E402

LOGLEVEL = config.get("LOGLEVEL", 10)


class JsonRequestFormatter(json_log_formatter.JSONFormatter):
    """
    Custom JSON log formatter for gunicorn access logs.

    Adapted from https://til.codeinthehole.com/posts/how-to-get-gunicorn-to-log-as-json/
    """

    def json_record(
        self,
        message: str,
        extra: "dict[str, str | int | float]",
        record: logging.LogRecord,
    ) -> "dict[str, str | int | float]":
        # Convert the log record to a JSON object.
        # See https://docs.gunicorn.org/en/stable/settings.html#access-log-format

        url = record.args["U"]  # type: ignore[call-overload, index]
        if record.args["q"]:  # type: ignore[call-overload, index]
            url += (  # type: ignore[operator]
                f"?{record.args['q']}"  # type: ignore[call-overload, index]
            )

        method = record.args["m"]  # type: ignore[call-overload, index]
        log_context = record.args.get(  # type: ignore[union-attr]
            "{log_context}e", {}
        )

        return dict(
            response_bytes=record.args[  # type: ignore[call-overload, dict-item, index]
                "B"
            ],
            request_time=float(
                record.args["L"]  # type: ignore[arg-type, call-overload, index]
            ),
            remote_address=record.args[  # type: ignore[call-overload, dict-item, index]
                "h"
            ],
            http_status=record.args[  # type: ignore[call-overload, dict-item, index]
                "s"
            ],
            http_request=f"{method} {url}",
            request_method=method,  # type: ignore[dict-item]
            **log_context,  # type: ignore[dict-item]
        )


class JsonErrorFormatter(json_log_formatter.JSONFormatter):
    """
    Custom JSON log formatter for gunicorn error logs.

    Adapted from https://til.codeinthehole.com/posts/how-to-get-gunicorn-to-log-as-json/
    """

    def json_record(
        self,
        message: str,
        extra: "dict[str, str | int | float]",
        record: logging.LogRecord,
    ) -> "dict[str, str | int | float]":
        payload: "dict[str, str | int | float]" = super().json_record(
            message, extra, record
        )
        payload["level"] = record.levelname
        return payload


__all__ = ["NylasWSGIWorker"]
