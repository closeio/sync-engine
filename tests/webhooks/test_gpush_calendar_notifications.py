from datetime import datetime, timedelta
from unittest import mock

import limitlion
import pytest
import pytz
from gevent import sleep

from inbox.models.calendar import Calendar

from tests.util.base import webhooks_client

__all__ = ["webhooks_client"]

CALENDAR_LIST_PATH = "/calendar_list_update/{}"
CALENDAR_PATH = "/calendar_update/{}"

ACCOUNT_WATCH_UUID = "this_is_a_unique_identifier"
CALENDAR_WATCH_UUID = "this_is_a_unique_identifier"  # lol

SYNC_HEADERS = {
    "X-Goog-Channel-Id": "id",
    "X-Goog-Message-Number": 1,
    "X-Goog-Resource-Id": "not relevant",
    "X-Goog-Resource-State": "sync",
    "X-Goog-Resource-URI": "resource/location",
}

UPDATE_HEADERS = {
    "X-Goog-Channel-Id": "id",
    "X-Goog-Message-Number": 2,
    "X-Goog-Resource-Id": "not relevant",
    "X-Goog-Resource-State": "update",
    "X-Goog-Resource-URI": "resource/location",
}

WATCH_EXPIRATION = datetime(2015, 3, 14)


@pytest.fixture
def watched_account(db, default_account):
    account = default_account
    account.new_calendar_list_watch(WATCH_EXPIRATION)
    db.session.add(account)
    db.session.commit()
    return account


@pytest.fixture
def watched_calendar(db, default_namespace):
    calendar = Calendar(
        name="Colander",
        uid="this_is_a_uid",
        read_only=True,
        namespace_id=default_namespace.id,
    )

    calendar.new_event_watch(WATCH_EXPIRATION)
    db.session.add(calendar)
    db.session.commit()
    return calendar


def test_should_update_logic_push(db, watched_account, watched_calendar):
    """
    Ensure we update calendars when a push notification is received, or when
    the watch is expired.
    """
    # Watch should be not-expired
    expiration = WATCH_EXPIRATION + timedelta(days=20 * 365)
    watched_account.new_calendar_list_watch(expiration)
    watched_calendar.new_event_watch(expiration)

    zero = timedelta()

    ten_minutes = timedelta(minutes=10)
    five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
    fifteen_minutes_ago = datetime.utcnow() - timedelta(minutes=15)

    # Never synced - should update
    assert watched_account.should_update_calendars(ten_minutes, zero)
    assert watched_calendar.should_update_events(ten_minutes, zero)

    # Watch renewed - should update
    watched_account.last_calendar_list_sync = five_minutes_ago
    watched_calendar.last_synced = five_minutes_ago
    assert watched_account.should_update_calendars(ten_minutes, zero)
    assert watched_calendar.should_update_events(ten_minutes, zero)

    # Updated recently and no webhook received - should not update
    watched_account.gpush_calendar_list_last_ping = fifteen_minutes_ago
    watched_calendar.gpush_last_ping = fifteen_minutes_ago
    assert not watched_account.should_update_calendars(ten_minutes, zero)
    assert not watched_calendar.should_update_events(ten_minutes, zero)

    # Max time between syncs exceeded - should update
    four_minutes = timedelta(minutes=4)
    assert watched_account.should_update_calendars(four_minutes, zero)
    assert watched_calendar.should_update_events(four_minutes, zero)

    # Received push notification - should update
    watched_account.handle_gpush_notification()
    watched_calendar.handle_gpush_notification()
    assert watched_account.should_update_calendars(ten_minutes, zero)
    assert watched_calendar.should_update_events(ten_minutes, zero)

    # Just synced - should not update
    watched_account.last_calendar_list_sync = datetime.utcnow()
    watched_calendar.last_synced = datetime.utcnow()
    assert not watched_account.should_update_calendars(ten_minutes, zero)
    assert not watched_calendar.should_update_events(ten_minutes, zero)

    # If watch is expired, should always update
    watched_account.new_calendar_list_watch(WATCH_EXPIRATION)
    watched_calendar.new_event_watch(WATCH_EXPIRATION)
    assert watched_account.should_update_calendars(ten_minutes, zero)
    assert watched_calendar.should_update_events(ten_minutes, zero)


def test_should_update_logic_no_push(db, default_account, calendar):
    """
    Ensure we update calendars with no push at the poll frequency.
    """
    assert default_account.needs_new_calendar_list_watch()
    assert calendar.needs_new_watch()

    now = datetime.utcnow()
    ten_seconds_ago = now - timedelta(seconds=10)
    one_minute_ago = now - timedelta(minutes=1)
    poll_frequency = timedelta(seconds=30)
    ten_minutes = timedelta(minutes=10)

    # Never synced - should update
    assert default_account.should_update_calendars(ten_minutes, poll_frequency)
    assert calendar.should_update_events(ten_minutes, poll_frequency)

    # Poll frequency exceeded - should update
    default_account.last_calendar_list_sync = one_minute_ago
    calendar.last_synced = one_minute_ago
    assert default_account.should_update_calendars(ten_minutes, poll_frequency)
    assert calendar.should_update_events(ten_minutes, poll_frequency)

    # Updated recently - should not update
    default_account.last_calendar_list_sync = ten_seconds_ago
    calendar.last_synced = ten_seconds_ago
    assert not default_account.should_update_calendars(ten_minutes, poll_frequency)
    assert not calendar.should_update_events(ten_minutes, poll_frequency)


def test_needs_new_watch_logic(db, watched_account, watched_calendar):
    assert watched_account.needs_new_calendar_list_watch()
    assert watched_calendar.needs_new_watch()

    expiration = WATCH_EXPIRATION + timedelta(days=20 * 365)
    watched_account.new_calendar_list_watch(expiration)
    watched_calendar.new_event_watch(expiration)

    assert not watched_account.needs_new_calendar_list_watch()
    assert not watched_calendar.needs_new_watch()


def test_receive_sync_message(db, webhooks_client, watched_account, watched_calendar):
    # Sync messages can basically be ignored
    # (see https://developers.google.com/google-apps/calendar/v3/push#sync)

    calendar_path = CALENDAR_LIST_PATH.format(watched_account.public_id)
    event_path = CALENDAR_PATH.format(watched_calendar.public_id)

    r = webhooks_client.post_data(calendar_path, {}, SYNC_HEADERS)
    assert r.status_code == 204  # No content

    r = webhooks_client.post_data(event_path, {}, SYNC_HEADERS)
    assert r.status_code == 204  # No content


def test_calendar_update(db, webhooks_client, watched_account):

    calendar_path = CALENDAR_LIST_PATH.format(watched_account.public_id)

    before = datetime.utcnow() - timedelta(seconds=1)
    watched_account.gpush_calendar_list_last_ping = datetime(2010, 1, 1)

    headers = UPDATE_HEADERS.copy()
    headers["X-Goog-Channel-Id"] = ACCOUNT_WATCH_UUID
    r = webhooks_client.post_data(calendar_path, {}, headers)
    assert r.status_code == 200
    db.session.refresh(watched_account)
    assert watched_account.gpush_calendar_list_last_ping > before

    unknown_id_path = CALENDAR_LIST_PATH.format(11111111111)
    r = webhooks_client.post_data(unknown_id_path, {}, headers)
    assert r.status_code == 404  # account not found

    invalid_id_path = CALENDAR_LIST_PATH.format("invalid_id")
    r = webhooks_client.post_data(invalid_id_path, {}, headers)
    assert r.status_code == 400

    bad_headers = UPDATE_HEADERS.copy()
    del bad_headers["X-Goog-Resource-State"]
    r = webhooks_client.post_data(calendar_path, {}, bad_headers)
    assert r.status_code == 400


def test_event_update(db, webhooks_client, watched_calendar):
    limitlion.throttle = mock.Mock(return_value=(True, 1, 1))
    event_path = CALENDAR_PATH.format(watched_calendar.public_id)

    before = datetime.utcnow() - timedelta(seconds=1)
    watched_calendar.gpush_last_ping = datetime(2010, 1, 1)

    headers = UPDATE_HEADERS.copy()
    headers["X-Goog-Channel-Id"] = CALENDAR_WATCH_UUID
    r = webhooks_client.post_data(event_path, {}, headers)
    assert r.status_code == 200
    assert len(limitlion.throttle.mock_calls) == 1
    db.session.refresh(watched_calendar)
    gpush_last_ping = watched_calendar.gpush_last_ping
    assert gpush_last_ping > before

    sleep(1)

    # Test that gpush_last_ping *is* updated if last updated too long ago
    watched_calendar.gpush_last_ping = gpush_last_ping - timedelta(seconds=22)
    db.session.commit()
    r = webhooks_client.post_data(event_path, {}, headers)
    db.session.refresh(watched_calendar)
    assert watched_calendar.gpush_last_ping > gpush_last_ping

    bad_event_path = CALENDAR_PATH.format(1111111111111)
    r = webhooks_client.post_data(bad_event_path, {}, headers)
    assert r.status_code == 404  # calendar not found

    invalid_id_path = CALENDAR_PATH.format("invalid_id")
    r = webhooks_client.post_data(invalid_id_path, {}, headers)
    assert r.status_code == 400

    bad_headers = UPDATE_HEADERS.copy()
    del bad_headers["X-Goog-Resource-State"]
    r = webhooks_client.post_data(event_path, {}, bad_headers)
    assert r.status_code == 400
