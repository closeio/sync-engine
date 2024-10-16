import queue
import time

from inbox import interruptible_threading
from inbox.interruptible_threading import InterruptibleThread


class SuccessfulThread(InterruptibleThread):
    def _run(self):
        pass


def test_successful_states():
    thread = SuccessfulThread()

    assert thread.ready() is False
    assert thread.successful() is False
    assert thread.exception is None

    thread.start()
    thread.join()

    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None


class FailingThread(InterruptibleThread):
    def _run(self):
        raise ValueError("This is a test exception")


def test_failing_states():
    thread = FailingThread()

    assert thread.ready() is False
    assert thread.successful() is False
    assert thread.exception is None

    thread.start()
    thread.join()

    assert thread.ready() is True
    assert thread.successful() is False
    assert isinstance(thread.exception, ValueError)


class SleepingThread(InterruptibleThread):
    def _run(self):
        while True:
            interruptible_threading.sleep(1)


def test_sleeping_can_be_interrupted():
    thread = SleepingThread()
    thread.start()
    thread.kill()

    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None


class WaitingOnEmptyQueue(InterruptibleThread):
    def _run(self):
        empty_queue = queue.Queue()
        interruptible_threading.queue_get(empty_queue)


def test_waiting_on_empty_queue_can_be_interrupted():
    thread = WaitingOnEmptyQueue()
    thread.start()
    thread.kill()

    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None


class CheckInterruptedThread(InterruptibleThread):
    def _run(self):
        while True:
            interruptible_threading.check_interrupted()


def test_check_interrupted_can_be_interrupted():
    thread = CheckInterruptedThread()
    thread.start()
    thread.kill()

    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None


class TimeoutThread(InterruptibleThread):
    def __init__(self):
        self.executed_to_end = False

        super().__init__()

    def _run(self):
        with interruptible_threading.timeout(1):
            interruptible_threading.sleep(1000)

        self.executed_to_end = True


def test_timeout_interrupts_sleep():
    thread = TimeoutThread()

    start = time.monotonic()
    thread.start()
    thread.join()
    end = time.monotonic()

    assert 1 < end - start < 2

    assert thread.executed_to_end is True
    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None


class SeveralTimeoutsThread(InterruptibleThread):
    def __init__(self):
        self.executed_to_end = False

        super().__init__()

    def _run(self):
        with interruptible_threading.timeout(1):
            interruptible_threading.sleep(1000)

        with interruptible_threading.timeout(1):
            interruptible_threading.sleep(1000)

        self.executed_to_end = True


def test_several_timeouts_interrupts_sleep():
    thread = SeveralTimeoutsThread()
    thread.start()
    thread.join()

    assert thread.executed_to_end is True
    assert thread.ready() is True
    assert thread.successful() is True
    assert thread.exception is None
