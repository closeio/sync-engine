import datetime
import json

import ciso8601
import pytest
import pytz
import responses
from responses.registries import OrderedRegistry

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
@pytest.mark.parametrize(
    "modified_after,subjects",
    [
        (datetime.datetime(2022, 9, 9, 12, tzinfo=pytz.UTC), {"Business meeting"}),
        (
            datetime.datetime(2022, 9, 8, 12, tzinfo=pytz.UTC),
            {"Business meeting", "Contract negotations"},
        ),
    ],
)
def test_iter_events_modified_after(client, modified_after, subjects):
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

    events = client.iter_events("fake_calendar_id", modified_after=modified_after,)

    assert {event["subject"] for event in events} == subjects


@responses.activate
def test_get_event(client):
    responses.get(
        BASE_URL + f"/me/events/{events_json['value'][0]['id']}",
        json=events_json["value"][0],
    )

    event = client.get_event(events_json["value"][0]["id"])
    assert event["subject"] == "Business meeting"


event_instances_first_page = {
    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/events/fake_event_id/instances?startDateTime=2022-10-17&endDateTime=2023-10-17&$skip=2",
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2q-SiNUAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACG91UqgAAbX_IPMmPs02YGYesdF-_dAACG93T0AAAEA==",
            "subject": "Instance 1",
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2rCbsz7AAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACG91UqgAAbX_IPMmPs02YGYesdF-_dAACG93T0AAAEA==",
            "subject": "Instance 2",
        },
    ],
}

event_instances_second_page = {
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQFRAAgI2rFk3aiAAEYAAAAAovUUFgf0jU2QbgCzOiiDrAcAbX_IPMmPs02YGYesdF-_dAACG91UqgAAbX_IPMmPs02YGYesdF-_dAACG93T0AAAEA==",
            "subject": "Instance 3",
        }
    ]
}


@responses.activate(registry=OrderedRegistry)
def test_iter_event_instances(client):
    responses.get(
        BASE_URL + "/me/events/fake_event_id/instances",
        json=event_instances_first_page,
    )

    responses.get(
        event_instances_first_page["@odata.nextLink"], json=event_instances_second_page
    )

    instances = client.iter_event_instances(
        "fake_event_id",
        start=datetime.datetime(2022, 10, 17, tzinfo=pytz.UTC),
        end=datetime.datetime(2023, 10, 17, tzinfo=pytz.UTC),
    )

    assert {instance["subject"] for instance in instances} == {
        "Instance 1",
        "Instance 2",
        "Instance 3",
    }


@responses.activate
def test_subscribe_to_calendar_changes(client):
    def request_callback(request):
        return (200, {}, request.body)

    responses.add_callback(
        responses.POST,
        BASE_URL + "/subscriptions",
        callback=request_callback,
        content_type="application/json",
    )

    subscription = client.subscribe_to_calendar_changes(
        webhook_url="https://example.com", secret="s3cr3t"
    )

    assert subscription["resource"] == "/me/calendars"
    assert subscription["changeType"] == "updated,deleted"
    assert subscription["notificationUrl"] == "https://example.com"
    assert subscription["clientState"] == "s3cr3t"


@responses.activate
def test_subscribe_to_event_changes(client):
    def request_callback(request):
        return (200, {}, request.body)

    responses.add_callback(
        responses.POST,
        BASE_URL + "/subscriptions",
        callback=request_callback,
        content_type="application/json",
    )

    subscription = client.subscribe_to_event_changes(
        "fake_calendar_id", webhook_url="https://example.com", secret="s3cr3t"
    )

    assert subscription["resource"] == "/me/calendars/fake_calendar_id/events"
    assert subscription["changeType"] == "created,updated,deleted"
    assert subscription["notificationUrl"] == "https://example.com"
    assert subscription["clientState"] == "s3cr3t"
