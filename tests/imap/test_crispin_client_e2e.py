import uuid
from email.parser import HeaderParser
from unittest import mock

import imapclient
import pytest
from flanker import mime

from inbox.crispin import CrispinClient, Flags, RawFolder, RawMessage
from inbox.sendmail.smtp.postel import SMTPConnection


@pytest.fixture
def recipient_email_address():
    return f"{uuid.uuid4().hex}-recipient@example.com"


@pytest.fixture
def crispin_client(recipient_email_address):
    connection = imapclient.IMAPClient(host="greenmail", ssl=False, port=3143)
    connection.login(recipient_email_address, "password")

    client = CrispinClient(-1, {}, recipient_email_address, connection)
    client.select_folder("INBOX", mock.Mock())
    return client


def test_capabilities(crispin_client):
    assert not crispin_client.condstore_supported()
    assert crispin_client.idle_supported()


def test_folders(crispin_client):
    assert crispin_client.folder_separator == "."
    assert crispin_client.folder_prefix == ""

    folders = crispin_client.folders()
    assert set(folders) == {
        RawFolder("INBOX", "inbox"),
    }

    folder_names = crispin_client.folder_names()
    assert folder_names == {
        "inbox": ["INBOX"],
    }

    sync_folders = crispin_client.sync_folders()
    assert sync_folders == ["INBOX"]


@pytest.fixture
def sender_email_address():
    return f"{uuid.uuid4().hex}-sender@example.com"


@pytest.fixture
def smtp_client(sender_email_address):
    return SMTPConnection(
        -1,
        sender_email_address,
        sender_email_address,
        "password",
        "password",
        ("greenmail", 3025),
        mock.Mock(),
        upgrade_connection=False,
    )


@pytest.fixture
def message(recipient_email_address, smtp_client):
    content = "Hello"

    smtp_client.sendmail([recipient_email_address], content)

    return content


def test_uids(message, sender_email_address, crispin_client):
    (uid,) = crispin_client.all_uids()
    (raw_message,) = crispin_client.uids([uid])

    assert raw_message == RawMessage(
        uid=uid,
        internaldate=mock.ANY,
        flags=(b"\\Recent",),
        body=mock.ANY,
        g_msgid=None,
        g_thrid=None,
        g_labels=None,
    )
    mimepart = mime.from_string(raw_message.body)
    assert dict(mimepart.headers) == {
        "Return-Path": f"<{sender_email_address}>",
        "Received": mock.ANY,
    }
    assert mimepart.body.strip() == message


def test_flags(message, crispin_client):
    (uid,) = crispin_client.all_uids()
    flags = crispin_client.flags([uid])
    assert flags == {uid: Flags(flags=(b"\\Recent",), modseq=None)}


def test_fetch_headers(message, sender_email_address, crispin_client):
    (uid,) = crispin_client.all_uids()
    imap_headers = crispin_client.fetch_headers([uid])

    assert imap_headers == {uid: {b"SEQ": mock.ANY, b"BODY[HEADER]": mock.ANY}}
    parser = HeaderParser()
    headers = dict(parser.parsestr(imap_headers[uid][b"BODY[HEADER]"].decode()))

    assert headers == {"Return-Path": f"<{sender_email_address}>", "Received": mock.ANY}


def test_find_by_header(message, sender_email_address, crispin_client):
    return_path_uids = crispin_client.find_by_header(
        "Return-Path", f"<{sender_email_address}>"
    )
    all_uids = crispin_client.all_uids()

    assert return_path_uids == all_uids
