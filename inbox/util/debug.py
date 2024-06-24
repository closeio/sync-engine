"""Utilities for debugging failures in development/staging."""


def bind_context(gr, role, account_id, *args):
    """Bind a human-interpretable "context" to the greenlet `gr`, for
    execution-tracing purposes. The context consists of the greenlet's role
    (e.g., "foldersyncengine"), the account_id it's operating on, and possibly
    additional values (e.g., folder id, device id).

    TODO(emfree): this should move to inbox/instrumentation.
    """
    gr.context = ":".join([role, str(account_id)] + [str(arg) for arg in args])
