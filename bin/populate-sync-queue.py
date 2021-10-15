#!/usr/bin/env python
"""
Continuously populates the queue of accounts to be synced. Run one of these per
deployment, or several for availability. (It is safe to run multiple
concurrently.) Currently, this script populates queues for all configured
zones. It could be modified so that different zones are populated
independently, if needed.
"""
import gevent
import gevent.monkey

gevent.monkey.patch_all()
from setproctitle import setproctitle

from inbox.config import config
from inbox.error_handling import maybe_enable_rollbar
from inbox.logging import configure_logging
from inbox.scheduling.queue import QueuePopulator

configure_logging()


def main():
    maybe_enable_rollbar()

    setproctitle("scheduler")
    zones = {h.get("ZONE") for h in config["DATABASE_HOSTS"]}
    threads = []
    for zone in zones:
        populator = QueuePopulator(zone)
        threads.append(gevent.spawn(populator.run))

    gevent.joinall(threads)


if __name__ == "__main__":
    main()
