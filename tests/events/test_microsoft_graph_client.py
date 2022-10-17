import datetime
import json

import ciso8601
import pytest
import pytz
import responses

from inbox.events.microsoft_graph_client import BASE_URL, MicrosoftGraphClient


@pytest.fixture
def client():
    return MicrosoftGraphClient(lambda: "fake_token")


calendars_json = {
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADafAAA=",
            "name": "Calendar",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADagAAA=",
            "name": "United States holidays",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADajAAA=",
            "name": "Birthdays",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAIM0_ZOAAA=",
            "name": "Test",
        },
    ],
}


@responses.activate
def test_iter_calendars(client):
    responses.get(
        BASE_URL + "/me/calendars", json=calendars_json,
    )

    calendars = client.iter_calendars()
    assert {calendar["name"] for calendar in calendars} == {
        "Calendar",
        "Birthdays",
        "United States holidays",
        "Test",
    }


@responses.activate
def test_get_calendar(client):
    responses.get(
        BASE_URL + f"/me/calendars/{calendars_json['value'][0]['id']}",
        json=calendars_json["value"][0],
    )

    calendar = client.get_calendar(calendars_json["value"][0]["id"])
    assert calendar["name"] == "Calendar"


events_json = {
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAENAABtf4g8yY_zTZgZh6x0X-50AAIDZtFgAAA=",
            "lastModifiedDateTime": "2022-09-09T12:09:43.7205143Z",
            "subject": "Business meeting",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAENAABtf4g8yY_zTZgZh6x0X-50AAIDZtFaAAA=",
            "lastModifiedDateTime": "2022-09-09T08:39:48.5690063Z",
            "subject": "Contract negotations",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAENAABtf4g8yY_zTZgZh6x0X-50AAIARKVOAAA=",
            "lastModifiedDateTime": "2022-09-07T17:36:12.925491Z",
            "subject": "Renewal agreement",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAENAABtf4g8yY_zTZgZh6x0X-50AAIARKVNAAA=",
            "lastModifiedDateTime": "2022-09-07T17:43:56.0246319Z",
            "subject": "API questions",
        },
    ]
}


@responses.activate
def test_iter_events(client):
    responses.get(
        BASE_URL + "/me/calendars/fake_calendar_id/events", json=events_json,
    )

    events = client.iter_events("fake_calendar_id")
    assert {event["subject"] for event in events} == {
        "Business meeting",
        "Contract negotations",
        "Renewal agreement",
        "API questions",
    }


@responses.activate
def test_iter_events_modified_after(client):
    def request_callback(request):
        ((_, odata_filter),) = request.params.items()
        _, _, modified_after = odata_filter.split()
        modified_after = ciso8601.parse_datetime(modified_after)

        events = [
            event
            for event in events_json["value"]
            if ciso8601.parse_datetime(event["lastModifiedDateTime"]) > modified_after
        ]

        return (200, {}, json.dumps({"value": events}))

    responses.add_callback(
        responses.GET,
        BASE_URL + "/me/calendars/fake_calendar_id/events",
        callback=request_callback,
        content_type="application/json",
    )

    events = client.iter_events(
        "fake_calendar_id",
        modified_after=datetime.datetime(2022, 9, 9, 12, tzinfo=pytz.UTC),
    )

    assert {event["subject"] for event in events} == {"Business meeting"}
