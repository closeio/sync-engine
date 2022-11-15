from unittest import mock

import pytest

from inbox.config import config


@pytest.fixture(autouse=True)
def populate_microsoft_subscrtipion_secret():
    with mock.patch.dict(config, {"MICROSOFT_SUBSCRIPTION_SECRET": "good_s3cr3t"}):
        yield


def test_handle_initial_validation_response(test_client):
    response = test_client.post(
        "/w/microsoft/calendar_list_update/fake_id",
        query_string={"validationToken": "asd"},
    )

    assert response.data.decode() == "asd"
    assert response.mimetype == "text/plain"
    assert response.status_code == 200


bad_client_state_payload = {
    "value": [
        {
            "changeType": "updated",
            "resourceData": {
                "@odata.type": "#Microsoft.Graph.Calendar",
                "id": "fake_id",
            },
            "clientState": "wrong_s3cr3t",
        }
    ]
}

bad_type_payload = {
    "value": [
        {
            "changeType": "updated",
            "resourceData": {"@odata.type": "#Microsoft.Graph.Event", "id": "fake_id"},
            "clientState": "good_s3cr3t",
        }
    ]
}


@pytest.mark.parametrize(
    "payload,data",
    [
        (
            bad_client_state_payload,
            "'clientState' did not match one provided when creating subscription",
        ),
        (bad_type_payload, "Expected '@odata.type' to be '#Microsoft.Graph.Calendar'"),
    ],
)
def test_validate_webhook_payload(test_client, payload, data):
    response = test_client.post(
        "/w/microsoft/calendar_list_update/fake_id", json=payload
    )

    assert response.data.decode() == data
    assert response.status_code == 400
