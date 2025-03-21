def get_search_client(account):  # type: ignore[no-untyped-def]  # noqa: ANN201
    from inbox.search.backends import module_registry

    search_mod = module_registry.get(account.provider)
    search_cls = getattr(search_mod, search_mod.SEARCH_CLS)
    search_client = search_cls(account)
    return search_client


class SearchBackendException(Exception):
    """
    Raised if there's an error proxying the search request to the
    provider.
    """

    def __init__(  # type: ignore[no-untyped-def]
        self, message, http_code, server_error=None
    ) -> None:
        self.message = message
        self.http_code = http_code
        self.server_error = server_error
        super().__init__(message, http_code, server_error)

    def __str__(self) -> str:
        return self.message


class SearchStoreException(Exception):
    """
    Raised if there's an error proxying the search request to the provider.
    This is a special EAS case where the Status code for the Store element has
    an error
    """

    def __init__(self, err_code) -> None:  # type: ignore[no-untyped-def]
        self.err_code = err_code
        super().__init__(err_code)
