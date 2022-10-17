import pytest
import responses

from inbox.events.microsoft_graph_client import BASE_URL, MicrosoftGraphClient


@pytest.fixture
def client():
    return MicrosoftGraphClient(lambda: "fake_token")


calendars_json = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users('0db5de84-a1b3-47bf-8342-44ab4f415fe4')/calendars",
    "value": [
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADafAAA=",
            "name": "Calendar",
            "color": "auto",
            "hexColor": "",
            "isDefaultCalendar": True,
            "changeKey": "bX+IPMmPs02YGYesdF/+dAAAAAACtA==",
            "canShare": True,
            "canViewPrivateItems": True,
            "canEdit": True,
            "allowedOnlineMeetingProviders": ["teamsForBusiness"],
            "defaultOnlineMeetingProvider": "teamsForBusiness",
            "isTallyingResponses": True,
            "isRemovable": False,
            "owner": {
                "name": "Sync Engine Testing",
                "address": "syncenginetesting@closetesting.onmicrosoft.com",
            },
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADagAAA=",
            "name": "United States holidays",
            "color": "auto",
            "hexColor": "",
            "isDefaultCalendar": False,
            "changeKey": "bX+IPMmPs02YGYesdF/+dAAAAAACwA==",
            "canShare": False,
            "canViewPrivateItems": True,
            "canEdit": False,
            "allowedOnlineMeetingProviders": [],
            "defaultOnlineMeetingProvider": "unknown",
            "isTallyingResponses": False,
            "isRemovable": True,
            "owner": {
                "name": "Sync Engine Testing",
                "address": "syncenginetesting@closetesting.onmicrosoft.com",
            },
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAAAADajAAA=",
            "name": "Birthdays",
            "color": "auto",
            "hexColor": "",
            "isDefaultCalendar": False,
            "changeKey": "bX+IPMmPs02YGYesdF/+dAAAAAAFCQ==",
            "canShare": False,
            "canViewPrivateItems": True,
            "canEdit": False,
            "allowedOnlineMeetingProviders": [],
            "defaultOnlineMeetingProvider": "unknown",
            "isTallyingResponses": False,
            "isRemovable": True,
            "owner": {
                "name": "Sync Engine Testing",
                "address": "syncenginetesting@closetesting.onmicrosoft.com",
            },
        },
        {
            "id": "AAMkADdiYzg5OGRlLTY1MjktNDc2Ni05YmVkLWMxMzFlNTQ0MzU3YQBGAAAAAACi9RQWB-SNTZBuALM6KIOsBwBtf4g8yY_zTZgZh6x0X-50AAAAAAEGAABtf4g8yY_zTZgZh6x0X-50AAIM0_ZOAAA=",
            "name": "Test",
            "color": "auto",
            "hexColor": "",
            "isDefaultCalendar": False,
            "changeKey": "bX+IPMmPs02YGYesdF/+dAACEomFgA==",
            "canShare": True,
            "canViewPrivateItems": True,
            "canEdit": True,
            "allowedOnlineMeetingProviders": ["teamsForBusiness"],
            "defaultOnlineMeetingProvider": "teamsForBusiness",
            "isTallyingResponses": False,
            "isRemovable": True,
            "owner": {
                "name": "Sync Engine Testing",
                "address": "syncenginetesting@closetesting.onmicrosoft.com",
            },
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
