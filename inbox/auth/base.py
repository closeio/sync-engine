def handler_from_provider(provider_name):
    """
    Return an authentication handler for the given provider.

    Params:
        provider_name (str): Name of the email provider ("custom", "gmail" or
            "outlook").

    Returns:
        An object that implements the AccountHandler interface.
    """
    if provider_name == "custom":
        from .generic import GenericAccountHandler

        return GenericAccountHandler()
    elif provider_name == "gmail":
        from .google import GoogleAccountHandler

        return GoogleAccountHandler()
    elif provider_name == "outlook":
        from .microsoft import MicrosoftAccountHandler

        return MicrosoftAccountHandler()


class AccountHandler(object):
    def create_account(self, account_data):
        raise NotImplementedError()

    def update_account(self, account, account_data):
        raise NotImplementedError()

    def get_imap_connection(self, account):
        raise NotImplementedError()

    def interactive_auth(self, email_address):
        raise NotImplementedError()
