def get_raw_from_provider(message):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """Get the raw contents of a message from the provider."""
    account = message.account
    return account.get_raw_message_contents(message)
