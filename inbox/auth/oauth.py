class OAuthAccountHandler(AccountHandler):
    def acquire_access_token(self, account):
        try:
            return account.new_token()
        except ValidationError:  # Token is invalid.
            # XXX
            # self.verify_account(account)
            return account.new_token()

    def authenticate_imap_connection(self, account, conn):
        token = self.acquire_access_token()
        try:
            conn.oauth2_login(account.email_address, token)
        except IMAPClient.Error as exc:
            log.error(
                "Error during IMAP XOAUTH2 login", account_id=account.id, error=exc,
            )
            raise


class OAuthRequestsWrapper(requests.auth.AuthBase):
    """Helper class for setting the Authorization header on HTTP requests."""

    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["Authorization"] = "Bearer {}".format(self.token)
        return r
