import pytest  # noqa: INP001

from inbox.events.microsoft.graph_client import MicrosoftGraphClient


@pytest.fixture
def client():
    return MicrosoftGraphClient(lambda: "fake_token")
