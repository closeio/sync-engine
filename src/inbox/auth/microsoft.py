import attr

from inbox.config import config
from inbox.exceptions import OAuthError
from inbox.models import Namespace
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.secret import SecretType
from inbox.util.url import url_concat

from .oauth import OAuthAuthHandler


@attr.s
class MicrosoftAccountData:
    email = attr.ib()  # type: ignore[var-annotated]

    secret_type = attr.ib()  # type: ignore[var-annotated]
    secret_value = attr.ib()  # type: ignore[var-annotated]

    client_id = attr.ib()  # type: ignore[var-annotated]
    scope = attr.ib()  # type: ignore[var-annotated]

    sync_email = attr.ib()  # type: ignore[var-annotated]
    sync_events = attr.ib()  # type: ignore[var-annotated]


class MicrosoftAuthHandler(OAuthAuthHandler):
    OAUTH_CLIENT_ID = config.get_required("MICROSOFT_OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET = config.get_required("MICROSOFT_OAUTH_CLIENT_SECRET")
    OAUTH_REDIRECT_URI = config.get_required("MICROSOFT_OAUTH_REDIRECT_URI")

    OAUTH_AUTHENTICATE_URL = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    OAUTH_ACCESS_TOKEN_URL = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    OAUTH_USER_INFO_URL = "https://outlook.office.com/api/v2.0/me"

    OAUTH_AUTH_SCOPE = " ".join(
        [
            "https://outlook.office.com/IMAP.AccessAsUser.All",
            "https://outlook.office.com/SMTP.Send",
            "https://outlook.office.com/User.Read",
            "offline_access",
            # Not needed here but gives us an id_token with user information.
            "openid",
            "profile",
        ]
    )

    def create_account(  # type: ignore[override]
        self, account_data: MicrosoftAccountData
    ) -> OutlookAccount:
        namespace = Namespace()
        account = OutlookAccount(namespace=namespace)  # type: ignore[call-arg]
        account.create_emailed_events_calendar()
        account.sync_should_run = False
        return self.update_account(account, account_data)

    def update_account(  # type: ignore[override]
        self, account: OutlookAccount, account_data: MicrosoftAccountData
    ) -> OutlookAccount:
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
            "prompt": "select_account",
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
                return MicrosoftAccountData(
                    email=auth_response["email"],
                    secret_type=SecretType.Token,
                    secret_value=auth_response["refresh_token"],
                    client_id=self.OAUTH_CLIENT_ID,
                    scope=auth_response["scope"],
                    sync_email=True,
                    sync_events=False,
                )
            except OAuthError:
                print(  # noqa: T201
                    "\nInvalid authorization code, try again...\n"
                )
