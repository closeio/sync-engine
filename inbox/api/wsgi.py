import sys

from gunicorn.workers.gthread import ThreadWorker

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


__all__ = ["NylasWSGIWorker"]
