"""Fixtures don't go here; see util/base.py and friends."""

import os

os.environ["USE_GEVENT"] = "0"
os.environ["NYLAS_ENV"] = "test"

from pytest import fixture

from tests.api.base import TestAPIClient

# Additional fixtures
pytest_plugins = ["inbox.util.testutils", "tests.util.base"]


@fixture
def make_api_client():
    def _make_api_client(db, namespace):
        from inbox.api.srv import app

        app.config["TESTING"] = True
        with app.test_client() as c:
            return TestAPIClient(c, namespace.public_id)

    return _make_api_client


@fixture
def api_client(db, default_namespace, make_api_client):
    return make_api_client(db, default_namespace)


@fixture
def blockstore_backend(monkeypatch, request):
    if request.param == "disk":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", False)
    elif request.param == "s3":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", True)
    else:
        raise AssertionError(f"Unknown blockstore backend {request.param}")
