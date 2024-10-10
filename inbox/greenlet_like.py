import dataclasses
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar


class GreenletLikeThreadExit(BaseException):
    pass


@dataclasses.dataclass
class GreenletLikeTarget:
    target: Callable[..., Any]
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]

    def __call__(self) -> Any:
        return self.target(*self.args, **self.kwargs)


CHECK_KILLED_TIMEOUT = 0.2

QueueElementT = TypeVar("QueueElementT")


class GreenletLikeThread(threading.Thread):
    def __init__(
        self, target: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any
    ) -> None:
        self.__should_be_killed = threading.Event()
        self.__ready = False
        self.__run_target = GreenletLikeTarget(target, args, kwargs) if target else None
        self.exception: Optional[Exception] = None

        super().__init__()

    def ready(self) -> bool:
        return self.__ready

    def successful(self) -> bool:
        return self.__ready and self.exception is None

    def run(self) -> None:
        try:
            self._run()
        except GreenletLikeThreadExit:
            pass
        except Exception as e:
            self.exception = e
        finally:
            self.__ready = True

    def _run(self) -> None:
        if self.__run_target:
            self.__run_target()
        else:
            raise NotImplementedError()

    def kill(self, block: bool = True) -> None:
        self.__should_be_killed.set()
        if block:
            self.join()

    def check_killed(self) -> None:
        if self.__should_be_killed.is_set():
            raise GreenletLikeThreadExit()

    def queue_get(self, queue: "queue.Queue[QueueElementT]") -> QueueElementT:
        while True:
            try:
                return queue.get(timeout=CHECK_KILLED_TIMEOUT)
            except queue.Empty:
                self.check_killed()

    def sleep(self, seconds: float) -> None:
        self.__should_be_killed.wait(seconds)
        self.check_killed()


def sleep(seconds: float) -> None:
    current_thread = threading.current_thread()
    if not isinstance(current_thread, GreenletLikeThread):
        return time.sleep(seconds)

    return current_thread.sleep(seconds)


def queue_get(queue: "queue.Queue[QueueElementT]") -> QueueElementT:
    current_thread = threading.current_thread()
    if not isinstance(current_thread, GreenletLikeThread):
        return queue.get()

    return current_thread.queue_get(queue)


def check_killed() -> None:
    current_thread = threading.current_thread()
    if not isinstance(current_thread, GreenletLikeThread):
        return

    return current_thread.check_killed()
