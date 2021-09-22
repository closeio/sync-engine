import importlib
import os

import gevent.threading
from nylas.logging import get_logger

log = get_logger()

_lock = gevent.threading.Lock()
_already_run = set()


def run_once(hookspec):
    if not hookspec:
        return

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


def run_startup():
    return run_once(os.environ.get("SYNC_ENGINE_STARTUP_HOOK"))
