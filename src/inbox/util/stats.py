import statsd  # type: ignore[import-untyped]

from inbox.config import config


def get_statsd_client():  # type: ignore[no-untyped-def]  # noqa: ANN201
    return statsd.StatsClient(
        str(config.get("STATSD_HOST", "localhost")),
        config.get("STATSD_PORT", 8125),
        prefix=config.get("STATSD_PREFIX", "stats"),
    )


statsd_client = get_statsd_client()
