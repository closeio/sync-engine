""" Fixtures don't go here; see util/base.py and friends. """
# Monkeypatch first, to prevent "AttributeError: 'module' object has no
# attribute 'poll'" errors when tests import socket, then monkeypatch.
from gevent import monkey

monkey.patch_all(aggressive=False)

import gevent_openssl

gevent_openssl.monkey_patch()

from inbox.test.util.base import *  # noqa
from inbox.util.testutils import dump_dns_queries  # noqa; noqa
from inbox.util.testutils import files  # noqa
from inbox.util.testutils import mock_dns_resolver  # noqa
from inbox.util.testutils import mock_imapclient  # noqa
from inbox.util.testutils import mock_smtp_get_connection  # noqa
from inbox.util.testutils import uploaded_file_ids
