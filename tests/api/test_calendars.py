import pytest
from sqlalchemy import true

from inbox.models import Calendar
from tests.util.base import add_fake_event, db, default_namespace

__all__ = ["db", "default_namespace"]


@pytest.mark.parametrize(
    ("uid", "default"), [("inboxapptest@gmail.com", True), ("other", False)]
)
def test_get_google_calendar(db, default_namespace, api_client, uid, default):
    cal = Calendar(namespace_id=default_namespace.id, uid=uid, name="Holidays")
    db.session.add(cal)
    db.session.commit()
    cal_id = cal.public_id
    calendar_item = api_client.get_data(f"/calendars/{cal_id}")

    assert calendar_item["account_id"] == default_namespace.public_id
    assert calendar_item["name"] == "Holidays"
    assert calendar_item["description"] is None
    assert calendar_item["read_only"] is False
    assert calendar_item["object"] == "calendar"
    assert calendar_item["default"] == default


def test_get_outlook_calendar(db, outlook_namespace, make_api_client):
    api_client = make_api_client(db, outlook_namespace)
    cal = Calendar(
        namespace_id=outlook_namespace.id,
        uid="uid",
        name="Holidays",
        default=False,
    )
    db.session.add(cal)
    db.session.commit()
    cal_id = cal.public_id
    calendar_item = api_client.get_data(f"/calendars/{cal_id}")

    assert calendar_item["account_id"] == outlook_namespace.public_id
    assert calendar_item["name"] == "Holidays"
    assert calendar_item["description"] is None
    assert calendar_item["read_only"] is False
    assert calendar_item["object"] == "calendar"
    assert calendar_item["default"] is False


def test_inbox_calendar(db, outlook_namespace, make_api_client):
    api_client = make_api_client(db, outlook_namespace)
    cal = db.session.query(Calendar).filter_by(uid="inbox").one()
    cal_id = cal.public_id
    calendar_item = api_client.get_data(f"/calendars/{cal_id}")

    assert calendar_item["account_id"] == outlook_namespace.public_id
    assert calendar_item["name"] == "Emailed events"
    assert calendar_item["description"] == "Emailed events"
    assert calendar_item["read_only"] is True
    assert calendar_item["object"] == "calendar"
    assert calendar_item["default"] is None


def test_handle_not_found_calendar(api_client):
    resp_data = api_client.get_raw("/calendars/foo")
    assert resp_data.status_code == 404


def test_add_to_specific_calendar(db, default_namespace, api_client):
    cal = Calendar(namespace_id=default_namespace.id, uid="uid", name="Custom")
    db.session.add(cal)
    db.session.commit()
    cal_id = cal.public_id

    e_data = {
        "calendar_id": cal_id,
        "title": "subj",
        "description": "body1",
        "when": {"time": 1},
        "location": "NylasHQ",
    }
    r = api_client.post_data("/events", e_data)
    assert r.status_code == 200

    events = api_client.get_data(f"/events?calendar_id={cal_id}")
    assert len(events) == 1


def test_add_to_read_only_calendar(db, api_client):
    cal_list = api_client.get_data("/calendars")
    ro_cal = None
    for c in cal_list:
        if c["read_only"]:
            ro_cal = c

    assert ro_cal

    e_data = {
        "calendar_id": ro_cal["id"],
        "title": "subj",
        "description": "body1",
        "when": {"time": 1},
        "location": "NylasHQ",
    }
    resp = api_client.post_data("/events", e_data)
    assert resp.status_code == 400


def test_delete_from_readonly_calendar(db, default_namespace, api_client):
    add_fake_event(
        db.session,
        default_namespace.id,
        calendar=db.session.query(Calendar)
        .filter(
            Calendar.namespace_id == default_namespace.id,
            Calendar.read_only == true(),
        )
        .first(),
        read_only=True,
    )
    calendar_list = api_client.get_data("/calendars")

    read_only_calendar = None
    for c in calendar_list:
        if c["read_only"]:
            read_only_calendar = c
            break
    events = api_client.get_data(
        "/events?calendar_id={}".format(read_only_calendar["id"])
    )
    for event in events:
        if event["read_only"]:
            read_only_event = event
            break

    assert read_only_calendar
    assert read_only_event
    e_id = read_only_event["id"]
    resp = api_client.delete(f"/events/{e_id}")
    assert resp.status_code == 400
