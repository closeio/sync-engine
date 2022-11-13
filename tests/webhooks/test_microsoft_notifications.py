def test_handle_initial_validation_response(test_client):
    response = test_client.post(
        "/w/microsoft/calendar_list_update/fake_id",
        query_string={"validationToken": "asd"},
    )

    assert response.data == b"asd"
    assert response.mimetype == "text/plain"
    assert response.status_code == 200
