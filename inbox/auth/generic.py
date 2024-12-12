import datetime
import getpass

import attr
from imapclient import IMAPClient

from inbox.auth.utils import auth_is_invalid, auth_requires_app_password
from inbox.exceptions import AppPasswordError, ValidationError
from inbox.logging import get_logger
from inbox.models import Namespace
from inbox.models.backends.generic import GenericAccount

from .base import AuthHandler

log = get_logger()


@attr.s
class GenericAccountData:
    email = attr.ib()

    imap_server_host = attr.ib()
    imap_server_port = attr.ib()
    imap_username = attr.ib()
    imap_password = attr.ib()

    smtp_server_host = attr.ib()
    smtp_server_port = attr.ib()
    smtp_username = attr.ib()
    smtp_password = attr.ib()

    sync_email = attr.ib()


class GenericAuthHandler(AuthHandler):
    def create_account(self, account_data):  # noqa: ANN201
        namespace = Namespace()
        account = GenericAccount(namespace=namespace)
        account.provider = "custom"
        account.create_emailed_events_calendar()
        account.sync_should_run = False
        return self.update_account(account, account_data)

    def update_account(self, account, account_data):  # noqa: ANN201
        account.email_address = account_data.email

        account.imap_endpoint = (
            account_data.imap_server_host,
            account_data.imap_server_port,
        )

        account.smtp_endpoint = (
            account_data.smtp_server_host,
            account_data.smtp_server_port,
        )

        account.imap_username = account_data.imap_username
        account.imap_password = account_data.imap_password

        account.smtp_username = account_data.smtp_username
        account.smtp_password = account_data.smtp_password

        account.date = datetime.datetime.utcnow()

        account.sync_email = account_data.sync_email

        return account

    def authenticate_imap_connection(self, account, conn) -> None:
        try:
            conn.login(account.imap_username, account.imap_password)
        except IMAPClient.Error as exc:
            if auth_is_invalid(exc):
                log.error(
                    "IMAP login failed", account_id=account.id, error=exc
                )
                raise ValidationError(exc)  # noqa: B904
            elif auth_requires_app_password(exc):
                raise AppPasswordError(exc)  # noqa: B904
            else:
                log.warning(
                    "IMAP login failed for an unknown reason. Check auth_is_invalid",
                    account_id=account.id,
                    error=exc,
                )
                raise

    def interactive_auth(self, email_address):  # noqa: ANN201
        imap_server_host = input("IMAP server host: ").strip()
        imap_server_port = input("IMAP server port: ").strip() or 993
        imap_um = "IMAP username (empty for same as email address): "
        imap_user = input(imap_um).strip() or email_address
        imap_pwm = "IMAP password for {0}: "
        imap_p = getpass.getpass(imap_pwm.format(email_address))

        smtp_server_host = input("SMTP server host: ").strip()
        smtp_server_port = input("SMTP server port: ").strip() or 587
        smtp_um = "SMTP username (empty for same as email address): "
        smtp_user = input(smtp_um).strip() or email_address
        smtp_pwm = "SMTP password for {0} (empty for same as IMAP): "
        smtp_p = getpass.getpass(smtp_pwm.format(email_address)) or imap_p

        return GenericAccountData(
            email=email_address,
            imap_server_host=imap_server_host,
            imap_server_port=imap_server_port,
            imap_username=imap_user,
            imap_password=imap_p,
            smtp_server_host=smtp_server_host,
            smtp_server_port=smtp_server_port,
            smtp_username=smtp_user,
            smtp_password=smtp_p,
            sync_email=True,
        )
