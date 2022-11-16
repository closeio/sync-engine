import pytest
import responses

from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.events.microsoft.graph_client import BASE_URL


@pytest.fixture
def provider(client):
    provider = MicrosoftEventsProvider("fake_account_id", "fake_namespace_id")
    provider.client = client

    return provider


calendars_json = {
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADafAAA=",
            "name": "Calendar",
            "canEdit": True,
            "isDefaultCalendar": True,
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAIM0_ZOAAA=",
            "name": "Test",
            "canEdit": True,
            "isDefaultCalendar": False,
        },
    ],
}


@responses.activate
def test_sync_calendars(provider):
    responses.get(
        BASE_URL + "/me/calendars", json=calendars_json,
    )

    _, calendars = provider.sync_calendars()
    calendars_by_name = {calendar.name: calendar for calendar in calendars}

    assert not calendars_by_name["Calendar"].read_only
    assert calendars_by_name["Calendar"].default
    assert not calendars_by_name["Test"].read_only
    assert not calendars_by_name["Test"].default
