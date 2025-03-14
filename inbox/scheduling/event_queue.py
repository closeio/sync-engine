import json
from typing import Any

from redis import StrictRedis

from inbox.config import config
from inbox.logging import get_logger

log = get_logger()

SOCKET_CONNECT_TIMEOUT = 5
SOCKET_TIMEOUT = 30


def _get_redis_client(  # type: ignore[no-untyped-def]
    host=None, port: int = 6379, db: int = 1
):
    return StrictRedis(
        host=host,
        port=port,
        db=db,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_timeout=SOCKET_TIMEOUT,
    )


class EventQueue:
    """
    Simple queue that clients can listen to and wait to be notified of some
    event that they're interested in.
    """

    def __init__(
        self,
        queue_name: str,
        redis: StrictRedis | None = None,  # type: ignore[type-arg]
    ) -> None:
        self.redis = redis
        if self.redis is None:
            redis_host = config["EVENT_QUEUE_REDIS_HOSTNAME"]
            redis_db = config["EVENT_QUEUE_REDIS_DB"]
            self.redis = _get_redis_client(host=redis_host, db=redis_db)
        self.queue_name = queue_name

    def receive_event(self, timeout: int | None = 0) -> dict[str, Any] | None:
        """
        Receive single event from the queue.

        * When timeout is set to 0:
            Block infinitely until receiving an event and return event dictionary.
        * When timeout is positive integer:
            Return None if the queue was still empty after timeout seconds.
            Return event dictionary if the queue was not empty.
        * When timeout is set to None:
            Return None immediately if the queue was empty.
            Return event dictionary if the queue was not empty.
        """
        assert self.redis

        if timeout is None:
            lpop_result: bytes | None = self.redis.lpop(self.queue_name)
            if lpop_result is None:
                return None

            queue_name: str = self.queue_name
            event_data = lpop_result
        else:
            blpop_result: tuple[bytes, bytes] | None = self.redis.blpop(
                [self.queue_name], timeout=timeout
            )
            if blpop_result is None:
                return None

            (blpop_queue_name, event_data) = blpop_result
            queue_name = blpop_queue_name.decode("utf-8")

        try:
            event = json.loads(event_data)
            event["queue_name"] = queue_name
            return event
        except Exception as e:
            log.error(
                "Failed to load event data from queue",
                error=e,
                event_data=event_data,
            )
            return None

    def send_event(self, event_data: dict[str, Any]) -> None:
        assert self.redis

        event_data.pop("queue_name", None)
        self.redis.rpush(self.queue_name, json.dumps(event_data))


class EventQueueGroup:
    """Group of queues that can all be simultaneously watched for new events."""

    def __init__(self, queues: list[EventQueue]) -> None:
        self.queues = queues
        self.redis = None
        if len(self.queues) > 0:
            self.redis = self.queues[0].redis

    def receive_event(self, timeout: int = 0) -> dict[str, Any] | None:
        assert self.redis

        result: tuple[bytes, bytes] | None = self.redis.blpop(
            [q.queue_name for q in self.queues], timeout=timeout
        )
        if result is None:
            return None
        blpop_queue_name, event_data = result
        queue_name = blpop_queue_name.decode("utf-8")
        try:
            event = json.loads(event_data)
            event["queue_name"] = queue_name
            return event
        except Exception as e:
            log.error(
                "Failed to load event data from queue",
                error=e,
                event_data=event_data,
            )
            return None
