import socket

from imapclient import IMAPClient
from nylas.logging import get_logger

from inbox.basicauth import (
    NotSupportedError,
    UserRecoverableConfigError,
)
from inbox.crispin import CrispinClient
from inbox.sendmail.smtp.postel import SMTPClient

from .utils import create_imap_connection

log = get_logger()


def handler_from_provider(provider_name):
    """
    Return an authentication handler for the given provider.

    Params:
        provider_name (str): Name of the email provider ("custom", "gmail" or
            "outlook").

    Returns:
        An object that implements the AuthHandler interface.
    """
    if provider_name == "custom":
        from .generic import GenericAuthHandler

        return GenericAuthHandler()
    elif provider_name == "gmail":
        from .google import GoogleAuthHandler

        return GoogleAuthHandler()
    elif provider_name == "outlook":
        from .microsoft import MicrosoftAuthHandler

        return MicrosoftAuthHandler()

    raise NotSupportedError("Nylas does not support the email provider.")


class AuthHandler(object):
    def create_account(self, account_data):
        """
        Create a new account with the given subclass-specific account data.

        This method does NOT check for the existence of an account for a
        provider and email_address. That should be done by the caller.
        """
        raise NotImplementedError()

    def update_account(self, account, account_data):
        """
        Update an existing account with the given subclass-specific account
        data.

        This method assumes the existence of the account passed in.
        """
        raise NotImplementedError()

    def get_imap_connection(self, account, use_timeout=True):
        host, port = account.imap_endpoint
        try:
            return create_imap_connection(host, port, use_timeout)
        except (IMAPClient.Error, socket.error) as exc:
            log.error(
                "Error instantiating IMAP connection", account_id=account.id, error=exc,
            )
            raise

    def authenticate_imap_connection(self, account, conn):
        raise NotImplementedError()

    def get_authenticated_imap_connection(self, account, use_timeout=True):
        conn = self.get_imap_connection(account, use_timeout=use_timeout)
        self.authenticate_imap_connection(account, conn)
        return conn

    def interactive_auth(self, email_address):
        raise NotImplementedError()

    def verify_account(self, account):
        """
        Verifies a generic IMAP account by logging in and logging out to both
        the IMAP/ SMTP servers.

        Note:
        Raises exceptions from connect_account(), SMTPClient._get_connection()
        on error.

        Returns
        -------
        True: If the client can successfully connect to both.

        """
        # Verify IMAP login
        conn = self.get_authenticated_imap_connection(account)
        crispin = CrispinClient(
            account.id, account.provider_info, account.email_address, conn
        )

        try:
            conn.list_folders()
            account.folder_separator = crispin.folder_separator
            account.folder_prefix = crispin.folder_prefix
        except Exception as e:
            log.error(
                "account_folder_list_failed", account_id=account.id, error=e.message
            )
            error_message = (
                "Full IMAP support is not enabled for this account. "
                "Please contact your domain "
                "administrator and try again."
            )
            raise UserRecoverableConfigError(error_message)
        finally:
            conn.logout()

        # Verify SMTP login
        try:
            # Check that SMTP settings work by establishing and closing and
            # SMTP session.
            smtp_client = SMTPClient(account)
            with smtp_client._get_connection():
                pass
        except socket.gaierror as exc:
            log.error(
                "Failed to resolve SMTP server domain", account_id=account.id, error=exc
            )
            error_message = (
                "Couldn't resolve the SMTP server domain name. "
                "Please check that your SMTP settings are correct."
            )
            raise UserRecoverableConfigError(error_message)

        except socket.timeout as exc:
            log.error(
                "TCP timeout when connecting to SMTP server",
                account_id=account.id,
                error=exc,
            )

            error_message = (
                "Connection timeout when connecting to SMTP server. "
                "Please check that your SMTP settings are correct."
            )
            raise UserRecoverableConfigError(error_message)

        except Exception as exc:
            log.error(
                "Failed to establish an SMTP connection",
                smtp_endpoint=account.smtp_endpoint,
                account_id=account.id,
                error=exc,
            )
            raise UserRecoverableConfigError(
                "Please check that your SMTP settings are correct."
            )

        # Reset the sync_state to 'running' on a successful re-auth.
        # Necessary for API requests to proceed and an account modify delta to
        # be returned to delta/ streaming clients.
        # NOTE: Setting this does not restart the sync. Sync scheduling occurs
        # via the sync_should_run bit (set to True in update_account() above).
        account.sync_state = "running" if account.sync_state else account.sync_state
        return True
