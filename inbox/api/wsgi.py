import gevent.monkey

gevent.monkey.patch_all()

import errno
import socket
import sys

import gunicorn.glogging
from gevent.pywsgi import WSGIHandler, WSGIServer
from gunicorn.workers.ggevent import GeventWorker

from inbox.instrumentation import Tracer
from inbox.logging import configure_logging, get_logger

log = get_logger()


class NylasWSGIHandler(WSGIHandler):
    """Custom WSGI handler class to customize request logging. Based on
    gunicorn.workers.ggevent.PyWSGIHandler."""

    def log_request(self):
        # gevent.pywsgi tries to call log.write(), but Python logger objects
        # implement log.debug(), log.info(), etc., so we need to monkey-patch
        # log_request(). See
        # http://stackoverflow.com/questions/9444405/gunicorn-and-websockets
        log = self.server.log
        length = self.response_length
        if self.time_finish:
            request_time = round(self.time_finish - self.time_start, 6)
        if isinstance(self.client_address, tuple):
            client_address = self.client_address[0]
        else:
            client_address = self.client_address

        # client_address is '' when requests are forwarded from nginx via
        # Unix socket. In that case, replace with a meaningful value
        if client_address == "":
            client_address = self.headers.get("X-Forwarded-For")
        status = getattr(self, "code", None)
        requestline = getattr(self, "requestline", None)
        method = getattr(self, "command", None)

        # To use this, generate a unique ID at your termination proxy (e.g.
        # haproxy or nginx) and set it as a header on the request
        request_uid = self.headers.get("X-Unique-Id")

        additional_context = self.environ.get("log_context") or {}

        # Since not all users may implement this, don't log null values
        if request_uid is not None:
            additional_context["request_uid"] = request_uid

        # pywsgi negates the status code if there was a socket error
        # (https://github.com/gevent/gevent/blob/master/src/gevent/pywsgi.py#L706)
        # To make the logs clearer, use the positive status code and include
        # the socket error
        if status and status < 0:
            additional_context["error"] = "socket.error"
            additional_context["error_message"] = getattr(self, "status", None)
            status = abs(status)

        log.info(
            "request handled",
            response_bytes=length,
            request_time=request_time,
            remote_addr=client_address,
            http_status=status,
            http_request=requestline,
            request_method=method,
            **additional_context
        )

    def get_environ(self):
        env = super().get_environ()
        env["gunicorn.sock"] = self.socket
        env["RAW_URI"] = self.path
        return env

    def handle_error(self, type, value, tb):
        # Suppress tracebacks when e.g. a client disconnects from the streaming
        # API.
        if (
            issubclass(type, socket.error)
            and value.args[0] == errno.EPIPE
            and self.response_length
        ):
            self.server.log.info("Socket error", exc=value)
            self.close_connection = True
        else:
            super().handle_error(type, value, tb)


class NylasWSGIWorker(GeventWorker):
    """Custom worker class for gunicorn. Based on
    gunicorn.workers.ggevent.GeventPyWSGIWorker."""

    server_class = WSGIServer
    wsgi_handler = NylasWSGIHandler

    def init_process(self):
        print("Python", sys.version, file=sys.stderr)

        if MAX_BLOCKING_TIME:
            self.tracer = Tracer(max_blocking_time=MAX_BLOCKING_TIME)
            self.tracer.start()
        super().init_process()


class NylasGunicornLogger(gunicorn.glogging.Logger):
    def __init__(self, cfg):
        gunicorn.glogging.Logger.__init__(self, cfg)
        configure_logging(log_level=LOGLEVEL)
        self.error_log = log


from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar

MAX_BLOCKING_TIME = config.get("MAX_BLOCKING_TIME", 1.0)
LOGLEVEL = config.get("LOGLEVEL", 10)

# legacy names for backcompat
InboxWSGIWorker = NylasWSGIWorker
GunicornLogger = NylasGunicornLogger


class RollbarWSGIWorker(NylasWSGIWorker):
    def init_process(self):
        maybe_enable_rollbar()

        super().init_process()


__all__ = [
    "NylasWSGIHandler",
    "NylasWSGIWorker",
    "NylasGunicornLogger",
    "InboxWSGIWorker",
    "RollbarWSGIWorker",
    "GunicornLogger",
]
