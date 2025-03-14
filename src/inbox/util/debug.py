"""Utilities for debugging failures in development/staging."""


def bind_context(  # type: ignore[no-untyped-def]
    thread, role, account_id, *args
) -> None:
    """
    Bind a human-interpretable "context" to the thread `gr`, for
    execution-tracing purposes. The context consists of the thread's role
    (e.g., "foldersyncengine"), the account_id it's operating on, and possibly
    additional values (e.g., folder id, device id).

    TODO(emfree): this should move to inbox/instrumentation.
    """
    thread.context = ":".join(
        [role, str(account_id)] + [str(arg) for arg in args]
    )
