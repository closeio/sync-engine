import socket

import attr
from imapclient import IMAPClient
from nylas.logging import get_logger

from inbox.basicauth import ImapSupportDisabledError, OAuthError
from inbox.crispin import GmailCrispinClient
from inbox.models import Namespace
from inbox.models.backends.gmail import GmailAccount
from inbox.providers import provider_info

from .oauth import OAuthAuthHandler
from .utils import create_imap_connection

log = get_logger()


@attr.s
class GoogleAccountData(object):
    email = attr.ib()

    secret_type = attr.ib()
    secret_value = attr.ib()

    client_id = attr.ib()
    scope = attr.ib()

    sync_email = attr.ib()
    sync_contacts = attr.ib()
    sync_events = attr.ib()


class GoogleAuthHandler(OAuthAuthHandler):
    OAUTH_ACCESS_TOKEN_URL = "https://www.googleapis.com/oauth2/v4/token"

    def create_account(self, account_data):
        namespace = Namespace()
        account = GmailAccount(namespace=namespace)
        account.create_emailed_events_calendar()
        return self.update_account(account, account_data)

    def update_account(self, account, account_data):
        account.email_address = account_data.email

        if account_data.secret_type:
            account.set_secret(account_data.secret_type, account_data.secret_value)
        if not account.secret:
            raise OAuthError("No valid auth info.")

        account.sync_email = account_data.sync_email
        account.sync_contacts = account_data.sync_contacts
        account.sync_events = account_data.sync_events

        account.client_id = account_data.client_id
        account.scope = account_data.scope

        return account

    def get_imap_connection(self, account, use_timeout=True):
        host, port = account.imap_endpoint
        ssl_required = True
        try:
            return create_imap_connection(host, port, ssl_required, use_timeout)
        except (IMAPClient.Error, socket.error) as exc:
            log.error(
                "Error instantiating IMAP connection", account_id=account.id, error=exc,
            )
            raise

    def verify_account(self, account):
        """
        Verify the credentials provided by logging in.
        Verify the account configuration -- specifically checks for the presence
        of the 'All Mail' folder.

        Raises
        ------
        An inbox.crispin.GmailSettingError if the 'All Mail' folder is
        not present and is required (account.sync_email == True).
        """
        try:
            # Verify login.
            conn = self.get_authenticated_imap_connection(account)
            # Verify configuration.
            client = GmailCrispinClient(
                account.id,
                provider_info("gmail"),
                account.email_address,
                conn,
                readonly=True,
            )
            client.sync_folders()
            conn.logout()
        except ImapSupportDisabledError:
            if account.sync_email:
                raise
        return True
