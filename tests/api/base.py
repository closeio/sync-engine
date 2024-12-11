import json
from base64 import b64encode


def new_api_client(db, namespace):
    from inbox.api.srv import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        return TestAPIClient(c, namespace.public_id)


class TestAPIClient:
    """Provide more convenient access to the API for testing purposes."""

    def __init__(self, test_client, default_namespace_public_id):
        self.client = test_client
        credential = f"{default_namespace_public_id}:".encode()
        self.auth_header = {
            "Authorization": f"Basic {b64encode(credential).decode()}"
        }

    def get_raw(self, path, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return self.client.get(path, headers=headers)

    def get_data(self, path, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return json.loads(self.client.get(path, headers=headers).data)

    def post_data(self, path, data, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return self.client.post(path, data=json.dumps(data), headers=headers)

    def post_raw(self, path, data, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return self.client.post(path, data=data, headers=headers)

    def put_data(self, path, data, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return self.client.put(path, headers=headers, data=json.dumps(data))

    def delete(self, path, data=None, headers=None):
        headers = headers or {}
        headers.update(self.auth_header)
        return self.client.delete(path, headers=headers, data=json.dumps(data))
