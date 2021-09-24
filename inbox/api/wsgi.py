import nylas.api.wsgi
from nylas.api.wsgi import NylasGunicornLogger, NylasWSGIHandler, NylasWSGIWorker

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar

nylas.api.wsgi.MAX_BLOCKING_TIME = config.get(
    "MAX_BLOCKING_TIME", nylas.api.wsgi.MAX_BLOCKING_TIME
)
nylas.api.wsgi.LOGLEVEL = config.get("LOGLEVEL", nylas.api.wsgi.LOGLEVEL)

# legacy names for backcompat
InboxWSGIWorker = NylasWSGIWorker
GunicornLogger = NylasGunicornLogger


class RollbarWSGIWorker(NylasWSGIWorker):
    def init_process(self):
        maybe_enable_rollbar()

        super(RollbarWSGIWorker, self).init_process()


__all__ = [
    "NylasWSGIHandler",
    "NylasWSGIWorker",
    "NylasGunicornLogger",
    "InboxWSGIWorker",
    "RollbarWSGIWorker",
    "GunicornLogger",
]
