from inbox.basicauth import NotSupportedError


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
        raise NotImplementedError()

    def update_account(self, account, account_data):
        raise NotImplementedError()

    def get_imap_connection(self, account, use_timeout=True):
        raise NotImplementedError()

    def authenticate_imap_connection(self, account, conn):
        raise NotImplementedError()

    def get_authenticated_imap_connection(self, account, use_timeout=True):
        conn = self.get_imap_connection(account, use_timeout=use_timeout)
        self.authenticate_imap_connection(account, conn)
        return conn

    def interactive_auth(self, email_address):
        raise NotImplementedError()
