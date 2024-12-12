import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from inbox.util.url import url_concat
from tests.util.base import add_fake_message

EPSILON = 0.5  # Switching time. VMs on Macs suck :()
LONGPOLL_EPSILON = 2 + EPSILON  # API implementation polls every second


@pytest.fixture
def streaming_test_client(db):
    from inbox.api.srv import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def get_cursor(api_client, timestamp, namespace):
    cursor_response = api_client.post_data(
        "/delta/generate_cursor", data={"start": timestamp}
    )
    return json.loads(cursor_response.data)["cursor"]


def validate_response_format(response_string) -> None:
    response = json.loads(response_string)
    assert "cursor" in response
    assert "attributes" in response
    assert "object" in response
    assert "id" in response
    assert "event" in response


def test_response_when_old_cursor_given(
    db, api_client, default_namespace
) -> None:
    url = url_concat("/delta/streaming", {"timeout": 0.1, "cursor": "0"})
    r = api_client.get_raw(url)
    assert r.status_code == 200
    responses = r.get_data(as_text=True).split("\n")
    for response_string in responses:
        if response_string:
            validate_response_format(response_string)


def test_empty_response_when_latest_cursor_given(
    db, api_client, default_namespace
) -> None:
    cursor = get_cursor(api_client, int(time.time() + 22), default_namespace)
    url = url_concat("/delta/streaming", {"timeout": 0.1, "cursor": cursor})
    r = api_client.get_raw(url)
    assert r.status_code == 200
    assert r.get_data(as_text=True).strip() == ""


def test_exclude_and_include_object_types(
    db, api_client, thread, default_namespace
) -> None:
    add_fake_message(
        db.session,
        default_namespace.id,
        thread,
        from_addr=[("Bob", "bob@foocorp.com")],
    )
    # Check that we do get message and contact changes by default.
    url = url_concat("/delta/streaming", {"timeout": 0.1, "cursor": "0"})
    r = api_client.get_raw(url)
    assert r.status_code == 200
    responses = r.get_data(as_text=True).split("\n")
    parsed_responses = [json.loads(resp) for resp in responses if resp != ""]
    assert any(resp["object"] == "message" for resp in parsed_responses)
    assert any(resp["object"] == "contact" for resp in parsed_responses)

    # And check that we don't get message/contact changes if we exclude them.
    url = url_concat(
        "/delta/streaming",
        {"timeout": 0.1, "cursor": "0", "exclude_types": "message,contact"},
    )
    r = api_client.get_raw(url)
    assert r.status_code == 200
    responses = r.get_data(as_text=True).split("\n")
    parsed_responses = [json.loads(resp) for resp in responses if resp != ""]
    assert not any(resp["object"] == "message" for resp in parsed_responses)
    assert not any(resp["object"] == "contact" for resp in parsed_responses)

    # And check we only get message objects if we use include_types
    url = url_concat(
        "/delta/streaming",
        {"timeout": 0.1, "cursor": "0", "include_types": "message"},
    )
    r = api_client.get_raw(url)
    assert r.status_code == 200
    responses = r.get_data(as_text=True).split("\n")
    parsed_responses = [json.loads(resp) for resp in responses if resp != ""]
    assert all(resp["object"] == "message" for resp in parsed_responses)


def test_expanded_view(
    db, api_client, thread, message, default_namespace
) -> None:
    url = url_concat(
        "/delta/streaming",
        {
            "timeout": 0.1,
            "cursor": "0",
            "include_types": "message,thread",
            "view": "expanded",
        },
    )
    r = api_client.get_raw(url)
    assert r.status_code == 200
    responses = r.get_data(as_text=True).split("\n")
    parsed_responses = [json.loads(resp) for resp in responses if resp != ""]
    for delta in parsed_responses:
        if delta["object"] == "message":
            assert "headers" in delta["attributes"]
        elif delta["object"] == "thread":
            assert "messages" in delta["attributes"]


def test_invalid_timestamp(api_client, default_namespace) -> None:
    # Valid UNIX timestamp
    response = api_client.post_data(
        "/delta/generate_cursor", data={"start": int(time.time())}
    )
    assert response.status_code == 200

    # Invalid timestamp
    response = api_client.post_data(
        "/delta/generate_cursor", data={"start": 1434591487647}
    )
    assert response.status_code == 400


def test_longpoll_delta_newitem(
    db, api_client, default_namespace, thread
) -> None:
    cursor = get_cursor(api_client, int(time.time() + 22), default_namespace)
    url = url_concat("/delta/longpoll", {"cursor": cursor})
    start_time = time.time()
    with ThreadPoolExecutor(max_workers=1) as executor:
        # Spawn the request in background thread
        longpoll_future = executor.submit(api_client.get_raw, url)
        # This should make it return immediately
        add_fake_message(
            db.session,
            default_namespace.id,
            thread,
            from_addr=[("Bob", "bob@foocorp.com")],
        )
        # context manager blocks and waits for
        # longpoll_future to finish when it exists
    end_time = time.time()
    assert end_time - start_time < LONGPOLL_EPSILON
    parsed_responses = json.loads(longpoll_future.result().data)
    assert len(parsed_responses["deltas"]) == 3
    assert {k["object"] for k in parsed_responses["deltas"]} == {
        "message",
        "contact",
        "thread",
    }


def test_longpoll_delta_timeout(db, api_client, default_namespace) -> None:
    test_timeout = 2
    cursor = get_cursor(api_client, int(time.time() + 22), default_namespace)
    url = url_concat(
        "/delta/longpoll", {"timeout": test_timeout, "cursor": cursor}
    )
    start_time = time.time()
    resp = api_client.get_raw(url)
    end_time = time.time()
    assert resp.status_code == 200

    assert end_time - start_time - test_timeout < EPSILON
    parsed_responses = json.loads(resp.data)
    assert len(parsed_responses["deltas"]) == 0
    assert type(parsed_responses["deltas"]) == list
    assert parsed_responses["cursor_start"] == cursor
    assert parsed_responses["cursor_end"] == cursor
