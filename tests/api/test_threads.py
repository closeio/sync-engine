import datetime
import json

import pytest

from inbox.api.ns_api import API_VERSIONS
from tests.util.base import (
    add_fake_message,
    add_fake_thread,
    db,
    default_account,
)

__all__ = ["db", "default_account"]


def test_thread_received_recent_date(db, api_client, default_account):
    date1 = datetime.datetime(2015, 1, 1, 0, 0, 0)
    date2 = datetime.datetime(2012, 1, 1, 0, 0, 0)

    thread1 = add_fake_thread(db.session, default_account.namespace.id)

    date_dict = dict()

    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject="Test Thread 1",
        received_date=date1,
        add_sent_category=True,
    )
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject="Test Thread 1",
        received_date=date2,
    )

    date_dict["Test Thread 1"] = date2

    thread2 = add_fake_thread(db.session, default_account.namespace.id)
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread2,
        subject="Test Thread 2",
        received_date=date1,
        add_sent_category=True,
    )

    date_dict["Test Thread 2"] = date1

    resp = api_client.get_raw("/threads/")
    assert resp.status_code == 200
    threads = json.loads(resp.data)

    for thread in threads:
        assert date_dict[thread["subject"]] == datetime.datetime.fromtimestamp(
            thread["last_message_received_timestamp"]
        )


def test_thread_sent_recent_date(db, api_client, default_account):
    date1 = datetime.datetime(2015, 1, 1, 0, 0, 0)
    date2 = datetime.datetime(2012, 1, 1, 0, 0, 0)
    date3 = datetime.datetime(2010, 1, 1, 0, 0, 0)
    date4 = datetime.datetime(2009, 1, 1, 0, 0, 0)
    date5 = datetime.datetime(2008, 1, 1, 0, 0, 0)

    thread1 = add_fake_thread(db.session, default_account.namespace.id)

    test_subject = "test_thread_sent_recent_date"

    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject=test_subject,
        received_date=date1,
    )
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject=test_subject,
        received_date=date2,
        add_sent_category=True,
    )
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject=test_subject,
        received_date=date3,
    )
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject=test_subject,
        received_date=date4,
        add_sent_category=True,
    )
    add_fake_message(
        db.session,
        default_account.namespace.id,
        thread1,
        subject=test_subject,
        received_date=date5,
    )

    resp = api_client.get_raw("/threads/")
    assert resp.status_code == 200
    threads = json.loads(resp.data)

    for thread in threads:  # should only be one
        assert (
            datetime.datetime.fromtimestamp(
                thread["last_message_sent_timestamp"]
            )
            == date2
        )


def test_thread_count(db, api_client, default_account):
    date1 = datetime.datetime(2015, 1, 1, 0, 0, 0)
    date2 = datetime.datetime(2012, 1, 1, 0, 0, 0)
    date3 = datetime.datetime(2010, 1, 1, 0, 0, 0)
    date4 = datetime.datetime(2009, 1, 1, 0, 0, 0)
    date5 = datetime.datetime(2008, 1, 1, 0, 0, 0)

    thread1 = add_fake_thread(db.session, default_account.namespace.id)
    thread2 = add_fake_thread(db.session, default_account.namespace.id)

    test_subject = "test_thread_view_count_with_category"

    for thread in [thread1, thread2]:
        add_fake_message(
            db.session,
            default_account.namespace.id,
            thread,
            subject=test_subject,
            received_date=date1,
        )
        add_fake_message(
            db.session,
            default_account.namespace.id,
            thread,
            subject=test_subject,
            received_date=date2,
            add_sent_category=True,
        )
        add_fake_message(
            db.session,
            default_account.namespace.id,
            thread,
            subject=test_subject,
            received_date=date3,
        )
        add_fake_message(
            db.session,
            default_account.namespace.id,
            thread,
            subject=test_subject,
            received_date=date4,
            add_sent_category=True,
        )
        add_fake_message(
            db.session,
            default_account.namespace.id,
            thread,
            subject=test_subject,
            received_date=date5,
        )

    resp = api_client.get_raw("/threads/?view=count&in=sent")
    assert resp.status_code == 200
    threads = json.loads(resp.data)
    assert threads["count"] == 2


@pytest.mark.skipif(True, reason="Need to investigate")
@pytest.mark.parametrize("api_version", API_VERSIONS)
def test_thread_label_updates(
    db, api_client, default_account, api_version, custom_label
):
    """
    Check that you can update a message (optimistically or not),
    and that the update is queued in the ActionLog.
    """
    headers = dict()
    headers["Api-Version"] = api_version

    # Gmail threads, messages have a 'labels' field
    gmail_thread = add_fake_thread(db.session, default_account.namespace.id)
    gmail_message = add_fake_message(
        db.session, default_account.namespace.id, gmail_thread
    )

    resp_data = api_client.get_data(
        f"/threads/{gmail_thread.public_id}", headers=headers
    )

    assert resp_data["labels"] == []

    category = custom_label.category
    update = dict(labels=[category.public_id])

    resp = api_client.put_data(
        f"/threads/{gmail_thread.public_id}", update, headers=headers
    )

    resp_data = json.loads(resp.data)

    if api_version == API_VERSIONS[0]:
        assert len(resp_data["labels"]) == 1
        assert resp_data["labels"][0]["id"] == category.public_id

        # Also check that the label got added to the message.
        resp_data = api_client.get_data(
            f"/messages/{gmail_message.public_id}", headers=headers
        )

        assert len(resp_data["labels"]) == 1
        assert resp_data["labels"][0]["id"] == category.public_id
    else:
        assert resp_data["labels"] == []
