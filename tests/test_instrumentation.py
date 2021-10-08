import time

import mock

from inbox.instrumentation import GreenletTracer


def test_greenlet_tracer(monkeypatch):
    logging_interval = 5
    greenlet_tracer = GreenletTracer(logging_interval=logging_interval)

    logger_mock = mock.Mock()
    get_logger_mock = mock.Mock(return_value=logger_mock)
    monkeypatch.setattr("inbox.instrumentation.get_logger", get_logger_mock)

    greenlet_tracer.start()

    time.sleep(logging_interval * 1.5)

    (call,) = logger_mock.method_calls
    method, args, kwargs = call
    assert method == "info"
    assert args == ("greenlet stats",)
    assert kwargs["total_time"] >= logging_interval

    greenlet_tracer.stop()
