import importlib
import os

import gevent.threading
from nylas.logging import get_logger

log = get_logger()

_lock = gevent.threading.Lock()
_already_run = set()


def run_once(hookspec):
    """Execute given hookspec once per process"""
    with _lock:
        if hookspec in _already_run:
            log.info("Not running hook {}, it was already run".format(hookspec))
            return

        module_name, function_name = hookspec.rsplit(".", 1)
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)

        log.info("Running hook {}".format(hookspec))

        function()

        _already_run.add(hookspec)


def maybe_run_startup():
    """Check if startup hook env var is set and run it"""
    startup_hook = os.environ.get("SYNC_ENGINE_STARTUP_HOOK")
    if not startup_hook:
        return

    return run_once(startup_hook)
