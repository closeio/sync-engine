# flake8: noqa: F811
import asyncore
import datetime
import os
import smtpd
import socket
import ssl
import sys
import time

import gevent
import pytest

from tests.api.base import new_api_client
from tests.util.base import default_account

smtpd.DEBUGSTREAM = sys.stderr

__all__ = ["api_client", "default_account"]

current_dir = os.path.dirname(__file__)
SELF_SIGNED_CERTFILE = os.path.realpath(
    os.path.join(current_dir, "..", "data/self_signed_cert.pem")
)
SELF_SIGNED_KEYFILE = os.path.realpath(
    os.path.join(current_dir, "..", "data/self_signed_cert.key")
)

from inbox.sendmail.smtp import postel

SHARD_ID = 0
SMTP_SERVER_HOST = "localhost"


class BadCertSMTPServer(smtpd.DebuggingServer):
    def __init__(self, localaddr, remoteaddr):
        smtpd.DebuggingServer.__init__(self, localaddr, remoteaddr)
        self.set_socket(
            ssl.wrap_socket(
                self.socket,
                certfile=SELF_SIGNED_CERTFILE,
                keyfile=SELF_SIGNED_KEYFILE,
                server_side=True,
            )
        )


def run_bad_cert_smtp_server():
    serv = BadCertSMTPServer((SMTP_SERVER_HOST, 0), (None, None))

    # override global so SMTP server knows we want an SSL connection
    postel.SMTP_OVER_SSL_TEST_PORT = serv.socket.getsockname()[1]

    asyncore.loop()


@pytest.fixture(scope="module")
def bad_cert_smtp_server():
    s = gevent.spawn(run_bad_cert_smtp_server)
    yield s
    s.kill()


@pytest.fixture
def patched_smtp(monkeypatch):
    monkeypatch.setattr(
        "inbox.sendmail.smtp.postel.SMTPConnection.smtp_password", lambda x: None
    )


@pytest.fixture(scope="function")
def local_smtp_account(db):
    from inbox.auth.generic import GenericAccountData, GenericAuthHandler

    handler = GenericAuthHandler()
    acc = handler.create_account(
        GenericAccountData(
            email="user@gmail.com",
            imap_username="user@gmail.com",
            smtp_username="user@gmail.com",
            imap_password="hunter2",
            smtp_password="hunter2",
            imap_server_host="imap-test.nylas.com",
            imap_server_port=143,
            smtp_server_host=SMTP_SERVER_HOST,
            smtp_server_port=postel.SMTP_OVER_SSL_TEST_PORT,
            sync_email=True,
        )
    )
    db.session.add(acc)
    db.session.commit()
    return acc


@pytest.fixture
def example_draft(db, default_account):
    return {
        "subject": f"Draft test at {datetime.datetime.utcnow()}",
        "body": "<html><body><h2>Sea, birds and sand.</h2></body></html>",
        "to": [
            {"name": "The red-haired mermaid", "email": default_account.email_address}
        ],
    }


if __name__ == "__main__":
    server = BadCertSMTPServer((SMTP_SERVER_HOST, 0), (None, None))
    asyncore.loop()
