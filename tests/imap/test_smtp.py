import smtplib
from unittest import mock

import pytest

from inbox.logging import get_logger
from inbox.sendmail.base import SendMailException
from inbox.sendmail.smtp.postel import SMTPConnection


@pytest.mark.networkrequired
def test_use_smtp_over_ssl():
    # Auth won't actually work but we just want to test connection
    # initialization here and below.
    SMTPConnection.smtp_password = mock.Mock()
    conn = SMTPConnection(
        account_id=1,
        email_address="inboxapptest@gmail.com",
        smtp_username="inboxapptest@gmail.com",
        auth_type="password",
        auth_token="secret_password",
        smtp_endpoint=("smtp.gmail.com", 465),
        log=get_logger(),
    )
    assert isinstance(conn.connection, smtplib.SMTP_SSL)


@pytest.mark.networkrequired
def test_use_starttls():
    conn = SMTPConnection(
        account_id=1,
        email_address="inboxapptest@gmail.com",
        smtp_username="inboxapptest@gmail.com",
        auth_type="password",
        auth_token="secret_password",
        smtp_endpoint=("smtp.gmail.com", 587),
        log=get_logger(),
    )
    assert isinstance(conn.connection, smtplib.SMTP)


@pytest.mark.skipif(True, reason="Need to investigate")
@pytest.mark.networkrequired
def test_use_plain():
    # ssl = True
    with pytest.raises(SendMailException):
        conn = SMTPConnection(
            account_id=1,
            email_address="test@tivertical.com",
            smtp_username="test@tivertical.com",
            auth_type="password",
            auth_token="testpwd",
            smtp_endpoint=("tivertical.com", 587),
            log=get_logger(),
        )

    # ssl = False
    conn = SMTPConnection(
        account_id=1,
        email_address="test@tivertical.com",
        smtp_username="test@tivertical.com",
        auth_type="password",
        auth_token="testpwd",
        smtp_endpoint=("tivertical.com", 587),
        log=get_logger(),
    )
    assert isinstance(conn.connection, smtplib.SMTP)


@pytest.mark.parametrize("smtp_port", [465, 587])
@pytest.mark.networkrequired
def test_handle_disconnect(monkeypatch, smtp_port):
    def simulate_disconnect(self):
        raise smtplib.SMTPServerDisconnected()

    monkeypatch.setattr("smtplib.SMTP.rset", simulate_disconnect)
    monkeypatch.setattr("smtplib.SMTP.mail", lambda *args: (550, "NOPE"))
    monkeypatch.setattr(
        "inbox.sendmail.smtp.postel.SMTPConnection.smtp_password", lambda *args: None
    )
    conn = SMTPConnection(
        account_id=1,
        email_address="inboxapptest@gmail.com",
        smtp_username="inboxapptest@gmail.com",
        auth_type="password",
        auth_token="secret_password",
        smtp_endpoint=("smtp.gmail.com", smtp_port),
        log=get_logger(),
    )
    with pytest.raises(smtplib.SMTPSenderRefused):
        conn.sendmail(["test@example.com"], "hello there")
