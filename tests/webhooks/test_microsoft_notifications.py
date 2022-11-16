from unittest import mock

import pytest

from inbox.config import config
from inbox.models.calendar import Calendar


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


def test_validate_webhook_payload_malformed(test_client):
    response = test_client.post(
        "/w/microsoft/calendar_list_update/fake_id", data="something"
    )

    assert response.data.decode() == "Malformed JSON payload"
    assert response.status_code == 400


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


def test_calendar_update_404(test_client):
    response = test_client.post(
        "/w/microsoft/calendar_list_update/does_not_exist",
        json={
            "value": [
                {
                    "changeType": "updated",
                    "resourceData": {
                        "@odata.type": "#Microsoft.Graph.Calendar",
                        "id": "fake_id",
                    },
                    "clientState": "good_s3cr3t",
                }
            ]
        },
    )

    assert response.status_code == 404


def test_calendar_update(db, test_client, outlook_account):
    assert outlook_account.webhook_calendar_list_last_ping is None

    response = test_client.post(
        f"/w/microsoft/calendar_list_update/{outlook_account.public_id}",
        json={
            "value": [
                {
                    "changeType": "updated",
                    "resourceData": {
                        "@odata.type": "#Microsoft.Graph.Calendar",
                        "id": "fake_id",
                    },
                    "clientState": "good_s3cr3t",
                }
            ]
        },
    )

    db.session.refresh(outlook_account)
    assert outlook_account.webhook_calendar_list_last_ping is not None

    assert response.status_code == 200


def test_event_update_404(test_client):
    response = test_client.post(
        "/w/microsoft/calendar_update/does_not_exist",
        json={
            "value": [
                {
                    "changeType": "updated",
                    "resourceData": {
                        "@odata.type": "#Microsoft.Graph.Event",
                        "id": "fake_id",
                    },
                    "clientState": "good_s3cr3t",
                }
            ]
        },
    )

    assert response.status_code == 404


def test_event_update(db, test_client, outlook_account):
    calendar = Calendar(
        name="Calendar",
        uid="uid",
        read_only=False,
        namespace_id=outlook_account.namespace.id,
    )
    db.session.add(calendar)
    db.session.commit()

    assert calendar.webhook_last_ping is None

    response = test_client.post(
        f"/w/microsoft/calendar_update/{calendar.public_id}",
        json={
            "value": [
                {
                    "changeType": "updated",
                    "resourceData": {
                        "@odata.type": "#Microsoft.Graph.Event",
                        "id": "fake_id",
                    },
                    "clientState": "good_s3cr3t",
                }
            ]
        },
    )

    db.session.refresh(calendar)

    assert calendar.webhook_last_ping is not None

    assert response.status_code == 200
