import dataclasses
import threading
from typing import Any, Callable, Dict, Optional, Tuple


class GreenletLikeThreadExit(Exception):
    pass


@dataclasses.dataclass
class GreenletLikeTarget:
    target: Callable[..., Any]
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]

    def __call__(self) -> Any:
        return self.target(*self.args, **self.kwargs)


class GreenletLikeThread(threading.Thread):
    def __init__(
        self, target: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any
    ) -> None:
        self.__should_be_killed = False
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
        self.__should_be_killed = True
        if block:
            self.join()

    def check_killed(self) -> None:
        if self.__should_be_killed:
            raise GreenletLikeThreadExit()


def spawn(
    target: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any
) -> GreenletLikeThread:
    thread = GreenletLikeThread(target, *args, **kwargs)
    thread.start()

    return thread
