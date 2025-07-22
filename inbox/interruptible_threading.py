"""
Minimal interruptible version of stdlib threading.

The API closely mimicks gevent's API for greenlets, it's almost a drop-in
replacement. It was coined while porting this project from gevent to
threading.

The module provides an `InterruptibleThread` class that can be used to run
interruptible code in a separate thread. The thread can be interrupted by
calling the `kill` method. The thread must collaborate and periodically check
if it is interrupted by calling the `check_interrupted` function unlike with
gevent where the greenlet is interrupted automatically when using gevent-native
APIs or monkey-patched version of stdlib.

The module also provides a few utility functions that can be used to write
interruptible code e.g. interruptible version of `time.sleep`, `queue.Queue.get`.

For simple examples see tests in tests/test_interruptible_threading.py.
"""

import contextlib
import dataclasses
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Concatenate, TypeVar

from typing_extensions import ParamSpec


class InterruptibleThreadExit(BaseException):
    """
    Exception raised when the thread is interrupted.

    This exception is raised after the `kill` method is called on the
    `InterruptibleThread` instance next time the thread checks if it's
    interrupted. It is then caught in `InterruptibleThread.run` and ignored
    since it means a successful interruption of the thread.

    This mimicks exactly the behavior of gevent's `GreenletExit` exception.
    Note that this exception is a subclass of `BaseException` and not
    `Exception` because it's not suppoed to be caught by `except Exception` block,
    just like https://greenlet.readthedocs.io/en/latest/api.html#greenlet.GreenletExit.
    """


@dataclasses.dataclass
class _InterruptibleThreadTarget:
    """
    Convenience class to store target function and its positional and keyword
    arguments.
    """

    target: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]

    def __call__(self) -> Any:
        return self.target(*self.args, **self.kwargs)


class InterruptibleThread(threading.Thread):
    def __init__(
        self,
        target: Callable[..., Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the thread.

        If target is provided, it will be called with args and kwargs when the
        thread is started. Otherwise, the subclass must implement the `_run`
        method.
        """
        self._should_be_interrupted = threading.Event()
        self.__should_be_killed = False
        self.__ready = False
        self.__run_target = (
            _InterruptibleThreadTarget(target, args, kwargs)
            if target
            else None
        )
        self.__exception: Exception | None = None

        self._timeout_deadline: float | None = None
        self.last_ping_time: float | None = None

        super().__init__()

    def ready(self) -> bool:
        """
        Return True if the thread has finished.
        """
        return self.__ready

    def successful(self) -> bool:
        """
        Return True if the thread has finished successfully
        i.e. without rising an exception.
        """
        return self.__ready and self.__exception is None

    @property
    def exception(self) -> Exception | None:
        """
        Stores an exception if one was raised during thread
        execution.
        """
        return self.__exception

    def run(self) -> None:
        try:
            self._run()
        except InterruptibleThreadExit:
            pass
        except Exception as e:
            self.__exception = e
        finally:
            self.__ready = True

    def _run(self) -> None:
        """
        Run thread body.

        Subclasses must implement this method unless the target
        function is provided to the initializer.
        """
        if self.__run_target:
            self.__run_target()
        else:
            raise NotImplementedError()

    def kill(self, block: bool = True) -> None:
        """
        Kill the thread.

        If block is True, wait until the thread is ready.
        """
        self.__should_be_killed = True
        self._should_be_interrupted.set()
        if block:
            self.join()

    def _check_interrupted(self) -> None:
        """
        Internally check if the thread should be interrupted.

        Don't use this directly instead use the public
        `check_interrupted` function below.
        """
        if self.__should_be_killed:
            raise InterruptibleThreadExit()

        if (
            self._timeout_deadline is not None
            and time.monotonic() >= self._timeout_deadline
        ):
            raise InterruptibleThreadTimeout()

        self._ping()

    def _ping(self) -> None:
        self.last_ping_time = time.monotonic()


P = ParamSpec("P")
T = TypeVar("T")


def _interruptible(
    blocking_function: Callable[P, T]
) -> Callable[
    [Callable[Concatenate[InterruptibleThread, P], T]], Callable[P, T]
]:
    """
    If the current thread is interruptible run interruptible version of
    the blocking function. Otherwise fallback to original implementation.
    """

    def decorator(
        interruptible_function: Callable[
            Concatenate[InterruptibleThread, P], T
        ]
    ) -> Callable[P, T]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            current_thread = threading.current_thread()
            if not isinstance(current_thread, InterruptibleThread):
                return blocking_function(*args)  # type: ignore[call-arg]

            return interruptible_function(current_thread, *args, **kwargs)

        return wrapper

    return decorator


# Time to wait between checking if the thread should be interrupted
# when using interruptible versions of blocking functions.
CHECK_INTERRUPTED_TIMEOUT = 0.2


@_interruptible(time.sleep)
def sleep(current_thread: InterruptibleThread, /, seconds: float) -> None:
    """
    Interruptible version of time.sleep.
    """
    if current_thread._timeout_deadline is not None:
        timeout = max(current_thread._timeout_deadline - time.monotonic(), 0)
        seconds = min(seconds, timeout)

    current_thread._should_be_interrupted.wait(seconds)

    current_thread._check_interrupted()


@_interruptible(queue.Queue.get)
def queue_get(
    current_thread: InterruptibleThread,
    /,
    self: "queue.Queue[queue._T]",
    block: bool = True,
    timeout: "float | None" = None,
) -> "queue._T":
    """
    Interruptible version of queue.Queue.get.
    """
    if not block:
        return self.get(block=False)

    if timeout is not None:
        raise NotImplementedError("timeout is not supported.")

    while True:
        try:
            return self.get(timeout=CHECK_INTERRUPTED_TIMEOUT)
        except queue.Empty:
            current_thread._check_interrupted()


@_interruptible(lambda: None)
def check_interrupted(current_thread: InterruptibleThread, /) -> None:
    """
    Check if the current thread is interrupted.
    """
    return current_thread._check_interrupted()


@_interruptible(lambda: None)
def ping(current_thread: InterruptibleThread, /) -> None:
    """
    Bump the last ping timestamp for the current thread.
    """
    return current_thread._ping()


class InterruptibleThreadTimeout(BaseException):
    """
    Exception raised when the the timeout set by `timeout` context manager
    elapses.

    This exception is raised after the deadline is reached and the thread
    checks if it's interrupted. It is then caught in `timeout` context manager.

    This exception is a subclass of `BaseException` and not `Exception` so it
    won't be caught by a generic `except Exception` block.
    """


@contextlib.contextmanager
def timeout(timeout: float):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Context manager to set a timeout for the interruptible
    operations run by the current interruptible thread.
    """
    current_thread = threading.current_thread()
    if not isinstance(current_thread, InterruptibleThread):
        yield
        return

    if current_thread._timeout_deadline is not None:
        raise RuntimeError("Nested timeout is not supported.")

    current_thread._timeout_deadline = time.monotonic() + timeout

    try:
        yield
    except InterruptibleThreadTimeout:
        pass
    finally:
        current_thread._timeout_deadline = None
