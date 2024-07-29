import _thread
import collections
import math
import signal
import socket
import sys
import time
import traceback
from typing import List

import gevent._threading  # This is a clone of the *real* threading module
import gevent.hub
import greenlet
import psutil

from inbox.config import config
from inbox.logging import get_logger
from inbox.util.concurrency import retry_with_logging
from inbox.util.stats import get_statsd_client

BLOCKING_SAMPLE_PERIOD = 5
MAX_BLOCKING_TIME_BEFORE_INTERRUPT = 60
GREENLET_SAMPLING_INTERVAL = 1
LOGGING_INTERVAL = 60


class ProfileCollector:
    """A simple stack sampler for low-overhead CPU profiling: samples the call
    stack every `interval` seconds and keeps track of counts by frame. Because
    this uses signals, it only works on the main thread.
    """

    def __init__(self, interval=0.005):
        self.interval = interval
        self._started = None
        self._stack_counts = collections.defaultdict(int)

    def start(self):
        self._started = time.time()
        try:
            signal.signal(signal.SIGVTALRM, self._sample)
        except ValueError:
            raise ValueError("Can only sample on the main thread")

        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval, 0)

    def _sample(self, signum, frame):
        stack: List[str] = []
        while frame is not None:
            stack.append(self._format_frame(frame))
            frame = frame.f_back

        stack_str = ";".join(reversed(stack))
        self._stack_counts[stack_str] += 1
        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval, 0)

    def _format_frame(self, frame):
        return "{}({})".format(frame.f_code.co_name, frame.f_globals.get("__name__"))

    def stats(self):
        if self._started is None:
            return ""
        elapsed = time.time() - self._started
        lines = [f"elapsed {elapsed}", f"granularity {self.interval}"]
        ordered_stacks = sorted(
            self._stack_counts.items(), key=lambda kv: kv[1], reverse=True
        )
        lines.extend([f"{frame} {count}" for frame, count in ordered_stacks])
        return "\n".join(lines) + "\n"

    def reset(self):
        self._started = time.time()
        self._stack_counts = collections.defaultdict(int)


class GreenletTracer:
    """Log if a greenlet blocks the event loop for too long, and optionally log
    statistics on time spent in individual greenlets.

    Parameters
    ----------
    blocking_sample_period: float
        Log a warning if a greenlet blocks for more than blocking_sample_period
        seconds.
    """

    def __init__(
        self,
        blocking_sample_period=BLOCKING_SAMPLE_PERIOD,
        sampling_interval=GREENLET_SAMPLING_INTERVAL,
        logging_interval=LOGGING_INTERVAL,
    ):
        self.blocking_sample_period = blocking_sample_period
        self.sampling_interval = sampling_interval
        self.logging_interval = logging_interval

        self.time_spent_by_context = collections.defaultdict(float)
        self.total_switches = 0
        self._last_switch_time = None
        self._switch_flag = False
        self._active_greenlet = None
        self._main_thread_id = gevent._threading.get_thread_ident()
        self._hub = gevent.hub.get_hub()
        self.last_logged_stats = time.time()
        self.last_checked_blocking = time.time()

        self.total_cpu_time = 0
        self.process = psutil.Process()
        self.pending_avgs = {1: 0, 5: 0, 15: 0}
        self.cpu_avgs = {1: 0, 5: 0, 15: 0}
        self.hostname = socket.gethostname().replace(".", "-")
        self.process_name = str(config.get("PROCESS_NAME", "unknown"))
        # We need a new client instance here because this runs in its own
        # thread.
        self.statsd_client = get_statsd_client()
        self.start_time = time.time()
        self._stopping = False
        self._thread_id = None

    def start(self):
        self.start_time = time.time()
        greenlet.settrace(self._trace)
        # Spawn a separate OS thread to periodically check if the active
        # greenlet on the main thread is blocking.
        self._thread_id = gevent._threading.start_new_thread(
            self._monitoring_thread, ()
        )

    def stop(self):
        assert self._thread_id, "Tracer not started"
        self._stopping = True

    def stats(self):
        total_time = time.time() - self.start_time
        idle_fraction = self.time_spent_by_context.get("hub", 0) / total_time
        return {
            "times": self.time_spent_by_context,
            "idle_fraction": idle_fraction,
            "total_time": total_time,
            "pending_avgs": self.pending_avgs,
            "cpu_avgs": self.cpu_avgs,
            "total_switches": self.total_switches,
        }

    def log_stats(self, max_stats=60):
        total_time = round(time.time() - self.start_time, 2)
        greenlets_by_cost = sorted(
            self.time_spent_by_context.items(), key=lambda k_v: k_v[1], reverse=True
        )
        formatted_times = {k: round(v, 2) for k, v in greenlets_by_cost[:max_stats]}
        self.log.info(
            "greenlet stats",
            times=str(formatted_times),
            total_switches=self.total_switches,
            total_time=total_time,
            pending_avgs=self.pending_avgs,
        )
        self._publish_load_avgs()

    def _trace(self, event, xxx_todo_changeme):
        (origin, target) = xxx_todo_changeme
        self.total_switches += 1
        current_time = time.time()
        if self._last_switch_time is not None:
            time_spent = current_time - self._last_switch_time
            if origin is not self._hub:
                context = getattr(origin, "context", None)
            else:
                context = "hub"
            self.time_spent_by_context[context] += time_spent
        self._active_greenlet = target
        self._last_switch_time = current_time
        self._switch_flag = True

    def _check_blocking(self, current_time):
        if self._switch_flag is False:
            active_greenlet = self._active_greenlet
            if active_greenlet is not None and active_greenlet != self._hub:
                self._notify_greenlet_blocked(active_greenlet, current_time)
        self._switch_flag = False

    def _notify_greenlet_blocked(self, active_greenlet, current_time):
        # greenlet.gr_frame doesn't work on another thread -- we have
        # to get the main thread's frame.
        frame = sys._current_frames()[self._main_thread_id]
        formatted_frame = "\t".join(traceback.format_stack(frame))
        self.log.warning(
            "greenlet blocking",
            frame=formatted_frame,
            context=getattr(active_greenlet, "context", None),
            blocking_greenlet_id=id(active_greenlet),
        )

    def _calculate_pending_avgs(self):
        # Calculate a "load average" for greenlet scheduling in roughly the
        # same way as /proc/loadavg.  I.e., a 1/5/15-minute
        # exponentially-damped moving average of the number of greenlets that
        # are waiting to run.
        pendingcnt = self._hub.loop.pendingcnt
        for k, v in self.pending_avgs.items():
            exp = math.exp(-self.sampling_interval / (60.0 * k))
            self.pending_avgs[k] = exp * v + (1.0 - exp) * pendingcnt

    def _calculate_cpu_avgs(self):
        times = self.process.cpu_times()
        new_total_time = times.user + times.system
        delta = new_total_time - self.total_cpu_time
        for k, v in self.cpu_avgs.items():
            exp = math.exp(-self.sampling_interval / (60.0 * k))
            self.cpu_avgs[k] = exp * v + (1.0 - exp) * delta
        self.total_cpu_time = new_total_time

    def _publish_load_avgs(self):
        for k, v in self.pending_avgs.items():
            path = "greenlet_tracer.pending_avg.{}.{}.{:02d}".format(
                self.hostname, self.process_name, k
            )
            self.statsd_client.gauge(path, v)
        for k, v in self.cpu_avgs.items():
            path = "greenlet_tracer.cpu_avg.{}.{}.{:02d}".format(
                self.hostname, self.process_name, k
            )
            self.statsd_client.gauge(path, v)

    def _monitoring_thread(self):
        # Logger needs to be instantiated in new thread.
        self.log = get_logger()
        while not self._stopping:
            retry_with_logging(self._run_impl, self.log)

    def _run_impl(self):
        try:
            self._calculate_pending_avgs()
            self._calculate_cpu_avgs()
            now = time.time()
            if now - self.last_checked_blocking > self.blocking_sample_period:
                self._check_blocking(now)
                self.last_checked_blocking = now
            if now - self.last_logged_stats > self.logging_interval:
                self.log_stats()
                self.last_logged_stats = now
            gevent.sleep(self.sampling_interval)
        # Swallow exceptions raised during interpreter shutdown.
        except Exception:
            if sys is not None:
                raise


class KillerGreenletTracer(GreenletTracer):
    def __init__(
        self,
        blocking_sample_period=BLOCKING_SAMPLE_PERIOD,
        sampling_interval=GREENLET_SAMPLING_INTERVAL,
        logging_interval=LOGGING_INTERVAL,
        max_blocking_time=MAX_BLOCKING_TIME_BEFORE_INTERRUPT,
    ):
        self._max_blocking_time = max_blocking_time
        super().__init__(blocking_sample_period, sampling_interval, logging_interval)

    def _notify_greenlet_blocked(self, active_greenlet, current_time):
        super()._notify_greenlet_blocked(active_greenlet, current_time)
        if self._last_switch_time is None:
            return

        time_spent = current_time - self._last_switch_time
        if time_spent <= self._max_blocking_time:
            return
        # This will cause the main thread (which is running the blocked greenlet)
        # to raise a KeyboardInterrupt exception.
        # We can't just call activet_greenlet.kill() here because gevent will
        # throw an exception on this thread saying that we would block forever
        # (which is true).
        self.log.error(
            "interrupting blocked greenlet",
            context=getattr(active_greenlet, "context", None),
            blocking_greenlet_id=id(active_greenlet),
        )
        _thread.interrupt_main()
