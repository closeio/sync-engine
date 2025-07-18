import socket
import time
from unittest import mock

import pytest
from MySQLdb import _exceptions as _mysql_exceptions
from sqlalchemy.exc import StatementError

from inbox.interruptible_threading import InterruptibleThreadExit
from inbox.util.concurrency import retry_with_logging


class FailingFunction:
    __name__ = "FailingFunction"

    def __init__(self, exc_type, max_executions=3, delay=0) -> None:
        self.exc_type = exc_type
        self.max_executions = max_executions
        self.delay = delay
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        time.sleep(self.delay)
        if self.call_count < self.max_executions:
            raise self.exc_type


@pytest.mark.usefixtures("mock_time_sleep")
def test_retry_with_logging() -> None:
    logger_mock = mock.Mock()
    failing_function = FailingFunction(ValueError)
    retry_with_logging(failing_function, logger=logger_mock, backoff_delay=0)
    assert logger_mock.mock_calls == [
        mock.call.exception(
            "Uncaught error", account_id=None, provider=None, occurrences=1
        ),
        mock.call.exception(
            "Uncaught error", account_id=None, provider=None, occurrences=1
        ),
    ]
    assert failing_function.call_count == failing_function.max_executions


def test_no_logging_on_interruptible_thread_exit() -> None:
    logger_mock = mock.Mock()
    failing_function = FailingFunction(InterruptibleThreadExit)
    with pytest.raises(InterruptibleThreadExit):
        retry_with_logging(failing_function, logger=logger_mock)
    assert logger_mock.mock_calls == []
    assert failing_function.call_count == 1


def test_selective_retry() -> None:
    logger_mock = mock.Mock()
    failing_function = FailingFunction(ValueError)
    with pytest.raises(ValueError):
        retry_with_logging(
            failing_function, logger=logger_mock, fail_classes=[ValueError]
        )
    assert logger_mock.mock_calls == []
    assert failing_function.call_count == 1


@pytest.mark.usefixtures("mock_time_sleep")
def test_no_logging_until_many_transient_error() -> None:
    transient = [
        socket.timeout,
        socket.error,
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) (1213, 'Deadlock "
            "found when trying to get lock; try restarting transaction')"
        ),
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) Lost connection to MySQL "
            "server during query"
        ),
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) MySQL server has gone away."
        ),
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) Can't connect to MySQL "
            "server on 127.0.0.1"
        ),
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) Max connect timeout reached "
            "while reaching hostgroup 71"
        ),
        StatementError(
            message="?",
            statement="SELECT *",
            params={},
            orig=_mysql_exceptions.OperationalError(
                "(_mysql_exceptions.OperationalError) MySQL server has gone away."
            ),
        ),
    ]

    for transient_exc in transient:
        logger_mock = mock.Mock()
        failing_function = FailingFunction(transient_exc, max_executions=2)
        retry_with_logging(failing_function, logger=logger_mock)

        assert (
            logger_mock.mock_calls == []
        ), f"{transient_exc} should not be logged"
        assert failing_function.call_count == 2

        failing_function = FailingFunction(socket.error, max_executions=21)
        retry_with_logging(failing_function, logger=logger_mock)

        assert logger_mock.mock_calls == [
            mock.call.exception(
                "Uncaught error",
                account_id=None,
                provider=None,
                occurrences=20,
            )
        ]
        assert failing_function.call_count == 21

        failing_function = FailingFunction(socket.error, max_executions=2)


@pytest.mark.usefixtures("mock_time_sleep")
def test_logging_on_critical_error() -> None:
    critical = [
        TypeError("Example TypeError"),
        StatementError(
            message="?", statement="SELECT *", params={}, orig=None
        ),
        StatementError(
            message="?",
            statement="SELECT *",
            params={},
            orig=_mysql_exceptions.OperationalError(
                "(_mysql_exceptions.OperationalError) Incorrect string value "
                "'\\xE7\\x(a\\x84\\xE5'"
            ),
        ),
        _mysql_exceptions.OperationalError(
            "(_mysql_exceptions.OperationalError) Incorrect string value "
            "'\\xE7\\x(a\\x84\\xE5'"
        ),
        _mysql_exceptions.IntegrityError(
            "(_mysql_exceptions.IntegrityError) Column not found"
        ),
    ]

    for critical_exc in critical:
        logger_mock = mock.Mock()
        failing_function = FailingFunction(critical_exc, max_executions=2)
        retry_with_logging(failing_function, logger=logger_mock)

        assert logger_mock.mock_calls == [
            mock.call.exception(
                "Uncaught error", account_id=None, provider=None, occurrences=1
            )
        ], f"{critical_exc} should be logged"
        assert failing_function.call_count == 2
