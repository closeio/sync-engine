import logging
import sys

import json_log_formatter
from gunicorn.workers.gthread import ThreadWorker

from inbox.instrumentation import Tracer
from inbox.logging import configure_logging, get_logger

log = get_logger()


class NylasWSGIWorker(ThreadWorker):
    """Custom worker class for gunicorn. Based on
    gunicorn.workers.ggevent.GeventPyWSGIWorker.
    """

    def init_process(self):
        print("Python", sys.version, file=sys.stderr)

        maybe_enable_rollbar()

        configure_logging(log_level=LOGLEVEL)

        if config.get("USE_GEVENT", True) and MAX_BLOCKING_TIME:
            self.tracer = Tracer(max_blocking_time=MAX_BLOCKING_TIME)
            self.tracer.start()
        super().init_process()


from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar

MAX_BLOCKING_TIME = config.get("MAX_BLOCKING_TIME", 1.0)
LOGLEVEL = config.get("LOGLEVEL", 10)


class JsonRequestFormatter(json_log_formatter.JSONFormatter):
    """Custom JSON log formatter for gunicorn access logs.

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

        url = record.args["U"]
        if record.args["q"]:
            url += f"?{record.args['q']}"

        method = record.args["m"]
        log_context = record.args.get("{log_context}e", {})

        return dict(
            response_bytes=record.args["B"],
            request_time=float(record.args["L"]),
            remote_address=record.args["h"],
            http_status=record.args["s"],
            http_request=f"{method} {url}",
            request_method=method,
            **log_context,
        )


class JsonErrorFormatter(json_log_formatter.JSONFormatter):
    """Custom JSON log formatter for gunicorn error logs.

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
