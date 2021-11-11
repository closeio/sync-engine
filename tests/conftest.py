""" Fixtures don't go here; see util/base.py and friends. """
# Monkeypatch first, to prevent "AttributeError: 'module' object has no
# attribute 'poll'" errors when tests import socket, then monkeypatch.
import sys

from gevent import monkey

monkey.patch_all(aggressive=False)

if sys.version_info < (3,):
    import gevent_openssl

    gevent_openssl.monkey_patch()

from pytest import fixture, yield_fixture

from inbox.util.testutils import files  # noqa
from inbox.util.testutils import mock_dns_resolver  # noqa
from inbox.util.testutils import mock_imapclient  # noqa
from inbox.util.testutils import mock_smtp_get_connection  # noqa
from inbox.util.testutils import uploaded_file_ids  # noqa

from tests.api.base import TestAPIClient
from tests.util.base import *  # noqa

from inbox.util.testutils import dump_dns_queries  # noqa; noqa


@yield_fixture
def api_client(db, default_namespace):
    from inbox.api.srv import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield TestAPIClient(c, default_namespace.public_id)


@fixture
def blockstore_backend(monkeypatch, request):
    if request.param == "disk":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", False)
    elif request.param == "s3":
        monkeypatch.setattr("inbox.util.blockstore.STORE_MSG_ON_S3", True)
    else:
        raise AssertionError("Unknown blockstore backend {}".format(request.param))
