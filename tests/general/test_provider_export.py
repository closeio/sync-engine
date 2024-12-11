import json

from inbox.providers import providers


def test_provider_export_as_json() -> None:
    """Provider dict should be exportable as json"""
    assert json.dumps(dict(providers))
