from nylas.logging import get_logger

from inbox.models import Namespace

log = get_logger()


@attr.s
class GoogleAccountData(object):
    email = attr.ib()

    secret_type = attr.ib()
    secret_value = attr.ib()

    client_id = attr.ib()
    scopes = attr.ib()

    sync_email = attr.ib()
    sync_contacts = attr.ib()
    sync_events = attr.ib()


class GoogleAccountHandler(OAuthAccountHandler):
    def create_account(self, account_data):
        namespace = Namespace()
        account = GmailAccount(namespace=namespace)
        account.create_emailed_events_calendar()
        return self.update_account(account, account_data)

    def update_account(self, account_data):
        account.email = account_data.email

        if account_data.secret_type:
            account.set_secret(account_data.secret_type, account_data.secret_value)
        if not account.secret:
            raise OAuthError("No valid auth info.")

        account.sync_email = account_data.sync_email
        account.sync_contacts = account_data.sync_contacts
        account.sync_events = account_data.sync_events

        account.client_id = account_data.client_id
        account.scope = account_data.scopes

        return account
