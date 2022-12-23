""" Operations for syncing back local Calendar changes to Gmail. """

from inbox.events.google import GoogleEventsProvider

PROVIDER = "gmail"

__all__ = ["remote_create_event", "remote_update_event", "remote_delete_event"]


def remote_create_event(account, event, db_session, extra_args):
    provider = GoogleEventsProvider(account)
    result = provider.create_remote_event(event, **extra_args)
    # The events crud API assigns a random uid to an event when creating it.
    # We need to update it to the value returned by the Google calendar API.
    event.uid = result["id"]
    db_session.commit()


def remote_update_event(account, event, db_session, extra_args):
    provider = GoogleEventsProvider(account)
    provider.update_remote_event(event, **extra_args)


def remote_delete_event(
    account, event_uid, calendar_name, calendar_uid, db_session, extra_args
):
    provider = GoogleEventsProvider(account)
    provider.delete_remote_event(calendar_uid, event_uid, **extra_args)
