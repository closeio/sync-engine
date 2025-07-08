import attr

from inbox.config import config
from inbox.crispin import GmailCrispinClient
from inbox.exceptions import GmailDisabledError, IMAPDisabledError, OAuthError
from inbox.logging import get_logger
from inbox.models import Namespace
from inbox.models.backends.gmail import GmailAccount
from inbox.models.secret import SecretType
from inbox.providers import provider_info
from inbox.util.url import url_concat

from .oauth import OAuthAuthHandler

log = get_logger()


@attr.s
class GoogleAccountData:
    email = attr.ib()  # type: ignore[var-annotated]

    secret_type = attr.ib()  # type: ignore[var-annotated]
    secret_value = attr.ib()  # type: ignore[var-annotated]

    client_id = attr.ib()  # type: ignore[var-annotated]
    scope = attr.ib()  # type: ignore[var-annotated]

    sync_email = attr.ib()  # type: ignore[var-annotated]
    sync_contacts = attr.ib()  # type: ignore[var-annotated]
    sync_events = attr.ib()  # type: ignore[var-annotated]


class GoogleAuthHandler(OAuthAuthHandler):
    OAUTH_CLIENT_ID = config.get_required("GOOGLE_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("GOOGLE_OAUTH_CLIENT_SECRET")
    OAUTH_REDIRECT_URI = config.get_required("GOOGLE_OAUTH_REDIRECT_URI")

    OAUTH_AUTHENTICATE_URL = "https://accounts.google.com/o/oauth2/auth"
    OAUTH_ACCESS_TOKEN_URL = "https://accounts.google.com/o/oauth2/token"
    OAUTH_USER_INFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

    OAUTH_AUTH_SCOPE = " ".join(
        [
            "email",  # email address
            "https://mail.google.com/",  # email
            "https://www.google.com/m8/feeds",  # contacts
            "https://www.googleapis.com/auth/calendar",  # calendar
        ]
    )

    def create_account(  # type: ignore[override]
        self, account_data: GoogleAccountData
    ) -> GmailAccount:
        namespace = Namespace()
        account = GmailAccount(namespace=namespace)  # type: ignore[call-arg]
        account.create_emailed_events_calendar()
        account.sync_should_run = False
        return self.update_account(account, account_data)

    def update_account(  # type: ignore[override]
        self, account: GmailAccount, account_data: GoogleAccountData
    ) -> GmailAccount:
        account.email_address = (  # type: ignore[method-assign]
            account_data.email
        )

        if account_data.secret_type:
            account.set_secret(
                account_data.secret_type, account_data.secret_value
            )

        if not account.secret:
            raise OAuthError("No valid auth info.")

        account.sync_email = account_data.sync_email
        account.sync_contacts = account_data.sync_contacts
        account.sync_events = account_data.sync_events

        account.client_id = account_data.client_id
        account.scope = account_data.scope

        return account

    def interactive_auth(  # type: ignore[no-untyped-def]  # noqa: ANN201
        self, email_address=None
    ):
        url_args = {
            "redirect_uri": self.OAUTH_REDIRECT_URI,
            "client_id": self.OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": self.OAUTH_AUTH_SCOPE,
            "access_type": "offline",
            "approval_prompt": "force",
        }
        if email_address:
            url_args["login_hint"] = email_address
        url = url_concat(self.OAUTH_AUTHENTICATE_URL, url_args)

        print(  # noqa: T201
            "To authorize Nylas, visit this URL and follow the directions:"
        )
        print(f"\n{url}")  # noqa: T201

        while True:
            auth_code = input("Enter authorization code: ").strip()
            try:
                auth_response = self._get_authenticated_user(auth_code)
                return GoogleAccountData(
                    email=auth_response["email"],
                    secret_type=SecretType.Token,
                    secret_value=auth_response["refresh_token"],
                    client_id=self.OAUTH_CLIENT_ID,
                    scope=auth_response["scope"],
                    sync_email=True,
                    sync_contacts=True,
                    sync_events=True,
                )
            except OAuthError:
                print(  # noqa: T201
                    "\nInvalid authorization code, try again...\n"
                )

    def verify_account(self, account) -> bool:  # type: ignore[no-untyped-def]
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
        except (GmailDisabledError, IMAPDisabledError):
            if account.sync_email:
                raise

        # Reset the sync_state to 'running' on a successful re-auth.
        # Necessary for API requests to proceed and an account modify delta to
        # be returned to delta/ streaming clients.
        # NOTE: Setting this does not restart the sync. Sync scheduling occurs
        # via the sync_should_run bit (set to True in update_account() above).
        account.sync_state = (
            "running" if account.sync_state else account.sync_state
        )
        return True
