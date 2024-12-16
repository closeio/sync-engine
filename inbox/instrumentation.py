import collections
import signal
import time


class ProfileCollector:
    """
    A simple stack sampler for low-overhead CPU profiling: samples the call
    stack every `interval` seconds and keeps track of counts by frame. Because
    this uses signals, it only works on the main thread.
    """

    def __init__(self, interval: float = 0.005) -> None:
        self.interval = interval
        self._started = None
        self._stack_counts = (  # type: ignore[var-annotated]
            collections.defaultdict(int)
        )

    def start(self) -> None:
        self._started = time.time()  # type: ignore[assignment]
        try:
            signal.signal(signal.SIGVTALRM, self._sample)
        except ValueError:
            raise ValueError(  # noqa: B904
                "Can only sample on the main thread"
            )

        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval, 0)

    def _sample(self, signum, frame) -> None:  # type: ignore[no-untyped-def]
        stack: list[str] = []
        while frame is not None:
            stack.append(self._format_frame(frame))
            frame = frame.f_back

        stack_str = ";".join(reversed(stack))
        self._stack_counts[stack_str] += 1
        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval, 0)

    def _format_frame(self, frame) -> str:  # type: ignore[no-untyped-def]
        return "{}({})".format(
            frame.f_code.co_name, frame.f_globals.get("__name__")
        )

    def stats(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self._started is None:
            return ""
        elapsed = time.time() - self._started  # type: ignore[unreachable]
        lines = [f"elapsed {elapsed}", f"granularity {self.interval}"]
        ordered_stacks = sorted(
            self._stack_counts.items(), key=lambda kv: kv[1], reverse=True
        )
        lines.extend([f"{frame} {count}" for frame, count in ordered_stacks])
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        self._started = time.time()  # type: ignore[assignment]
        self._stack_counts = collections.defaultdict(int)
