"""Fixtures don't go here; see util/base.py and friends."""

import os

import werkzeug

os.environ["NYLAS_ENV"] = "test"

from pytest import fixture  # noqa: PT013

from tests.api.base import TestAPIClient

# Additional fixtures
pytest_plugins = ["inbox.util.testutils", "tests.util.base"]


@fixture
def make_api_client():
    def _make_api_client(db, namespace):
        from inbox.api.srv import app

        app.config["TESTING"] = True
        # test_client uses werkzeug.__version__ attribute
        # which has been deprecated
        # To avoid a rushed flask upgrade we'll patch it here
        werkzeug.__version__ = "1.0.0"
        with app.test_client() as c:
            return TestAPIClient(c, namespace.public_id)

    return _make_api_client


@fixture
def api_client(db, default_namespace, make_api_client):
    return make_api_client(db, default_namespace)


@fixture
def blockstore_backend(monkeypatch, request) -> None:
    if request.param == "disk":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", False)
    elif request.param == "s3":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", True)
    else:
        raise AssertionError(f"Unknown blockstore backend {request.param}")
