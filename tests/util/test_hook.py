import mock

from inbox.util.hook import run_once

def hook():
    pass


def another_hook():
    pass


def test_run_once():
    with mock.patch("inbox.util.hook.log") as log_mock:
        run_once("tests.util.test_hook.hook")

    assert log_mock.info.call_count == 1
    assert log_mock.info.call_args == (('Running hook tests.util.test_hook.hook',),)

    with mock.patch("inbox.util.hook.log") as log_mock:
        run_once("tests.util.test_hook.hook")

    assert log_mock.info.call_count == 1
    assert log_mock.info.call_args == (('Not running hook tests.util.test_hook.hook, it was already run',),)

    with mock.patch("inbox.util.hook.log") as log_mock:
        run_once("tests.util.test_hook.another_hook")

    assert log_mock.info.call_count == 1
    assert log_mock.info.call_args == (('Running hook tests.util.test_hook.another_hook',),)

