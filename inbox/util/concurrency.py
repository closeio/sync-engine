import concurrent.futures
import datetime
import functools
import random
import socket
import ssl
import sys
import time
from collections.abc import Callable
from typing import Any, Iterable, TypeVar

from MySQLdb import _mysql_exceptions
from redis import TimeoutError
from sqlalchemy.exc import StatementError

from inbox import interruptible_threading
from inbox.error_handling import log_uncaught_errors
from inbox.logging import create_error_log_context, get_logger
from inbox.models import Account
from inbox.models.session import session_scope

log = get_logger()

BACKOFF_DELAY = 30  # seconds to wait before retrying after a failure

TRANSIENT_NETWORK_ERRS = (socket.timeout, TimeoutError, socket.error, ssl.SSLError)

TRANSIENT_MYSQL_MESSAGES = (
    "try restarting transaction",
    "Too many connections",
    "Lost connection to MySQL server",
    "MySQL server has gone away",
    "Can't connect to MySQL server",
    "Max connect timeout reached",
)


def retry(
    func,
    retry_classes=None,
    fail_classes=None,
    exc_callback=None,
    backoff_delay=BACKOFF_DELAY,
):
    """
    Executes the callable func, retrying on uncaught exceptions matching the
    class filters.

    Arguments
    ---------
    func : function
    exc_callback : function, optional
        Function to execute if an exception is raised within func. The exception
        is passed as the first argument. (e.g., log something)
    retry_classes: list of Exception subclasses, optional
        Configures what to retry on. If specified, func is retried only if one
        of these exceptions is raised. Default is to retry on all exceptions.
    fail_classes: list of Exception subclasses, optional
        Configures what not to retry on. If specified, func is /not/ retried if
        one of these exceptions is raised.
    """
    if fail_classes and retry_classes and set(fail_classes).intersection(retry_classes):
        raise ValueError("Can't include exception classes in both fail_on and retry_on")

    def should_retry_on(exc):
        if fail_classes and isinstance(exc, tuple(fail_classes)):
            return False
        if retry_classes and not isinstance(exc, tuple(retry_classes)):
            return False
        return True

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            # Note that InterruptibleThreadExit isn't actually a subclass of Exception
            # (It's a subclass of BaseException) so it won't be caught here.
            # This is also considered to be a successful execution
            # (somebody intentionally killed the thread).
            except Exception as e:
                if not should_retry_on(e):
                    raise
                if exc_callback is not None:
                    exc_callback(e)

            # Sleep a bit so that we don't poll too quickly and re-encounter
            # the error. Also add a random delay to prevent herding effects.
            interruptible_threading.sleep(backoff_delay + int(random.uniform(1, 10)))

    return wrapped


def retry_with_logging(
    func,
    logger=None,
    retry_classes=None,
    fail_classes=None,
    account_id=None,
    provider=None,
    backoff_delay=BACKOFF_DELAY,
):
    # Sharing the network_errs counter between invocations of callback by
    # placing it inside an array:
    # http://stackoverflow.com/questions/7935966/python-overwriting-variables-in-nested-functions
    occurrences = [0]

    def callback(e):
        is_transient = isinstance(e, TRANSIENT_NETWORK_ERRS)
        mysql_error = None

        log = logger or get_logger()

        if isinstance(e, _mysql_exceptions.OperationalError):
            mysql_error = e
        elif isinstance(e, StatementError) and isinstance(
            e.orig, _mysql_exceptions.OperationalError
        ):
            mysql_error = e.orig

        if mysql_error and mysql_error.args and isinstance(mysql_error.args[0], str):
            for msg in TRANSIENT_MYSQL_MESSAGES:
                if msg in mysql_error.args[0]:
                    is_transient = True

        if is_transient:
            occurrences[0] += 1
            if occurrences[0] < 20:
                return
        else:
            occurrences[0] = 1

        if account_id:
            try:
                with session_scope(account_id) as db_session:
                    account = db_session.query(Account).get(account_id)
                    sync_error = account.sync_error
                    if not sync_error or isinstance(sync_error, str):
                        account.update_sync_error(e)
                        db_session.commit()
            except Exception:
                log.error(
                    "Error saving sync_error to account object",
                    account_id=account_id,
                    **create_error_log_context(sys.exc_info())
                )

        log_uncaught_errors(
            logger, account_id=account_id, provider=provider, occurrences=occurrences[0]
        )

    return retry(
        func,
        exc_callback=callback,
        retry_classes=retry_classes,
        fail_classes=fail_classes,
        backoff_delay=backoff_delay,
    )()


IterableItemT = TypeVar("IterableItemT")


DEFAULT_SWITCH_PERIOD = datetime.timedelta(seconds=1)


def iterate_and_periodically_check_interrupted(
    iterable: Iterable[IterableItemT],
    *,
    switch_period: datetime.timedelta = DEFAULT_SWITCH_PERIOD
) -> Iterable[IterableItemT]:
    """
    Given an iterable, yield each item, and periodically check if the
    thread has been interrupted.

    Use this with CPU-bound loops to make sure that the thread can be interrupted.
    Otherwise the thread might not get killed in sensible time.
    """
    last_sleep_time = time.monotonic()
    for item in iterable:
        if time.monotonic() - last_sleep_time >= switch_period.total_seconds():
            interruptible_threading.check_interrupted()
            last_sleep_time = time.monotonic()

        yield item


def kill_all(
    interruptible_threads: "Iterable[interruptible_threading.InterruptibleThread]",
    *,
    block: bool = True
) -> None:
    if not interruptible_threads:
        return

    for thread in interruptible_threads:
        thread.kill(block=False)

    while block and not all(thread.ready() for thread in interruptible_threads):
        time.sleep(0.2)


def run_in_parallel(functions: "list[Callable[[], Any]]") -> None:
    if not functions:
        return

    with concurrent.futures.ThreadPoolExecutor(len(functions)) as executor:
        for function in functions:
            executor.submit(function)


def introduce_jitter(value: float, ratio: float = 0.3) -> float:
    return value + value * ratio * random.uniform(-1, 1)
