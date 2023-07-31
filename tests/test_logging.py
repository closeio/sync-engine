import sys
from unittest.mock import ANY

import pytest

from inbox.logging import MAX_ERROR_MESSAGE_LENGTH, create_error_log_context


@pytest.mark.parametrize(
    ("error_class", "error_message", "expected_error_log_context"),
    [
        (Exception, "test", {"error_name": "Exception", "error_message": "test"}),
        (
            ValueError,
            "test2" * 4096,
            {
                "error_name": "ValueError",
                "error_message": ("test2" * 4096)[:MAX_ERROR_MESSAGE_LENGTH] + "...",
            },
        ),
    ],
)
def test_create_error_log_context(
    error_class, error_message, expected_error_log_context
):
    try:
        raise error_class(error_message)
    except error_class:
        exc_info = sys.exc_info()

    error_log_context = create_error_log_context(exc_info)

    assert error_log_context == {**expected_error_log_context, "error_traceback": ANY}
    assert error_log_context["error_traceback"].startswith("Traceback")
    assert "test_create_error_log_context" in error_log_context["error_traceback"]
