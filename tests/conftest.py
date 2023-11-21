""" Fixtures don't go here; see util/base.py and friends. """
# Monkeypatch first, to prevent "AttributeError: 'module' object has no
# attribute 'poll'" errors when tests import socket, then monkeypatch.
from gevent import monkey

monkey.patch_all(aggressive=False)

import os

os.environ["NYLAS_ENV"] = "test"

from pytest import fixture

from inbox.util.testutils import dump_dns_queries  # noqa
from inbox.util.testutils import files  # noqa
from inbox.util.testutils import mock_dns_resolver  # noqa
from inbox.util.testutils import mock_imapclient  # noqa
from inbox.util.testutils import mock_smtp_get_connection  # noqa
from inbox.util.testutils import uploaded_file_ids  # noqa

from tests.api.base import TestAPIClient
from tests.util.base import *  # noqa


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
